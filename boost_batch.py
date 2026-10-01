"""
boost_batch.py  —  Phase 2 batch runner
Creates Facebook engagement ad campaigns for multiple profiles concurrently,
capped at --workers simultaneous Chrome instances (default: 2).

Usage:
    python boost_batch.py
    python boost_batch.py --workers 2
    python boost_batch.py --profiles EMI_AUTO_2,EMI_AUTO_3 --publish
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

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
    publish: bool,
    results: dict,
):
    tag = f"[{profile_name}]"
    # Everything post.py/boost.py prints from this task (and the threads it
    # starts) gets this profile's tag -- see console.py.
    profile_tag.set(tag)

    async with semaphore:
        jitter = random.uniform(5, 20)
        print(f"{tag} Waiting {jitter:.1f}s before launch (anti-detection stagger)...")
        await asyncio.sleep(jitter)

        client = None
        started = None

        try:
            print(f"{tag} Starting Multilogin profile...")
            from mlx_context import start_profile_for
            client, started = await asyncio.to_thread(start_profile_for, profile_name)
            print(f"{tag} Profile running on port {started.port}")

            from boost import boost
            await boost(started.cdp_url, profile_name, publish=publish)

            results[profile_name] = "success"
            print(f"{tag} Done.")

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


async def main_async(profiles: list[str], workers: int, publish: bool):
    semaphore = asyncio.Semaphore(workers)
    results: dict[str, str] = {}

    tasks = [
        run_profile(name, semaphore, publish, results)
        for name in profiles
    ]

    print(f"\nBoosting {len(profiles)} profile(s) — {workers} worker(s) max\n")
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

    parser = argparse.ArgumentParser(description="Batch booster — run ads for multiple profiles.")
    parser.add_argument("--workers",  type=int, default=2,
                        help="Max simultaneous Chrome instances (default: 2)")
    parser.add_argument("--profiles", type=str, default=None,
                        help="Comma-separated profile names (default: all in mlx_profiles.json)")
    parser.add_argument("--publish",  action="store_true",
                        help="Publish campaigns (default: save as draft)")
    args = parser.parse_args()

    # Ask for any missing credentials now, before profiles start in parallel,
    # never halfway through a batch.
    from boost import ensure_mlx_credentials
    ensure_mlx_credentials()

    if args.profiles:
        profiles = [p.strip() for p in args.profiles.split(",")]
        unknown = [p for p in profiles if p not in all_profiles]
        if unknown:
            print(f"Error: Unknown profile(s): {', '.join(unknown)}")
            sys.exit(1)
    else:
        profiles = all_profiles

    if not profiles:
        print("No profiles to run.")
        sys.exit(0)

    print("=" * 54)
    print("   Facebook Ad Booster — Batch Mode")
    print("=" * 54)
    print(f"  Profiles : {', '.join(profiles)}")
    print(f"  Workers  : {args.workers}")
    print(f"  Mode     : {'Publish' if args.publish else 'Draft'}")

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
        asyncio.run(main_async(profiles, args.workers, args.publish))
    except KeyboardInterrupt:
        print("\nCancelled.")


if __name__ == "__main__":
    main()
