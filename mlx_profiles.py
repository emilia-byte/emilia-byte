"""
mlx_profiles.py

Loads the Multilogin profile map from mlx_profiles.json.

Expected JSON shape:

    {
      "BFL_001": "profile-uuid",
      "BFL_002": "profile-uuid",
      ...
    }

Run sync_profiles.py to auto-populate this file from Multilogin.
"""

from __future__ import annotations

import json
from pathlib import Path


class ProfileMapError(RuntimeError):
    """Raised when mlx_profiles.json is missing or malformed."""


def load_profile_map(path: str | Path) -> dict[str, str]:
    """Returns {profile_name: profile_uuid}."""
    path = Path(path)
    if not path.exists():
        raise ProfileMapError(f"{path} does not exist. Run sync_profiles.py first.")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ProfileMapError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileMapError(f"{path} must be a JSON object mapping profile name to UUID")
    return data
