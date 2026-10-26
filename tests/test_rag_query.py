"""M11.2 §6.2 — the deterministic RAG query builder (pure, no service)."""

from __future__ import annotations

from fass.config import RagConfig
from fass.rag.query import (
    build_query,
    jargon_candidates,
    media_facts_line,
    mentioned_components,
    select_corpus,
)

PROBED_901 = "ffmpeg version 9.0.1 Copyright (c) 2000-2025 the FFmpeg developers"
CORPORA = ["ffmpeg-6.1.1", "ffmpeg-9.0.1"]


class _Media:
    def __init__(self, duration=12.345, width=1920, height=1080,
                 has_audio=True, fps=25.0):
        self.duration = duration
        self.width = width
        self.height = height
        self.has_audio = has_audio
        self.fps = fps


def _rag(**kw):
    return RagConfig(**kw)


# ------------------------------------------------- component mentions

def test_mentioned_filters_encoder_bsf():
    got = mentioned_components(
        "crop the frame and encode with libx264 plus h264_mp4toannexb")
    assert got == [("filter", "crop"),
                   ("encoder", "libx264"),
                   ("bitstream_filter", "h264_mp4toannexb")]


def test_mentioned_case_insensitive_and_order():
    got = mentioned_components("apply Silenceremove then Scale")
    assert got == [("filter", "silenceremove"), ("filter", "scale")]


def test_mention_word_boundaries():
    # underscore is a word character: 'trim' never fires inside a longer
    # identifier, and 'ac3' never fires inside 'eac3'
    assert mentioned_components("add showinfo2") == []
    assert mentioned_components("encode eac3") == [("encoder", "eac3")]


def test_mentioned_longest_first():
    got = mentioned_components("use h264_mp4toannexb")
    assert got == [("bitstream_filter", "h264_mp4toannexb")]


def test_no_mention():
    assert mentioned_components("make it look nicer") == []


# ------------------------------------------------- jargon fallback

def test_jargon_candidates():
    assert jargon_candidates("please stabilise the shaky clip") == [
        ("filter", "vidstabdetect"), ("filter", "vidstabtransform")]


def test_jargon_first_occurrence_order():
    assert jargon_candidates("crossfade then remove silence") == [
        ("filter", "xfade"), ("filter", "acrossfade"),
        ("filter", "silenceremove")]


def test_jargon_none():
    assert jargon_candidates("crop the video") == []


# ------------------------------------------------- media facts

def test_media_facts_line_full():
    assert media_facts_line(_Media()) == \
        "media: duration=12.3s 1920x1080 has_audio=true fps=25"


def test_media_facts_line_partial():
    assert media_facts_line(_Media(duration=None, fps=None)) == \
        "media: 1920x1080 has_audio=true"
    assert media_facts_line(_Media(duration=None, fps=None, has_audio=False,
                                   width=None, height=None)) == \
        "media: has_audio=false"
    assert media_facts_line(None) is None


# ------------------------------------------------- corpus selection

def test_select_corpus_exact():
    assert select_corpus(PROBED_901, CORPORA) == ("ffmpeg-9.0.1", "exact")


def test_select_corpus_nearest_lower_with_warning():
    warnings = []
    got = select_corpus("ffmpeg version 9.0.2", ["ffmpeg-9.0.1"],
                        warnings=warnings)
    assert got == ("ffmpeg-9.0.1", "nearest")
    assert len(warnings) == 1 and "lower release" in warnings[0]


def test_select_corpus_nearest_skips_higher():
    # 6.1.1 is the only release at or below 9.0.1
    assert select_corpus(PROBED_901, CORPORA + ["ffmpeg-6.1.0"]) \
        == ("ffmpeg-9.0.1", "exact")
    assert select_corpus("ffmpeg version 6.1.2", CORPORA) \
        == ("ffmpeg-6.1.1", "nearest")


def test_select_corpus_nightly_fallback():
    warnings = []
    got = select_corpus("ffmpeg version 7.0",
                        ["nightly-2026-09-15", "nightly-2026-10-01"],
                        warnings=warnings)
    assert got == ("nightly-2026-10-01", "nightly")
    assert len(warnings) == 1 and "nightly" in warnings[0].lower()


