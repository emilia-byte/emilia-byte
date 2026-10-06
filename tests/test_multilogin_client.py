"""
Tests for MultiloginClient's auth and profile search against a fake
`requests`, plus mlx_context's shared client and generate_posts's
HuggingFace timeout. No network, no Multilogin account.

What's pinned here comes from Multilogin's docs: sign-in tokens last 30
minutes, profile search is POST /profile/search returning
data.profiles[].id, and the workspace shares one requests-per-minute limit.
"""

import threading
import types

import pytest

import generate_posts
import mlx_context
import multilogin_client
from multilogin_client import MultiloginClient, MultiloginError


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body if body is not None else {}
        self.text = text or str(self._body)

    def json(self):
        return self._body


class FakeAPI:
    """Stands in for requests.post (sign-in) and requests.request (everything else)."""

    def __init__(self):
        self.signins = 0
        self.calls = []          # (method, url, kwargs)
        self.responses = []      # queued for requests.request
        self.lock = threading.Lock()

    def post(self, url, json=None, timeout=None):
        assert url.endswith("/user/signin")
        with self.lock:
            self.signins += 1
            return _Resp(body={"data": {"token": f"tok{self.signins}"}})

    def request(self, method, url, headers=None, **kwargs):
        with self.lock:
            self.calls.append((method, url, dict(kwargs, headers=headers)))
            return self.responses.pop(0) if self.responses else _Resp()


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(multilogin_client.requests, "post", fake.post)
    monkeypatch.setattr(multilogin_client.requests, "request", fake.request)
    return fake


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(multilogin_client.time, "monotonic", lambda: now["t"])
    return now


# ── token renewal ─────────────────────────────────────────────────────────

def test_first_request_signs_in_lazily(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    client.stop_profile("pid")

    assert api.signins == 1
    assert api.calls[0][2]["headers"] == {"Authorization": "Bearer tok1"}


def test_token_reused_within_ttl_and_renewed_after(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    client.stop_profile("p1")
    clock["t"] += 20 * 60
    client.stop_profile("p2")
    assert api.signins == 1

    clock["t"] += 6 * 60  # 26 min since sign-in, past the 25-min renewal point
    client.stop_profile("p3")
    assert api.signins == 2
    assert api.calls[-1][2]["headers"] == {"Authorization": "Bearer tok2"}


def test_401_signs_in_again_and_retries_once(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    api.responses = [_Resp(401, text="token expired"), _Resp(200)]

    client.stop_profile("pid")

    assert api.signins == 2
    assert [c[2]["headers"]["Authorization"] for c in api.calls] == ["Bearer tok1", "Bearer tok2"]


def test_401_twice_is_not_retried_forever(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    api.responses = [_Resp(401), _Resp(401)]

    resp = client._request("GET", "https://launcher.mlx.yt:45001/x", timeout=1)

    assert resp.status_code == 401
    assert len(api.calls) == 2


def test_stop_profile_after_long_run_still_works(api, clock, monkeypatch, tmp_path):
    """The case that motivated this: a run outlasting the 30-minute token."""
    monkeypatch.setattr(multilogin_client, "_wait_for_cdp_ready", lambda port: None)
    monkeypatch.setattr(multilogin_client, "_PORT_CACHE_DIR", tmp_path)
    client = MultiloginClient("a@b.c", "pw")
    api.responses = [_Resp(200, body={"data": {"port": 5555}})]
    client.start_profile("folder", "pid")

    clock["t"] += 60 * 60
    client.stop_profile("pid")

    assert api.signins == 2
    assert api.calls[-1][2]["headers"] == {"Authorization": "Bearer tok2"}


# ── documented profile search ─────────────────────────────────────────────

def test_search_uses_documented_endpoint_and_reads_id(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    api.responses = [_Resp(body={"data": {"profiles": [
        {"id": "uuid-other", "name": "EMI_AUTO_30", "folder_id": "folder"},
        {"id": "uuid-3", "name": "EMI_AUTO_3", "folder_id": "folder"},
    ]}})]

    assert client.search_profile_by_name("EMI_AUTO_3", "folder") == "uuid-3"
    method, url, kwargs = api.calls[0]
    assert (method, url) == ("POST", "https://api.multilogin.com/profile/search")
    assert kwargs["json"] == {"search_text": "EMI_AUTO_3", "is_removed": False,
                              "limit": 100, "offset": 0, "storage_type": "all"}


def test_search_pages_by_offset(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    page1 = [{"id": f"u{i}", "name": f"X{i}"} for i in range(100)]
    page2 = [{"id": "target", "name": "WANTED"}]
    api.responses = [_Resp(body={"data": {"profiles": page1}}),
                     _Resp(body={"data": {"profiles": page2}})]

    assert client.search_profile_by_name("WANTED", "folder") == "target"
    assert [c[2]["json"]["offset"] for c in api.calls] == [0, 100]


def test_search_ignores_same_name_in_another_folder(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    api.responses = [_Resp(body={"data": {"profiles": [
        {"id": "wrong", "name": "EMI_AUTO_3", "folder_id": "other-folder"},
    ]}})]

    assert client.search_profile_by_name("EMI_AUTO_3", "folder") is None


def test_search_error_and_bad_shape_raise(api, clock):
    client = MultiloginClient("a@b.c", "pw")
    api.responses = [_Resp(500, text="boom")]
    with pytest.raises(MultiloginError):
        client.search_profile_by_name("X", "folder")

    api.responses = [_Resp(body={"unexpected": True})]
    with pytest.raises(MultiloginError):
        client.search_profile_by_name("X", "folder")


# ── mlx_context shared client ─────────────────────────────────────────────

def test_mlx_context_shares_one_signed_in_client_across_threads(api, monkeypatch):
    monkeypatch.setattr(mlx_context, "_shared_client", None)
    monkeypatch.setenv("MLX_EMAIL", "a@b.c")
    monkeypatch.setenv("MLX_PASSWORD", "pw")
    got = []

    threads = [threading.Thread(target=lambda: got.append(mlx_context._client())) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert api.signins == 1
    assert len({id(c) for c in got}) == 1


# ── HuggingFace timeout ───────────────────────────────────────────────────

def test_generate_image_sets_timeout_and_survives_timeouts(monkeypatch, tmp_path):
    import huggingface_hub
    from huggingface_hub import InferenceTimeoutError

    created = []

    class FakeClient:
        def __init__(self, token=None, timeout=None):
            created.append(timeout)

        def text_to_image(self, prompt, model=None):
            raise InferenceTimeoutError("took too long")

    monkeypatch.setattr(huggingface_hub, "InferenceClient", FakeClient)
    monkeypatch.setattr(generate_posts, "_hf_token", lambda: "hf_x")
    monkeypatch.setattr(generate_posts, "IMAGES_DIR", tmp_path)

    assert generate_posts.generate_image("a lake at dawn", 0) is None
    assert created == [generate_posts.HF_TIMEOUT_SECONDS] * 3  # 3 bounded attempts, no hang
