"""
boost.py  —  Phase 2
Creates a Facebook engagement ad campaign for the most recent post on a page.
Run this after post.py has published posts.

Prompts at the end to either publish the campaign immediately or leave it
as a draft in Ads Manager for manual review -- defaults to draft, since
publishing spends real ad budget.
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

# Batch runs boost several profiles at once: tag every line with the profile
# it's about, and prompt without freezing the other profiles (see console.py).
from console import ask, tagged_print as print

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"
LAST_POST_PATH = ROOT / "last_post.json"
POST_MATCH_STALENESS = timedelta(minutes=30)


async def _debug_screenshot(page, name: str, account: str) -> str:
    """Save debug_<name>_<account>.png -- per-account so concurrent profiles
    don't overwrite each other's evidence. Returns the file name; a failed
    screenshot is logged, never raised, so it can't mask the real error."""
    filename = f"debug_{name}_{re.sub(r'[^A-Za-z0-9_-]', '_', account)}.png"
    try:
        await page.screenshot(path=str(ROOT / filename))
    except Exception as exc:
        log.debug("failed to save debug screenshot %s: %s", filename, exc)
    return filename


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


def _save_env(values: dict[str, str]) -> None:
    keys = list(values.keys())
    existing = []
    if ENV_FILE.exists():
        existing = [l for l in ENV_FILE.read_text().splitlines()
                    if not any(l.startswith(k) for k in keys)]
    lines = existing + [f"{k}={v}" for k, v in values.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print("  Saved to .env")


def ensure_mlx_credentials():
    """Required for every run -- starting a Multilogin profile needs these
    regardless of whether the campaign ends up published or left as a draft."""
    _load_dotenv()

    values = {k: os.environ.get(k, "").strip() for k in ["MLX_EMAIL", "MLX_PASSWORD"]}
    if all(values.values()):
        return

    print("\n── Multilogin credentials ────────────────────────────────")
    print("(Saved to .env so you only need to enter them once.)\n")
    if not values["MLX_EMAIL"]:
        values["MLX_EMAIL"] = input("  Multilogin email: ").strip()
    if not values["MLX_PASSWORD"]:
        values["MLX_PASSWORD"] = getpass.getpass("  Multilogin password: ").strip()

    for k, v in values.items():
        os.environ[k] = v
    _save_env(values)


def ensure_textverified_credentials():
    """Only needed if we're actually going to click Publish -- SMS
    verification can't trigger on a campaign left as a draft, so a
    draft-only run should never have to provide these."""
    _load_dotenv()
    values = {k: os.environ.get(k, "").strip() for k in ["TEXTVERIFIED_API_KEY", "TEXTVERIFIED_USERNAME"]}
    if all(values.values()):
        return

    print("\n── TextVerified credentials ──────────────────────────────")
    print("(Needed for SMS verification when actually publishing. Saved to .env.)\n")

    if not values["TEXTVERIFIED_API_KEY"]:
        values["TEXTVERIFIED_API_KEY"] = input("  TextVerified API key: ").strip()
    if not values["TEXTVERIFIED_USERNAME"]:
        values["TEXTVERIFIED_USERNAME"] = input("  TextVerified username (email): ").strip()
    for k, v in values.items():
        os.environ[k] = v
    _save_env(values)


# ── Account picker ────────────────────────────────────────────────────────────

def _run_sync():
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "sync_profiles.py")], cwd=str(ROOT), env=os.environ.copy())


def pick_account() -> str:
    import json
    from mlx_context import list_accounts

    profiles_file = ROOT / "mlx_profiles.json"
    if not profiles_file.exists():
        print("\nNo profiles file found — syncing from Multilogin now...")
        _run_sync()

    accounts = list_accounts()

    def _show_list():
        print("\n── Accounts ──────────────────────────────────────────────")
        for i, name in enumerate(accounts, 1):
            print(f"  {i}. {name}")
        print( "  S. Sync profiles from Multilogin")
        print( "  Or type a profile name directly (e.g. EMI_AUTO_3)")

    _show_list()

    while True:
        choice = input(f"\n  Pick [1-{len(accounts)}], S to sync, or type a name: ").strip()

        if choice.lower() == "s":
            _run_sync()
            accounts = list_accounts()
            _show_list()

        elif choice.isdigit() and 1 <= int(choice) <= len(accounts):
            return accounts[int(choice) - 1]

        elif choice:
            return choice  # start_profile_for resolves via live API lookup if not in local map

        else:
            print("  Please enter a number, S to sync, or a profile name.")


