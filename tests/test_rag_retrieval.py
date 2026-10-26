"""Lexical retrieval tests (joint PRD 5.4): filters, exact boosts, dedupe,
parent/adjacent expansion, evidence budget, evidence-status thresholds.

Runs on a small SYNTHETIC corpus built in tmp_path — never on the full
committed corpus, except the one skipif-guarded real-corpus smoke test.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rag.retrieval import MAX_TOP_K, Retriever

REPO = Path(__file__).resolve().parent.parent
REAL_CORPUS = REPO / "rag" / "corpus" / "ffmpeg-9.0.1"


def _chunk(**kw) -> dict:
    base = dict(
        chunk_id="c0", corpus_id="synth-1.0", source_url="https://example.test/doc.html#x",
        anchor="x", heading_path=["H"], component_type=None, component_name=None,
        option_names=[], text="body", parent_id=None, content_hash="h-c0",
        version_status="pinned-release", source_kind="manual", level=2, oversized=False,
    )
    base.update(kw)
    return base


MANUAL = [
    _chunk(chunk_id="m-parent", anchor="audio-filters",
           heading_path=["Audio Filters"], text="Audio Filters\n\nSection listing audio filters.",
           content_hash="h-parent"),
    _chunk(chunk_id="m-silence", anchor="silenceremove",
           heading_path=["Audio Filters", "silenceremove"],
           component_type="filter", component_name="silenceremove",
           option_names=["start_periods", "stop_periods", "start_threshold"],
           text="Audio Filters > silenceremove\n\nRemove silence from the beginning, middle "
                "or end of the audio. Uses amix-style detection internally.",
           parent_id="m-parent", content_hash="h-silence", level=3),
    _chunk(chunk_id="m-loudnorm-1", anchor="loudnorm",
           heading_path=["Audio Filters", "loudnorm"],
           component_type="filter", component_name="loudnorm",
           option_names=["I, i", "LRA, lra", "TP, tp", "offset", "linear"],
           text="Audio Filters > loudnorm\n\nEBU R128 loudness normalization. "
                "The loudnorm.I option sets integrated loudness.",
           parent_id="m-parent", content_hash="h-loudnorm-1", level=3),
    _chunk(chunk_id="m-loudnorm-2", anchor="loudnorm-1",
           heading_path=["Audio Filters", "loudnorm", "Examples"],
           component_type="filter", component_name="loudnorm",
           option_names=[], text="loudnorm Examples\n\nffmpeg -i in -af loudnorm out",
           parent_id="m-loudnorm-1", content_hash="h-loudnorm-2", level=4),
    _chunk(chunk_id="m-scale", anchor="scale_005fcuda",
           heading_path=["Video Filters", "scale_cuda"],
           component_type="filter", component_name="scale_cuda",
           option_names=["w", "h"], text="Video Filters > scale_cuda\n\nScale the input "
           "video with NVIDIA GPU acceleration.", content_hash="h-scale", level=3),
    _chunk(chunk_id="m-cli", anchor="Options", heading_path=["ffmpeg", "Options"],
           option_names=["map", "map_chapters"],
           text="Options\n\n-map 0:a — include all audio streams. ffmpeg -i INPUT -map 0:a OUTPUT",
           content_hash="h-cli"),
    _chunk(chunk_id="m-amix", anchor="amix", heading_path=["Audio Filters", "amix"],
           component_type="filter", component_name="amix", option_names=["inputs"],
           text="Audio Filters > amix\n\nMixes multiple audio inputs.", content_hash="h-amix"),
]
BUILD_HELP = [
    _chunk(chunk_id="b-nvenc", corpus_id="synth-1.0-build-help", source_url="", anchor="",
           heading_path=["encoders.txt"], component_type="encoder",
           component_name="h264_nvenc", text="ffmpeg encoders\nh264_nvenc",
           content_hash="h-nvenc", source_kind="build-help"),
    # same text as the manual silenceremove chunk -> dedupe by content_hash
    _chunk(chunk_id="b-silence", corpus_id="synth-1.0-build-help", source_url="", anchor="",
           heading_path=["filters.txt"], component_type="build_help_filter",
           component_name="silenceremove",
           text="Audio Filters > silenceremove\n\nRemove silence from the beginning, middle "
                "or end of the audio. Uses amix-style detection internally.",
           content_hash="h-silence", source_kind="build-help"),
]


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "synth-1.0"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps(
        {"corpus_id": "synth-1.0", "version_status": "pinned-release", "total_chunks": 9}))
    for kind, rows in (("manual", MANUAL), ("build-help", BUILD_HELP)):
        gen = root / "chunks" / kind / "gen-1"
        gen.mkdir(parents=True)
        (root / "chunks" / kind / "generations.json").write_text(
            json.dumps({"active": 1, "history": [{"generation": 1, "total_chunks": len(rows)}]}))
        (gen / "chunks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return root


@pytest.fixture()
def r(corpus: Path) -> Retriever:
    return Retriever(corpus)


def test_load_counts(r: Retriever):
    assert r.corpus_id == "synth-1.0"
    assert r.kind_counts == {"manual": 7, "build-help": 2}
    assert len(r.chunks) == 9


# -- smoke queries (PRD 5.4 identifier handling) --------------------------

SMOKE = ["silenceremove", "-map 0:a", "remove silence from the audio",
         "scale_cuda", "loudnorm.I"]


@pytest.mark.parametrize("q", SMOKE)
def test_smoke_shape_and_budget(r: Retriever, q: str):
    resp = r.search(q).response()
    assert resp["corpus_match"] == "exact"
    assert resp["version_status"] == "pinned-release"
    assert resp["evidence_status"] in ("supported", "partial", "insufficient")
    assert len(resp["chunks"]) <= MAX_TOP_K
    assert sum(len(c["text"]) for c in resp["chunks"]) <= 4000
    for c in resp["chunks"]:
        assert set(c) == {"chunk_id", "title", "heading_path", "url", "anchor",
                          "component_type", "component_name", "option_names",
                          "text", "score", "parent_id"}
        assert "#" not in c["url"]


def test_smoke_exact_component(r: Retriever):
    resp = r.search("silenceremove").response()
    assert {"type": "filter", "name": "silenceremove"} in resp["exact_matches"]
    assert resp["evidence_status"] == "supported"
    assert resp["chunks"][0]["component_name"] == "silenceremove"


def test_smoke_dash_identifier(r: Retriever):
    resp = r.search("-map 0:a").response()
    assert resp["evidence_status"] == "supported"
    assert any("-map" in c["text"] for c in resp["chunks"])


def test_smoke_scale_cuda(r: Retriever):
    resp = r.search("scale_cuda").response()
    assert resp["exact_matches"] == [{"type": "filter", "name": "scale_cuda"}]
    assert resp["evidence_status"] == "supported"


def test_smoke_loudnorm_dotted(r: Retriever):
    resp = r.search("loudnorm.I").response()
    assert {"type": "filter", "name": "loudnorm"} in resp["exact_matches"]
    assert resp["chunks"][0]["component_name"] == "loudnorm"


def test_smoke_nl_query(r: Retriever):
    resp = r.search("remove silence from the audio").response()
    assert resp["chunks"][0]["component_name"] == "silenceremove"
    assert resp["evidence_status"] in ("supported", "partial")


# -- filters ---------------------------------------------------------------

def test_component_type_filter(r: Retriever):
    resp = r.search("h264_nvenc", component_types=["encoder"]).response()
    assert resp["chunks"] and all(c["component_type"] == "encoder" for c in resp["chunks"])


def test_source_kind_filter(r: Retriever):
    resp = r.search("silenceremove", source_kinds=["build-help"]).response()
    assert all(c["component_type"] == "build_help_filter" for c in resp["chunks"])


def test_component_name_filter(r: Retriever):
    resp = r.search("loudnorm", component_names=["amix"]).response()
    assert resp["evidence_status"] == "insufficient"
    assert resp["chunks"] == []


def test_corpus_mismatch(r: Retriever):
    from rag.retrieval import CorpusMismatch
    with pytest.raises(CorpusMismatch):
        r.search("silenceremove", corpus_id="ffmpeg-6.1.1")


# -- dedupe / expansion / budget -------------------------------------------

def test_dedupe_by_content_hash(r: Retriever):
    resp = r.search("silenceremove").response()
    ids = [c["chunk_id"] for c in resp["chunks"]]
    assert not ("m-silence" in ids and "b-silence" in ids)


def test_parent_expansion(r: Retriever):
    resp = r.search("silenceremove").response()
    ids = [c["chunk_id"] for c in resp["chunks"]]
    assert "m-silence" in ids and "m-parent" in ids


def test_char_budget(r: Retriever):
    resp = r.search("silenceremove", max_evidence_chars=100).response()
    assert sum(len(c["text"]) for c in resp["chunks"]) <= 200
    assert resp["omitted"]["chunks"] > 0 and resp["omitted"]["chars"] > 0


def test_top_k_capped_at_8(r: Retriever):
    resp = r.search("audio", top_k=20).response()
    assert len(resp["chunks"]) <= MAX_TOP_K


# -- evidence status thresholds --------------------------------------------

def test_insufficient(r: Retriever):
    resp = r.search("zzzqqqxy").response()
    assert resp["evidence_status"] == "insufficient"
    assert resp["chunks"] == []


def test_partial(r: Retriever):
    # "audio" matches many chunks lexically, no exact component/option token
    resp = r.search("audio").response()
    assert resp["chunks"]
    assert resp["evidence_status"] == "partial"
    assert resp["exact_matches"] == []


# -- exact lookup & mentioned components ------------------------------------

def test_exact_lookup_returns_all_chunks(r: Retriever):
    chunks = r.get_component("filter", "loudnorm")
    assert {c.chunk_id for c in chunks} == {"m-loudnorm-1", "m-loudnorm-2"}


def test_mentioned_components(r: Retriever):
    resp = r.search("silenceremove").response()
    names = {m["name"] for m in resp["mentioned_components"]}
    assert "amix" in names  # mentioned inside the silenceremove chunk text
    assert all(m["type"] and m["name"] for m in resp["mentioned_components"])


# -- real corpus smoke (skipif-guarded; chunks are gitignored data) ---------

@pytest.mark.skipif(not (REAL_CORPUS / "chunks" / "manual").is_dir(),
                    reason="real corpus chunks not present")
def test_real_corpus_silenceremove():
    t0 = time.perf_counter()
    r = Retriever(REAL_CORPUS)
    assert time.perf_counter() - t0 < 5.0
    resp = r.search("silenceremove").response()
    assert any(c["component_name"] == "silenceremove" for c in resp["chunks"])
    assert resp["evidence_status"] == "supported"
