"""
sync_profiles.py

Uses the Multilogin X CLI (xcli) to list all profiles in the BUFFALO FB folder
and save them to mlx_profiles.json automatically.

Run this once whenever new profiles are added in Multilogin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PROFILES_FILE = ROOT / "mlx_profiles.json"
FOLDER_ID = "5bfc9a9a-4d09-4988-ad84-2e2b0cf107c6"


def find_xcli() -> Path:
    username = os.environ.get("USERNAME") or os.environ.get("USER", "")
    candidates = [
        Path(f"C:/Users/{username}/mlx/deps/cli/xcli.exe"),
        Path(f"C:/Users/{username}/mlx/deps/cli/xcli"),
        Path("xcli.exe"),
        Path("xcli"),
    ]
    for p in candidates:
        if p.exists():
            return p
    print("Error: xcli not found. Make sure Multilogin X is installed.")
    print("Expected location: C:/Users/<username>/mlx/deps/cli/xcli.exe")
    sys.exit(1)


def _load_dotenv():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def run(xcli: Path, *args: str) -> str:
    result = subprocess.run(
        [str(xcli)] + list(args),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"xcli error: {result.stderr or result.stdout}")
        sys.exit(1)
    return result.stdout.strip()


def main():
    _load_dotenv()

    email = os.environ.get("MLX_EMAIL", "").strip()
    password = os.environ.get("MLX_PASSWORD", "").strip()

    if not email or not password:
        print("Error: MLX_EMAIL and MLX_PASSWORD must be set in .env")
        sys.exit(1)

    xcli = find_xcli()
    print(f"Found xcli at: {xcli}")

    print("Logging in to Multilogin...")
    run(xcli, "login", "--username", email, "--password", password)
    print("[OK] Logged in.")

    print("\nListing profiles in BUFFALO FB folder...")
    raw = run(xcli, "profile-list", "-f", FOLDER_ID, "-l", "100")
    # Parse the text table: rows between the first and second separator lines
    profiles: dict[str, str] = {}
    in_table = False
    for line in raw.splitlines():
        if line.startswith("---"):
            if not in_table:
                in_table = True
            else:
                break
            continue
        if not in_table:
            continue
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 36 and parts[0].count("-") == 4:
            profiles[parts[1]] = parts[0]

    if not profiles:
        print("No profiles found or could not parse output.")
        print("Check the raw output above.")
        sys.exit(1)

    PROFILES_FILE.write_text(json.dumps(profiles, indent=2) + "\n")
    print(f"Saved {len(profiles)} profiles to mlx_profiles.json:")
    for name in sorted(profiles):
        print(f"  {name}")


if __name__ == "__main__":
    main()
    input("\nPress Enter to close...")
