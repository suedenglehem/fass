"""M11.2 §6.4/§6.5 — the pipeline's RAG hook: retrieval happens on the
LLM path only, the evidence block reaches the LLM's user turn, citations
ride on the response, and the rules path never touches the service."""

from __future__ import annotations

import fass.pipeline as pipe
from fass.config import RagConfig
from fass.nlu.base import AdapterError
from fass.pipeline import generate
from fass.rag.client import RagSearchResult
from fass.schema import (
    Asset,
    AssetKind,
    EditPlan,
    FilterOp,
    OutputSpec,
    ParseResult,
    ParseStatus,
)

SILENCE_CHUNK = {
    "chunk_id": "ffmpeg-6.1.1:filters:silenceremove:2",
    "title": "silenceremove",
    "url": "https://ffmpeg.org/ffmpeg-filters.html",
    "anchor": "silenceremove_2",
    "component_type": "filter",
    "component_name": "silenceremove",
    "option_names": ["stop_threshold"],
    "text": "Remove silent parts.",
    "score": 9.0,
}
DETECT_CHUNK = {
    "chunk_id": "ffmpeg-6.1.1:filters:selectivecolor:1",
    "title": "selectivecolor",
    "url": "https://ffmpeg.org/ffmpeg-filters.html",
    "anchor": "selectivecolor_2",
    "component_type": "filter",
    "component_name": "selectivecolor",
    "option_names": [],
    "text": "Adjust color components selectively.",
    "score": 6.0,
}


def _result(chunks, ok=True, status="supported"):
    return RagSearchResult(ok=ok, corpus_id="ffmpeg-6.1.1", corpus_match="exact",
                           evidence_status=status, chunks=chunks,
                           retrieval_ms=12)


def _edit():
    return EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        operations=[FilterOp(domain="audio", expr="silenceremove")],
    )


def _assets():
    return {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}


def _ready():
    return ParseResult(status=ParseStatus.READY, plan=_edit())


class FakeLLMAdapter:
    """A stand-in LLM adapter that records the evidence kwarg it received."""
    name = "llm"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def parse(self, instruction, assets, previous_plan, media, evidence=None):
        self.calls.append({"instruction": instruction, "evidence": evidence})
        return self.result


class FakeRulesAdapter:
    name = "rules"

    def __init__(self, result):
        self.result = result

    def parse(self, instruction, assets, previous_plan, media):
        return self.result


def _patch(monkeypatch, adapter_result, rag_cfg, rag_result,
           adapter_name="llm"):
    """Point the pipeline's seams at fakes; returns (adapter, retrieve_calls).
    The fake retrieve raises when the RAG is off and it is ever called —
    'never called' assertions then mean something."""
    if adapter_name == "llm":
        adapter = FakeLLMAdapter(adapter_result)
    else:
        adapter = FakeRulesAdapter(adapter_result)
    monkeypatch.setattr(pipe, "get_adapter", lambda name="auto": adapter)
    monkeypatch.setattr(pipe, "resolved_rag", lambda: rag_cfg)
    calls = []

    def fake_retrieve(cfg, instruction, media=None, probed_version=None):
        calls.append({"cfg": cfg, "instruction": instruction,
                      "media": media, "probed_version": probed_version})
        return rag_result

    monkeypatch.setattr(pipe, "rag_retrieve", fake_retrieve)
    return adapter, calls


def _rag_on(**kw):
    return RagConfig(enabled=True, url="http://127.0.0.1:8100", **kw)


# ------------------------------------------------- ready plan

def test_llm_rag_on_evidence_reaches_llm_and_citation_rides_response(monkeypatch):
    adapter, calls = _patch(monkeypatch, _ready(), _rag_on(),
                            _result([SILENCE_CHUNK]))
    resp = generate("remove the silence from clip.mp4", _assets(),
                    adapter_name="llm")
    assert resp.status == "ready"
    # one retrieval, with the instruction
    assert len(calls) == 1
    assert calls[0]["instruction"] == "remove the silence from clip.mp4"
    # the delimited block went into the LLM's user turn
    ev = adapter.calls[0]["evidence"]
    assert ev is not None and "<<<EVIDENCE" in ev and "EVIDENCE>>>" in ev
    assert "silenceremove" in ev
    # the response cites the used filter from the retrieved chunk
    assert resp.evidence is not None
    assert resp.evidence["status"] == "supported"
    assert resp.evidence["corpus_id"] == "ffmpeg-6.1.1"
    c = [x for x in resp.evidence["citations"]
         if x["component"] == "filter:silenceremove"]
    assert len(c) == 1
    assert c[0]["source"] == "retrieved"
    assert c[0]["chunk_id"] == SILENCE_CHUNK["chunk_id"]


def test_llm_rag_off_never_retrieves_static_citations(monkeypatch):
    adapter, calls = _patch(monkeypatch, _ready(),
                            RagConfig(enabled=False), None)
    resp = generate("remove the silence from clip.mp4", _assets(),
                    adapter_name="llm")
    assert resp.status == "ready"
    assert calls == []
    assert adapter.calls[0]["evidence"] is None
    # static registry doc_ref citation, no service, no corpus
    assert resp.evidence is not None
    assert resp.evidence["status"] == "static"
    assert resp.evidence["corpus_id"] is None
    c = resp.evidence["citations"]
    assert len(c) == 1
    assert c[0]["component"] == "filter:silenceremove"
    assert c[0]["source"] == "registry_doc_ref"
    assert c[0]["chunk_id"] is None


