"""M6 LLM provider: the OpenAI-compatible chat path (strata / vLLM) with
a mocked transport — normal answer, the thinking-model retry, HTTP
errors, auth header, and availability checks."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from fass.nlu import llm as llm_mod
from fass.nlu.llm import LLMAdapter


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _adapter(api_key: str | None = None) -> LLMAdapter:
    return LLMAdapter(api_key=api_key, model="qwen3.8-27b",
                      provider="openai", host="127.0.0.1", port=8080)


def _ok_payload(content="ok"):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": "stop"}]}


def test_normal_answer_single_call(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        return _Resp(_ok_payload())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    assert _adapter()._chat_openai("sys", "user") == "ok"
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 2048
    assert calls[0]["model"] == "qwen3.8-27b"
    assert calls[0]["temperature"] == 0


def test_thinking_model_retries_with_headroom(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        if len(calls) == 1:
            # reasoning exhausted max_tokens: content null + trace
            return _Resp({"choices": [{
                "message": {"content": None, "reasoning_content": "hmm…"},
                "finish_reason": "length"}]})
        return _Resp(_ok_payload("final answer"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    assert _adapter()._chat_openai("s", "u") == "final answer"
    assert [c["max_tokens"] for c in calls] == [2048, 16384]


def test_two_empty_answers_raise(monkeypatch):
    def fake(req, timeout=None):
        return _Resp({"choices": [{
            "message": {"content": None, "reasoning_content": "…"},
            "finish_reason": "length"}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="no answer"):
        _adapter()._chat_openai("s", "u")


def test_http_error_raises(monkeypatch):
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 500, "boom", {}, io.BytesIO(b"server exploded"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _adapter()._chat_openai("s", "u")


def test_unreachable_raises(monkeypatch):
    def fake(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="unreachable"):
        _adapter()._chat_openai("s", "u")


def test_bearer_header_only_when_key(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(_ok_payload())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    _adapter(api_key="sekret")._chat_openai("s", "u")
    assert seen["auth"] == "Bearer sekret"
    _adapter()._chat_openai("s", "u")
    assert seen["auth"] is None


def test_availability(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert LLMAdapter(provider="openai", host="h", port=1, model="m").available()
    # openai without a configured model
    assert not LLMAdapter(provider="openai", host="h", port=1, model="").available()
    # openai without host/port
    assert not LLMAdapter(provider="openai", model="m").available()
    # anthropic needs a key (constructor or env)
    assert not LLMAdapter(api_key=None).available()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert LLMAdapter(api_key=None).available()


def test_probe_returns_answer_and_seconds(monkeypatch):
    def fake(req, timeout=None):
        return _Resp(_ok_payload("ok"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    answer, secs = _adapter().test()
    assert answer == "ok"
    assert secs >= 0.0
