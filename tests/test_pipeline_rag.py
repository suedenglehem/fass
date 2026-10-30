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


# ------------------------------------------------- second try (LLM paths)

def _patch_retry(monkeypatch, rules_result, llm_result, llm_parse=None):
    """First pass on the rules adapter, the second try on the LLM adapter —
    get_adapter routed by name. Volume dirs are pinned so the expected
    query string is host-independent. Returns the LLM fake (records calls)."""
    rules = FakeRulesAdapter(rules_result)
    llm = FakeLLMAdapter(llm_result)
    if llm_parse is not None:
        llm.parse = llm_parse
    monkeypatch.setattr(
        pipe, "get_adapter",
        lambda name="auto": rules if name == "rules" else llm)
    monkeypatch.setattr(pipe, "resolved_rag",
                        lambda: RagConfig(enabled=False))
    monkeypatch.setattr(pipe, "_input_dir", lambda: "/input")
    monkeypatch.setattr(pipe, "_output_dir", lambda: "/output")
    return llm


def test_unsupported_retries_llm_with_paths_and_uses_ready_plan(monkeypatch):
    llm = _patch_retry(monkeypatch,
                       ParseResult(status=ParseStatus.UNSUPPORTED),
                       _ready())
    resp = generate(
        "copy the audio and video tracks, and add the audio track of "
        "clip2.mp4 as a second audio track",
        {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
         "clip2": Asset(path="clip2.mp4", kind=AssetKind.VIDEO)},
        output_filename="out.mp4",
        adapter_name="rules")
    assert resp.status == "ready"
    assert resp.adapter == "llm"
    # one retry, with the exact second-pass query: prefix + verbatim
    # instruction + full stop, then ALL inputs enumerated input1=/input2=
    # with full deployment paths, output= last
    assert len(llm.calls) == 1
    assert llm.calls[0]["instruction"] == (
        "generate ffmpeg command for copy the audio and video tracks, and "
        "add the audio track of clip2.mp4 as a second audio track. "
        "input1=/input/clip.mp4, input2=/input/clip2.mp4, "
        "output=/output/out.mp4")
    # the retry plan passed the normal planning step
    assert resp.command is not None
    # the response notes that the plan came from the second pass
    assert any("second LLM pass" in d for d in resp.defaults)


def test_unsupported_retry_also_unsupported_keeps_first_pass(monkeypatch):
    llm = _patch_retry(monkeypatch,
                       ParseResult(status=ParseStatus.UNSUPPORTED),
                       ParseResult(status=ParseStatus.UNSUPPORTED))
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "unsupported"
    assert resp.adapter == "rules"
    assert resp.command is None
    # exactly one retry attempt, then the first pass stands
    assert len(llm.calls) == 1
    assert llm.calls[0]["instruction"].startswith(
        "generate ffmpeg command for do selective color grading")


def test_unsupported_no_llm_configured_first_pass_stands(monkeypatch):
    def no_llm(name="auto"):
        if name == "llm":
            raise AdapterError("no LLM provider configured")
        return FakeRulesAdapter(ParseResult(status=ParseStatus.UNSUPPORTED))

    monkeypatch.setattr(pipe, "get_adapter", no_llm)
    monkeypatch.setattr(pipe, "resolved_rag",
                        lambda: RagConfig(enabled=False))
    resp = generate("do selective color grading on clip.mp4", _assets(),
                    adapter_name="rules")
    assert resp.status == "unsupported"
    assert resp.adapter == "rules"
    assert resp.command is None
