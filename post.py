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

log = logging.getLogger(__name__)

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
    """Click 'What's on your mind?' in the main feed only, then wait for the dialog."""
    human_pause(1.0, 1.5)

    for selector in [
        "div[role='main'] div[role='button']:has-text(\"What's on your mind\")",
        "div[role='main'] div[aria-label*=\"What's on your mind\"]",
        "div[role='main'] span:has-text(\"What's on your mind\")",
        "div[role='button']:has-text(\"What's on your mind\")",
        "[aria-label*=\"What's on your mind\"]",
    ]:
        try:
            el = page.locator(selector).first
            if el.is_visible():
                el.click()
                human_pause(2.0, 3.0)
                # Confirm dialog opened by waiting for the textbox, not role='dialog'
                # (Facebook Pages use a different container structure)
                for confirm in [
                    "div[contenteditable='true'][role='textbox']",
                    "div[role='dialog']",
                    "div[aria-label='Create post']",
                ]:
                    try:
                        page.wait_for_selector(confirm, timeout=5000)
                        human_pause(1.0, 1.5)
                        return True
                    except Exception as exc:
                        log.debug("composer confirm selector %r not found: %s", confirm, exc)
                        continue
        except Exception as exc:
            log.debug("composer open selector %r failed: %s", selector, exc)
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

    if not _click_first_visible(all_selectors):
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
            print(f"Image attached: {image_path.name}")
            human_pause(3.0, 5.0)
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
    boosting the right post instead of inferring "most recent" blind."""
    data = {}
    if LAST_POST_PATH.exists():
        try:
            data = json.loads(LAST_POST_PATH.read_text())
        except Exception as exc:
            log.debug("could not read existing last_post.json, overwriting: %s", exc)
            data = {}
    data[account_name] = {
        "post_id": post_info.get("post_id") if post_info else None,
        "url": post_info.get("url") if post_info else None,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    LAST_POST_PATH.write_text(json.dumps(data, indent=2))


def publish_post(page, post, index, page_url="https://www.facebook.com/", image_path=None, navigate=True):
    print(f"\n── Post {index + 1} {'(with link)' if post['url'] else ''} {'📷' if image_path else ''} ──")

    if navigate:
        js_navigate(page, page_url)
        human_pause(2.5, 4.0)
    else:
        # After a previous post the dialog closes and we're still on the page feed.
        # Scroll to top instantly so the "What's on your mind?" button is in view.
        human_pause(2.0, 3.0)
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
        page.keyboard.press("Escape")  # dismiss any hashtag autocomplete
        human_pause(0.2, 0.4)
        page.keyboard.press("End")
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


def run(account_name, posts_path, min_delay=DEFAULT_MIN_DELAY, max_delay=DEFAULT_MAX_DELAY):
    posts = parse_posts(posts_path)
    print(f"Loaded {len(posts)} posts from {posts_path}")
    for i, p in enumerate(posts):
        preview = p["text"][:60].replace("\n", " ")
        print(f"  {i+1}. {'[LINK] ' if p['url'] else ''}  {preview}...")

    mlx, started = start_profile_for(account_name)

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(started.cdp_url)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            print("\nChecking session...")
            SUFFIXES = ["LS", "HOB", "CSI", "MF"]

            # "facebook.com" alone also matches adsmanager.facebook.com,
            # business.facebook.com, etc. — a profile left open on one of
            # those from a prior session would be wrongly treated as
            # already on the main feed, and the composer search below
            # would fail against the wrong page entirely.
            if "www.facebook.com" not in page.url:
                js_navigate(page, "https://www.facebook.com/")
                human_pause(2.0, 3.0)

            if "login" in page.url:
                print("Not logged in. Run manual_session.py first.")
                sys.exit(1)

            # Check if already in page context by looking at the composer placeholder
            try:
                main_text = page.locator("div[role='main']").first.text_content(timeout=8000) or ""
            except Exception as exc:
                log.debug("could not read main content for page-context check: %s", exc)
                main_text = ""
            already_on_page = any(
                re.search(rf'\b{s}\b', page.url, re.I) or
                re.search(rf'\b{s}\b', main_text, re.I)
                for s in SUFFIXES
            )

            if already_on_page:
                print("Already in page context.")
            else:
                # Try sidebar link by category suffix
                switched = False
                try:
                    all_links = page.locator("a").all()
                    for link in all_links:
                        text = (link.text_content() or "").strip()
                        if any(re.search(rf'\b{s}\b', text) for s in SUFFIXES):
                            print(f"Found page in sidebar: '{text}' — clicking...")
                            link.click()
                            page.wait_for_load_state("domcontentloaded")
                            human_pause(2.0, 3.0)
                            switched = True
                            break
                except Exception as exc:
                    log.debug("sidebar page-link scan failed: %s", exc)

                # Fallback: Switch Now button
                if not switched:
                    try:
                        for locator in [
                            page.get_by_role("button", name=re.compile(r"switch now", re.I)),
                            page.get_by_role("link",   name=re.compile(r"switch now", re.I)),
                            page.locator("a:has-text('Switch Now'), div[role='button']:has-text('Switch Now')"),
                        ]:
                            if locator.count() > 0:
                                print("Clicking Switch Now...")
                                locator.first.click()
                                page.wait_for_load_state("domcontentloaded")
                                human_pause(2.0, 3.0)
                                switched = True
                                break
                    except Exception as exc:
                        log.debug("Switch Now fallback failed: %s", exc)

                if not switched:
                    print("Could not find page in sidebar or Switch Now — continuing with current URL.")

            active_page_url = page.url
            print(f"Active page URL: {active_page_url}")
            print("Session active. Starting to post...\n")

            for i, post in enumerate(posts):
                image_path = IMAGES_DIR / f"post_{i + 1}.jpg"
                success = publish_post(
                    page, post, i, active_page_url,
                    image_path if image_path.exists() else None,
                    navigate=(i == 0),
                )
                if success and i < len(posts) - 1:
                    delay = random.randint(min_delay, max_delay)
                    print(f"Waiting {delay}s before next post...")
                    time.sleep(delay)

            print("\nAll posts done.")
            post_info = capture_published_post(page)
            save_last_post(account_name, post_info)
            if post_info and post_info.get("post_id"):
                print(f"Recorded published post for boost.py: {post_info['post_id']}")
            else:
                print(
                    "Could not capture the published post's ID — boost.py will fall back "
                    "to inferring the most recent post and will warn you to verify it."
                )

            print("Browser is open. Close it when done.")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception as exc:
                log.debug("wait_for_event(close) ended: %s", exc)
        finally:
            pass  # Leave profile running so next run reconnects to the live session


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
