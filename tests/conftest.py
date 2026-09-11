import sys
from pathlib import Path

# Modules under test (post.py, login.py, etc.) live at the repo root, not in
# a package -- put the repo root on sys.path so tests can `import post`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
