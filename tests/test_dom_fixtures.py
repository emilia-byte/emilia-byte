"""
DOM-fixture tests for the post<->boost post-ID handoff's browser-facing
halves: post.capture_published_post() and boost.select_target_post().

These drive a real (headless) Chromium against local static HTML files
under tests/fixtures/ that stand in for Facebook's feed article and the
Ads Manager "Select post" table -- not against facebook.com. That means
they verify the selector/regex/matching logic actually behaves against a
DOM shaped the way this code assumes, with no Facebook account and no ban
risk. It does NOT verify that Facebook's real markup still matches that
shape -- these fixtures are frozen assumptions, and Facebook is exactly
the kind of thing that drifts (see this repo's commit history). Treat a
pass here as "the logic is correct given the assumption," not "this will
work against live Facebook."
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

import boost
import post

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _uri(name: str) -> str:
    return (FIXTURES_DIR / name).as_uri()


@pytest.fixture(autouse=True)
def _fast_pauses(monkeypatch):
    """capture_published_post() calls human_pause() for realism against a
    live site; that's pure wasted wall-clock time against a static local
    fixture, so skip it here."""
    monkeypatch.setattr(post, "human_pause", lambda *a, **k: None)


# ── post.capture_published_post ───────────────────────────────────────────

def test_capture_published_post_numeric_story_fbid():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(_uri("feed_numeric.html"))
        result = post.capture_published_post(page)
        browser.close()

    assert result is not None
    assert result["post_id"] == "123456789012345"


def test_capture_published_post_opaque_pfbid_token():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(_uri("feed_pfbid.html"))
        result = post.capture_published_post(page)
        browser.close()

    assert result is not None
    assert result["post_id"] == "pfbid02abcXYZ123"


def test_capture_published_post_no_permalink_returns_none():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(_uri("feed_no_match.html"))
        result = post.capture_published_post(page)
        browser.close()

    assert result is None


# ── boost.select_target_post ──────────────────────────────────────────────

def _run_select_target_post(account: str) -> tuple[str | None, str]:
    """Launch Chromium against the fixture table, run select_target_post(),
    and return (clicked_row_text, captured_stdout)."""

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(_uri("select_post_table.html"))
            result = await boost.select_target_post(page, account)
            await browser.close()
            return result

    return asyncio.run(scenario())


def _write_last_post(monkeypatch, tmp_path, account: str, post_id: str | None, age_minutes: float = 0):
    path = tmp_path / "last_post.json"
    published_at = datetime.now(timezone.utc)
    if age_minutes:
        from datetime import timedelta
        published_at -= timedelta(minutes=age_minutes)
    path.write_text(json.dumps({
        account: {"post_id": post_id, "url": None, "published_at": published_at.isoformat()},
    }))
    monkeypatch.setattr(boost, "LAST_POST_PATH", path)


def test_select_target_post_falls_back_to_newest_when_no_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(boost, "LAST_POST_PATH", tmp_path / "does_not_exist.json")

    clicked = _run_select_target_post("EMI_AUTO_2")

    assert clicked == "111111111111111"  # first/newest row
    assert "WARNING" in capsys.readouterr().out


def test_select_target_post_matches_recorded_numeric_id_exactly(tmp_path, monkeypatch, capsys):
    _write_last_post(monkeypatch, tmp_path, "EMI_AUTO_2", "222222222222222")

    clicked = _run_select_target_post("EMI_AUTO_2")

    assert clicked == "222222222222222"  # NOT the newest row -- the matched one
    out = capsys.readouterr().out
    assert "Matched recorded post ID 222222222222222 exactly" in out
    assert "WARNING" not in out


def test_select_target_post_falls_back_when_recorded_id_not_in_table(tmp_path, monkeypatch, capsys):
    _write_last_post(monkeypatch, tmp_path, "EMI_AUTO_2", "999999999999999")

    clicked = _run_select_target_post("EMI_AUTO_2")

    assert clicked == "111111111111111"  # fallback to newest row
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "999999999999999" in out


def test_select_target_post_falls_back_on_non_numeric_id(tmp_path, monkeypatch, capsys):
    _write_last_post(monkeypatch, tmp_path, "EMI_AUTO_2", "pfbid02abcXYZ123")

    clicked = _run_select_target_post("EMI_AUTO_2")

    assert clicked == "111111111111111"  # fallback to newest row
    out = capsys.readouterr().out
    assert "NOTE" in out
    assert "isn't the numeric scheme" in out


def test_select_target_post_falls_back_on_stale_record(tmp_path, monkeypatch, capsys):
    _write_last_post(monkeypatch, tmp_path, "EMI_AUTO_2", "222222222222222", age_minutes=45)

    clicked = _run_select_target_post("EMI_AUTO_2")

    assert clicked == "111111111111111"  # fallback -- record ignored as stale
    out = capsys.readouterr().out
    assert "stale" in out
    assert "WARNING" in out
