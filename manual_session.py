"""
Open Facebook inside a Multilogin profile and wait for you to log in manually.
Multilogin saves the session in the profile automatically — no cookie file needed.
Run post.py after this completes.

Usage:
    python3 manual_session.py --account "EMI_AUTO_111"

Requirements:
    MLX_EMAIL and MLX_PASSWORD environment variables must be set.
    mlx_profiles.json must have an entry for the account.
"""

import argparse
import sys
import time

from playwright.sync_api import sync_playwright

from mlx_context import start_profile_for

TIMEOUT = 600  # 10 minutes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, help="Account name (e.g. EMI_AUTO_111)")
    args = parser.parse_args()

    print(f"Opening Facebook login for account: {args.account}")
    print("→ Log in as usual (email, password, 2FA, checkpoints).")
    print("→ Multilogin will save the session in your profile automatically.\n")

    mlx, started = start_profile_for(args.account)

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(started.cdp_url)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.evaluate("window.location.href = 'https://www.facebook.com/login'")
            page.wait_for_load_state("domcontentloaded")

            start = time.time()
            while time.time() - start < TIMEOUT:
                url = page.url
                if "login" not in url and "checkpoint" not in url:
                    print(f"Login detected. Session saved in Multilogin profile '{args.account}'.")
                    print("You can close the browser.")
                    try:
                        page.wait_for_event("close", timeout=0)
                    except Exception:
                        pass
                    break
                time.sleep(1)
            else:
                print("Timed out. Please re-run and log in within 10 minutes.")
                sys.exit(1)
        finally:
            mlx.stop_profile(started.profile_id)


if __name__ == "__main__":
    main()
