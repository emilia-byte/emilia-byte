"""
run_batch.py  —  Full pipeline batch runner (Phase 1 + Phase 2)
For each profile: posts 3 posts, then boosts the most recent one.
All profiles run concurrently, capped at --workers simultaneous Chrome instances.

Usage:
    python run_batch.py --posts posts.txt
    python run_batch.py --posts posts.txt --workers 2
    python run_batch.py --posts posts.txt --profiles EMI_AUTO_2,EMI_AUTO_3 --publish
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from batch_common import LaunchPacer, use_thread_pool
from console import profile_tag

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"


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


async def run_profile(
    profile_name: str,
    semaphore: asyncio.Semaphore,
    posts_path: str,
    min_delay: int,
    max_delay: int,
    publish: bool,
    results: dict,
    pacer: LaunchPacer | None = None,
):
    tag = f"[{profile_name}]"
    # Everything post.py/boost.py prints from this task (and the threads it
    # starts) gets this profile's tag -- see console.py.
    profile_tag.set(tag)

    async with semaphore:
        # Spaced launches: Multilogin's request limit is shared by the whole
        # workspace (see batch_common.py).
        waited = await (pacer or LaunchPacer()).wait()
        print(f"{tag} Launching (waited {waited:.1f}s for a launch slot)...")

        client = None
        started = None

        try:
            # ── Phase 1: start profile + post (single thread — Playwright sync requires it) ──
            print(f"{tag} Starting Multilogin profile...")
            from post import run_phase1
            client, started = await asyncio.to_thread(
                run_phase1, profile_name, posts_path, min_delay, max_delay
            )
            print(f"{tag} Phase 1 done.")

            # ── Phase 2: boost ─────────────────────────────────────────────
            print(f"{tag} Phase 2: boosting...")
            from boost import boost
            await boost(started.cdp_url, profile_name, publish=publish)
            print(f"{tag} Phase 2 done.")

            results[profile_name] = "success"

        except Exception as exc:
            results[profile_name] = f"error: {exc}"
            print(f"{tag} Failed: {exc}")

        finally:
            if client and started:
                try:
                    print(f"{tag} Stopping profile...")
                    await asyncio.to_thread(client.stop_profile, started.profile_id)
                except Exception as exc:
                    print(f"{tag} Warning: stop_profile failed: {exc}")


async def main_async(profiles, workers, posts_path, min_delay, max_delay, publish):
    use_thread_pool(workers)  # else asyncio caps concurrent profiles at ~cpu_count+4
    semaphore = asyncio.Semaphore(workers)
    pacer = LaunchPacer()
    results: dict[str, str] = {}

    tasks = [
        run_profile(name, semaphore, posts_path, min_delay, max_delay, publish, results, pacer)
        for name in profiles
    ]

    print(f"\nRunning full pipeline for {len(profiles)} profile(s) — {workers} worker(s) max\n")
    await asyncio.gather(*tasks, return_exceptions=True)

    print("\n" + "=" * 54)
    print("  Results")
    print("=" * 54)
    ok   = [n for n, r in results.items() if r == "success"]
    fail = [(n, r) for n, r in results.items() if r != "success"]
    for name in ok:
        print(f"  OK   {name}")
    for name, reason in fail:
        print(f"  FAIL {name}: {reason}")
    print("=" * 54)
    print(f"  {len(ok)} succeeded, {len(fail)} failed")
    print("=" * 54)


def main():
    _load_dotenv()

    profiles_file = ROOT / "mlx_profiles.json"
    if not profiles_file.exists():
        print("Error: mlx_profiles.json not found. Run sync_profiles.py first.")
        sys.exit(1)

    all_profiles = list(json.loads(profiles_file.read_text()).keys())

    parser = argparse.ArgumentParser(description="Full pipeline — post + boost for multiple profiles.")
    parser.add_argument("--posts",      required=True, help="Path to posts.txt")
    parser.add_argument("--workers",    type=int, default=2,
                        help="Max simultaneous Chrome instances (default: 2)")
    parser.add_argument("--profiles",   type=str, default=None,
                        help="Comma-separated profile names (default: all in mlx_profiles.json)")
    parser.add_argument("--publish",    action="store_true",
                        help="Publish boost campaigns (default: save as draft)")
    parser.add_argument("--min-delay",  type=int, default=45,
                        help="Min seconds between posts (default: 45)")
    parser.add_argument("--max-delay",  type=int, default=90,
                        help="Max seconds between posts (default: 90)")
    args = parser.parse_args()

    # Ask for any missing credentials now, before profiles start in parallel,
    # never halfway through a batch.
    from boost import ensure_mlx_credentials
    ensure_mlx_credentials()

    if not Path(args.posts).exists():
        print(f"Error: posts file not found: {args.posts}")
        sys.exit(1)

    if args.profiles:
        profiles = [p.strip() for p in args.profiles.split(",")]
    else:
        profiles = all_profiles

    if not profiles:
        print("No profiles to run.")
        sys.exit(0)

    print("=" * 54)
    print("   Full Pipeline — Post + Boost")
    print("=" * 54)
    print(f"  Profiles : {', '.join(profiles)}")
    print(f"  Workers  : {args.workers}")
    print(f"  Posts    : {args.posts}")
    print(f"  Delay    : {args.min_delay}–{args.max_delay}s between posts")
    print(f"  Boost    : {'Publish' if args.publish else 'Draft'}")

    if args.publish:
        # Publishing spends real ad budget on every profile in the batch --
        # require an explicit typed confirmation, never just the flag.
        answer = input(
            f"\n  --publish will PUBLISH {len(profiles)} campaign(s) and spend real ad "
            f"budget.\n  Type 'publish' to confirm (anything else saves drafts): "
        ).strip().lower()
        if answer != "publish":
            print("  Not confirmed — campaigns will be saved as drafts.")
            args.publish = False

    if args.publish:
        # Publishing can trigger SMS verification; without these, every
        # profile that hits it would stall on a manual-verification prompt.
        from boost import ensure_textverified_credentials
        ensure_textverified_credentials()

    try:
        asyncio.run(main_async(
            profiles, args.workers, args.posts,
            args.min_delay, args.max_delay, args.publish,
        ))
    except KeyboardInterrupt:
        print("\nCancelled.")


if __name__ == "__main__":
    main()
