"""
Unit tests for the post.py -> boost.py "last published post" handoff:
post.save_last_post() / post.POST_ID_PATTERN (write side) and
boost._load_last_post() (read side, including the staleness cutoff).
No browser/Playwright involved -- LAST_POST_PATH is monkeypatched to a
tmp_path on both modules so tests never touch the real last_post.json.
"""

import json
from datetime import datetime, timedelta, timezone

import boost
import post


# ── post.save_last_post ───────────────────────────────────────────────────

def test_save_last_post_writes_record(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    monkeypatch.setattr(post, "LAST_POST_PATH", path)

    post.save_last_post("EMI_AUTO_2", {"post_id": "123456789012345", "url": "https://x"})

    data = json.loads(path.read_text())
    assert data["EMI_AUTO_2"]["post_id"] == "123456789012345"
    assert data["EMI_AUTO_2"]["url"] == "https://x"
    assert "published_at" in data["EMI_AUTO_2"]


def test_save_last_post_preserves_other_accounts(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    path.write_text(json.dumps({"OTHER_ACCOUNT": {"post_id": "999", "url": None, "published_at": "x"}}))
    monkeypatch.setattr(post, "LAST_POST_PATH", path)

    post.save_last_post("EMI_AUTO_2", {"post_id": "123", "url": None})

    data = json.loads(path.read_text())
    assert "OTHER_ACCOUNT" in data
    assert data["EMI_AUTO_2"]["post_id"] == "123"


def test_save_last_post_handles_no_capture(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    monkeypatch.setattr(post, "LAST_POST_PATH", path)

    post.save_last_post("EMI_AUTO_2", None)

    data = json.loads(path.read_text())
    assert data["EMI_AUTO_2"]["post_id"] is None
    assert data["EMI_AUTO_2"]["url"] is None


# ── post.POST_ID_PATTERN ──────────────────────────────────────────────────

def test_post_id_pattern_extracts_numeric_story_fbid():
    match = post.POST_ID_PATTERN.search(
        "https://www.facebook.com/1234/posts/?story_fbid=123456789012345&id=1234"
    )
    assert match.group(1) == "123456789012345"


def test_post_id_pattern_extracts_opaque_pfbid_token():
    match = post.POST_ID_PATTERN.search("https://www.facebook.com/MyPage/posts/pfbid02abcXYZ")
    assert match.group(1) == "pfbid02abcXYZ"


def test_post_id_pattern_no_match_on_unrelated_url():
    assert post.POST_ID_PATTERN.search("https://www.facebook.com/MyPage/photos") is None


# ── boost._load_last_post ─────────────────────────────────────────────────

def test_load_last_post_returns_fresh_record(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    fresh = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps({"EMI_AUTO_2": {"post_id": "123", "url": None, "published_at": fresh}}))
    monkeypatch.setattr(boost, "LAST_POST_PATH", path)

    record = boost._load_last_post("EMI_AUTO_2")
    assert record["post_id"] == "123"


def test_load_last_post_ignores_stale_record(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps({"EMI_AUTO_2": {"post_id": "123", "url": None, "published_at": stale}}))
    monkeypatch.setattr(boost, "LAST_POST_PATH", path)

    assert boost._load_last_post("EMI_AUTO_2") is None


def test_load_last_post_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(boost, "LAST_POST_PATH", tmp_path / "does_not_exist.json")
    assert boost._load_last_post("EMI_AUTO_2") is None


def test_load_last_post_missing_account_returns_none(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    path.write_text(json.dumps({
        "SOME_OTHER_ACCOUNT": {"post_id": "1", "published_at": datetime.now(timezone.utc).isoformat()},
    }))
    monkeypatch.setattr(boost, "LAST_POST_PATH", path)

    assert boost._load_last_post("EMI_AUTO_2") is None


def test_load_last_post_malformed_json_returns_none(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(boost, "LAST_POST_PATH", path)

    assert boost._load_last_post("EMI_AUTO_2") is None
