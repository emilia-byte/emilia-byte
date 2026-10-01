"""
Post content to a Facebook Page using a Multilogin browser profile.

Reads posts from a text file (3 posts separated by blank lines).
The post containing a URL is always posted first, followed by the rest in order.
A random delay is added between posts to appear natural.

Usage:
    python3 post.py --account "NOS_PAN_001" --posts path/to/posts.txt
    python3 post.py --account "NOS_PAN_001" --posts path/to/posts.txt --min-delay 5 --max-delay 10

Requirements:
    - MLX_EMAIL and MLX_PASSWORD environment variables must be set.
    - mlx_profiles.json must have an entry for the account.
    - The Multilogin profile must already be logged in to Facebook
      (run manual_session.py first if not).
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from mlx_context import start_profile_for
from fb_dom import js_navigate
from state_file import read_json, update_json

log = logging.getLogger(__name__)

PAGE_URLS_PATH = Path(__file__).parent / "page_urls.json"


def _load_page_urls() -> dict:
    return read_json(PAGE_URLS_PATH)


_INVALID_PAGE_SLUGS = {
    "professional_dashboard", "professional-dashboard", "dashboard",
    "creatorstudio", "pages", "settings", "notifications", "help",
    "login", "marketplace", "groups", "events", "bookmarks", "watch",
    "friends", "memories", "saved", "videos", "photos", "reels",
    "business", "ads", "about", "privacy", "policies", "terms",
    "fundraisers", "climate", "jobs", "facebook", "messenger", "instagram",
}


def _is_valid_page_url(url: str) -> bool:
    """Return True only if url looks like a real Facebook Page (not a dashboard or tool)."""
    clean = url.split("?")[0].rstrip("/")
    slug = clean.replace("https://www.facebook.com/", "").strip("/")
    if not slug or slug in _INVALID_PAGE_SLUGS:
        return False
    if "/" in slug and not slug.startswith("pages/"):
        return False
    return True


def _save_page_url(account: str, url: str) -> None:
    data = _load_page_urls()
    id_match = re.search(r'[?&]id=(\d+)', url)
    if id_match:
        clean = f"https://www.facebook.com/{id_match.group(1)}"
    else:
        clean = url.split("?")[0].rstrip("/")
    if not _is_valid_page_url(clean):
        return
    if data.get(account) != clean:
        # Other profiles in a batch may be saving theirs concurrently.
        update_json(PAGE_URLS_PATH, lambda d: d.__setitem__(account, clean))
        print(f"  Cached page URL for {account}: {clean}")

DEFAULT_MIN_DELAY = 45
DEFAULT_MAX_DELAY = 90

LAST_POST_PATH = Path(__file__).parent / "last_post.json"


def parse_posts(path):
    """Parse posts.txt into a list of {text, url} dicts, URL post first."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    blocks = [b.strip() for b in re.split(r"\n{2,}", raw) if b.strip()]

    posts = []
    url_pattern = re.compile(r"https?://\S+")

    for block in blocks:
        lines = block.splitlines()
        url = None
        text_lines = []
        for line in lines:
            match = url_pattern.search(line)
            if match and line.strip() == match.group():
                url = line.strip()
            else:
                text_lines.append(line)
        posts.append({"text": "\n".join(text_lines).strip(), "url": url})

    # Merge standalone URL-only blocks into adjacent text blocks
    merged = []
    i = 0
    while i < len(posts):
        post = posts[i]
        if post["url"] and not post["text"]:
            if i + 1 < len(posts) and not posts[i + 1]["url"]:
                posts[i + 1]["url"] = post["url"]
            elif merged and not merged[-1]["url"]:
                merged[-1]["url"] = post["url"]
            else:
                merged.append(post)
        else:
            merged.append(post)
        i += 1

    url_posts  = [p for p in merged if p["url"]]
    text_posts = [p for p in merged if not p["url"]]
    return url_posts + text_posts


def human_type(page, text, min_delay=0.04, max_delay=0.14):
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(min_delay, max_delay))


