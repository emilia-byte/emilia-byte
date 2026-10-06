"""
batch_common.py

Shared plumbing for the batch runners (post_batch.py, boost_batch.py,
run_batch.py) so --workers really means "this many profiles at once", up to
the 50 we plan to run.

- use_thread_pool(): asyncio.to_thread() runs on the loop's default
  executor, which Python caps at min(32, cpu_count + 4) threads -- 20 on a
  16-core machine. Posting runs one profile per thread, so without this a
  50-worker batch silently runs ~20 at a time.
- LaunchPacer: spaces profile launches out. Every launch costs a Multilogin
  start request, and the workspace shares a 50-100 requests/minute limit
  (Pro 10 = 50); 50 profiles launched inside the old 5-20s jitter window
  would blow straight through it. The random jitter on top keeps launches
  from looking machine-regular.
"""

from __future__ import annotations

import asyncio
import random
import time
from concurrent.futures import ThreadPoolExecutor

# 2.5s apart = at most 24 launches/minute: room left in a 50 RPM budget for
# the matching stop requests and for teammates sharing the workspace.
LAUNCH_INTERVAL_SECONDS = 2.5
LAUNCH_JITTER_SECONDS = (0.5, 3.0)


def use_thread_pool(workers: int) -> ThreadPoolExecutor:
    """Give the running loop a default executor big enough for `workers`
    profiles plus a few short-lived helper calls (start/stop requests,
    verification polling). Call from inside main_async()."""
    executor = ThreadPoolExecutor(max_workers=workers + 8, thread_name_prefix="profile")
    asyncio.get_running_loop().set_default_executor(executor)
    return executor


class LaunchPacer:
    """Await wait() before each profile launch; launches are released at
    least `interval` seconds apart, in arrival order."""

    def __init__(self, interval: float | None = None, jitter: tuple[float, float] | None = None):
        self._interval = LAUNCH_INTERVAL_SECONDS if interval is None else interval
        self._jitter = LAUNCH_JITTER_SECONDS if jitter is None else jitter
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def wait(self) -> float:
        """Returns how long this caller waited, for logging."""
        started = time.monotonic()
        async with self._lock:
            delay = max(0.0, self._next_slot - time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            self._next_slot = time.monotonic() + self._interval
        await asyncio.sleep(random.uniform(*self._jitter))
        return time.monotonic() - started
