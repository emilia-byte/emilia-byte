"""
Tests for batch_common.py -- what lets --workers 50 actually mean 50 --
and for MultiloginClient's 429 backoff. No browser, no network.
"""

import asyncio
import threading
import time

import batch_common
import multilogin_client
from multilogin_client import MultiloginClient


def test_thread_pool_runs_50_profiles_at_once():
    """Without use_thread_pool(), asyncio.to_thread caps at cpu_count+4
    (20 on a 16-core box), so 50 'profiles' would run in ~3 waves."""
    peak, active = 0, 0
    lock = threading.Lock()

    def profile_thread():
        nonlocal peak, active
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.3)
        with lock:
            active -= 1

    async def scenario():
        batch_common.use_thread_pool(50)
        await asyncio.gather(*(asyncio.to_thread(profile_thread) for _ in range(50)))

    asyncio.run(scenario())
    assert peak == 50


def test_launch_pacer_spaces_launches():
    async def scenario():
        pacer = batch_common.LaunchPacer(interval=0.1, jitter=(0, 0))
        stamps = []

        async def launch():
            await pacer.wait()
            stamps.append(time.monotonic())

        await asyncio.gather(*(launch() for _ in range(5)))
        return sorted(stamps)

    stamps = asyncio.run(scenario())
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.09 for g in gaps), gaps


def test_launch_pacer_default_keeps_under_multilogin_pro10_rpm():
    # One start request per launch; leave half of Pro 10's 50 RPM for stops/teammates.
    assert 60 / batch_common.LAUNCH_INTERVAL_SECONDS <= 25


# ── 429 backoff ───────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.text = str(status)

    def json(self):
        return {"data": {"token": "tok"}}


def _client_with(monkeypatch, responses):
    sleeps, calls = [], []
    monkeypatch.setattr(multilogin_client.requests, "post", lambda *a, **k: _Resp(200))
    monkeypatch.setattr(multilogin_client.requests, "request",
                        lambda *a, **k: calls.append(a) or responses.pop(0))
    monkeypatch.setattr(multilogin_client.time, "sleep", sleeps.append)
    return MultiloginClient("a@b.c", "pw"), sleeps, calls


def test_429_waits_retry_after_then_succeeds(monkeypatch):
    client, sleeps, calls = _client_with(monkeypatch, [_Resp(429, {"Retry-After": "7"}), _Resp(200)])

    resp = client._request("GET", "https://launcher.mlx.yt:45001/x", timeout=1)

    assert resp.status_code == 200
    assert sleeps == [7.0]
    assert len(calls) == 2


def test_429_exponential_backoff_without_retry_after_then_gives_up(monkeypatch):
    client, sleeps, calls = _client_with(monkeypatch, [_Resp(429) for _ in range(4)])

    resp = client._request("GET", "https://launcher.mlx.yt:45001/x", timeout=1)

    assert resp.status_code == 429
    assert sleeps == [10, 20, 40]
    assert len(calls) == multilogin_client.RATE_LIMIT_RETRIES + 1
