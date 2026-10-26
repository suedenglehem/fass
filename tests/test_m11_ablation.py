"""Unit tests for scripts/m11_ablation.py (the §9.2 ablation harness).

Offline only: the scorer, the schemas-block renderer and the citation
relevance heuristic. Live LLM / RAG runs are a manual step (they need
the vLLM endpoint and the fass-rag service)."""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import m11_ablation as abl


def _entry(eid: str, instruction: str, **exp) -> dict:
    return {"id": eid, "instruction": instruction, "expected": exp}


def _res(arm: str, eid: str, status: str, plan=None, **kw):
    return abl.ArmResult(arm=arm, id=eid, status=status, plan=plan, **kw)


# ------------------------------------------------- render_schemas_block

def test_schemas_block_covers_schema_bearing_filters():
    block = abl.render_schemas_block()
    # dynaudnorm (audio) has option schemas (M10.0) — kind + range rendered
    assert "[audio] dynaudnorm" in block
    assert "int[10..8000]" in block
    # eq has no schema in this registry — stays description-only
    assert "[video] eq:" not in block


def test_schemas_block_choice_and_bool_kinds():
    block = abl.render_schemas_block()
    # xfade (M10.3) carries a choice schema for its transition
    assert "choice(" in block
    assert "bool" in block  # at least one bool-typed option exists


# ---------------------------------------------------------------- scorer

def test_score_ready_full_match():
    entry = _entry("e1", "resize to 720p", status="ready", ops=["resize"],
                   container="mp4")
    plan = {"operations": [{"type": "resize", "height": 720}],
            "output": {"container": "mp4"}, "trim": None}
    s = abl.score_entry(entry, _res("rules", "e1", "ready", plan))
    assert s == {"status_ok": True, "ops_ok": True,
                 "params_checked": 1, "params_passed": 1}


def test_score_ready_wrong_ops_and_container():
    entry = _entry("e2", "make it black and white", status="ready",
                   ops=["grayscale"], container="webm")
    plan = {"operations": [{"type": "resize"}], "output": {"container": "mp4"},
            "trim": None}
    s = abl.score_entry(entry, _res("llm", "e2", "ready", plan))
    assert s["status_ok"] is True
    assert s["ops_ok"] is False
    assert s["params_passed"] == 0 and s["params_checked"] == 1


def test_score_trim_tolerance():
    entry = _entry("e3", "cut 00:10 to 00:30", status="ready",
                   trim={"start_seconds": 10, "duration_seconds": 20})
    good = {"operations": [], "output": {},
            "trim": {"start_seconds": 10.005, "duration_seconds": 19.999}}
    bad = {"operations": [], "output": {},
           "trim": {"start_seconds": 10.02, "duration_seconds": 20}}
    assert abl.score_entry(entry, _res("rules", "e3", "ready", good))["params_passed"] == 1
    assert abl.score_entry(entry, _res("rules", "e3", "ready", bad))["params_passed"] == 0


def test_score_ready_without_plan_scores_status_only():
    entry = _entry("e4", "resize to 720p", status="ready", ops=["resize"])
    s = abl.score_entry(entry, _res("llm", "e4", "error", None,
                                    errors=["schema: bad"]))
    assert s == {"status_ok": False}


def test_score_unsupported_never_partial_plan():
    entry = _entry("e5", "denoise the clip", status="unsupported")
    ok = abl.score_entry(entry, _res("rules", "e5", "unsupported", None))
    assert ok["unsupported_ok"] is True
    bad = abl.score_entry(entry, _res("llm", "e5", "unsupported",
                                      {"operations": []}))
    assert bad["unsupported_ok"] is False


def test_score_clarification():
    entry = _entry("e6", "make it smaller", status="needs_clarification")
    ok = abl.score_entry(entry, _res("llm", "e6", "needs_clarification", None))
    assert ok["clarification_ok"] is True
    bad = abl.score_entry(entry, _res("llm", "e6", "ready",
                                      {"operations": []}))
    assert bad["status_ok"] is False
    assert bad["clarification_ok"] is False


# ------------------------------------------------------ citation relevance

def _cit(name, text):
    return {"chunk_id": "c1", "component_name": name,
            "component_type": "filter", "text": text}


def test_citation_relevant_when_component_in_plan():
    plan = {"operations": [{"type": "filter", "expr": "loudnorm=I=-16"}]}
    assert abl._citation_relevant(_cit("loudnorm", "irrelevant text"),
                                  plan, "normalize loudness")


def test_citation_relevant_by_token_overlap():
    plan = None
    cit = _cit(None, "the scale filter sets the output width and height "
                      "of the video frame")
    assert abl._citation_relevant(cit, plan, "change the width and height")


def test_citation_irrelevant():
    plan = {"operations": [{"type": "grayscale"}]}
    cit = _cit("overlay", "the overlay filter composites a second video "
                          "on top of the first one")
    assert not abl._citation_relevant(cit, plan, "make it black and white")


# ------------------------------------------------------------- aggregation

def test_aggregate_shapes_and_citation_precision():
    entries = [
        _entry("e1", "normalize loudness", status="ready", ops=["filter"]),
        _entry("e2", "make it black and white", status="ready",
               ops=["grayscale"]),
    ]
    results = [
        _res("llm_schemas_evidence", "e1", "ready",
             {"operations": [{"type": "filter", "expr": "loudnorm"}]},
             evidence_chunks=1,
             citations=[_cit("loudnorm", "loudness normalization")]),
        _res("llm_schemas_evidence", "e2", "ready",
             {"operations": [{"type": "grayscale"}]},
             evidence_chunks=1,
             citations=[_cit("overlay", "compositing")]),
    ]
    m = abl.aggregate(results, entries)["llm_schemas_evidence"]
    assert m["n"] == 2
    assert m["status_acc"] == 1.0
    assert m["ops_acc"] == 1.0
    assert m["evidence_entries"] == 2
    # one of two citations relevant
    assert m["citation_precision"] == 0.5
    assert m["provider_errors"] == 0


def test_load_datasets_skips_missing_and_tags_source():
    entries = abl.load_datasets(
        ["tests/benchmark/requests.json", "tests/benchmark/nope.json"], None)
    assert len(entries) == 91
    assert all(e["_dataset"] == "requests.json" for e in entries)
    assert abl.load_datasets(["tests/benchmark/nope.json"], None) == []
    limited = abl.load_datasets(["tests/benchmark/requests.json"], 5)
    assert [e["id"] for e in limited] == [f"bench-00{i}" for i in range(1, 6)]
