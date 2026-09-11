"""
Unit tests for mlx_profiles.load_profile_map() -- loading and validating
the profile-name-to-UUID map from mlx_profiles.json. No browser/network
involved.
"""

import json

import pytest

from mlx_profiles import ProfileMapError, load_profile_map


def test_load_profile_map_returns_dict(tmp_path):
    path = tmp_path / "mlx_profiles.json"
    path.write_text(json.dumps({"EMI_AUTO_2": "uuid-1", "EMI_AUTO_4": "uuid-2"}))

    result = load_profile_map(path)

    assert result == {"EMI_AUTO_2": "uuid-1", "EMI_AUTO_4": "uuid-2"}


def test_load_profile_map_missing_file_raises(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(ProfileMapError):
        load_profile_map(missing)


def test_load_profile_map_invalid_json_raises(tmp_path):
    path = tmp_path / "mlx_profiles.json"
    path.write_text("{not valid json")
    with pytest.raises(ProfileMapError):
        load_profile_map(path)


def test_load_profile_map_non_dict_json_raises(tmp_path):
    path = tmp_path / "mlx_profiles.json"
    path.write_text(json.dumps(["EMI_AUTO_2", "EMI_AUTO_4"]))
    with pytest.raises(ProfileMapError):
        load_profile_map(path)
