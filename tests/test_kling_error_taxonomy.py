"""T-B2 可靠性步骤 3：KlingError 错误码分类 + check_credentials + 退避。

全部走 mock HTTP，不触网。验收要求：1102 不可重试、1302 可重试各有断言。
"""

import contextlib
import os
import tempfile
import time
from unittest.mock import patch

import pytest
import requests as real_requests

import src.models.kling as kling_mod
from src.models.kling import KlingError, KlingModel, classify_kling_error


# ---------------------------------------------------------------------------
# 分类表单元
# ---------------------------------------------------------------------------

class TestClassificationTable:

    @pytest.mark.parametrize("code,category,retryable", [
        (1000, "auth", False),
        (1004, "auth", False),
        # 验收点①：1102 资源包耗尽 —— 账户类，一律不可重试
        (1100, "account", False),
        (1101, "account", False),
        (1102, "account", False),
        (1103, "account", False),
        (1200, "params", False),
        (1300, "policy", False),
        # 验收点②：1302 限频 —— 可退避重试
        (1302, "rate_limit", True),
        (1303, "rate_limit", True),
        (5000, "server", True),
        (5001, "server", True),
    ])
    def test_official_codes(self, code, category, retryable):
        got_category, got_retryable = classify_kling_error(code=code)
        assert got_category == category
        assert got_retryable is retryable

    def test_http_fallback(self):
        assert classify_kling_error(status_code=429)[1] is True
        assert classify_kling_error(status_code=503)[1] is True
        assert classify_kling_error(status_code=400)[1] is False
        assert classify_kling_error()[1] is False

    def test_body_code_wins_over_http_status(self):
        # 官方码与 HTTP 状态同时存在时，官方码语义优先（1102 不是"限流"）
        assert classify_kling_error(code=1102, status_code=429) == ("account", False)


# ---------------------------------------------------------------------------
# mock HTTP 基建
# ---------------------------------------------------------------------------

class FakeHTTPResponse:
    def __init__(self, status_code=200, payload=None, text="", content=b"fake-video-bytes"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or str(self._payload)
        self.content = content
        self.headers = {}

    def json(self):
        if self._payload == {} and self.text and not self.text.lstrip().startswith("{"):
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise real_requests.HTTPError(f"HTTP {self.status_code}")


def _ok_submit(task_id="task-1"):
    return FakeHTTPResponse(payload={"code": 0, "data": {"id": task_id}})


def _ok_poll_done(url="https://cdn.example.com/v.mp4"):
    return FakeHTTPResponse(payload={
        "code": 0,
        "data": [{"status": "succeeded", "outputs": [{"type": "video", "url": url}]}],
    })


@contextlib.contextmanager
def tempfile_out():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "out.mp4")


@pytest.fixture
def kling(monkeypatch):
    """无网络 KlingModel：requests 全 mock、sleep 归零、调用可观测。"""
    model = KlingModel({"api_key": "test-key"})
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(kling_mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("requests.post not installed")))
    monkeypatch.setattr(kling_mod.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("requests.get not installed")))
    calls = {"post": [], "get": []}

    def install(post_responses=None, get_responses=None, post_exc=None, get_exc=None):
        post_seq = list(post_responses or [])
        get_seq = list(get_responses or [])

        def fake_post(url, headers=None, json=None, timeout=None):
            calls["post"].append({"url": url, "json": json})
            if post_exc is not None:
                raise post_exc
            return post_seq.pop(0)

        def fake_get(url, headers=None, timeout=None, params=None):
            calls["get"].append({"url": url})
            if get_exc is not None:
                raise get_exc
            if "/tasks" in url:
                if get_seq:
                    item = get_seq.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                return _ok_poll_done()
            # 视频下载等其它 GET
            return FakeHTTPResponse()

        monkeypatch.setattr(kling_mod.requests, "post", fake_post)
        monkeypatch.setattr(kling_mod.requests, "get", fake_get)
        return calls

    model._install = install  # type: ignore[attr-defined]
    model._sleeps = sleeps  # type: ignore[attr-defined]
    return model