def test_degraded_rag_reports_unavailable_but_keeps_static_citations(monkeypatch):
    _, _ = _patch(monkeypatch, _ready(), _rag_on(),
                  _result([], ok=False, status="unavailable"))
    resp = generate("remove the silence from clip.mp4", _assets(),
                    adapter_name="llm")
    assert resp.status == "ready"
    assert resp.evidence is not None
    assert resp.evidence["status"] == "unavailable"
    assert resp.evidence["citations"][0]["source"] == "registry_doc_ref"


# ------------------------------------------------- rules path

def test_rules_path_never_retrieves(monkeypatch):
    _, calls = _patch(monkeypatch, _ready(), _rag_on(),
                      _result([SILENCE_CHUNK]), adapter_name="rules")
    resp = generate("remove the silence from clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "ready"
    assert calls == []
    # the rules path still shows the static doc_ref citation
    assert resp.evidence["status"] == "static"


# ------------------------------------------------- unsupported

def test_unsupported_gets_closest_supported_note_status_unchanged(monkeypatch):
    _, calls = _patch(monkeypatch,
                      ParseResult(status=ParseStatus.UNSUPPORTED),
                      _rag_on(), _result([DETECT_CHUNK]))
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="llm")
    assert resp.status == "unsupported"
    assert resp.arguments is None and resp.command is None
    assert resp.evidence is not None
    assert resp.evidence["status"] == "unsupported"
    assert resp.evidence["note"] == (
        "FFmpeg documents this as `filter:selectivecolor`; "
        "fass does not support it yet.")
    assert resp.evidence["citations"][0]["source"] == "retrieved"


def test_unsupported_rag_off_no_note(monkeypatch):
    _, calls = _patch(monkeypatch,
                      ParseResult(status=ParseStatus.UNSUPPORTED),
                      RagConfig(enabled=False), None)
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="llm")
    assert resp.status == "unsupported"
    assert calls == []
    assert resp.evidence is None


# ------------------------------------------------- second try (direct LLM)
# The second approach is a DIRECT call for a raw ffmpeg command string —
# no EditPlan schema in the prompt — shlex-split and run through the same
# validate_argv gate an expert hand-edit goes through.

RETRY_QUERY = (
    "generate ffmpeg command for: copy the audio and video tracks, and "
    "add the audio track of clip2.mp4 as a second audio track. "
    "input1=/input/clip.mp4, input2=/input/clip2.mp4, "
    "output=/output/out.mp4")
RETRY_REPLY = (
    "Here is the command:\n\n```bash\n"
    "ffmpeg -i /input/clip.mp4 -i /input/clip2.mp4 "
    "-map 0:v:0 -map 0:a:0 -map 1:a:0 -c:v copy -c:a copy "
    "/output/out.mp4\n```\n")
RETRY_ARGV = [
    "ffmpeg", "-i", "/input/clip.mp4", "-i", "/input/clip2.mp4",
    "-map", "0:v:0", "-map", "0:a:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "copy", "/output/out.mp4",
]


class FakeDirectLLM:
    """The LLM adapter as the direct second-try call sees it: a provider
    with _chat_openai(system, user) — no parse(), no EditPlan schema."""
    name = "llm"
    provider = "openai"
    model = "fake"

    def __init__(self, reply):
        self._reply = reply
        self.queries = []

    def available(self):
        return True

    def _chat_openai(self, system, user):
        self.queries.append({"system": system, "user": user})
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _patch_retry(monkeypatch, rules_result, direct_reply,
                 llm_configured=True):
    """First pass on the rules adapter; the second try is a direct LLM
    call (get_adapter('llm') -> fake with _chat_openai). Volume dirs are
    pinned so the expected query string is host-independent. Returns the
    direct fake (records the (system, user) pairs), or None when the LLM
    is not configured."""
    rules = FakeRulesAdapter(rules_result)
    direct = FakeDirectLLM(direct_reply) if llm_configured else None

    def fake_get_adapter(name="auto"):
        if name == "llm":
            if direct is None:
                raise AdapterError("no LLM provider configured")
            return direct
        return rules

    monkeypatch.setattr(pipe, "get_adapter", fake_get_adapter)
    monkeypatch.setattr(pipe, "resolved_rag",
                        lambda: RagConfig(enabled=False))
    monkeypatch.setattr(pipe, "_input_dir", lambda: "/input")
    monkeypatch.setattr(pipe, "_output_dir", lambda: "/output")
    return direct


