"""
Tests for state_file.update_json() -- the locked, atomic read-modify-write
used for last_post.json and page_urls.json, which batch runs update from
several profiles at once. No browser/Playwright involved.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import post
import state_file

ROOT = Path(__file__).resolve().parent.parent


def test_update_json_creates_file_and_returns_data(tmp_path):
    path = tmp_path / "state.json"
    result = state_file.update_json(path, lambda d: d.__setitem__("a", 1))

    assert result == {"a": 1}
    assert json.loads(path.read_text()) == {"a": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]  # no lock/tmp left


def test_concurrent_thread_writers_lose_no_entries(tmp_path):
    path = tmp_path / "state.json"
    names = [f"PROFILE_{i}" for i in range(40)]
    barrier = threading.Barrier(len(names))

    def writer(name):
        barrier.wait()  # maximise overlap
        state_file.update_json(path, lambda d: d.__setitem__(name, name.lower()))

    threads = [threading.Thread(target=writer, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert json.loads(path.read_text()) == {n: n.lower() for n in names}


def test_concurrent_process_writers_lose_no_entries(tmp_path):
    path = tmp_path / "state.json"
    code = (
        "import sys, state_file;"
        "[state_file.update_json(sys.argv[1], lambda d, k=f'{sys.argv[2]}_{i}': d.__setitem__(k, i))"
        " for i in range(15)]"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(path), f"P{n}"], cwd=ROOT)
        for n in range(4)
    ]
    assert all(p.wait(timeout=60) == 0 for p in procs)

    data = json.loads(path.read_text())
    assert len(data) == 4 * 15


def test_stale_lock_from_crashed_writer_is_recovered(tmp_path):
    path = tmp_path / "state.json"
    lock = tmp_path / "state.json.lock"
    lock.write_text("12345")
    old = time.time() - state_file.STALE_LOCK_SECONDS - 5
    os.utime(lock, (old, old))

    state_file.update_json(path, lambda d: d.__setitem__("a", 1))

    assert json.loads(path.read_text()) == {"a": 1}
    assert not lock.exists()


def test_malformed_existing_file_is_replaced_not_fatal(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")

    state_file.update_json(path, lambda d: d.__setitem__("a", 1))

    assert json.loads(path.read_text()) == {"a": 1}


# ── post.py call sites ────────────────────────────────────────────────────

def test_save_last_post_concurrent_profiles_all_recorded(tmp_path, monkeypatch):
    path = tmp_path / "last_post.json"
    monkeypatch.setattr(post, "LAST_POST_PATH", path)
    names = [f"EMI_AUTO_{i}" for i in range(20)]

    threads = [
        threading.Thread(target=post.save_last_post, args=(n, {"post_id": str(i), "url": None}))
        for i, n in enumerate(names)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = json.loads(path.read_text())
    assert set(data) == set(names)
    assert data["EMI_AUTO_7"]["post_id"] == "7"


def test_save_page_url_concurrent_profiles_all_recorded(tmp_path, monkeypatch):
    path = tmp_path / "page_urls.json"
    monkeypatch.setattr(post, "PAGE_URLS_PATH", path)
    names = [f"EMI_AUTO_{i}" for i in range(20)]

    threads = [
        threading.Thread(target=post._save_page_url,
                         args=(n, f"https://www.facebook.com/1000000000{i:05d}"))
        for i, n in enumerate(names)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = json.loads(path.read_text())
    assert set(data) == set(names)
    assert data["EMI_AUTO_3"] == "https://www.facebook.com/100000000000003"
