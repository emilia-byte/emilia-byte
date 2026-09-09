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


def list_accounts() -> list[str]:
    return sorted(_load_profiles().keys())


def start_profile_for(account_name: str):
    """
    Look up profile UUID from mlx_profiles.json, sign in, start the profile.
    Returns (MultiloginClient, StartedProfile).
    Caller must call client.stop_profile(started.profile_id) when done.
    """
    profiles = _load_profiles()

    if account_name not in profiles:
        print(f"Error: '{account_name}' not found in mlx_profiles.json.")
        print(f"Available: {sorted(profiles.keys())}")
        print("Run sync_profiles.py to refresh the profile list.")
        sys.exit(1)

    profile_id = profiles[account_name]
    client = _client()
    started = client.start_profile(FOLDER_ID, profile_id)
    return client, started
