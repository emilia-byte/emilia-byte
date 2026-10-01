"""
state_file.py

Concurrency-safe updates for the small JSON state files shared between runs
(last_post.json, page_urls.json).

The batch runners post to several profiles at once -- threads in one
process, and separate post.py runs can overlap too. A plain
read-modify-write lets two writers each read the old file and the second
silently drop the first one's entry, and a reader can catch a half-written
file. update_json() serialises writers with a lock file (works across
threads and processes, no extra dependency) and swaps the result in with an
atomic replace, so readers always see either the old or the new complete
file and never need the lock themselves.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Lock files older than this belong to a crashed writer, not a live one --
# an update holds the lock for milliseconds.
STALE_LOCK_SECONDS = 30
LOCK_TIMEOUT_SECONDS = 15

_thread_lock = threading.Lock()


def _acquire(lock_path: Path) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > STALE_LOCK_SECONDS:
                    log.warning("Removing stale lock %s (%.0fs old)", lock_path, age)
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue  # released between our open() and stat() -- retry now
            if time.monotonic() > deadline:
                raise TimeoutError(f"Timed out waiting for {lock_path}")
            time.sleep(0.05)


def _replace(tmp: Path, path: Path) -> None:
    # On Windows, os.replace fails while a reader briefly has the target
    # open -- retry rather than lose the write.
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.05)


def read_json(path: Path) -> dict:
    """Read a state file; a missing or unreadable one counts as empty."""
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("Could not read %s, treating as empty: %s", path, exc)
        return {}


def update_json(path: Path, mutate: Callable[[dict], None]) -> dict:
    """Lock `path`, load it, apply `mutate(data)` in place, write it back
    atomically, and return the written data."""
    path = Path(path)
    lock_path = path.with_name(path.name + ".lock")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _thread_lock:
        _acquire(lock_path)
        try:
            data = read_json(path)
            mutate(data)
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            _replace(tmp, path)
            return data
        finally:
            tmp.unlink(missing_ok=True)
            lock_path.unlink(missing_ok=True)
