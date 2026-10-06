"""
Tests for running several profiles at once: per-profile output tags and
non-blocking, serialised prompts (console.py), per-account debug screenshots
(boost._debug_screenshot), the batch runner's per-profile wiring, and
run.py's batch-boost menu option. No browser or Multilogin involved.
"""

import asyncio
import builtins
import threading
import time
import types

import batch_common
import boost
import boost_batch
import console
import run


# ── tagged_print ──────────────────────────────────────────────────────────

def test_tagged_print_without_tag_is_plain_print(capsys):
    console.tagged_print("hello")
    assert capsys.readouterr().out == "hello\n"


def test_tagged_print_prefixes_tag_and_keeps_leading_newlines_above_it(capsys):
    async def scenario():
        console.profile_tag.set("[EMI_AUTO_3]")
        console.tagged_print("\nAll posts done.")

    asyncio.run(scenario())
    assert capsys.readouterr().out == "\n[EMI_AUTO_3] All posts done.\n"


def test_concurrent_tasks_and_their_threads_keep_their_own_tags(capsys):
    def worker_thread():
        console.tagged_print("from thread")

    async def profile(name):
        console.profile_tag.set(f"[{name}]")
        await asyncio.sleep(0.01)
        console.tagged_print("from task")
        await asyncio.to_thread(worker_thread)

    async def scenario():
        await asyncio.gather(*(profile(f"P{i}") for i in range(5)))

    asyncio.run(scenario())
    lines = capsys.readouterr().out.splitlines()
    for i in range(5):
        assert f"[P{i}] from task" in lines
        assert f"[P{i}] from thread" in lines


# ── ask ───────────────────────────────────────────────────────────────────

def test_ask_does_not_block_other_profiles(monkeypatch):
    def slow_input(prompt):
        time.sleep(0.5)  # a human taking their time
        return ""

    monkeypatch.setattr(builtins, "input", slow_input)
    ticks = 0

    async def other_profile():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks += 1

    async def scenario():
        await asyncio.gather(console.ask("press Enter"), other_profile())

    asyncio.run(scenario())
    assert ticks == 20  # kept running while the prompt waited


def test_ask_serialises_prompts_and_tags_them(monkeypatch):
    active, max_active, prompts = 0, 0, []
    guard = threading.Lock()

    def fake_input(prompt):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
            prompts.append(prompt)
        time.sleep(0.05)
        with guard:
            active -= 1
        return ""

    monkeypatch.setattr(builtins, "input", fake_input)

    async def profile(name):
        console.profile_tag.set(f"[{name}]")
        await console.ask("  Complete verification, then press Enter...")

    async def scenario():
        await asyncio.gather(*(profile(f"P{i}") for i in range(4)))

    asyncio.run(scenario())
    assert max_active == 1  # never two prompts on screen at once
    assert sorted(prompts) == [f"[P{i}] Complete verification, then press Enter..." for i in range(4)]


# ── boost._debug_screenshot ───────────────────────────────────────────────

class _FakePage:
    def __init__(self, fail=False):
        self.paths, self.fail = [], fail

    async def screenshot(self, path):
        if self.fail:
            raise RuntimeError("target closed")
        self.paths.append(path)


def test_debug_screenshot_is_named_per_account():
    page = _FakePage()
    name = asyncio.run(boost._debug_screenshot(page, "create", "EMI_AUTO_3"))

    assert name == "debug_create_EMI_AUTO_3.png"
    assert page.paths[0].endswith("debug_create_EMI_AUTO_3.png")


def test_debug_screenshot_sanitises_account_and_never_raises():
    name = asyncio.run(boost._debug_screenshot(_FakePage(fail=True), "next", "../weird name"))
    assert name == "debug_next____weird_name.png"


# ── boost_batch.run_profile ───────────────────────────────────────────────

def test_batch_run_profile_passes_account_and_tags_output(monkeypatch, capsys):
    import mlx_context

    started = types.SimpleNamespace(cdp_url="http://127.0.0.1:1", port=1, profile_id="pid")
    client = types.SimpleNamespace(stop_profile=lambda pid: None)
    monkeypatch.setattr(mlx_context, "start_profile_for", lambda name: (client, started))
    monkeypatch.setattr(batch_common, "LAUNCH_JITTER_SECONDS", (0, 0))
    seen = {}

    async def fake_boost(cdp_url, account, publish=False):
        seen.update(cdp_url=cdp_url, account=account, publish=publish)
        boost.print("inside boost")

    monkeypatch.setattr(boost, "boost", fake_boost)
    results = {}
    asyncio.run(boost_batch.run_profile("EMI_AUTO_3", asyncio.Semaphore(1), False, results))

    assert results == {"EMI_AUTO_3": "success"}
    assert seen == {"cdp_url": "http://127.0.0.1:1", "account": "EMI_AUTO_3", "publish": False}
    assert "[EMI_AUTO_3] inside boost" in capsys.readouterr().out


# ── run.py batch boost option ─────────────────────────────────────────────

def _run_batch_boost(monkeypatch, answer):
    calls = []
    monkeypatch.setattr(run, "_pick_batch_profiles", lambda: ["EMI_AUTO_2", "EMI_AUTO_3"])
    monkeypatch.setattr(run, "_pick_workers", lambda n: 2)
    monkeypatch.setattr(builtins, "input", lambda prompt: answer)
    monkeypatch.setattr(run, "run_cmd", lambda args: calls.append(args) or True)
    assert run.batch_boost() is True
    return calls[0]


def test_run_batch_boost_defaults_to_draft(monkeypatch):
    args = _run_batch_boost(monkeypatch, "")
    assert args == ["boost_batch.py", "--profiles", "EMI_AUTO_2,EMI_AUTO_3", "--workers", "2"]


def test_run_batch_boost_publish_only_on_explicit_answer(monkeypatch):
    assert "--publish" not in _run_batch_boost(monkeypatch, "yes")
    assert "--publish" in _run_batch_boost(monkeypatch, "publish")
