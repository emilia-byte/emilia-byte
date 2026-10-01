"""
Shared helper: sign into Multilogin, start a profile, and return
(client, started_profile) ready for connect_over_cdp().

Credentials come from environment variables MLX_EMAIL and MLX_PASSWORD.
Profile UUIDs come from mlx_profiles.json next to this file.
Run sync_profiles.py to auto-populate mlx_profiles.json from Multilogin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from multilogin_client import MultiloginClient

FOLDER_ID = "5bfc9a9a-4d09-4988-ad84-2e2b0cf107c6"
MLX_PROFILES_PATH = Path(__file__).parent / "mlx_profiles.json"


def _load_profiles() -> dict[str, str]:
    """Returns {profile_name: profile_uuid}."""
    if not MLX_PROFILES_PATH.exists():
        print(f"Error: {MLX_PROFILES_PATH} not found. Run sync_profiles.py first.")
        sys.exit(1)
    return json.loads(MLX_PROFILES_PATH.read_text())


def _client() -> MultiloginClient:
    email = os.environ.get("MLX_EMAIL")
    password = os.environ.get("MLX_PASSWORD")
    if not email or not password:
        print("Error: MLX_EMAIL and MLX_PASSWORD environment variables must be set.")
        sys.exit(1)
    client = MultiloginClient(email=email, password=password)
    client.sign_in()
    return client


def _find_xcli() -> Path | None:
    username = os.environ.get("USERNAME") or os.environ.get("USER", "")
    candidates = [
        Path(f"C:/Users/{username}/mlx/deps/cli/xcli.exe"),
        Path(f"C:/Users/{username}/mlx/deps/cli/xcli"),
        Path("xcli.exe"),
        Path("xcli"),
    ]
    return next((p for p in candidates if p.exists()), None)


def _search_via_xcli(name: str, folder_id: str) -> str | None:
    """Search for a profile by name using the xcli binary."""
    xcli = _find_xcli()
    if not xcli:
        print(f"  [xcli] not found — checked standard Multilogin install paths")
        return None

    print(f"  [xcli] found at {xcli}")
    email = os.environ.get("MLX_EMAIL", "")
    password = os.environ.get("MLX_PASSWORD", "")
    try:
        login_result = subprocess.run(
            [str(xcli), "login", "--username", email, "--password", password],
            capture_output=True, text=True,
        )
        if login_result.returncode != 0:
            print(f"  [xcli] login failed: {login_result.stderr or login_result.stdout}")
            return None
        print(f"  [xcli] login OK — listing profiles...")
    except Exception as e:
        print(f"  [xcli] error: {e}")
        return None

    def _parse_page(stdout: str) -> list[tuple[str, str]]:
        rows, in_table = [], False
        for line in stdout.splitlines():
            if line.startswith("---"):
                in_table = not in_table
                continue
            if not in_table:
                continue
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 36 and parts[0].count("-") == 4:
                rows.append((parts[1], parts[0]))  # (name, uuid)
        return rows

    page, total = 0, 0
    while True:
        cmd = [str(xcli), "profile-list", "-f", folder_id, "-l", "100"]
        if page > 0:
            cmd += ["-p", str(page)]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            if page == 0:
                print(f"  [xcli] profile-list failed: {result.stderr or result.stdout}")
            break

        rows = _parse_page(result.stdout)
        total += len(rows)
        for pname, puuid in rows:
            if pname == name:
                print(f"  [xcli] found on page {page} ({total} profiles searched)")
                return puuid

        if len(rows) < 100:
            break  # last page
        page += 1

    print(f"  [xcli] searched {total} profiles across {page + 1} page(s) — '{name}' not found")
    return None


def list_accounts() -> list[str]:
    return sorted(_load_profiles().keys())


def start_profile_for(account_name: str):
    """
    Look up profile UUID from mlx_profiles.json, sign in, start the profile.
    Returns (MultiloginClient, StartedProfile).
    Caller must call client.stop_profile(started.profile_id) when done.
    """
    profiles = _load_profiles()

    import time
    from multilogin_client import MultiloginError

    client = _client()

    if account_name in profiles:
        profile_id = profiles[account_name]
    else:
        print(f"  '{account_name}' not in local map — searching Multilogin...")
        profile_id = None
        try:
            profile_id = client.search_profile_by_name(account_name, FOLDER_ID)
        except Exception as e:
            print(f"  API profile search failed ({e}) — trying xcli...")
        if not profile_id:
            profile_id = _search_via_xcli(account_name, FOLDER_ID)
        if not profile_id:
            raise RuntimeError(f"Profile '{account_name}' not found in Multilogin. Check the name and try again.")
        print(f"  Found: {profile_id}")

    for attempt in range(5):
        try:
            started = client.start_profile(FOLDER_ID, profile_id)
            return client, started
        except MultiloginError as e:
            err = str(e)
            if "LOCK_PROFILE_ERROR" in err:
                # Never auto-stop: a locked profile is most likely open on a
                # teammate's machine right now.
                raise MultiloginError(
                    f"Profile '{account_name}' is locked -- it's likely open on another "
                    f"teammate's machine. Refusing to stop it; close it in the Multilogin "
                    f"app first if you're sure it's yours."
                ) from e
            elif "CORE_DOWNLOADING_STARTED" in err and attempt < 4:
                wait = 30 * (attempt + 1)
                print(f"  Multilogin is downloading browser core — waiting {wait}s ({attempt + 1}/4)...")
                time.sleep(wait)
            else:
                raise