# ---------------------------------------------------------------------------
# 提交阶段分类
# ---------------------------------------------------------------------------

class TestSubmitClassification:

    def test_submit_code_1102_not_retryable(self, kling):
        """验收点①：1102 资源包耗尽 → 不重试，直接抛 account/不可重试。"""
        calls = kling._install(post_responses=[
            FakeHTTPResponse(payload={"code": 1102, "message": "resource package exhausted", "request_id": "req-1102"}),
        ])
        with pytest.raises(KlingError) as exc_info:
            kling.generate("p", "/tmp/out.mp4")
        err = exc_info.value
        assert err.code == 1102
        assert err.retryable is False
        assert err.category == "account"
        assert err.request_id == "req-1102"
        assert len(calls["post"]) == 1  # 只打了一次，没有重试

    def test_submit_code_1302_retries_with_backoff(self, kling):
        """验收点②：1302 限频 → 指数退避重试后成功（共 3 次提交）。"""
        calls = kling._install(post_responses=[
            FakeHTTPResponse(payload={"code": 1302, "message": "rate limit"}),
            FakeHTTPResponse(payload={"code": 1302, "message": "rate limit"}),
            _ok_submit("task-1302"),
        ])
        with tempfile_out() as out:
            path, _ = kling.generate("p", out)
        assert path == out
        assert len(calls["post"]) == 3
        assert len(kling._sleeps) >= 2  # 两次退避真的发生了

    def test_submit_5xxx_exhausts_attempts_then_raises(self, kling):
        calls = kling._install(post_responses=[
            FakeHTTPResponse(payload={"code": 5001, "message": "server busy"}),
            FakeHTTPResponse(payload={"code": 5001, "message": "server busy"}),
            FakeHTTPResponse(payload={"code": 5001, "message": "server busy"}),
        ])
        with pytest.raises(KlingError) as exc_info:
            kling.generate("p", "/tmp/out.mp4")
        err = exc_info.value
        assert err.code == 5001
        assert err.retryable is True
        assert err.category == "server"
        assert len(calls["post"]) == 3  # 3 次尝试全失败后才抛

    def test_submit_network_timeout_retryable(self, kling):
        kling._install(post_exc=real_requests.Timeout("timed out"))
        with pytest.raises(KlingError) as exc_info:
            kling.generate("p", "/tmp/out.mp4")
        err = exc_info.value
        assert err.category == "network"
        assert err.retryable is True


# ---------------------------------------------------------------------------
# 轮询阶段分类 + 终态
# ---------------------------------------------------------------------------

