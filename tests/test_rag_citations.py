"""M11.2 §6.4 — citation derivation from the plan's used components
(deterministic, never model-generated)."""

from __future__ import annotations

from types import SimpleNamespace

from fass.rag.citations import (
    chunk_citation,
    derive_citations,
    used_components,
)
from fass.rag.client import RagSearchResult

SILENCE_CHUNK = {
    "chunk_id": "ffmpeg-6.1.1:filters:silenceremove:2",
    "title": "silenceremove",
    "url": "https://ffmpeg.org/ffmpeg-filters.html",
    "anchor": "silenceremove_2",
    "component_type": "filter",
    "component_name": "silenceremove",
    "option_names": ["threshold"],
    "text": "Remove silent parts.",
    "score": 9.0,
}
LOUD_CHUNK = {
    "chunk_id": "ffmpeg-6.1.1:filters:loudnorm:1",
    "title": "loudnorm",
    "url": "https://ffmpeg.org/ffmpeg-filters.html",
    "anchor": "loudnorm",
    "component_type": "filter",
    "component_name": "loudnorm",
    "option_names": [],
    "text": "Loudness normalization.",
    "score": 5.0,
}
LOUD_CHUNK_LATE = dict(LOUD_CHUNK,
                       chunk_id="ffmpeg-6.1.1:filters:loudnorm:9",
                       score=8.0)
ENC_CHUNK = {
    "chunk_id": "ffmpeg-6.1.1:codecs:libx264:1",
    "title": "libx264",
    "url": "https://ffmpeg.org/ffmpeg-codecs.html",
    "anchor": "libx264",
    "component_type": "encoder",
    "component_name": "libx264",
    "option_names": ["crf"],
    "text": "The libx264 encoder.",
    "score": 7.0,
}
FMT_CHUNK = {
    "chunk_id": "ffmpeg-6.1.1:formats:mp4:3",
    "title": "mp4",
    "url": "https://ffmpeg.org/ffmpeg-formats.html",
    "anchor": "mp4",
    "component_type": "format",
    "component_name": "mp4",
    "option_names": ["movflags", "hls_time"],
    "text": "MP4 demuxer/muxer options.",
    "score": 6.0,
}


def _result(chunks, ok=True, corpus_id="ffmpeg-6.1.1",
            evidence_status="supported"):
    return RagSearchResult(ok=ok, corpus_id=corpus_id,
                           corpus_match="exact",
                           evidence_status=evidence_status, chunks=chunks,
                           retrieval_ms=12)


def _edit(video_codec="h264", audio_codec="aac", bsfs=(), fmt_opts=None):
    """A duck-typed EditPlan — citations.py only reads output and
    bitstream_filters."""
    return SimpleNamespace(
        output=SimpleNamespace(
            video_codec=video_codec, audio_codec=audio_codec,
            gpu=False, qsv=False,
            format_options=dict(fmt_opts or {})),
        bitstream_filters=([SimpleNamespace(name=n) for n in bsfs]
                           or None),
    )


# ------------------------------------------------- used components

def test_used_components_first_use_order():
    got = used_components(
        _edit(bsfs=("null",), fmt_opts={"movflags": "faststart"}),
        used_filters=["silenceremove", "loudnorm"])
    assert got == [
        ("filter", "silenceremove"),
        ("filter", "loudnorm"),
        ("encoder", "libx264"),
        ("encoder", "aac"),
        ("bsf", "null"),
        ("format", "movflags"),
    ]


def test_used_components_no_video_codec():
    got = used_components(_edit(video_codec=None), used_filters=[])
    assert got == [("encoder", "aac")]


def test_kinds_are_distinct():
    # a bsf and a filter sharing a name are two different components
    got = used_components(_edit(bsfs=("trim",)), used_filters=["trim"])
    assert ("filter", "trim") in got and ("bsf", "trim") in got


# ------------------------------------------------- retrieved matches

