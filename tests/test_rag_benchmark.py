"""Benchmark tests (PRD 9.1): generator determinism, committed query-set
shape, and — skipif-guarded on the gitignored corpus chunks — the full
all-gates run.

The full run is the real M11.1B gate; it needs both corpora's active
generations on disk, so it is skipped when the corpus data is absent
(same pattern as tests/test_rag_retrieval.py's real-corpus smoke).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.benchmark.generate_queries import generate
from rag.benchmark.run_benchmark import run_benchmark

HERE = Path(__file__).resolve().parent.parent / "rag" / "benchmark"
REAL_CORPUS = Path(__file__).resolve().parent.parent / "rag" / "corpus"
CORPORA = ("ffmpeg-6.1.1", "ffmpeg-9.0.1")
CATEGORIES = ("exact", "option", "nl", "mismatch", "insufficient")
REQUIRED_KEYS = ("id", "category", "corpus", "query", "expect_top1",
                 "relevant", "relevant_anchors", "novel_tokens", "note")


def _rows() -> list[dict]:
    return [json.loads(line) for line in
            (HERE / "queries.jsonl").read_text().splitlines() if line.strip()]


def test_generator_deterministic(tmp_path: Path):
    outs = []
    for i in range(2):
        out = tmp_path / f"run{i}"
        generate(out_dir=out)
        outs.append(((out / "queries.jsonl").read_bytes(),
                     (out / "dataset.json").read_bytes()))
    assert outs[0] == outs[1], "generate() is not byte-deterministic"


def test_queries_jsonl_shape():
    rows = _rows()
    dataset = json.loads((HERE / "dataset.json").read_text())
    assert dataset["total"] == len(rows)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    assert counts == dataset["counts"]
    assert set(counts) == set(CATEGORIES)
    # unique ids
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        for k in REQUIRED_KEYS:
            assert k in r, f"{r.get('id')}: missing key {k!r}"
        assert r["corpus"] in CORPORA, r["id"]
        assert r["query"].strip(), r["id"]
        # mismatch = queried against the older corpus, with novel tokens
        if r["category"] == "mismatch":
            assert r["corpus"] == "ffmpeg-6.1.1", r["id"]
            assert r["novel_tokens"], r["id"]
            assert r["relevant"] == [] or r["expect_top1"], r["id"]
        # exact: top-1 expectation is consistent with the relevant set
        if r["category"] == "exact" and r["expect_top1"]:
            assert r["expect_top1"] in r["relevant"], r["id"]


@pytest.mark.skipif(
    any(not (REAL_CORPUS / cid / "chunks" / kind).is_dir()
        for cid in CORPORA for kind in ("manual", "build-help")),
    reason="real corpus chunks not present (gitignored data)")
def test_full_benchmark_all_gates(tmp_path: Path):
    results = run_benchmark(out_path=tmp_path / "results.json")
    failed = {name: g for name, g in results["gates"].items()
              if not g["pass"]}
    assert not failed, f"benchmark gates failed: {json.dumps(failed, indent=1)}"
