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

    def _show_list():
        print("\n── Accounts ──────────────────────────────────────────────")
        for i, name in enumerate(accounts, 1):
            print(f"  {i}. {name}")
        print("  S. Sync profiles from Multilogin")
        print("  Or type any profile name directly")

    _show_list()

    while True:
        choice = input(f"\n  Pick [1-{len(accounts)}], S to sync, or type a name: ").strip()
        if choice.lower() == "s":
            run_cmd(["sync_profiles.py"])
            accounts = list_accounts()
            _show_list()
        elif choice.isdigit() and 1 <= int(choice) <= len(accounts):
            return accounts[int(choice) - 1]
        elif choice:
            return choice  # start_profile_for will resolve via live API lookup if needed
        else:
            print("  Please enter a number, S, or a profile name.")


# ── Mode picker ───────────────────────────────────────────────────────────────

def pick_mode() -> str:
    print("\n── What do you want to do? ───────────────────────────────")
    print("  1. Generate posts + publish  (full run, one profile)")
    print("  2. Generate posts only       (one profile)")
    print("  3. Publish existing posts.txt (one profile)")
    print("  4. Batch: generate unique posts per profile + publish all")
    # Phase 2 (Ads Manager) ships separately; offer it only once it's unpacked here.
    choices = ["1", "2", "3", "4"]
    if (ROOT / "boost_batch.py").exists():
        print("  5. Batch: boost the latest post on several profiles")
        choices.append("5")

    while True:
        choice = input(f"\n  Pick [1-{choices[-1]}]: ").strip()
        if choice in choices:
            return choice
        print(f"  Please enter one of: {', '.join(choices)}.")


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


def _pick_batch_profiles() -> list[str]:
    from mlx_context import list_accounts

    profiles = list_accounts()
    if not profiles:
        print("No profiles found in mlx_profiles.json.")
        return []

    # ── Profile selection ──────────────────────────────────────────────────
    print(f"\n── Batch mode: profile selection ─────────────────────────")
    print(f"  Available ({len(profiles)}): {', '.join(profiles)}")
    print(f"\n  A        — run all {len(profiles)} profiles")
    print(f"  15       — run first 15  (or any number)")
    print(f"  name,... — comma-separated list (e.g. EMI_AUTO_2,EMI_AUTO_5)")

    while True:
        sel = input("\n  Pick: ").strip()
        if not sel or sel.upper() == "A":
            selected = profiles
            break
        elif sel.isdigit():
            n = int(sel)
            selected = profiles[:n]
            if not selected:
                print(f"  No profiles available.")
                continue
            break
        else:
            names = [s.strip() for s in sel.split(",") if s.strip()]
            invalid = [n for n in names if n not in profiles]
            if invalid:
                print(f"  Unknown profiles: {', '.join(invalid)}. Available: {', '.join(profiles)}")
                continue
            selected = names
            break

    print(f"\n  Running on {len(selected)} profile(s): {', '.join(selected)}")
    return selected


def _pick_workers(n_profiles: int) -> int:
    default = min(n_profiles, 5)
    workers = input(f"\n  How many to run simultaneously? [default: {default}]: ").strip()
    return int(workers) if workers.isdigit() and int(workers) > 0 else default


def batch_generate_and_publish(category: str | None) -> bool:
    profiles = _pick_batch_profiles()
    if not profiles:
        return False

    if not category:
        CATEGORIES = {"1": "LS", "2": "HOB", "3": "CSI", "4": "MF"}
        LABELS = {"LS": "Lifestyle", "HOB": "Hobbies",
                  "CSI": "Career and Self Improvement", "MF": "Market and Finance"}
        print("\n── Post category ─────────────────────────────────────────")
        for k, code in CATEGORIES.items():
            print(f"  {k}. {code} — {LABELS[code]}")
        while True:
            choice = input("\n  Pick [1-4]: ").strip()
            if choice in CATEGORIES:
                category = CATEGORIES[choice]
                break
            print("  Please enter 1, 2, 3, or 4.")

    workers = _pick_workers(len(profiles))

    print("\n" + "─" * 54)
    print(f"Generating unique posts for {len(profiles)} profiles...")
    print("─" * 54)

    from generate_posts import generate_for_profiles, _hf_token, generate_image, IMAGES_DIR, write_images_txt
    from generate_posts import generate_three_posts, CATEGORY_MAP, resolve_url

    cat_name = CATEGORY_MAP[category]
    url = resolve_url(None)
    print(f"Category: {cat_name}\n")
    generate_for_profiles(profiles, cat_name, url)

    hf = _hf_token()
    if hf:
        print("\nGenerating 3 shared images via Hugging Face...")
        _, image_prompts = generate_three_posts(cat_name, url)
        write_images_txt(image_prompts)
        saved = [generate_image(p, i) for i, p in enumerate(image_prompts)]
        print(f"{sum(1 for p in saved if p)}/3 images saved.")
    else:
        print("\nNo HF_TOKEN — place images manually as images/post_1.jpg, post_2.jpg, post_3.jpg")

    print("\n" + "─" * 54)
    print(f"Batch publishing to {len(profiles)} profiles ({workers} at a time)...")
    print("─" * 54)

    return run_cmd(["post_batch.py", "--posts", str(ROOT / "posts.txt"),
                    "--profiles", ",".join(profiles),
                    "--workers", str(workers)])


def batch_boost() -> bool:
    profiles = _pick_batch_profiles()
    if not profiles:
        return False
    workers = _pick_workers(len(profiles))

    print("\n── Boost mode ────────────────────────────────────────────")
    choice = input("  Leave campaigns as drafts, or publish? [draft/publish] (default: draft): ").strip().lower()
    # boost_batch.py still asks for a typed confirmation before publishing.
    publish_flag = ["--publish"] if choice == "publish" else []

    print("\n" + "─" * 54)
    print(f"Batch boosting {len(profiles)} profiles ({workers} at a time)...")
    print("─" * 54)
    return run_cmd(["boost_batch.py", "--profiles", ",".join(profiles),
                    "--workers", str(workers)] + publish_flag)


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
    mode = pick_mode()

    ok = True

    if mode == "4":
        category = pick_category()
        ok = batch_generate_and_publish(category)
    elif mode == "5":
        ok = batch_boost()
    else:
        account = pick_account()
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