class TestPollClassification:

    def test_poll_1302_backoff_then_success(self, kling):
        """轮询中遇 1302 → 退避后继续轮询，不终止任务。"""
        calls = kling._install(post_responses=[_ok_submit("task-poll")], get_responses=[
            FakeHTTPResponse(payload={"code": 1302, "message": "rate limit"}),
            _ok_poll_done(),
        ])
        with tempfile_out() as out:
            path, _ = kling.generate("p", out)
        task_gets = [c for c in calls["get"] if "/tasks" in c["url"]]
        assert len(task_gets) == 2  # 1302 那次不算终态，接着轮询
        assert path == out

    def test_poll_5001_backoff_then_success(self, kling):
        calls = kling._install(post_responses=[_ok_submit("t")], get_responses=[
            FakeHTTPResponse(payload={"code": 5001, "message": "server error"}),
            _ok_poll_done(),
        ])
        with tempfile_out() as out:
            kling.generate("p", out)
        assert len([c for c in calls["get"] if "/tasks" in c["url"]]) == 2

    def test_poll_task_failed_carries_code(self, kling):
        kling._install(post_responses=[_ok_submit("t")], get_responses=[
            FakeHTTPResponse(payload={
                "code": 0,
                "data": [{"status": "failed", "code": 1201, "message": "invalid prompt"}],
            }),
        ])
        with pytest.raises(KlingError) as exc_info:
            kling.generate("p", "/tmp/out.mp4")
        err = exc_info.value
        assert err.code == 1201
        assert err.category == "params"
        assert err.retryable is False

    def test_poll_terminal_cancelled(self, kling):
        kling._install(post_responses=[_ok_submit("t")], get_responses=[
            FakeHTTPResponse(payload={"code": 0, "data": [{"status": "cancelled"}]}),
        ])
        with pytest.raises(KlingError) as exc_info:
            kling.generate("p", "/tmp/out.mp4")
        assert exc_info.value.category == "terminal"

    def test_poll_terminal_expired(self, kling):
        kling._install(post_responses=[_ok_submit("t")], get_responses=[
            FakeHTTPResponse(payload={"code": 0, "data": [{"status": "expired"}]}),
        ])
        with pytest.raises(KlingError) as exc_info:
            kling.generate("p", "/tmp/out.mp4")
        assert exc_info.value.category == "terminal"

    def test_poll_network_error_backs_off_then_succeeds(self, kling):
        calls = kling._install(post_responses=[_ok_submit("t")], get_responses=[
            real_requests.ConnectionError("reset by peer"),
            _ok_poll_done(),
        ])
        with tempfile_out() as out:
            kling.generate("p", out)
        assert len([c for c in calls["get"] if "/tasks" in c["url"]]) == 2


# ---------------------------------------------------------------------------
# check_credentials（GET /account/costs，替代烧资源包的验证方式）
# ---------------------------------------------------------------------------

class TestCheckCredentials:

    def test_check_credentials_ok_returns_costs_data(self, kling, monkeypatch):
        costs = {"user_id": "u1", "costs": []}
        captured = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return FakeHTTPResponse(payload={"code": 0, "data": costs})

        monkeypatch.setattr(kling_mod.requests, "get", fake_get)
        data = kling.check_credentials()

        assert data == costs
        assert "/account/costs" in captured["url"]
        assert "start_time" in captured["params"] and "end_time" in captured["params"]

    def test_check_credentials_1101_arrears_not_retryable(self, kling, monkeypatch):
        def fake_get(url, headers=None, params=None, timeout=None):
            return FakeHTTPResponse(
                status_code=403,
                payload={"code": 1101, "message": "arrears", "request_id": "req-x"},
            )

        monkeypatch.setattr(kling_mod.requests, "get", fake_get)
        with pytest.raises(KlingError) as exc_info:
            kling.check_credentials()
        err = exc_info.value
        assert err.code == 1101
        assert err.retryable is False
        assert err.category == "account"
        assert err.request_id == "req-x"

    def test_check_credentials_1001_auth_not_retryable(self, kling, monkeypatch):
        def fake_get(url, headers=None, params=None, timeout=None):
            return FakeHTTPResponse(status_code=401, payload={"code": 1001, "message": "invalid authorization"})

        monkeypatch.setattr(kling_mod.requests, "get", fake_get)
        with pytest.raises(KlingError) as exc_info:
            kling.check_credentials()
        assert exc_info.value.category == "auth"
        assert exc_info.value.retryable is False

    def test_check_credentials_no_key(self, monkeypatch):
        monkeypatch.delenv("KLING_API_KEY", raising=False)
        model = KlingModel({})
        with pytest.raises(KlingError) as exc_info:
            model.check_credentials()
        assert exc_info.value.category == "auth"

    def test_check_credentials_network_error_retryable(self, kling, monkeypatch):
        def fake_get(url, headers=None, params=None, timeout=None):
            raise real_requests.ConnectionError("dns failure")

        monkeypatch.setattr(kling_mod.requests, "get", fake_get)
        with pytest.raises(KlingError) as exc_info:
            kling.check_credentials()
        assert exc_info.value.category == "network"
        assert exc_info.value.retryable is True
