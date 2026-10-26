"""fass-rag HTTP tests (joint PRD 5.5): all five endpoints via TestClient.

Uses the synthetic corpus fixture (tmp_path) through FASS_RAG_DATA_DIR /
FASS_RAG_CORPUS env overrides; the module is re-imported per test so env
config is picked up cleanly.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_rag_retrieval import BUILD_HELP, MANUAL


def _make_corpus(tmp_path: Path) -> Path:
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
def client(tmp_path, monkeypatch):
    _make_corpus(tmp_path)
    monkeypatch.setenv("FASS_RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FASS_RAG_CORPUS", "synth-1.0")
    monkeypatch.setenv("FASS_RAG_MAX_EVIDENCE_CHARS", "4000")
    import rag.server
    importlib.reload(rag.server)
    with TestClient(rag.server.app) as c:
        yield c


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readyz(client):
    body = client.get("/readyz").json()
    assert body["status"] == "ready"
    assert body["corpus_id"] == "synth-1.0"
    assert body["kinds"]["manual"]["chunks"] == 7
    assert body["kinds"]["build-help"]["chunks"] == 2
    assert body["index_build_ms"] >= 0


def test_corpora(client):
    body = client.get("/v1/corpora").json()
    assert body["loaded_corpus_id"] == "synth-1.0"
    (corpus,) = body["corpora"]
    assert corpus["corpus_id"] == "synth-1.0"
    assert corpus["version_status"] == "pinned-release"
    assert corpus["kinds"]["manual"]["active_generation"] == 1
    assert corpus["kinds"]["manual"]["total_chunks"] == 7


def test_search_exact(client):
    resp = client.post("/v1/search", json={
        "query": "remove silence from the audio",
        "corpus_id": "synth-1.0",
        "component_types": ["filter"],
        "component_names": ["silenceremove"],
        "source_kinds": ["manual"],
        "top_k": 6,
        "max_evidence_chars": 4000,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus_match"] == "exact"
    assert body["version_status"] == "pinned-release"
    assert body["evidence_status"] == "supported"
    assert {"type": "filter", "name": "silenceremove"} in body["exact_matches"]
    top = body["chunks"][0]
    assert top["component_name"] == "silenceremove"
    assert top["title"] == "silenceremove"
    assert top["url"] == "https://example.test/doc.html"
    assert top["anchor"] == "silenceremove"
    assert sum(len(c["text"]) for c in body["chunks"]) <= 4000
    assert "omitted" in body and "timing_ms" in body


def test_search_nl_no_filters(client):
    body = client.post("/v1/search", json={"query": "scale_cuda"}).json()
    assert body["evidence_status"] == "supported"
    assert body["exact_matches"] == [{"type": "filter", "name": "scale_cuda"}]


def test_search_corpus_mismatch(client):
    resp = client.post("/v1/search", json={"query": "x", "corpus_id": "ffmpeg-6.1.1"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["corpus_match"] == "missing"
    assert body["loaded_corpus_id"] == "synth-1.0"


def test_search_budget(client):
    body = client.post("/v1/search",
                       json={"query": "silenceremove", "max_evidence_chars": 150}).json()
    assert sum(len(c["text"]) for c in body["chunks"]) <= 150
    assert body["omitted"]["chunks"] > 0


def test_component_lookup(client):
    resp = client.get("/v1/components/filter/loudnorm")
    assert resp.status_code == 200
    body = resp.json()
    assert {c["chunk_id"] for c in body["chunks"]} == {"m-loudnorm-1", "m-loudnorm-2"}
    assert all(c["component_name"] == "loudnorm" for c in body["chunks"])


def test_component_lookup_missing(client):
    resp = client.get("/v1/components/filter/nosuchfilter")
    assert resp.status_code == 404
    assert "error" in resp.json()
