"""
Interactive launcher for the Facebook Page publishing pipeline.
No coding required — just answer the prompts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"
POSTS_FILE = ROOT / "posts.txt"


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

    email = os.environ.get("MLX_EMAIL", "").strip()
    password = os.environ.get("MLX_PASSWORD", "").strip()

    if email and password:
        return

    print("\n── Multilogin credentials ────────────────────────────────")
    print("(These are stored locally in a .env file so you only need")
    print(" to enter them once.)\n")

    if not email:
        email = input("  Multilogin email: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("  Multilogin password: ").strip()

    os.environ["MLX_EMAIL"] = email
    os.environ["MLX_PASSWORD"] = password

    save = input("\n  Save credentials to .env so you're not asked again? [Y/n]: ").strip().lower()
    if save in ("", "y", "yes"):
        lines = []
        if ENV_FILE.exists():
            lines = [l for l in ENV_FILE.read_text().splitlines()
                     if not l.startswith("MLX_EMAIL") and not l.startswith("MLX_PASSWORD")]
        lines += [f"MLX_EMAIL={email}", f"MLX_PASSWORD={password}"]
        ENV_FILE.write_text("\n".join(lines) + "\n")
        print("  Saved to .env")


# ── Account picker ────────────────────────────────────────────────────────────

def pick_account() -> str:
    from mlx_context import list_accounts

    accounts = list_accounts()

    print("\n── Accounts ──────────────────────────────────────────────")
    for i, name in enumerate(accounts, 1):
        print(f"  {i}. {name}")
    print(f"  S. Sync profiles from Multilogin")

    while True:
        choice = input(f"\n  Pick an account [1-{len(accounts)}] or S to sync: ").strip().lower()
        if choice == "s":
            run_cmd(["sync_profiles.py"])
            accounts = list_accounts()
            print("\n── Accounts ──────────────────────────────────────────────")
            for i, name in enumerate(accounts, 1):
                print(f"  {i}. {name}")
            print(f"  S. Sync profiles from Multilogin")
        elif choice.isdigit() and 1 <= int(choice) <= len(accounts):
            return accounts[int(choice) - 1]
        else:
            print("  Please enter a number from the list, or S to sync.")


# ── Mode picker ───────────────────────────────────────────────────────────────

def pick_mode() -> str:
    print("\n── What do you want to do? ───────────────────────────────")
    print("  1. Generate posts + publish  (full run)")
    print("  2. Generate posts only")
    print("  3. Publish existing posts.txt")

    while True:
        choice = input("\n  Pick [1-3]: ").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("  Please enter 1, 2, or 3.")


# ── Category picker (optional override) ──────────────────────────────────────

def pick_category() -> str | None:
    CATEGORIES = {"1": "LS", "2": "HOB", "3": "CSI", "4": "MF"}
    LABELS = {
        "LS": "Lifestyle",
        "HOB": "Hobbies",
        "CSI": "Career and Self Improvement",
        "MF": "Market and Finance",
    }

    print("\n── Post category ─────────────────────────────────────────")
    print("  0. Auto-detect from Facebook (recommended)")
    for k, code in CATEGORIES.items():
        print(f"  {k}. {code} — {LABELS[code]}")

    while True:
        choice = input("\n  Pick [0-4]: ").strip()
        if choice == "0":
            return None
        if choice in CATEGORIES:
            return CATEGORIES[choice]
        print("  Please enter 0, 1, 2, 3, or 4.")


# ── Runners ───────────────────────────────────────────────────────────────────

def run_cmd(args: list[str]) -> bool:
    result = subprocess.run(
        [sys.executable] + args,
        cwd=str(ROOT),
        env=os.environ.copy(),
    )
    return result.returncode == 0


def generate(account: str, category: str | None) -> bool:
    print("\n" + "─" * 54)
    print("Generating posts and images...")
    print("─" * 54)
    cmd = ["generate_posts.py", "--account", account]
    if category:
        cmd += ["--category", category]
    return run_cmd(cmd)


def publish(account: str) -> bool:
    if not POSTS_FILE.exists():
        print(f"\nError: {POSTS_FILE} not found. Run 'Generate posts' first.")
        return False
    print("\n" + "─" * 54)
    print("Publishing posts to Facebook...")
    print("─" * 54)
    return run_cmd(["post.py", "--account", account, "--posts", str(POSTS_FILE)])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 54)
    print("   Facebook Page Publisher")
    print("=" * 54)

    ensure_credentials()
    account = pick_account()
    mode = pick_mode()

    ok = True

    if mode == "1":
        category = pick_category()
        ok = generate(account, category)
        if ok:
            ok = publish(account)

    elif mode == "2":
        category = pick_category()
        ok = generate(account, category)

    elif mode == "3":
        ok = publish(account)

    print("\n" + "=" * 54)
    if ok:
        print("   Done.")
    else:
        print("   Finished with errors — check the output above.")
    print("=" * 54)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
    except Exception as exc:
        print(f"\n\nError: {exc}")
    input("\nPress Enter to close...")