def test_one_citation_per_matched_component():
    edit = _edit()
    got = derive_citations(edit, ["silenceremove"],
                           _result([SILENCE_CHUNK, LOUD_CHUNK]))
    # silenceremove is cited from the chunk; the encoders (libx264, aac)
    # have no retrieved evidence and no registry doc_ref — no citation.
    assert len(got) == 1
    c = got[0]
    assert c == {
        "chunk_id": "ffmpeg-6.1.1:filters:silenceremove:2",
        "title": "silenceremove",
        "url": "https://ffmpeg.org/ffmpeg-filters.html",
        "anchor": "silenceremove_2",
        "component": "filter:silenceremove",
        "source": "retrieved",
        "excerpt_chars": len("Remove silent parts."),
    }


def test_highest_scoring_chunk_wins():
    got = derive_citations(_edit(), ["loudnorm"],
                           _result([LOUD_CHUNK, LOUD_CHUNK_LATE]))
    assert len(got) == 1
    assert got[0]["chunk_id"] == "ffmpeg-6.1.1:filters:loudnorm:9"


def test_encoder_component_matched():
    got = derive_citations(_edit(), [], _result([ENC_CHUNK]))
    encs = [c for c in got if c["component"] == "encoder:libx264"]
    assert len(encs) == 1
    assert encs[0]["source"] == "retrieved"
    assert encs[0]["chunk_id"] == "ffmpeg-6.1.1:codecs:libx264:1"


def test_format_option_key_matched_via_option_names():
    edit = _edit(fmt_opts={"movflags": "faststart"})
    got = derive_citations(edit, [], _result([FMT_CHUNK]))
    fmts = [c for c in got if c["component"] == "format:movflags"]
    assert len(fmts) == 1
    assert fmts[0]["chunk_id"] == "ffmpeg-6.1.1:formats:mp4:3"


def test_dropped_chunks_not_cited():
    # loudnorm retrieved, plan uses silenceremove: the loudnorm chunk is
    # dropped — no citation may reference it (silenceremove falls back
    # to its registry doc_ref instead).
    got = derive_citations(_edit(), ["silenceremove"], _result([LOUD_CHUNK]))
    assert all(c.get("chunk_id") != LOUD_CHUNK["chunk_id"] for c in got)


# ------------------------------------------------- registry fallback

def test_registry_doc_ref_fallback_when_rag_off():
    got = derive_citations(_edit(), ["silenceremove", "loudnorm"], None)
    comps = {c["component"]: c for c in got}
    assert set(comps) == {"filter:silenceremove", "filter:loudnorm"}
    c = comps["filter:loudnorm"]
    assert c["source"] == "registry_doc_ref"
    assert c["chunk_id"] is None
    assert c["anchor"] == "loudnorm"
    assert c["url"].startswith("https://ffmpeg.org/ffmpeg-filters.html#")
    assert c["excerpt_chars"] is None


def test_retrieved_wins_over_registry():
    got = derive_citations(_edit(), ["loudnorm"], _result([LOUD_CHUNK]))
    assert [c["source"] for c in got] == ["retrieved"]


def test_degraded_result_falls_back_to_registry():
    got = derive_citations(_edit(), ["loudnorm"],
                           _result([LOUD_CHUNK], ok=False))
    assert len(got) == 1
    assert got[0]["source"] == "registry_doc_ref"


def test_no_evidence_anywhere_no_citations():
    # a codec without a registry doc_ref and no retrieved chunk
    got = derive_citations(_edit(video_codec=None, audio_codec=None),
                           [], None)
    assert got == []


# ------------------------------------------------- the citation shape

def test_chunk_citation_shape():
    c = chunk_citation(SILENCE_CHUNK, "filter:silenceremove")
    assert c["component"] == "filter:silenceremove"
    assert c["source"] == "retrieved"
    assert c["excerpt_chars"] == len(SILENCE_CHUNK["text"])
