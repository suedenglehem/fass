"""Ingest CLI tests (PRD 5.2): drift gate, immutable generations, dedup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rag import PARSER_VERSION
from rag.ingest import KINDS, ingest_corpus, main

FIXTURES = Path(__file__).resolve().parent.parent / "rag" / "fixtures"

CORPUS_ID = "test-1.0"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_corpus(root: Path) -> Path:
    """A minimal two-kind corpus: the filter_options fixture as the one
    manual doc, and a build-facts file with two identical paragraphs
    (dedup case, PRD 6)."""
    docs = root / "docs"
    docs.mkdir(parents=True)
    bf = root / "build-facts"
    bf.mkdir()

    html = (FIXTURES / "filter_options.html").read_bytes()
    (docs / "filter_options.html").write_bytes(html)
    (root / "manifest.json").write_text(json.dumps({
        "corpus_id": CORPUS_ID,
        "version_status": "pinned-release",
        "source_kind": "manual",
        "source": {"type": "test"},
        "built_at": "2026-01-01T00:00:00Z",
        "documents": [{"name": "filter_options.html",
                       "sha256": _sha(html), "bytes": len(html)}],
    }), encoding="utf-8")

    para = " ".join(f"tok{i}" for i in range(400))  # 400 tokens
    facts = f"{para}\n\n{para}\n".encode("utf-8")
    (bf / "version.txt").write_bytes(facts)
    (bf / "manifest.json").write_text(json.dumps({
        "corpus_id": CORPUS_ID + "-build-help",
        "version_status": "pinned-release",
        "source_kind": "build_help",
        "source": {"type": "test"},
        "built_at": "2026-01-01T00:00:00Z",
        "documents": [{"name": "version.txt",
                       "sha256": _sha(facts), "bytes": len(facts)}],
    }), encoding="utf-8")
    return root


def _gen_bytes(root: Path, kind: str, gen: int) -> bytes:
    return (root / "chunks" / kind.replace("_", "-") /
            f"gen-{gen}" / "chunks.jsonl").read_bytes()


def _read_gen_registry(root: Path, kind: str) -> dict:
    return json.loads(
        (root / "chunks" / kind.replace("_", "-") /
         "generations.json").read_text(encoding="utf-8"))


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    return _make_corpus(tmp_path / "corpus")


# ------------------------------------------------------------- happy path


def test_first_ingest_creates_generation_1(corpus: Path):
    results = ingest_corpus(corpus, list(KINDS))
    by_kind = {r["kind"]: r for r in results}
    assert by_kind["manual"]["generation"] == 1
    # Description + Video Filters + tflip, one chunk each
    assert by_kind["manual"]["total_chunks"] == 3
    assert by_kind["build_help"]["generation"] == 1
    # dedup (PRD 6): two identical 400-token paragraphs -> one chunk
    assert by_kind["build_help"]["total_chunks"] == 1

    reg = _read_gen_registry(corpus, "manual")
    assert reg["active"] == 1
    assert reg["history"][0]["parser_version"] == PARSER_VERSION
    assert reg["history"][0]["documents"] == 1

    # manifest updated atomically with the new generation
    m = json.loads((corpus / "manifest.json").read_text())
    assert m["parser_version"] == PARSER_VERSION
    assert m["total_chunks"] == 3

    # chunks.jsonl carries the full 14-field record
    line = _gen_bytes(corpus, "manual", 1).splitlines()[0]
    rec = json.loads(line)
    assert set(rec) >= {"chunk_id", "corpus_id", "source_url", "anchor",
                        "heading_path", "component_type", "component_name",
                        "option_names", "text", "parent_id", "content_hash",
                        "version_status", "source_kind"}
    assert rec["corpus_id"] == CORPUS_ID


def test_reingest_writes_new_generation_previous_untouched(corpus: Path):
    ingest_corpus(corpus, list(KINDS))
    gen1 = _gen_bytes(corpus, "manual", 1)
    ingest_corpus(corpus, list(KINDS))
    # generation 2 activated; generation 1 bytes byte-identical (rollback)
    assert _gen_bytes(corpus, "manual", 1) == gen1
    reg = _read_gen_registry(corpus, "manual")
    assert reg["active"] == 2
    assert [e["generation"] for e in reg["history"]] == [1, 2]


def test_cli_main_happy_path(corpus: Path):
    rc = main(["--corpus-dir", str(corpus), "--kinds", "manual"])
    assert rc == 0
    assert _read_gen_registry(corpus, "manual")["active"] == 1
    # build_help untouched: no registry created
    assert not (corpus / "chunks" / "build-help" /
                "generations.json").exists()


# ----------------------------------------------------------- failure gates


def test_explicit_generation_collision_is_rejected(corpus: Path):
    ingest_corpus(corpus, ["manual"])
    with pytest.raises(ValueError, match="immutable"):
        ingest_corpus(corpus, ["manual"], generation=1)


def test_hash_drift_gate(corpus: Path):
    (corpus / "docs" / "filter_options.html").write_bytes(
        b"<p>corrupted</p>\n")
    with pytest.raises(ValueError, match="hash drift"):
        ingest_corpus(corpus, ["manual"])


def test_missing_document_gate(corpus: Path):
    (corpus / "docs" / "filter_options.html").unlink()
    with pytest.raises(ValueError, match="document missing"):
        ingest_corpus(corpus, ["manual"])


def test_no_manifest_for_kind(corpus: Path):
    (corpus / "build-facts" / "manifest.json").unlink()
    with pytest.raises(ValueError, match="no manifest for kind"):
        ingest_corpus(corpus, ["build_help"])


def test_cli_reports_failure_nonzero(corpus: Path):
    (corpus / "docs" / "filter_options.html").unlink()
    rc = main(["--corpus-dir", str(corpus), "--kinds", "manual"])
    assert rc == 1


def test_not_a_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="not a directory"):
        ingest_corpus(tmp_path / "absent", list(KINDS))