def ask_publish_mode() -> bool:
    """Defaults to draft: this spends real ad money once published, and
    Facebook auto-saves an in-progress campaign as a draft in Ads Manager
    when you navigate away without clicking Publish, so "draft" is the safe
    default and an explicit opt-in is required to actually publish."""
    print("\n── Publish mode ──────────────────────────────────────────")
    choice = input(
        "  Publish this campaign now, or leave it as a draft to review? "
        "[draft/publish] (default: draft): "
    ).strip().lower()
    return choice in ("publish", "p", "yes", "y")


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


MANUAL_VERIFY_PROMPT = (
    "  Complete verification manually in this profile's Multilogin window, "
    "then press Enter..."
)


async def _handle_verification(page) -> bool:
    """Reserve a TextVerified number, enter it in Facebook, receive and submit the code.
    Everything that waits (TextVerified calls, the manual fallback prompt)
    runs off the event loop, so in a batch the other profiles keep going."""
    try:
        from textverified import TextVerified
        from textverified.models import ReservationCapability
    except ImportError:
        print("  textverified package not installed — run setup.bat first.")
        await ask(MANUAL_VERIFY_PROMPT)
        return True

    try:
        tv = TextVerified(
            api_key=os.environ["TEXTVERIFIED_API_KEY"],
            api_username=os.environ["TEXTVERIFIED_USERNAME"],
        )
        print("  Requesting US number from TextVerified...")
        verification = await asyncio.to_thread(
            tv.verifications.create,
            service_name="Facebook",
            capability=ReservationCapability.SMS,
        )
        phone = verification.phone_number
        print(f"  Got number: {phone}")

        phone_input = page.locator(
            'input[type="tel"], input[placeholder*="phone"], input[placeholder*="number"]'
        ).first
        await phone_input.wait_for(timeout=10000)
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
            code_input = page.locator(
                'input[placeholder*="code"], input[type="number"], input[autocomplete="one-time-code"]'
            ).first
            await code_input.wait_for(timeout=10000)
            await code_input.fill(code)
            await page.wait_for_timeout(500)
            for label in ["Confirm", "Submit", "Continue"]:
                try:
                    await page.click(f'button:has-text("{label}")', timeout=3000)
                    break
                except Exception as exc:
                    log.debug("code-submit button %r failed: %s", label, exc)
                    continue
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
            except Exception as exc:
                log.debug("_handle_verification: ignored error: %s", exc)
            await asyncio.to_thread(tv.verifications.cancel, verification.id)
            return True
        else:
            print("  Could not receive SMS code automatically.")
            await ask(MANUAL_VERIFY_PROMPT)
            await asyncio.to_thread(tv.verifications.cancel, verification.id)
            return True

    except Exception as exc:
        print(f"  Verification error: {exc}")
        await ask(MANUAL_VERIFY_PROMPT)
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


# ── Auth popup dismissal ──────────────────────────────────────────────────────

async def _dismiss_auth_prompt(page) -> bool:
    """Dismiss Facebook identity/security prompts that interrupt the ad creation flow.
    Only acts when the page actually contains auth/verification language."""
    auth_keywords = ["verify your identity", "confirm your identity", "identity verification",
                     "security check", "confirm it's you", "verifica tu identidad"]
    body = (await page.evaluate("() => document.body.innerText")).lower()
    if not any(kw in body for kw in auth_keywords):
        return False
    for label in ["Not now", "Skip", "Maybe later", "Remind me later"]:
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                await page.wait_for_timeout(600)
                return True
        except Exception as exc:
            log.debug("_dismiss_auth_prompt: ignored error: %s", exc)
            continue
    return False


# ── Main ad creation flow ─────────────────────────────────────────────────────

CREATE_BUTTON_SELECTORS = [
    # data-surface is the most reliable match: DevTools inspection showed the
    # button's accessible name is empty on some accounts (Facebook renders
    # "Create" via child markup, not real accessible text), so role/name and
    # plain text matching can't find it -- but this internal surface
    # identifier is stable.
    '[data-surface="/am/table/tool_bar/lib:creation-button"]',
    '[data-testid="create-entity-button"]',
    'a[href*="create"]:has-text("Create"):not(:has-text("view"))',
    'div[aria-label="Create campaign"]',
]


