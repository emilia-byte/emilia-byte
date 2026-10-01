import sys
from pathlib import Path

# Modules under test (post.py, login.py, etc.) live at the repo root, not in
# a package -- put the repo root on sys.path so tests can `import post`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def _isolate_used_content(tmp_path, monkeypatch):
    # generate_posts remembers used templates across runs in used_content.json;
    # keep tests from reading or rewriting the real one in the repo root.
    import generate_posts
    monkeypatch.setattr(generate_posts, "USED_CONTENT_FILE", tmp_path / "used_content.json")
