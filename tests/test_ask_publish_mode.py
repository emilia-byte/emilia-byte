"""
Unit test for boost.ask_publish_mode() -- confirms the default (anything
that isn't an explicit "publish"/"p"/"yes"/"y") is "leave as draft", since
publishing spends real ad budget. No browser/Playwright involved.
"""

import pytest

import boost


@pytest.mark.parametrize("typed", ["", "draft", "d", "no", "n", "garbage"])
def test_defaults_to_draft(monkeypatch, typed):
    monkeypatch.setattr("builtins.input", lambda *a: typed)
    assert boost.ask_publish_mode() is False


@pytest.mark.parametrize("typed", ["publish", "Publish", "p", "P", "yes", "y", "YES"])
def test_explicit_publish_opts_in(monkeypatch, typed):
    monkeypatch.setattr("builtins.input", lambda *a: typed)
    assert boost.ask_publish_mode() is True