def human_pause(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def open_composer(page):
    """Click 'What's on your mind?' then wait for the dialog to open."""
    human_pause(1.0, 1.5)

    # Scroll down slightly so the composer box comes into view
    try:
        page.evaluate("window.scrollBy(0, 300)")
        human_pause(0.5, 1.0)
    except Exception as exc:
        log.debug("open_composer: ignored error: %s", exc)

    COMPOSER_SELECTORS = [
        "div[role='main'] div[role='button']:has-text(\"What's on your mind\")",
        "div[role='main'] div[aria-label*=\"What's on your mind\"]",
        "div[role='main'] span:has-text(\"What's on your mind\")",
        "div[role='button']:has-text(\"What's on your mind\")",
        "[aria-label*=\"What's on your mind\"]",
    ]
    CONFIRM_SELECTORS = [
        "div[contenteditable='true'][role='textbox']",
        "div[role='dialog']",
        "div[aria-label='Create post']",
    ]

    for selector in COMPOSER_SELECTORS:
        try:
            el = page.locator(selector).first
            if el.count() == 0:
                continue
            # Scroll element into view even if partially off-screen
            try:
                el.scroll_into_view_if_needed(timeout=3000)
                human_pause(0.3, 0.6)
            except Exception as exc:
                log.debug("open_composer: ignored error: %s", exc)
            el.click()
            human_pause(2.0, 3.0)
            for confirm in CONFIRM_SELECTORS:
                try:
                    page.wait_for_selector(confirm, timeout=5000)
                    human_pause(1.0, 1.5)
                    return True
                except Exception as exc:
                    log.debug("open_composer: ignored error: %s", exc)
                    continue
        except Exception as exc:
            log.debug("open_composer: ignored error: %s", exc)
            continue

    try:
        page.screenshot(path=os.path.join(os.path.dirname(__file__), "debug_composer.png"))
        print("Composer not found. Screenshot saved to debug_composer.png")
    except Exception as exc:
        log.debug("failed to save debug screenshot: %s", exc)
    return False


def submit_post(page):
    """Dismiss autocomplete, click Next or Post, then handle the Next→Post two-step flow."""
    # Dismiss hashtag/mention autocomplete without closing the dialog
    try:
        autocomplete = page.locator("div[role='dialog'] ul[role='listbox'], div[role='dialog'] div[role='listbox']")
        if autocomplete.count() > 0:
            header = page.locator("div[role='dialog'] h2").first
            if header.is_visible():
                header.click()
            else:
                page.locator("div[role='dialog']").first.click(position={"x": 10, "y": 10})
            human_pause(0.3, 0.6)
    except Exception as exc:
        log.debug("autocomplete dismissal failed: %s", exc)

    def _click_first_visible(selectors):
        for selector in selectors:
            try:
                el = page.locator(selector).last
                if el.is_visible():
                    el.click()
                    return True
            except Exception as exc:
                log.debug("submit selector %r failed: %s", selector, exc)
                continue
        for name in [r"^Next$", r"^Post$"]:
            try:
                btn = page.locator("div[role='dialog']").get_by_role(
                    "button", name=re.compile(name, re.I)
                ).last
                if btn.is_visible():
                    btn.click()
                    return True
            except Exception as exc:
                log.debug("submit button role-name %r failed: %s", name, exc)
                continue
        return False

    all_selectors = [
        "div[role='dialog'] div[aria-label='Next'][role='button']",
        "div[role='dialog'] div[aria-label='Post'][role='button']",
        "div[role='dialog'] div[aria-label='Publish'][role='button']",
        "div[role='dialog'] div[role='button']:has-text('Next')",
        "div[role='dialog'] div[role='button']:has-text('Post')",
        "div[role='dialog'] div[role='button']:has-text('Publish')",
        "div[role='dialog'] div[role='button']:has-text('Share')",
        "div[role='dialog'] [data-testid='react-composer-post-button']",
    ]

    # Wait up to 20s for the submit button to become enabled.
    # It's disabled while an image is uploading or in an error state.
    enabled_sel = (
        "div[role='dialog'] div[aria-label='Next'][role='button']:not([aria-disabled='true']), "
        "div[role='dialog'] div[aria-label='Post'][role='button']:not([aria-disabled='true']), "
        "div[role='dialog'] div[role='button']:not([aria-disabled='true']):has-text('Next'), "
        "div[role='dialog'] div[role='button']:not([aria-disabled='true']):has-text('Post')"
    )
    try:
        page.wait_for_selector(enabled_sel, timeout=20000)
    except Exception as exc:
        log.debug("submit_post: ignored error: %s", exc)
        # Button still disabled — attachment failed or is stuck. Clear it.
        print("Submit button not ready — clearing any stuck attachment...")
        # Text-based check first (covers the explicit "can't be uploaded" error)
        try:
            dialog = page.locator("div[role='dialog']")
            if dialog.filter(has_text="can't be uploaded").count() > 0:
                print("File upload error detected.")
        except Exception as exc:
            log.debug("submit_post: ignored error: %s", exc)
        # Remove any visible attachment regardless of error text
        for rm_sel in [
            "div[role='dialog'] [aria-label*='Remove']",
            "div[role='dialog'] [aria-label*='remove']",
        ]:
            try:
                rm = page.locator(rm_sel).first
                if rm.count() > 0 and rm.is_visible():
                    rm.click()
                    human_pause(2.0, 3.0)
                    break
            except Exception as exc:
                log.debug("submit_post: ignored error: %s", exc)
                continue

    clicked = _click_first_visible(all_selectors)

    # Last resort: force-click even if aria-disabled
    if not clicked:
        for selector in all_selectors:
            try:
                el = page.locator(selector).last
                if el.count() > 0:
                    el.click(force=True)
                    clicked = True
                    print("Force-clicked submit button.")
                    break
            except Exception as exc:
                log.debug("submit_post: ignored error: %s", exc)
                continue

    if not clicked:
        return False

    # Handle the Next → Post two-step flow (common with link previews):
    # clicking Next loads a second step with the final Post/Publish button.
    human_pause(2.5, 3.5)
    final_selectors = [
        "div[role='dialog'] div[aria-label='Post'][role='button']",
        "div[role='dialog'] div[aria-label='Publish'][role='button']",
        "div[role='dialog'] div[role='button']:has-text('Post')",
        "div[role='dialog'] div[role='button']:has-text('Publish')",
        "div[role='dialog'] div[role='button']:has-text('Share')",
        "div[role='dialog'] [data-testid='react-composer-post-button']",
    ]
    try:
        for selector in final_selectors:
            el = page.locator(selector).last
            if el.is_visible():
                print("Clicking final Post button...")
                el.click()
                break
    except Exception as exc:
        log.debug("final Post/Publish click failed: %s", exc)

    return True


IMAGES_DIR = Path(__file__).parent / "images"


def attach_image(page, image_path: Path) -> bool:
    """Click the photo button in the composer and attach the image file."""

    def _via_chooser(locator) -> bool:
        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                locator.click()
            fc_info.value.set_files(str(image_path))
            # Wait for Facebook to render the image preview thumbnail before proceeding.
            # Without this, submit_post() fires while the upload is still in flight.
            preview_appeared = False
            for sel in [
                "div[role='dialog'] [aria-label*='Remove']",
                "div[role='dialog'] img[src^='blob:']",
            ]:
                try:
                    page.wait_for_selector(sel, timeout=20000)
                    preview_appeared = True
                    break
                except Exception as exc:
                    log.debug("_via_chooser: ignored error: %s", exc)
                    continue
            if preview_appeared:
                print(f"Image attached: {image_path.name}")
            else:
                print(f"Image set (no preview visible): {image_path.name}")
            human_pause(1.0, 2.0)
            return True
        except Exception as exc:
            log.debug("file-chooser attach failed: %s", exc)
            return False

    # Phase 1: wait up to 8s for the Add-to-post toolbar to appear, then click the icon
    try:
        page.wait_for_selector(
            "div[role='dialog'] [aria-label='Photo/video'], div[role='dialog'] [aria-label*='Photo'], [aria-label='Photo/video']",
            timeout=8000,
        )
    except Exception as exc:
        log.debug("photo toolbar wait failed: %s", exc)
    human_pause(0.5, 1.0)
    for selector in [
        "div[role='dialog'] [aria-label='Photo/video']",
        "div[role='dialog'] [aria-label*='Photo']",
        "[aria-label='Photo/video']",
    ]:
        try:
            btn = page.locator(selector).first
            if not btn.is_visible():
                continue
            if _via_chooser(btn):
                return True
            # Clicking opened the "Add to your post" panel — proceed to Phase 2
            human_pause(0.5, 1.0)
            break
        except Exception as exc:
            log.debug("photo button selector %r failed: %s", selector, exc)
            continue

    # Phase 2: "Add to your post" panel is open — click Photo/video inside it
    for selector in [
        "span:has-text('Photo/video')",
        "div[role='menuitem']:has-text('Photo/video')",
        "li:has-text('Photo/video')",
        "div:has-text('Photo/video')",
    ]:
        try:
            el = page.locator(selector).first
            if el.is_visible():
                if _via_chooser(el):
                    return True
        except Exception as exc:
            log.debug("Add-to-post panel selector %r failed: %s", selector, exc)
            continue

    # Phase 3: direct file input fallback
    for selector in [
        "div[role='dialog'] input[type='file']",
        "input[type='file']",
    ]:
        try:
            file_input = page.locator(selector).first
            file_input.set_input_files(str(image_path))
            print(f"Image attached: {image_path.name}")
            human_pause(3.0, 5.0)
            return True
        except Exception as exc:
            log.debug("file input selector %r failed: %s", selector, exc)
            continue

    print("Could not attach image — photo button not found.")
    return False


POST_ID_PATTERN = re.compile(r"(?:story_fbid=|fbid=|/posts/|/permalink/)([\w.-]+)")


def capture_published_post(page) -> dict | None:
    """
    Best-effort: read the newest post's permalink out of the page feed right
    after publishing and pull an ID out of it, so boost.py doesn't have to
    guess "the most recent post" from Ads Manager's table sort order alone.

    Facebook post links carry either a legacy numeric story_fbid or (on
    newer rollouts) an opaque pfbid token, depending on the account -- this
    returns whichever is present. boost.py only trusts the numeric form for
    an exact match against the Ads Manager "Select post" table (that table
    only ever shows plain numeric IDs) and falls back loudly, with a
    warning, when it can't verify one. This is deliberately best-effort: it
    narrows the failure mode from "silently boosts the wrong post" to
    "tells you it isn't sure," not a guaranteed exact match.
    """
    try:
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    except Exception as exc:
        log.debug("scroll-to-top before post-ID capture failed: %s", exc)
    human_pause(1.5, 2.5)

    for selector in [
        "div[role='article'] a[href*='/posts/']",
        "div[role='article'] a[href*='story_fbid']",
        "div[role='article'] a[href*='/permalink/']",
        "a[href*='/posts/'][aria-label]",
        "a[href*='story_fbid'][aria-label]",
    ]:
        try:
            links = page.locator(selector).all()
        except Exception as exc:
            log.debug("post-ID capture selector %r failed: %s", selector, exc)
            continue
        for link in links[:3]:
            try:
                href = link.get_attribute("href") or ""
            except Exception as exc:
                log.debug("could not read href during post-ID capture: %s", exc)
                continue
            match = POST_ID_PATTERN.search(href)
            if match:
                return {"post_id": match.group(1), "url": href}
    return None


def save_last_post(account_name: str, post_info: dict | None) -> None:
    """Record what post.py just published so boost.py can verify it's
    boosting the right post instead of inferring "most recent" blind.
    Batch runs call this from several profiles at once, so it goes through
    update_json() rather than a bare read-modify-write."""
    record = {
        "post_id": post_info.get("post_id") if post_info else None,
        "url": post_info.get("url") if post_info else None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    update_json(LAST_POST_PATH, lambda data: data.__setitem__(account_name, record))


def publish_post(page, post, index, page_url="https://www.facebook.com/", image_path=None, navigate=True):
    print(f"\n── Post {index + 1} {'(with link)' if post['url'] else ''} {'📷' if image_path else ''} ──")

    if navigate:
        for attempt in range(4):
            js_navigate(page, page_url)
            try:
                page.wait_for_selector("div[role='main']", timeout=30000)
                break
            except Exception as exc:
                log.debug("publish_post: ignored error: %s", exc)
                if attempt == 3:
                    print("Page failed to load after 4 attempts — continuing anyway.")
                    break
                wait = [5, 10, 20][attempt]
                print(f"Page load failed (attempt {attempt + 1}/4), retrying in {wait}s...")
                time.sleep(wait)
        human_pause(2.5, 4.0)
    else:
        # After a previous post the dialog closes and we're still on the page feed.
        # Scroll to top instantly so the "What's on your mind?" button is in view.
        human_pause(2.0, 3.0)
        # Dismiss any dialog left open from a previous failed post
        try:
            close_btn = page.locator("div[role='dialog'] [aria-label='Close'], div[role='dialog'] [aria-label='close']").first
            if close_btn.is_visible():
                close_btn.click()
                human_pause(1.0, 1.5)
        except Exception as exc:
            log.debug("publish_post: ignored error: %s", exc)
        try:
            page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        except Exception as exc:
            log.debug("scroll-to-top failed: %s", exc)
        human_pause(1.0, 1.5)

    print("Opening composer...")
    if not open_composer(page):
        print("Could not find composer. Skipping this post.")
        return False

    print("Typing post content...")
    try:
        textbox = page.locator("div[role='dialog'] div[role='textbox'][contenteditable='true']").first
        textbox.click()
        human_pause(0.5, 1.0)
    except Exception as exc:
        log.debug("composer textbox click failed: %s", exc)

    human_type(page, post["text"])

    # Dismiss any hashtag/mention autocomplete before looking for the photo button —
    # the autocomplete dropdown sits on top of the Add-to-post toolbar and hides it.
    try:
        page.keyboard.press("Escape")
        human_pause(0.4, 0.7)
    except Exception as exc:
        log.debug("autocomplete-dismiss Escape failed: %s", exc)

    if image_path and image_path.exists():
        attach_image(page, image_path)

    if post["url"]:
        human_pause(0.5, 1.2)
        print(f"Adding URL: {post['url']}")
        try:
            textbox = page.locator("div[role='dialog'] div[role='textbox'][contenteditable='true']").first
            textbox.click()
            human_pause(0.5, 0.8)
        except Exception as exc:
            log.debug("publish_post: ignored error: %s", exc)
        page.keyboard.press("Control+End")  # move cursor to absolute end of content
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")  # blank line between hashtags and URL
        human_type(page, post["url"])
        print("Waiting for link preview to load...")
        human_pause(5.0, 7.0)

    human_pause(1.0, 2.0)

    print("Submitting post...")
    if not submit_post(page):
        print("Could not find Next/Post button. Please click it manually.")
        human_pause(20.0, 20.0)
        return False

    page.wait_for_load_state("domcontentloaded")
    human_pause(2.5, 4.0)
    print(f"Post {index + 1} published.")
    return True


def _switch_to_page_if_prompted(page) -> bool:
    """Click 'Switch Now' if Facebook shows a page-switch prompt. Returns True if clicked."""
    for sw in [
        page.get_by_role("button", name=re.compile(r"switch now", re.I)),
        page.get_by_role("link",   name=re.compile(r"switch now", re.I)),
        page.locator("div[role='button']:has-text('Switch Now'), a:has-text('Switch Now')"),
    ]:
        try:
            if sw.count() > 0:
                print("Clicking Switch Now...")
                sw.first.click()
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=60000)
                except Exception as exc:
                    log.debug("_switch_to_page_if_prompted: ignored error: %s", exc)
                human_pause(2.0, 3.0)
                return True
        except Exception as exc:
            log.debug("_switch_to_page_if_prompted: ignored error: %s", exc)
    return False


def _setup_session(page, account_name: str = ""):
    """Ensure we're in the page posting context. Returns the active page URL."""
    base_urls = {"https://www.facebook.com/", "https://www.facebook.com", "https://m.facebook.com/"}

    # If we have a valid cached URL, use it — navigate if not already there
    if account_name:
        cached_url = _load_page_urls().get(account_name)
        if cached_url and not _is_valid_page_url(cached_url):
            cached_url = None
        if cached_url:
            cached_id = cached_url.rstrip("/").split("/")[-1]
            already_there = cached_id and cached_id in page.url
            print(f"Using cached page URL: {cached_url}")
            if not already_there:
                js_navigate(page, cached_url)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception as exc:
                    log.debug("_setup_session: ignored error: %s", exc)
                human_pause(2.0, 3.0)
            _switch_to_page_if_prompted(page)
            return page.url

    if "www.facebook.com" not in page.url:
        js_navigate(page, "https://www.facebook.com/")
        human_pause(2.0, 3.0)

    if "login" in page.url:
        raise RuntimeError("Not logged in — run manual_session.py first.")

    try:
        if page.locator("input[name='email'], input[type='email']").count() > 0:
            raise RuntimeError("Facebook session expired — re-login via Multilogin first.")
    except RuntimeError:
        raise
    except Exception as exc:
        log.debug("_setup_session: ignored error: %s", exc)

    SUFFIXES = ["LS", "HOB", "CSI", "MF"]
    already_on_page = page.url not in base_urls and any(
        re.search(rf'\b{s}\b', page.url, re.I) for s in SUFFIXES
    )

    if already_on_page:
        print("Already in page context.")
    else:
        switched = False

        # Step 1: check if Facebook is already showing a Switch Now prompt
        switched = _switch_to_page_if_prompted(page)

        # Step 2: scroll sidebar and look for a page link by category suffix
        if not switched:
            try:
                for _ in range(25):
                    for link in page.locator("a").all():
                        text = (link.text_content() or "").strip()
                        if any(re.search(rf'\b{s}\b', text) for s in SUFFIXES):
                            print(f"Found page in sidebar: '{text}' — clicking...")
                            link.click()
                            human_pause(2.0, 3.0)
                            _switch_to_page_if_prompted(page)
                            if page.url not in base_urls:
                                switched = True
                            break
                    if switched:
                        break
                    page.evaluate(
                        "document.querySelector('[data-pagelet=\"LeftRail\"]')?.scrollBy(0, 300)"
                    )
                    human_pause(0.4, 0.6)
            except Exception as exc:
                log.debug("_setup_session: ignored error: %s", exc)

        # Step 3: navigate to Pages Manager and pick the first managed page
        if not switched:
            print("Could not find page in sidebar — navigating to Pages Manager...")
            js_navigate(page, "https://www.facebook.com/pages/manage/")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception as exc:
                log.debug("_setup_session: ignored error: %s", exc)
            try:
                page.wait_for_selector("div[role='main']", timeout=10000)
            except Exception as exc:
                log.debug("_setup_session: ignored error: %s", exc)
            human_pause(2.0, 3.0)

            EXCLUDED_SLUGS = {
                "pages", "settings", "notifications", "login", "marketplace",
                "groups", "events", "bookmarks", "gaming", "watch", "help",
                "privacy", "policies", "terms", "about", "business", "ads",
                "friends", "memories", "saved", "videos", "photos", "reels",
                "fundraisers", "climate", "jobs", "professional-dashboard",
                "professional_dashboard", "dashboard", "creatorstudio",
                "facebook", "messenger", "instagram",
            }
            EXCLUDED_PARAMS = {"action=", "story_fbid", "__cft__"}
            page_link = None
            all_links = page.locator("a[href]").all()
            # Pass 1: prefer numeric page IDs (most reliable)
            for link in all_links:
                href = (link.get_attribute("href") or "").split("?")[0]
                if not href.startswith("https://www.facebook.com/"):
                    continue
                slug = href.replace("https://www.facebook.com/", "").strip("/")
                if re.match(r'^\d{10,}$', slug):
                    page_link = href
                    print(f"Found managed page (ID): {href}")
                    break
            # Pass 2: slug-based with expanded exclusion list
            if not page_link:
                for link in all_links:
                    href = link.get_attribute("href") or ""
                    if not href.startswith("https://www.facebook.com/"):
                        continue
                    if any(p in href for p in EXCLUDED_PARAMS):
                        continue
                    slug = href.replace("https://www.facebook.com/", "").split("?")[0].strip("/")
                    if slug and "/" not in slug and slug not in EXCLUDED_SLUGS:
                        page_link = href
                        print(f"Found managed page (slug): {href}")
                        break

            if page_link:
                js_navigate(page, page_link)
                try:
                    page.wait_for_selector("div[role='main']", timeout=15000)
                except Exception as exc:
                    log.debug("_setup_session: ignored error: %s", exc)
                human_pause(2.0, 3.0)
                _switch_to_page_if_prompted(page)
            else:
                raise RuntimeError(
                    "No managed Facebook Page found. Make sure the profile is logged in "
                    "and has a Page assigned at facebook.com/pages/manage/"
                )

    return page.url


def post_with_cdp(cdp_url, posts_path, min_delay=DEFAULT_MIN_DELAY, max_delay=DEFAULT_MAX_DELAY, account_name=""):
    """Run the full posting flow on an already-open Multilogin profile (CDP URL).
    Does not start or stop the profile — caller owns the lifecycle."""
    posts = parse_posts(posts_path)
    print(f"Loaded {len(posts)} posts from {posts_path}")
    for i, p in enumerate(posts):
        preview = p["text"][:60].replace("\n", " ")
        print(f"  {i+1}. {'[LINK] ' if p['url'] else ''}  {preview}...")

    with sync_playwright() as p:
        last_err = None
        for _attempt in range(6):
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as e:
                last_err = e
                wait = 5 * (_attempt + 1)
                print(f"  CDP not ready yet — retrying in {wait}s ({_attempt + 1}/6)...")
                time.sleep(wait)
        else:
            raise RuntimeError(f"Could not connect to Multilogin browser after 6 attempts: {last_err}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        print("\nChecking session...")
        try:
            active_page_url = _setup_session(page, account_name)
        except Exception as e:
            if "closed" in str(e).lower() or "target" in str(e).lower():
                raise RuntimeError(
                    "Multilogin browser closed unexpectedly during session setup — "
                    "the profile may have been stopped by a previous run. Retry in a few seconds."
                ) from e
            raise
        print(f"Active page URL: {active_page_url}")
        if active_page_url in {"https://www.facebook.com/", "https://www.facebook.com", "https://m.facebook.com/"}:
            raise RuntimeError("Could not navigate to a Facebook Page — check that the profile is logged in.")
        if account_name:
            _save_page_url(account_name, active_page_url)
        print("Session active.\n")

        for i, post in enumerate(posts):
            image_path = IMAGES_DIR / f"post_{i + 1}.jpg"
            should_navigate = True
            publish_post(
                page, post, i, active_page_url,
                image_path if image_path.exists() else None,
                navigate=should_navigate,
            )
            if i < len(posts) - 1:
                delay = random.randint(min_delay, max_delay)
                print(f"Waiting {delay}s before next post...")
                time.sleep(delay)

        print("\nAll posts done.")
        if account_name:
            post_info = capture_published_post(page)
            save_last_post(account_name, post_info)
            if post_info and post_info.get("post_id"):
                print(f"Recorded published post for boost.py: {post_info['post_id']}")
            else:
                print(
                    "Could not capture the published post's ID — boost.py will fall back "
                    "to inferring the most recent post and will warn you to verify it."
                )


def run_phase1(account_name, posts_path, min_delay=DEFAULT_MIN_DELAY, max_delay=DEFAULT_MAX_DELAY):
    """Start profile, post, and return (mlx, started) with the profile still running.
    Caller is responsible for stopping the profile afterwards."""
    mlx, started = start_profile_for(account_name)
    post_with_cdp(started.cdp_url, posts_path, min_delay, max_delay, account_name=account_name)
    return mlx, started


def run(account_name, posts_path, min_delay=DEFAULT_MIN_DELAY, max_delay=DEFAULT_MAX_DELAY):
    mlx, started = start_profile_for(account_name)
    try:
        post_with_cdp(started.cdp_url, posts_path, min_delay, max_delay, account_name=account_name)
    finally:
        try:
            mlx.stop_profile(started.profile_id)
        except Exception as exc:
            log.debug("run: ignored error: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="Post content to a Facebook Page.")
    parser.add_argument("--account",   required=True,          help="Account name (e.g. NOS_PAN_001)")
    parser.add_argument("--posts",     required=True,          help="Path to posts.txt")
    parser.add_argument("--min-delay", type=int, default=DEFAULT_MIN_DELAY, help="Min seconds between posts (default: 45)")
    parser.add_argument("--max-delay", type=int, default=DEFAULT_MAX_DELAY, help="Max seconds between posts (default: 90)")
    args = parser.parse_args()

    run(args.account, args.posts, args.min_delay, args.max_delay)


if __name__ == "__main__":
    main()