def test_unsupported_second_try_direct_command_ready(monkeypatch):
    direct = _patch_retry(monkeypatch,
                          ParseResult(status=ParseStatus.UNSUPPORTED),
                          RETRY_REPLY)
    resp = generate(
        "copy the audio and video tracks, and add the audio track of "
        "clip2.mp4 as a second audio track",
        {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
         "clip2": Asset(path="clip2.mp4", kind=AssetKind.VIDEO)},
        output_filename="out.mp4",
        adapter_name="rules")
    assert resp.status == "ready"
    assert resp.adapter == "llm"
    # one direct call, with the exact second-pass query: prefix + verbatim
    # instruction + full stop, then ALL inputs enumerated input1=/input2=
    # with full deployment paths, output= last
    assert len(direct.queries) == 1
    assert direct.queries[0]["user"] == RETRY_QUERY
    # the prompt carries NO EditPlan schema — it asks for the command
    assert "EditPlan" not in direct.queries[0]["system"]
    assert "schema" not in direct.queries[0]["system"].lower()
    assert "command" in direct.queries[0]["system"]
    # the reply's command passed the hand-edit gate and is returned as-is
    assert resp.arguments == RETRY_ARGV
    assert resp.command == pipe.render_posix(RETRY_ARGV)
    assert resp.raw_llm == RETRY_REPLY
    assert resp.plan is None
    assert resp.validation_level == "static"
    # the response logs that the first attempt failed and the second
    # approach (direct command) was used
    note = [d for d in resp.defaults
            if "first attempt" in d and "second approach" in d]
    assert len(note) == 1


def test_unsupported_second_try_reply_without_command_keeps_first_pass(
        monkeypatch):
    direct = _patch_retry(monkeypatch,
                          ParseResult(status=ParseStatus.UNSUPPORTED),
                          "I'm sorry, I can't help with that request.")
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "unsupported"
    assert resp.adapter == "rules"
    assert resp.command is None and resp.arguments is None
    # exactly one attempt, and the second try's own error is surfaced
    assert len(direct.queries) == 1
    assert direct.queries[0]["user"].startswith(
        "generate ffmpeg command for: do selective color grading")
    assert len(resp.errors) == 1
    assert "second LLM try failed" in resp.errors[0]
    assert "no ffmpeg command" in resp.errors[0]
    # the verbatim second reply is what the user sees
    assert resp.raw_llm == "I'm sorry, I can't help with that request."


def test_unsupported_second_try_command_rejected_by_gate_keeps_first_pass(
        monkeypatch):
    reply = ("ffmpeg -i /input/clip.mp4 -definitely_not_a_flag "
             "/output/out.mp4\n")
    _patch_retry(monkeypatch,
                 ParseResult(status=ParseStatus.UNSUPPORTED), reply)
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "unsupported"
    assert resp.command is None
    assert len(resp.errors) == 1
    assert "failed validation" in resp.errors[0]
    assert "unknown flag" in resp.errors[0]
    assert resp.raw_llm == reply


def test_unsupported_second_try_llm_call_error_is_surfaced(monkeypatch):
    _patch_retry(monkeypatch,
                 ParseResult(status=ParseStatus.UNSUPPORTED),
                 RuntimeError("LLM provider unreachable: connection refused"))
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "unsupported"
    assert resp.adapter == "rules"
    assert resp.command is None
    assert len(resp.errors) == 1
    assert "LLM call failed" in resp.errors[0]
    assert "connection refused" in resp.errors[0]


def test_unsupported_no_llm_configured_first_pass_stands(monkeypatch):
    _patch_retry(monkeypatch,
                 ParseResult(status=ParseStatus.UNSUPPORTED), None,
                 llm_configured=False)
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "unsupported"
    assert resp.adapter == "rules"
    assert resp.command is None
    assert len(resp.errors) == 1
    assert "no LLM provider configured" in resp.errors[0]


# ------------------------------------------------- command extraction

def test_extract_command_bare():
    assert pipe._extract_ffmpeg_command("ffmpeg -i a.mp4 -f null -") == \
        "ffmpeg -i a.mp4 -f null -"


def test_extract_command_fenced_with_prose():
    text = ("Sure! Here you go:\n\n```bash\n"
            "ffmpeg -i a.mp4 -c:v libx264 out.mp4\n```\n\nHope that helps.")
    assert pipe._extract_ffmpeg_command(text) == \
        "ffmpeg -i a.mp4 -c:v libx264 out.mp4"


def test_extract_command_backslash_continuations():
    text = "ffmpeg -i a.mp4 \\\n  -i b.mp4 \\\n  -map 0:v:0 -map 1:a:0 out.ts"
    assert pipe._extract_ffmpeg_command(text) == \
        "ffmpeg -i a.mp4 -i b.mp4 -map 0:v:0 -map 1:a:0 out.ts"


def test_extract_command_bullet_start():
    assert pipe._extract_ffmpeg_command(
        "- ffmpeg -i a.mp4 -f null -\n") == "ffmpeg -i a.mp4 -f null -"


def test_extract_command_stops_at_prose_after_command():
    text = "ffmpeg -i a.mp4 -f null -\nUse this one, it works."
    assert pipe._extract_ffmpeg_command(text) == "ffmpeg -i a.mp4 -f null -"


def test_extract_command_none_when_absent():
    assert pipe._extract_ffmpeg_command("I cannot do that.") is None
    assert pipe._extract_ffmpeg_command("") is None
