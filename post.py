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
import os
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from mlx_context import start_profile_for
from fb_dom import js_navigate

DEFAULT_MIN_DELAY = 45
DEFAULT_MAX_DELAY = 90


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
                    except Exception:
                        continue
        except Exception:
            continue

    try:
        page.screenshot(path=os.path.join(os.path.dirname(__file__), "debug_composer.png"))
        print("Composer not found. Screenshot saved to debug_composer.png")
    except Exception:
        pass
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
    except Exception:
        pass

    def _click_first_visible(selectors):
        for selector in selectors:
            try:
                el = page.locator(selector).last
                if el.is_visible():
                    el.click()
                    return True
            except Exception:
                continue
        for name in [r"^Next$", r"^Post$"]:
            try:
                btn = page.locator("div[role='dialog']").get_by_role(
                    "button", name=re.compile(name, re.I)
                ).last
                if btn.is_visible():
                    btn.click()
                    return True
            except Exception:
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
    except Exception:
        pass

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
        except Exception:
            return False

    # Phase 1: wait up to 8s for the Add-to-post toolbar to appear, then click the icon
    try:
        page.wait_for_selector(
            "div[role='dialog'] [aria-label='Photo/video'], div[role='dialog'] [aria-label*='Photo'], [aria-label='Photo/video']",
            timeout=8000,
        )
    except Exception:
        pass
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
        except Exception:
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
        except Exception:
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
        except Exception:
            continue

    print("Could not attach image — photo button not found.")
    return False


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
        except Exception:
            pass
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
    except Exception:
        pass

    human_type(page, post["text"])

    # Dismiss any hashtag/mention autocomplete before looking for the photo button —
    # the autocomplete dropdown sits on top of the Add-to-post toolbar and hides it.
    try:
        page.keyboard.press("Escape")
        human_pause(0.4, 0.7)
    except Exception:
        pass

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
            except Exception:
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
                except Exception:
                    pass

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
                    except Exception:
                        pass

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
            print("Browser is open. Close it when done.")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
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
