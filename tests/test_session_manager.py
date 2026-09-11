"""
Unit tests for session_manager.py's cookie-file save/load/delete helpers.
Filesystem only, no browser involved. SESSIONS_DIR is monkeypatched to a
tmp_path so tests never touch the real ./sessions/ directory.
"""

import session_manager


def _use_tmp_sessions_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(session_manager, "SESSIONS_DIR", str(tmp_path))


def test_save_and_load_roundtrip(monkeypatch, tmp_path):
    _use_tmp_sessions_dir(monkeypatch, tmp_path)
    cookies = [{"name": "c_user", "value": "12345"}]

    session_manager.save_session("EMI_AUTO_2", cookies)
    loaded = session_manager.load_session("EMI_AUTO_2")

    assert loaded == cookies


def test_load_session_missing_returns_none(monkeypatch, tmp_path):
    _use_tmp_sessions_dir(monkeypatch, tmp_path)
    assert session_manager.load_session("NO_SUCH_ACCOUNT") is None


def test_delete_session_removes_file(monkeypatch, tmp_path):
    _use_tmp_sessions_dir(monkeypatch, tmp_path)
    session_manager.save_session("EMI_AUTO_2", [{"name": "x", "value": "y"}])

    session_manager.delete_session("EMI_AUTO_2")

    assert session_manager.load_session("EMI_AUTO_2") is None


def test_delete_session_missing_file_is_a_noop(monkeypatch, tmp_path):
    _use_tmp_sessions_dir(monkeypatch, tmp_path)
    session_manager.delete_session("NEVER_SAVED")  # must not raise


def test_session_path_sanitizes_account_name(monkeypatch, tmp_path):
    _use_tmp_sessions_dir(monkeypatch, tmp_path)
    path = session_manager.session_path("weird/name with spaces")
    assert path == str(tmp_path / "weird_name_with_spaces.json")
