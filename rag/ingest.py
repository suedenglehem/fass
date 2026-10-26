"""Operator ingestion CLI for RAG corpora (PRD 5.2).

    python -m rag.ingest --corpus-dir rag/corpus/ffmpeg-9.0.1
    python -m rag.ingest --corpus-dir DIR --corpus-dir DIR2
    python -m rag.ingest --corpus-dir DIR --kinds manual --generation 2

Ingestion is an operator action only — never called by the serving path.
Per corpus directory, the two source kinds are independent identities:

    manual      docs/*.html            manifest at <dir>/manifest.json
    build_help  build-facts/*.txt      manifest at <dir>/build-facts/manifest.json

Per identity this CLI:
  1. verifies every document against its manifest sha256 (corpus drift gate)
  2. parses + chunks it (rag.parser / rag.chunker)
  3. validates the chunk set (required metadata, hashes, uniqueness)
  4. writes an IMMUTABLE generation dir chunks/<kind>/gen-<N>/chunks.jsonl
     (error if it already exists — generations are never rewritten)
  5. activates it atomically via chunks/<kind>/generations.json
     (previous generation stays on disk for rollback)
  6. updates the manifest (parser_version, total_chunks) atomically

No network access at any point.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

from . import PARSER_VERSION
from .chunker import chunk_document, chunk_text_file
from .parser import parse_file

KINDS = ("manual", "build_help")

REQUIRED_KEYS = (
    "chunk_id", "corpus_id", "source_url", "anchor", "heading_path",
    "component_type", "component_name", "option_names", "text",
    "parent_id", "content_hash", "version_status", "source_kind",
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(corpus_dir: Path, kind: str):
    """(manifest_path, doc_dir, corpus_id) for one source kind, or None."""
    if kind == "manual":
        mpath = corpus_dir / "manifest.json"
        if not mpath.exists():
            return None
        m = _load_manifest(mpath)
        return mpath, corpus_dir / "docs", m["corpus_id"]
    mpath = corpus_dir / "build-facts" / "manifest.json"
    if not mpath.exists():
        return None
    m = _load_manifest(mpath)
    return mpath, corpus_dir / "build-facts", m["corpus_id"]


def _verify_documents(manifest: dict, doc_dir: Path, kind: str) -> list[Path]:
    """Check every manifest document exists and matches its sha256."""
    paths: list[Path] = []
    for doc in manifest["documents"]:
        p = doc_dir / doc["name"]
        if not p.is_file():
            raise ValueError(f"document missing: {p}")
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != doc["sha256"]:
            raise ValueError(
                f"document hash drift: {p.name} manifest={doc['sha256'][:12]}… "
                f"actual={actual[:12]}…")
        paths.append(p)
    return paths


def _build_chunks(kind: str, corpus_id: str, doc_dir: Path) -> list[dict]:
    if kind == "manual":
        chunks = []
        for p in sorted(doc_dir.glob("*.html")):
            doc = parse_file(p)
            chunks.extend(
                c.as_dict() for c in chunk_document(doc, corpus_id))
    else:  # build_help
        chunks = []
        for p in sorted(doc_dir.glob("*.txt")):
            chunks.extend(c.as_dict()
                          for c in chunk_text_file(p, corpus_id))
    # dedup (PRD 6): identical text carries no extra information — e.g.
    # `ffmpeg -h full` prints the shared mpegvideo encoder AVOptions block
    # once per mpegvideo-based encoder. Keyed per source document (manual
    # name / facts file) so same text in different sources is kept.
    # First occurrence wins, order kept.
    seen: set[tuple[str, str]] = set()
    out = []
    for c in chunks:
        src = c["source_url"] or c["heading_path"][0]
        key = (src, c["content_hash"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _validate_chunks(kind: str, corpus_id: str, chunks: list[dict]) -> None:
    if not chunks:
        raise ValueError(f"{kind}: no chunks produced")
    seen: set[str] = set()
    for c in chunks:
        for k in REQUIRED_KEYS:
            if k not in c:
                raise ValueError(f"{kind}: chunk missing key {k!r}")
        if not c["text"].strip():
            raise ValueError(f"{kind}: empty chunk text {c['chunk_id']}")
        if c["corpus_id"] != corpus_id:
            raise ValueError(f"{kind}: corpus_id mismatch {c['chunk_id']}")
        if c["source_kind"] != kind:
            raise ValueError(f"{kind}: source_kind mismatch {c['chunk_id']}")
        want = hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
        if c["content_hash"] != want:
            raise ValueError(f"{kind}: content_hash mismatch {c['chunk_id']}")
        if c["chunk_id"] in seen:
            raise ValueError(f"{kind}: duplicate chunk_id {c['chunk_id']}")
        seen.add(c["chunk_id"])


def ingest_identity(corpus_dir: Path, kind: str, generation: int | None) -> dict:
    """Ingest one source kind of one corpus. Returns a summary dict."""
    ident = _identity(corpus_dir, kind)
    if ident is None:
        raise ValueError(f"{corpus_dir}: no manifest for kind {kind!r}")
    manifest_path, doc_dir, corpus_id = ident
    manifest = _load_manifest(manifest_path)

    docs = _verify_documents(manifest, doc_dir, kind)
    chunks = _build_chunks(kind, corpus_id, doc_dir)
    _validate_chunks(kind, corpus_id, chunks)

    kind_dir = corpus_dir / "chunks" / kind.replace("_", "-")
    # resolve the generation number: explicit, or active+1
    gens_path = kind_dir / "generations.json"
    gens = (json.loads(gens_path.read_text())
            if gens_path.exists() else {"active": 0, "history": []})
    if generation is None:
        generation = gens["active"] + 1
    gen_dir = kind_dir / f"gen-{generation}"
    if gen_dir.exists():
        raise ValueError(f"generation dir already exists (immutable): {gen_dir}")

    gen_dir.mkdir(parents=True)
    out = gen_dir / "chunks.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # activate atomically; previous generation stays for rollback
    gens["active"] = generation
    entry = {"generation": generation, "created_at": _now(),
             "documents": len(docs), "total_chunks": len(chunks),
             "parser_version": PARSER_VERSION}
    if any(e["generation"] == generation for e in gens["history"]):
        raise ValueError(f"generation {generation} already in history")
    gens["history"].append(entry)
    _atomic_write_json(gens_path, gens)

    # manifest update (parser_version, total_chunks) — atomic
    manifest["parser_version"] = PARSER_VERSION
    manifest["total_chunks"] = len(chunks)
    _atomic_write_json(manifest_path, manifest)

    return {"corpus_dir": str(corpus_dir), "kind": kind,
            "corpus_id": corpus_id, "generation": generation,
            "documents": len(docs), "total_chunks": len(chunks),
            "output": str(out)}


def ingest_corpus(corpus_dir: Path, kinds: list[str],
                  generation: int | None = None) -> list[dict]:
    corpus_dir = corpus_dir.resolve()
    if not corpus_dir.is_dir():
        raise ValueError(f"not a directory: {corpus_dir}")
    results = []
    for kind in kinds:
        r = ingest_identity(corpus_dir, kind, generation)
        results.append(r)
        print(f"  {kind:11s} gen {r['generation']}: {r['documents']} docs -> "
              f"{r['total_chunks']} chunks -> {r['output']}")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m rag.ingest",
        description="Parse + chunk a RAG corpus into a new immutable index "
                    "generation (operator action only).")
    ap.add_argument("--corpus-dir", action="append", required=True,
                    metavar="DIR",
                    help="corpus directory (rag/corpus/ffmpeg-<v>); repeatable")
    ap.add_argument("--kinds", default=",".join(KINDS),
                    help=f"comma list of {','.join(KINDS)} (default: both)")
    ap.add_argument("--generation", type=int, default=None,
                    help="generation number (default: active+1)")
    args = ap.parse_args(argv)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    for k in kinds:
        if k not in KINDS:
            ap.error(f"unknown kind {k!r} (choose from {', '.join(KINDS)})")
    rc = 0
    for d in args.corpus_dir:
        print(f"ingest: {d}")
        try:
            ingest_corpus(Path(d), kinds, args.generation)
        except ValueError as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
