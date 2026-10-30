"""M6 LLM provider: the OpenAI-compatible chat path (strata / vLLM) with
a mocked transport — normal answer, the thinking-model retry, HTTP
errors, auth header, and availability checks."""

from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from fass.nlu import _llm_from_config, llm as llm_mod
from fass.nlu.llm import LLMAdapter
from fass.schema import Asset, ParseStatus, guess_kind


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _adapter(api_key: str | None = None, timeout: float = 90.0,
             thinking: bool | None = None) -> LLMAdapter:
    return LLMAdapter(api_key=api_key, model="qwen3.8-27b", timeout=timeout,
                      provider="openai", host="127.0.0.1", port=8080,
                      thinking=thinking)


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


# ------------------------------------------------- connection timeout (config)


def _cfg_file(tmp_path, monkeypatch, llm: dict):
    """Point FASS_CONFIG at a config.json holding just this llm block."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"llm": llm}), encoding="utf-8")
    monkeypatch.setenv("FASS_CONFIG", str(p))
    return p


def test_openai_request_carries_the_timeout(monkeypatch):
    seen = []

    def fake(req, timeout=None):
        seen.append(timeout)
        return _Resp(_ok_payload())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    assert _adapter(timeout=3)._chat_openai("s", "u") == "ok"
    assert seen == [3]


def test_anthropic_request_carries_the_timeout(monkeypatch):
    seen = []

    def fake(req, timeout=None):
        seen.append(timeout)
        return _Resp({"content": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    adapter = LLMAdapter(api_key="sk-x", timeout=6)
    assert adapter._chat_anthropic("s", "u") == "ok"
    assert seen == [6]


def test_retry_keeps_the_configured_timeout(monkeypatch):
    # the thinking-model retry must not fall back to a fresh default: both
    # attempts run under the same configured budget
    seen = []

    def fake(req, timeout=None):
        seen.append(timeout)
        if len(seen) == 1:
            return _Resp({"choices": [{
                "message": {"content": None, "reasoning_content": "hmm…"},
                "finish_reason": "length"}]})
        return _Resp(_ok_payload("final answer"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    assert _adapter(timeout=4)._chat_openai("s", "u") == "final answer"
    assert seen == [4, 4]


def test_openai_timeout_is_a_clean_error(monkeypatch):
    # a hung endpoint: the socket times out, the caller gets a readable
    # RuntimeError (the config tab shows it verbatim) — not a hang
    def fake(req, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="unreachable"):
        _adapter(timeout=1)._chat_openai("s", "u")


def test_anthropic_timeout_is_a_clean_error(monkeypatch):
    def fake(req, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="unreachable"):
        LLMAdapter(api_key="sk-x", timeout=1)._chat_anthropic("s", "u")


def test_adapter_from_config_uses_the_config_timeout(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch,
              {"provider": "openai", "host": "172.17.0.1", "port": 8080,
               "model": "qwen3.8-27b", "timeout": 7})
    adapter = _llm_from_config()
    assert (adapter.provider, adapter.host, adapter.timeout) == ("openai", "172.17.0.1", 7)


def test_adapter_from_config_anthropic_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _cfg_file(tmp_path, monkeypatch,
              {"provider": "anthropic", "api_key": "sk-x", "timeout": 25})
    adapter = _llm_from_config()
    assert (adapter.provider, adapter.timeout) == ("anthropic", 25)


def test_adapter_from_config_timeout_default(tmp_path, monkeypatch):
    # an older config.json without the key: the adapter gets the 90 s default
    _cfg_file(tmp_path, monkeypatch,
              {"provider": "openai", "host": "172.17.0.1", "port": 8080,
               "model": "qwen3.8-27b"})
    assert _llm_from_config().timeout == 90


# ------------------------------------------------------------- raw output

def test_parse_records_the_verbatim_model_output(monkeypatch):
    # the model's reply is preserved verbatim (fences and all) in
    # ParseResult.raw, so the UI can show what the LLM actually returned
    raw = ('```json\n{"status": "unsupported",\n'
           ' "unsupported": [{"fragment": "rotate 45 deg", '
           '"reason": "only 90-degree rotations"}]}\n```')
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_ok_payload(raw)))
    res = _adapter().parse(
        "rotate the file 45 degrees",
        {"main": Asset(path="clip.mp4", kind=guess_kind("clip.mp4"))})
    assert res.status is ParseStatus.UNSUPPORTED
    assert res.raw == raw


def test_parse_schema_failure_carries_the_verbatim_output(monkeypatch):
    # broken model JSON -> the error still carries the verbatim reply,
    # because a broken plan is exactly when the user needs to see the raw
    garbage = '{"status": "wobbly"}'
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_ok_payload(garbage)))
    with pytest.raises(RuntimeError, match="schema validation") as ei:
        _adapter().parse(
            "x", {"main": Asset(path="clip.mp4", kind=guess_kind("clip.mp4"))})
    assert ei.value.raw_output == garbage


# -------------------------------------------------------- thinking mode


def test_thinking_defaults_off(monkeypatch):
    monkeypatch.delenv("FASS_LLM_THINKING", raising=False)
    assert _adapter().thinking is False
    # explicit opt-in via the constructor or the env fallback
    assert _adapter(thinking=True).thinking is True
    monkeypatch.setenv("FASS_LLM_THINKING", "1")
    assert _adapter().thinking is True


def test_openai_request_pins_thinking_off_by_default(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        return _Resp(_ok_payload())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    assert _adapter()._chat_openai("s", "u") == "ok"
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_openai_request_pins_thinking_on_when_enabled(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        return _Resp(_ok_payload())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    _adapter(thinking=True)._chat_openai("s", "u")
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_chat_template_kwargs_rejection_self_heals(monkeypatch):
    # a strict OpenAI-compatible endpoint rejects the kwarg (HTTP 400):
    # one retry without it, and the latch keeps it off for the adapter
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 400, "bad request", {},
                io.BytesIO(b"Unexpected value for 'chat_template_kwargs'"))
        return _Resp(_ok_payload())

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    a = _adapter(thinking=True)
    assert a._chat_openai("s", "u") == "ok"
    assert "chat_template_kwargs" in calls[0]      # first request carries it
    assert "chat_template_kwargs" not in calls[1]  # retry after the 400
    assert a._chat_openai("s", "u") == "ok"
    assert "chat_template_kwargs" not in calls[2]  # latched: skipped entirely
    assert len(calls) == 3


def test_unrelated_http_400_does_not_latch(monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(json.loads(req.data))
        raise urllib.error.HTTPError(
            req.full_url, 400, "bad request", {},
            io.BytesIO(b"model 'nope' not found"))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        _adapter()._chat_openai("s", "u")
    assert len(calls) == 1  # the 400 is re-raised, not retried


def test_adapter_from_config_thinking(tmp_path, monkeypatch):
    _cfg_file(tmp_path, monkeypatch,
              {"provider": "openai", "host": "172.17.0.1", "port": 8080,
               "model": "qwen3.8-27b", "thinking": True})
    assert _llm_from_config().thinking is True


def test_adapter_from_config_thinking_default(tmp_path, monkeypatch):
    # an older config.json without the key: thinking stays off
    _cfg_file(tmp_path, monkeypatch,
              {"provider": "openai", "host": "172.17.0.1", "port": 8080,
               "model": "qwen3.8-27b"})
    assert _llm_from_config().thinking is False
