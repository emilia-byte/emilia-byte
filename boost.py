"""
boost.py  —  Phase 2
Creates a Facebook engagement ad campaign for the most recent post on a page.
Run this after post.py has published posts.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"
LAST_POST_PATH = ROOT / "last_post.json"
POST_MATCH_STALENESS = timedelta(minutes=30)


# ── Credentials ───────────────────────────────────────────────────────────────

def _load_dotenv():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def ensure_credentials():
    _load_dotenv()

    keys = ["MLX_EMAIL", "MLX_PASSWORD", "TEXTVERIFIED_API_KEY", "TEXTVERIFIED_USERNAME"]
    values = {k: os.environ.get(k, "").strip() for k in keys}

    if all(values.values()):
        return

    print("\n── Credentials ───────────────────────────────────────────")
    print("(Saved to .env so you only need to enter them once.)\n")

    if not values["MLX_EMAIL"]:
        values["MLX_EMAIL"] = input("  Multilogin email: ").strip()
    if not values["MLX_PASSWORD"]:
        values["MLX_PASSWORD"] = getpass.getpass("  Multilogin password: ").strip()
    if not values["TEXTVERIFIED_API_KEY"]:
        values["TEXTVERIFIED_API_KEY"] = input("  TextVerified API key: ").strip()
    if not values["TEXTVERIFIED_USERNAME"]:
        values["TEXTVERIFIED_USERNAME"] = input("  TextVerified username (email): ").strip()

    for k, v in values.items():
        os.environ[k] = v

    existing = []
    if ENV_FILE.exists():
        existing = [l for l in ENV_FILE.read_text().splitlines()
                    if not any(l.startswith(k) for k in keys)]
    lines = existing + [f"{k}={v}" for k, v in values.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print("  Saved to .env")


# ── Account picker ────────────────────────────────────────────────────────────

def pick_account() -> str:
    from mlx_context import list_accounts
    accounts = list_accounts()

    print("\n── Accounts ──────────────────────────────────────────────")
    for i, name in enumerate(accounts, 1):
        print(f"  {i}. {name}")

    while True:
        choice = input(f"\n  Pick an account [1-{len(accounts)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(accounts):
            return accounts[int(choice) - 1]
        print("  Please enter a number from the list.")


# ── Last-published-post lookup ────────────────────────────────────────────────

def _load_last_post(account: str) -> dict | None:
    """
    Read what post.py recorded as the most recently published post for this
    account (see post.save_last_post). Returns None if there's no record, the
    file is unreadable, or the record is older than POST_MATCH_STALENESS --
    in all those cases the caller falls back to inferring "most recent" from
    the Ads Manager table, same as before, but now it says so out loud.
    """
    if not LAST_POST_PATH.exists():
        return None
    try:
        data = json.loads(LAST_POST_PATH.read_text())
    except Exception as exc:
        log.debug("could not read last_post.json: %s", exc)
        return None

    record = data.get(account)
    if not record:
        return None

    published_at = record.get("published_at")
    if published_at:
        try:
            when = datetime.fromisoformat(published_at)
            if datetime.now(timezone.utc) - when > POST_MATCH_STALENESS:
                print(f"  NOTE: recorded post for {account} is stale ({published_at}) — ignoring.")
                return None
        except Exception as exc:
            log.debug("could not parse published_at %r: %s", published_at, exc)

    return record


# ── TextVerified SMS ──────────────────────────────────────────────────────────

def _get_sms_code(verification) -> str | None:
    from textverified import TextVerified
    tv = TextVerified(
        api_key=os.environ["TEXTVERIFIED_API_KEY"],
        api_username=os.environ["TEXTVERIFIED_USERNAME"],
    )
    print("  Waiting for SMS code (up to 2 minutes)...")
    for sms in tv.sms.incoming(data=verification, timeout=120.0):
        digits = "".join(filter(str.isdigit, sms.text or ""))
        if digits:
            return digits
    return None


async def _handle_verification(page) -> bool:
    """Reserve a TextVerified number, enter it in Facebook, receive and submit the code."""
    try:
        from textverified import TextVerified
        from textverified.models import ReservationCapability
    except ImportError:
        print("  textverified package not installed — run setup.bat first.")
        input("  Complete verification manually, then press Enter...")
        return True

    try:
        tv = TextVerified(
            api_key=os.environ["TEXTVERIFIED_API_KEY"],
            api_username=os.environ["TEXTVERIFIED_USERNAME"],
        )
        print("  Requesting US number from TextVerified...")
        verification = tv.verifications.create(
            service_name="Facebook",
            capability=ReservationCapability.SMS,
        )
        phone = verification.phone_number
        print(f"  Got number: {phone}")

        phone_input = await page.wait_for_selector(
            'input[type="tel"], input[placeholder*="phone"], input[placeholder*="number"]',
            timeout=10000,
        )
        await phone_input.fill(phone)
        await page.wait_for_timeout(500)

        for label in ["Send code", "Get code", "Send"]:
            try:
                await page.click(f'button:has-text("{label}")', timeout=3000)
                break
            except Exception as exc:
                log.debug("phone-submit button %r failed: %s", label, exc)
                continue

        await page.wait_for_timeout(2000)

        # Poll for code in a thread so we don't block the event loop
        code = await asyncio.to_thread(_get_sms_code, verification)

        if code:
            print(f"  Received code: {code}")
            code_input = await page.wait_for_selector(
                'input[placeholder*="code"], input[type="number"], input[autocomplete="one-time-code"]',
                timeout=10000,
            )
            await code_input.fill(code)
            await page.wait_for_timeout(500)
            for label in ["Confirm", "Submit", "Continue"]:
                try:
                    await page.click(f'button:has-text("{label}")', timeout=3000)
                    break
                except Exception as exc:
                    log.debug("code-submit button %r failed: %s", label, exc)
                    continue
            await page.wait_for_load_state("networkidle")
            tv.verifications.cancel(verification.id)
            return True
        else:
            print("  Could not receive SMS code automatically.")
            input("  Complete verification manually, then press Enter...")
            tv.verifications.cancel(verification.id)
            return True

    except Exception as exc:
        print(f"  Verification error: {exc}")
        input("  Complete verification manually, then press Enter...")
        return True


# ── Post-selection matching ───────────────────────────────────────────────────

async def select_target_post(page, account: str) -> str | None:
    """
    Called with the Ads Manager "Select post" table already open and its
    rows rendered (a table of Facebook post / Post ID / Source / Media /
    Date created, sorted newest first -- not a media grid). Clicks the row
    matching post.py's recorded post ID when it can verify one; otherwise
    falls back to the newest (first) row and prints a loud warning so the
    mismatch isn't silent.

    Returns the post ID actually clicked (best-effort -- None if even the
    fallback row's text couldn't be read). Factored out of boost() so this
    logic can be exercised directly against a DOM fixture in tests, without
    having to drive Facebook's Ads Manager end to end.
    """
    last_post = _load_last_post(account)
    target_post_id = last_post.get("post_id") if last_post else None
    # The table only ever shows the legacy numeric ID scheme -- an opaque
    # pfbid token (which post.py may have captured instead) can't be
    # matched here, so don't pretend a non-numeric ID is verifiable.
    if target_post_id and not re.fullmatch(r"\d{15,}", target_post_id):
        print(
            f"  NOTE: recorded post ID {target_post_id!r} isn't the numeric "
            f"scheme this table uses — can't verify it here."
        )
        target_post_id = None

    candidate_rows = page.locator("text=/^\\d{15,}$/")
    chosen = None
    if target_post_id:
        exact = candidate_rows.filter(has_text=re.compile(rf"^{re.escape(target_post_id)}$"))
        if await exact.count() > 0:
            chosen = exact.first
            print(f"  Matched recorded post ID {target_post_id} exactly.")
        else:
            print(
                f"  WARNING: post.py recorded post ID {target_post_id} but it doesn't "
                f"appear in this table (ID scheme mismatch, or the post isn't indexed "
                f"here yet). Falling back to the newest row — verify this is the right "
                f"post before confirming Publish."
            )
    elif last_post is not None:
        print(
            f"  WARNING: post.py's recorded post has no usable numeric ID "
            f"(published_at={last_post.get('published_at')}). Falling back to the "
            f"newest row in this table — verify it matches before confirming Publish."
        )
    else:
        print(
            "  WARNING: no record from post.py for this account (missing or stale) — "
            "cannot verify which post this is. Falling back to the newest row in this "
            "table — verify it matches before confirming Publish."
        )

    if chosen is None:
        chosen = candidate_rows.first

    await chosen.click(timeout=15000)
    try:
        return (await chosen.text_content() or "").strip()
    except Exception as exc:
        log.debug("could not read clicked row's text after selection: %s", exc)
        return None


# ── Main ad creation flow ─────────────────────────────────────────────────────

async def boost(cdp_url: str, account: str):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        # ── Navigate to Ads Manager ───────────────────────────────
        # Use window.location, not page.goto() — Playwright's own navigation
        # bypasses Multilogin's proxy-auth injection and fails with
        # ERR_INVALID_AUTH_CREDENTIALS.
        print("Opening Ads Manager...")
        await page.evaluate(
            "(u) => { window.location.href = u; }",
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
        )
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("text=Campaigns", timeout=30000)
        except Exception as exc:
            log.debug("Campaigns header wait failed: %s", exc)
        await page.wait_for_timeout(2000)

        # ── Create campaign ───────────────────────────────────────
        print("Creating campaign...")
        await page.wait_for_timeout(2000)

        # Target the green "+ Create" button precisely — avoid "Create a view" etc.
        clicked = False
        for selector in [
            '[data-testid="create-entity-button"]',
            'a[href*="create"]:has-text("Create"):not(:has-text("view"))',
            'div[aria-label="Create campaign"]',
        ]:
            try:
                await page.click(selector, timeout=4000)
                clicked = True
                break
            except Exception as exc:
                log.debug("create-campaign selector %r failed: %s", selector, exc)
                continue

        if not clicked:
            # Last resort: find the link/button whose full text is exactly "Create" or "+ Create"
            try:
                btn = page.locator('a, button, div[role="button"]').filter(
                    has_text=re.compile(r'^\+?\s*Create$', re.I)
                ).first
                await btn.click(timeout=8000)
                clicked = True
            except Exception as exc:
                log.debug("fallback Create button match failed: %s", exc)

        if not clicked:
            raise RuntimeError("Could not find the + Create campaign button in Ads Manager.")
        await page.wait_for_timeout(1500)

        # Some accounts show a "Loading creation..." spinner before the
        # objective modal actually renders — wait it out or the Engagement
        # click below can land before the dialog content exists.
        try:
            await page.wait_for_selector("text=Loading creation", state="hidden", timeout=20000)
        except Exception as exc:
            log.debug("Loading-creation spinner wait failed: %s", exc)

        # Current Ads Manager UI: modal shows objective radio buttons directly.
        # No "Manual campaign" step — just select Engagement and Continue.
        # Scope to the dialog: a bare "text=Engagement" can match a leftover
        # draft campaign named "New Engagement Campaign" in the table behind
        # the modal, which then hangs waiting to click through a side panel.
        await page.click('div[role="dialog"] :text("Engagement")')
        await page.wait_for_timeout(800)
        continue_btn = page.locator('div[role="dialog"]').get_by_role(
            "button", name=re.compile(r"^Continue$", re.I)
        )
        await continue_btn.click(timeout=10000)
        await page.wait_for_timeout(1500)

        # Second dialog: "Choose a campaign setup" — Recommended vs Manual.
        # We want full manual control (targeting, post selection), not
        # Advantage+ presets.
        try:
            await page.locator('div[role="dialog"] :text("Manual")').first.click(timeout=8000)
            await page.wait_for_timeout(800)
            manual_continue = page.locator('div[role="dialog"]').get_by_role(
                "button", name=re.compile(r"^Continue$", re.I)
            )
            await manual_continue.click(timeout=8000)
            await page.wait_for_timeout(1200)
        except Exception as exc:
            log.debug("Manual-setup dialog step skipped: %s", exc)  # some accounts may skip straight past this dialog

        # Single-page campaign editor (Campaign name / details / Budget) —
        # click Next to move to the Ad set section. Not a real <button>.
        next_btn = page.get_by_role("button", name=re.compile(r"^Next$", re.I))
        await next_btn.click(timeout=10000)
        await page.wait_for_timeout(1500)

        # ── Ad set ────────────────────────────────────────────────
        print("Configuring ad set...")
        # "Conversion location" dropdown replaces the old standalone
        # "On your ad" / "Post engagement" clicks.
        await page.click("text=Message destinations", timeout=10000)
        await page.wait_for_timeout(600)
        await page.click('text="On your ad"', timeout=8000)
        await page.wait_for_timeout(1000)

        # "Engagement type" dropdown defaults to "Video views" — switch to
        # "Post engagement" since our posts are image/text, not video.
        try:
            await page.click("text=Video views", timeout=8000)
            await page.wait_for_timeout(600)
            await page.click("text=Post engagement", timeout=5000)
            await page.wait_for_timeout(800)
        except Exception as exc:
            log.debug("Engagement-type dropdown skipped: %s", exc)  # already set, or account defaults differently

        # Location targeting lives inside "Audience controls", not rendered
        # until scrolled near — some accounts' Ad Set page isn't virtualized
        # (the section exists in the DOM immediately) and some accounts'
        # is (it only attaches once scrolled close), so scroll by mouse
        # wheel checking cheap DOM-presence rather than full visibility,
        # then do a precise native scrollIntoView once it's attached.
        print("Setting location: Paraguay...")
        box = await page.locator("text=Conversion").first.bounding_box()
        if box:
            await page.mouse.move(box["x"] + 50, box["y"] + 10)

        included = page.locator("text=Included location:").first
        attached = False
        for _ in range(30):
            if await included.count() > 0:
                attached = True
                break
            await page.mouse.wheel(0, 150)
            await page.wait_for_timeout(250)
        if not attached:
            raise RuntimeError("Never reached the Locations section.")

        inc_handle = await included.element_handle()
        await page.evaluate(
            "el => el.scrollIntoView({block: 'center', behavior: 'instant'})",
            inc_handle,
        )
        await page.wait_for_timeout(800)

        # The "* Locations" heading row carries its own "Edit" link (not
        # the page's "Show more options", which expands age/gender
        # instead) — but on some accounts it only renders after the
        # location chip itself is clicked to "activate" the box. Anchor
        # proximity search on the heading, not "Included location:" — the
        # Edit link sits level with the heading, above the location list.
        inc_box = await included.bounding_box(timeout=10000)
        heading_box = await page.locator("text=* Locations").first.bounding_box()
        anchor_box = heading_box or inc_box

        def nearest_below(label):
            async def _find():
                candidates = await page.locator(label).all()
                best, best_dy = None, None
                for c in candidates:
                    cbox = await c.bounding_box()
                    if cbox and anchor_box and cbox["y"] >= anchor_box["y"] - 5:
                        dy = cbox["y"] - anchor_box["y"]
                        if best_dy is None or dy < best_dy:
                            best_dy, best = dy, c
                return best
            return _find()

        best = await nearest_below('text="Edit"')
        if best is None:
            # Clicking anywhere in the location summary "activates" the box
            # and reveals its Edit link on some accounts.
            try:
                await included.click(timeout=5000)
                await page.wait_for_timeout(800)
                best = await nearest_below('text="Edit"')
            except Exception as exc:
                log.debug("Locations 'Edit' link activation-click failed: %s", exc)
        if best is None:
            raise RuntimeError("Could not find the Locations 'Edit' link.")
        await best.click(timeout=8000)
        await page.wait_for_timeout(1000)

        search = None
        for sel in [
            'input[placeholder*="ountry" i]',
            'input[placeholder*="ocation" i]',
            'input[aria-label*="ocation" i]',
            'div[role="dialog"] input[type="text"]',
        ]:
            try:
                cand = page.locator(sel).first
                if await cand.is_visible(timeout=2000):
                    search = cand
                    break
            except Exception as exc:
                log.debug("location search input %r failed: %s", sel, exc)
                continue
        if not search:
            raise RuntimeError("No location search input found after clicking Edit.")
        await search.fill("Paraguay")
        await page.wait_for_timeout(1000)
        # Press Enter rather than clicking a result row — "Paraguay" also
        # substring-matches city results ("Asunción, Paraguay" etc.), so a
        # has-text click is ambiguous. Enter takes the top (country) match.
        await search.press("Enter")
        await page.wait_for_timeout(1000)

        next_btn2 = page.get_by_role("button", name=re.compile(r"^Next$", re.I))
        await next_btn2.click(timeout=10000)
        await page.wait_for_load_state("networkidle")

        # ── Ad level: select most recent post ─────────────────────
        print("Selecting most recent post...")
        # Scroll position carries over from the Ad Set page. On some
        # accounts the "Ad setup" dropdown's own text isn't a reliable
        # scroll target -- its closed-state value is duplicated elsewhere
        # in the DOM as a permanently display:none legacy node (same
        # text, but genuinely no layout box, so scrollIntoView on *that*
        # copy is a no-op and the real one never gets its own scroll).
        # The "Ad setup" heading right above it doesn't have that problem.
        try:
            ad_setup_heading = page.locator("text=Ad setup").first
            await ad_setup_heading.wait_for(state="attached", timeout=8000)
            handle = await ad_setup_heading.element_handle()
            await page.evaluate(
                "el => el.scrollIntoView({block: 'start', behavior: 'instant'})",
                handle,
            )
            await page.wait_for_timeout(500)
        except Exception as exc:
            log.debug("Ad setup heading scroll failed: %s", exc)
        await page.click("text=Use existing post")
        await page.wait_for_timeout(1000)
        await page.click("text=Select post")
        await page.wait_for_timeout(2000)

        # "Select post" is a table (Facebook post / Post ID / Source / Media
        # / Date created), sorted newest first — not a media grid. select_target_post()
        # tries to match the post ID post.py actually recorded instead of blindly
        # trusting the first (newest) row, and falls back loudly if it can't.
        await select_target_post(page, account)
        await page.wait_for_timeout(1000)

        continue_post_btn = page.get_by_role("button", name=re.compile(r"^Continue$", re.I))
        await continue_post_btn.click(timeout=8000)
        await page.wait_for_load_state("networkidle")

        # ── Publish ───────────────────────────────────────────────
        print("Publishing campaign...")
        await page.click('button:has-text("Publish")')
        await page.wait_for_timeout(3000)

        # ── SMS verification (if triggered) ───────────────────────
        needs_verification = False
        for text in ["Verifying your changes", "Enter confirmation code", "confirm your identity"]:
            try:
                el = await page.query_selector(f"text={text}")
                if el:
                    needs_verification = True
                    break
            except Exception as exc:
                log.debug("verification-prompt check for %r failed: %s", text, exc)

        if needs_verification:
            print("\nSMS verification required...")
            await _handle_verification(page)

        await page.wait_for_timeout(2000)
        print("\nCampaign published successfully.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("=" * 54)
    print("   Facebook Ad Booster")
    print("=" * 54)

    ensure_credentials()
    account = pick_account()

    print(f"\nStarting profile '{account}'...")
    from mlx_context import start_profile_for
    client, started = start_profile_for(account)

    try:
        asyncio.run(boost(started.cdp_url, account))
    finally:
        client.stop_profile(started.profile_id)

    print("\n" + "=" * 54)
    print("   Done.")
    print("=" * 54)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
    except Exception as exc:
        print(f"\n\nError: {exc}")
    input("\nPress Enter to close...")