async def click_create_button(page, timeout: int = 4000) -> str | None:
    """
    Click Ads Manager's green "+ Create" button -- not "Create a view" or
    other Create-ish controls. Returns a label for the strategy that worked
    (for logging and the DOM-fixture tests), or None if nothing matched.
    """
    for selector in CREATE_BUTTON_SELECTORS:
        try:
            await page.click(selector, timeout=timeout)
            return selector
        except Exception as exc:
            log.debug("create-campaign selector %r failed: %s", selector, exc)

    # Accessible-name match: the "+" is an aria-hidden icon glyph, so the
    # computed name is just "Create" -- sturdier than raw textContent, which
    # can pick up whitespace/icon text depending on the DOM structure.
    try:
        await page.get_by_role("button", name=re.compile(r"^\+?\s*Create$", re.I)).first.click(timeout=timeout * 2)
        return "role=button[name=Create]"
    except Exception as exc:
        log.debug("accessible-name Create button match failed: %s", exc)

    # Last resort: the link/button whose full text is exactly "Create" or "+ Create"
    try:
        await page.locator('a, button, div[role="button"]').filter(
            has_text=re.compile(r'^\+?\s*Create$', re.I)
        ).first.click(timeout=timeout * 2)
        return "text=Create"
    except Exception as exc:
        log.debug("fallback Create button match failed: %s", exc)
    return None


async def remove_other_location_chips(page, keep: str, max_removals: int = 10) -> int:
    """
    Remove every location chip except `keep` (some accounts pre-fill a
    default country). Uses a locator, re-resolved on every pass, rather than
    element handles collected up front: each removal re-renders the chip
    list (React), which leaves previously collected handles pointing at
    detached nodes. Returns how many chips were removed.
    """
    others = page.locator(f'div[aria-label^="Remove "]:not([aria-label*="{keep}"])')
    removed = 0
    while removed < max_removals:
        try:
            if await others.count() == 0:
                break
            await others.first.click(timeout=5000)
        except Exception as exc:
            log.debug("location chip removal failed: %s", exc)
            break
        removed += 1
        await page.wait_for_timeout(400)
    return removed


VERIFICATION_TEXTS = ["Verifying your changes", "Enter confirmation code", "confirm your identity"]


async def needs_sms_verification(page) -> bool:
    """True if Ads Manager is showing an identity / SMS verification step."""
    for text in VERIFICATION_TEXTS:
        try:
            if await page.get_by_text(text).count() > 0:
                return True
        except Exception as exc:
            log.debug("verification-prompt check for %r failed: %s", text, exc)
    return False


