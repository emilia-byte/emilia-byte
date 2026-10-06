"""
build_release.py

Build the two hand-off bundles:

  dist/phase1.zip  Posting: generate posts and publish them on many profiles
                   at once (run.py / post_batch.py, up to 50 workers).
  dist/phase2.zip  Ads Manager: boost the posts phase 1 published
                   (boost.py / boost_batch.py).

Both unzip into the SAME folder: phase 2 reads last_post.json, which phase 1
writes next to itself, to know which post to boost. Shared modules are
byte-identical in both zips, so unpacking one over the other is safe.

Files are taken from the last commit (git HEAD), never the working tree, so
uncommitted edits and untracked files such as .env, spreadsheets and session
cookies can't end up in a bundle. A final check refuses to build if any
secret-looking path got onto the lists anyway: phase1.zip/phase2.zip once
shipped with a .env inside.

    python build_release.py            # writes dist/phase1.zip, dist/phase2.zip
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"

SHARED = [
    "mlx_context.py", "multilogin_client.py", "console.py", "batch_common.py",
    "sync_profiles.py", "sync_profiles.bat", "setup.bat", "requirements.txt",
    "mlx_profiles.json",
]

PHASES = {
    "phase1": SHARED + [
        "run.py", "run.bat",
        "post.py", "post_batch.py", "post_batch.bat",
        "generate_posts.py", "fb_dom.py", "state_file.py",
        "login.py", "manual_session.py",
        "page_urls.json",
    ],
    "phase2": SHARED + [
        "boost.py", "boost.bat",
        "boost_batch.py", "boost_batch.bat",
    ],
}

# Never ship these, whatever the lists above say.
FORBIDDEN = re.compile(
    r"(^|/)(\.env|mlx_env\.ps1|sessions/)|\.(xlsx|png|zip)$|"
    r"(^|/)(last_post|current_url|used_content)\.json$",
    re.I,
)

README = {
    "phase1": """PHASE 1 - POSTING
=================

1. Unzip into a folder (phase 2 goes into the SAME folder later).
2. Double-click setup.bat once.
3. Double-click run.bat:
     1-3  one profile: generate and/or publish posts
     4    batch: unique posts per profile, published on many profiles at once

How many at once: run.bat option 4 asks "How many to run simultaneously?".
Up to 50 works, but each profile is a full browser (about 0.5-1 GB RAM), and
your Multilogin plan limits how many profiles can run at the same time.
Start with 3, then scale up. Launches are spaced 2.5s apart on purpose
(Multilogin's request limit is shared by your whole workspace).

Command line: post_batch.bat --workers 50 --profiles EMI_AUTO_2,EMI_AUTO_3

Credentials are asked for on first run and saved to .env in this folder.
Never zip, email or commit that .env file.
""",
    "phase2": """PHASE 2 - ADS MANAGER (BOOST)
=============================

Unzip into the SAME folder as phase 1. Phase 2 boosts the post phase 1
just published, using last_post.json from that folder; in a separate
folder it can't tell which post is the right one and falls back to the
newest one with a warning.

  boost.bat         one profile, interactive
  boost_batch.bat   many profiles: boost_batch.bat --workers 3 --profiles A,B,C
  run.bat option 5  same as boost_batch, from the phase 1 menu

Campaigns are saved as DRAFTS by default. Publishing spends real ad budget:
it needs --publish (or answering "publish" in run.bat) AND typing "publish"
again to confirm. Publishing can trigger SMS verification via TextVerified;
its credentials are asked for up front.

Credentials are saved to .env in this folder. Never zip, email or commit it.
""",
}


def _git_show(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=ROOT, check=True, capture_output=True
    ).stdout


def build(phase: str, files: list[str]) -> Path:
    for f in files:
        if FORBIDDEN.search(f):
            sys.exit(f"Refusing to build {phase}: {f} must never be shipped.")
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{phase}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            try:
                zf.writestr(f, _git_show(f))
            except subprocess.CalledProcessError:
                sys.exit(f"Refusing to build {phase}: {f} isn't committed (git HEAD).")
        zf.writestr(f"README_{phase.upper()}.txt", README[phase])
    return out


def main() -> None:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    for phase, files in PHASES.items():
        out = build(phase, files)
        print(f"{out.relative_to(ROOT)}  ({len(files) + 1} files, from commit {commit})")


if __name__ == "__main__":
    main()
