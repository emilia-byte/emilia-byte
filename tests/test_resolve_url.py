"""
Unit tests for generate_posts.resolve_url() -- the interactive
"keep the current destination URL or paste a new one" prompt, plus its
current_url.json persistence. No browser/Playwright involved.
CURRENT_URL_PATH is monkeypatched to a tmp_path so tests never touch the
real current_url.json.
"""

import json

import generate_posts as gp


def _use_tmp_current_url(monkeypatch, tmp_path):
    monkeypatch.setattr(gp, "CURRENT_URL_PATH", tmp_path / "current_url.json")


def test_cli_url_wins_without_prompting(monkeypatch, tmp_path):
    _use_tmp_current_url(monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("should not prompt")))

    result = gp.resolve_url("https://example.com/explicit")

    assert result == "https://example.com/explicit"


def test_cli_url_is_remembered_for_next_time(monkeypatch, tmp_path):
    _use_tmp_current_url(monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "")

    gp.resolve_url("https://example.com/rotated")

    assert gp.resolve_url(None) == "https://example.com/rotated"


def test_no_cli_url_no_stored_url_prompts_with_default(monkeypatch, tmp_path):
    _use_tmp_current_url(monkeypatch, tmp_path)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": (prompts.append(prompt), "")[1])

    result = gp.resolve_url(None)

    assert result == gp.DEFAULT_URL


def test_enter_keeps_stored_url(monkeypatch, tmp_path):
    path = tmp_path / "current_url.json"
    path.write_text(json.dumps({"url": "https://example.com/stored"}))
    monkeypatch.setattr(gp, "CURRENT_URL_PATH", path)
    monkeypatch.setattr("builtins.input", lambda *a: "")  # just press Enter

    result = gp.resolve_url(None)

    assert result == "https://example.com/stored"


def test_typing_new_url_at_prompt_overrides_and_saves(monkeypatch, tmp_path):
    path = tmp_path / "current_url.json"
    path.write_text(json.dumps({"url": "https://example.com/old"}))
    monkeypatch.setattr(gp, "CURRENT_URL_PATH", path)
    monkeypatch.setattr("builtins.input", lambda *a: "https://example.com/new")

    result = gp.resolve_url(None)

    assert result == "https://example.com/new"
    assert json.loads(path.read_text())["url"] == "https://example.com/new"


def test_malformed_current_url_json_falls_back_to_default(monkeypatch, tmp_path):
    path = tmp_path / "current_url.json"
    path.write_text("{not valid json")
    monkeypatch.setattr(gp, "CURRENT_URL_PATH", path)
    monkeypatch.setattr("builtins.input", lambda *a: "")

    assert gp.resolve_url(None) == gp.DEFAULT_URL