async def boost(cdp_url: str, account: str, publish: bool = False):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = None
        for attempt in range(10):
            try:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as exc:
                log.debug("boost: ignored error: %s", exc)
                if attempt == 9:
                    raise
                await asyncio.sleep(3)
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
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)
        try:
            await page.wait_for_selector("text=Campaigns", timeout=30000)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)
        await page.wait_for_timeout(2000)
        await _dismiss_auth_prompt(page)

        # ── Dismiss policy / consent modals ──────────────────────
        for label in ["I accept", "Accept", "Got it", "OK", "Continue"]:
            try:
                btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
                if await btn.count() > 0 and await btn.first.is_visible(timeout=3000):
                    await btn.first.click(timeout=5000)
                    await page.wait_for_timeout(1500)
                    print(f"  Dismissed policy modal: '{label}'")
            except Exception as exc:
                log.debug("boost: ignored error: %s", exc)
                continue

        # ── Create campaign ───────────────────────────────────────
        print("Creating campaign...")
        await page.wait_for_timeout(2000)

        if await click_create_button(page) is None:
            shot = await _debug_screenshot(page, "create", account)
            raise RuntimeError(f"Could not find the + Create campaign button in Ads Manager. Screenshot: {shot}")
        await page.wait_for_timeout(1500)

        # Wait for "Loading creation" spinner to fully disappear before interacting.
        # Use wait_for_function polling the DOM text directly — more reliable than
        # selector state on slow networks where the overlay can persist well past 20s.
        print("  Waiting for creation dialog to load...")
        try:
            await page.wait_for_function(
                "() => !document.body.innerText.includes('Loading creation')",
                timeout=90000,
            )
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)
        await page.wait_for_timeout(2000)
        await _dismiss_auth_prompt(page)

        # If we landed directly on the campaign editor (Facebook remembers the last objective
        # and skips the picker), the "Next" button will already be present — skip ahead.
        already_on_editor = await page.get_by_role("button", name=re.compile(r"^Next$", re.I)).count() > 0

        if not already_on_editor:
            # Objective picker is showing — click Engagement then Continue
            engagement_clicked = False
            for locator in [
                page.get_by_text("Engagement", exact=True),
                page.locator('div[role="dialog"] :text("Engagement")'),
                page.locator(':text("Engagement")').filter(has_not_text="New").filter(has_not_text="Post"),
            ]:
                try:
                    await locator.first.click(timeout=6000)
                    engagement_clicked = True
                    break
                except Exception as exc:
                    log.debug("boost: ignored error: %s", exc)
                    continue
            if not engagement_clicked:
                shot = await _debug_screenshot(page, "after_create", account)
                raise RuntimeError(f"Could not find Engagement objective — check {shot}")
            await page.wait_for_timeout(800)

            for locator in [
                page.locator('div[role="dialog"]').get_by_role("button", name=re.compile(r"^Continue$", re.I)),
                page.get_by_role("button", name=re.compile(r"^Continue$", re.I)),
            ]:
                try:
                    await locator.click(timeout=6000)
                    break
                except Exception as exc:
                    log.debug("boost: ignored error: %s", exc)
                    continue
            await page.wait_for_timeout(1500)

        # Some accounts show a second "Manual vs Recommended" dialog
        try:
            await page.locator('div[role="dialog"] :text("Manual")').first.click(timeout=6000)
            await page.wait_for_timeout(800)
            await page.locator('div[role="dialog"]').get_by_role(
                "button", name=re.compile(r"^Continue$", re.I)
            ).click(timeout=6000)
            await page.wait_for_timeout(1200)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)

        # Campaign editor page — click Next to reach the Ad Set section
        next_clicked = False
        for locator in [
            page.get_by_role("button", name=re.compile(r"^Next$", re.I)),
            page.locator('button:has-text("Next")'),
            page.locator('div[role="button"]:has-text("Next")'),
        ]:
            try:
                await locator.first.click(timeout=6000)
                next_clicked = True
                break
            except Exception as exc:
                log.debug("boost: ignored error: %s", exc)
                continue
        if not next_clicked:
            shot = await _debug_screenshot(page, "next", account)
            raise RuntimeError(f"Could not find the Next button on campaign editor. Screenshot: {shot}")
        await page.wait_for_timeout(1500)

        # ── Ad set ────────────────────────────────────────────────
        print("Configuring ad set...")
        # "On your ad" is an option inside the "Message destinations" dropdown
        await page.click("text=Message destinations", timeout=10000)
        await page.wait_for_timeout(600)
        await page.click('text="On your ad"', timeout=8000)
        await page.wait_for_timeout(1000)

        # Switch engagement type from default "Video views" to "Post engagement"
        try:
            await page.click("text=Video views", timeout=8000)
            await page.wait_for_timeout(600)
            await page.click("text=Post engagement", timeout=5000)
            await page.wait_for_timeout(800)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)
            pass  # already set, or account defaults differently

        # Location targeting — scroll the inner form container (Ads Manager doesn't use window scroll)
        print("Setting location: Paraguay...")

        async def scroll_form(amount):
            vp = page.viewport_size or {"width": 1280, "height": 800}
            # 50% width lands in the main form area, not the left campaign tree panel
            x = int(vp["width"] * 0.50)
            y = int(vp["height"] * 0.5)
            await page.mouse.move(x, y)
            await page.mouse.wheel(0, amount)

        # Expand hidden audience/location fields if collapsed
        try:
            await page.click("text=Show more options", timeout=3000)
            await page.wait_for_timeout(800)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)

        included = page.locator("text=Included location:").first
        for _ in range(40):
            if await included.count() > 0:
                visible = await included.is_visible()
                if visible:
                    break
            await scroll_form(200)
            await page.wait_for_timeout(200)

        await included.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
        await page.wait_for_timeout(1000)

        # Click the Edit link nearest to the Locations heading
        heading = page.locator("text=* Locations").first
        heading_box = await heading.bounding_box()
        anchor_box = heading_box or await included.bounding_box()

        async def nearest_edit():
            best, best_dy = None, None
            for c in await page.locator('text="Edit"').all():
                cbox = await c.bounding_box()
                if cbox and anchor_box and cbox["y"] >= anchor_box["y"] - 5:
                    dy = cbox["y"] - anchor_box["y"]
                    if best_dy is None or dy < best_dy:
                        best_dy, best = dy, c
            return best

        edit_btn = await nearest_edit()
        if edit_btn is None:
            # Clicking anywhere in the location summary "activates" the box
            # and reveals its Edit link on some accounts.
            try:
                await included.click(timeout=5000)
                await page.wait_for_timeout(800)
                edit_btn = await nearest_edit()
            except Exception as exc:
                log.debug("Locations 'Edit' link activation-click failed: %s", exc)
        if edit_btn is None:
            shot = await _debug_screenshot(page, "location", account)
            raise RuntimeError(f"Could not find the Locations Edit link. Screenshot: {shot}")
        await edit_btn.click(timeout=8000)
        await page.wait_for_timeout(1000)

        # Find the country search input (label varies by account)
        search = None
        for sel in [
            'input[placeholder*="ountry" i]',
            'input[placeholder*="ocation" i]',
            'input[aria-label*="ocation" i]',
            'div[role="dialog"] input[type="text"]',
            'input[aria-label="Add locations"]',
        ]:
            try:
                cand = page.locator(sel).first
                if await cand.is_visible(timeout=2000):
                    search = cand
                    break
            except Exception as exc:
                log.debug("boost: ignored error: %s", exc)
                continue
        if not search:
            raise RuntimeError("No location search input found after clicking Edit.")

        # Add Paraguay first, then remove any chip that is not Paraguay
        await search.fill("Paraguay")
        await page.wait_for_timeout(1000)
        await search.press("Enter")
        await page.wait_for_timeout(1000)

        await remove_other_location_chips(page, keep="Paraguay")

        await page.get_by_role("button", name=re.compile(r"^Next$", re.I)).click(timeout=10000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)

        # ── Ad level: select existing post ────────────────────────
        print("Selecting most recent post...")
        try:
            ad_setup_heading = page.locator("text=Ad setup").first
            await ad_setup_heading.wait_for(state="attached", timeout=8000)
            await ad_setup_heading.evaluate("el => el.scrollIntoView({block: 'start', behavior: 'instant'})")
            await page.wait_for_timeout(500)
        except Exception as exc:
            log.debug("Ad setup heading scroll failed: %s", exc)
        use_existing = page.locator("text=Use existing post").first
        await use_existing.wait_for(state="attached", timeout=15000)
        for _ in range(20):
            if await use_existing.is_visible():
                break
            await scroll_form(200)
            await page.wait_for_timeout(200)
        await use_existing.click(timeout=10000)
        await page.wait_for_timeout(1000)
        await page.click("text=Select post")
        await page.wait_for_timeout(2000)

        # "Select post" is a table (Facebook post / Post ID / Source / Media
        # / Date created), sorted newest first — not a media grid. select_target_post()
        # tries to match the post ID post.py actually recorded instead of blindly
        # trusting the first (newest) row, and falls back loudly if it can't.
        await select_target_post(page, account)
        await page.wait_for_timeout(1000)

        for label in ["Continue", "Select"]:
            try:
                await page.click(f'button:has-text("{label}")', timeout=4000)
                break
            except Exception as exc:
                log.debug("boost: ignored error: %s", exc)
                continue
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception as exc:
            log.debug("boost: ignored error: %s", exc)


        # ── Publish (or leave as a draft) ───────────────────────────
        if not publish:
            print(
                "\nLeaving campaign as a draft — not clicking Publish. Facebook "
                "auto-saves it under Ads Manager > Drafts; review and publish it "
                "there manually when ready."
            )
            return

        print("Publishing campaign...")
        publish_clicked = False
        for locator in [
            page.get_by_role("button", name=re.compile(r"publish", re.I)),
            page.locator('button:has-text("Publish")'),
            page.locator('div[role="button"]:has-text("Publish")'),
        ]:
            try:
                await locator.first.click(timeout=10000)
                publish_clicked = True
                break
            except Exception as exc:
                log.debug("boost: ignored error: %s", exc)
                continue
        if not publish_clicked:
            raise RuntimeError("Could not find the Publish button.")
        await page.wait_for_timeout(3000)

        # ── SMS verification (if triggered) ───────────────────────
        if await needs_sms_verification(page):
            print("\nSMS verification required...")
            await _handle_verification(page)

        await page.wait_for_timeout(2000)
        print("\nCampaign published successfully.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("=" * 54)
    print("   Facebook Ad Booster")
    print("=" * 54)

    ensure_mlx_credentials()
    account = pick_account()
    publish = ask_publish_mode()
    if publish:
        ensure_textverified_credentials()

    print(f"\nStarting profile '{account}'...")
    from mlx_context import start_profile_for
    client, started = start_profile_for(account)

    try:
        asyncio.run(boost(started.cdp_url, account, publish=publish))
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
