"""
Redirected output (a pipe, or a batch run logged to a file) must not crash
on the menus' box-drawing/dash characters. On Windows, Python encodes
redirected stdout as cp1252 unless console.py switches it to UTF-8.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_piped(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    env["PYTHONLEGACYWINDOWSSTDIO"] = ""
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, input="",
                          capture_output=True, env=env, timeout=60)


def test_run_menu_survives_redirected_output():
    r = _run_piped("import run; run.pick_mode()")

    out = r.stdout.decode("utf-8")
    assert "UnicodeEncodeError" not in r.stderr.decode("utf-8", "replace")
    assert "── What do you want to do?" in out
    assert "4. Batch" in out


def test_tagged_batch_output_survives_redirected_output():
    r = _run_piped("import console; console.tagged_print('Generating — 3 posts ──')")

    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("utf-8").strip() == "Generating — 3 posts ──"