def test_select_corpus_override():
    assert select_corpus(PROBED_901, CORPORA, override="ffmpeg-6.1.1") \
        == ("ffmpeg-6.1.1", "override")
    # an override the service does not have is still honored + warned
    warnings = []
    got = select_corpus(PROBED_901, CORPORA, override="ffmpeg-1.0.0",
                        warnings=warnings)
    assert got == ("ffmpeg-1.0.0", "override")
    assert len(warnings) == 1 and "not available" in warnings[0]


def test_select_corpus_no_probed_uses_loaded():
    warnings = []
    assert select_corpus(None, CORPORA, loaded="ffmpeg-9.0.1",
                         warnings=warnings) == ("ffmpeg-9.0.1", "loaded")
    assert warnings == []


def test_select_corpus_unparseable_and_empty():
    warnings = []
    assert select_corpus("not a version", [], warnings=warnings) \
        == (None, "none")
    assert any("could not parse" in w for w in warnings)
    warnings2 = []
    assert select_corpus(PROBED_901, [], warnings=warnings2) == (None, "none")
    assert warnings2


# ------------------------------------------------- build_query

def test_build_query_exact_request_json():
    rag = _rag(top_k=4, max_evidence_chars=2000)
    q = build_query("crop the video", media=_Media(),
                    probed_version=PROBED_901, available_corpora=CORPORA,
                    loaded_corpus="ffmpeg-9.0.1", rag=rag)
    assert q.to_request() == {
        "query": "crop the video media: duration=12.3s 1920x1080 "
                 "has_audio=true fps=25",
        "corpus_id": "ffmpeg-9.0.1",
        "source_kinds": ["manual"],
        "top_k": 4,
        "max_evidence_chars": 2000,
        "component_types": ["filter"],
        "component_names": ["crop"],
    }
    assert q.corpus_match == "exact"
    assert q.corpus_warnings == []


def test_build_query_nearest_warns():
    q = build_query("crop the video",
                    probed_version="ffmpeg version 9.0.2",
                    available_corpora=["ffmpeg-9.0.1"],
                    loaded_corpus="ffmpeg-9.0.1", rag=_rag())
    assert q.corpus_id == "ffmpeg-9.0.1"
    assert q.corpus_match == "nearest"
    assert len(q.corpus_warnings) == 1


def test_build_query_jargon_becomes_filter():
    q = build_query("remove silence from the podcast", rag=_rag())
    assert q.component_names == ["silenceremove"]
    assert q.component_types == ["filter"]
    # the raw instruction stays the semantic query
    assert q.to_request()["query"] == "remove silence from the podcast"


def test_build_query_mention_beats_jargon_and_dedupes():
    # 'yadif' is mentioned exactly; the 'deinterlace' jargon would add the
    # same name — it must not appear twice
    q = build_query("use yadif to deinterlace", rag=_rag())
    assert q.component_names == ["yadif"]


def test_build_query_send_instruction_false():
    q = build_query("please crop the video", rag=_rag(send_instruction=False))
    assert q.to_request()["query"] == "crop"
    q2 = build_query("please crop the video", media=_Media(),
                     rag=_rag(send_instruction=False))
    assert q2.to_request()["query"].startswith("crop media: ")
    # nothing mentioned at all -> fall back to the raw instruction
    q3 = build_query("make it look nicer", rag=_rag(send_instruction=False))
    assert q3.to_request()["query"] == "make it look nicer"


def test_build_query_offline_default():
    # no service metadata (unit tests): override honored, else server default
    q = build_query("crop the video", rag=_rag())
    assert q.corpus_id is None and q.corpus_match == "none"
    q2 = build_query("crop the video", rag=_rag(corpus="ffmpeg-6.1.1"))
    assert q2.corpus_id == "ffmpeg-6.1.1" and q2.corpus_match == "override"


def test_build_query_no_component_keys_when_unmentioned():
    req = build_query("make it look nicer", rag=_rag()).to_request()
    assert "component_types" not in req
    assert "component_names" not in req
    assert req["query"] == "make it look nicer"


def test_build_query_deterministic():
    kw = dict(probed_version=PROBED_901, available_corpora=CORPORA,
              loaded_corpus="ffmpeg-9.0.1", rag=_rag())
    a = build_query("crop the video", media=_Media(), **kw).to_request()
    b = build_query("crop the video", media=_Media(), **kw).to_request()
    assert a == b
