"""fass-rag HTTP service (joint PRD 5.5 API contract; ops per 5.6, deploy 6.7).

FastAPI app exposing the five PRD 5.5 endpoints over the lexical Retriever
(rag/retrieval.py). CPU-only, localhost-bound, no network calls, no docker
socket. Configuration via environment (PRD 5.6 surface, lexical subset):

    FASS_RAG_DATA_DIR          corpus root dir (default: rag/corpus)
    FASS_RAG_CORPUS            corpus to load (default: ffmpeg-9.0.1)
    FASS_RAG_PORT              listen port (default: 8100)
    FASS_RAG_MAX_EVIDENCE_CHARS default evidence budget (default: 4000)
    FASS_RAG_LOG_LEVEL          uvicorn log level (default: info)

Run: ``python -m rag.server`` (binds 127.0.0.1).

Corpus-mismatch decision (PRD 5.5 leaves it open): a request naming a
corpus_id other than the loaded one returns HTTP 404 with a structured JSON
error carrying ``corpus_match: "missing"`` — the service loads exactly one
corpus per process, and refusing is safer than silently answering from the
wrong pinned version.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rag.retrieval import (
    DEFAULT_MAX_EVIDENCE_CHARS,
    KINDS,
    MAX_TOP_K,
    CorpusMismatch,
    Retriever,
)

DATA_DIR = Path(os.environ.get("FASS_RAG_DATA_DIR", "rag/corpus"))
CORPUS = os.environ.get("FASS_RAG_CORPUS", "ffmpeg-9.0.1")
PORT = int(os.environ.get("FASS_RAG_PORT", "8100"))
MAX_EVIDENCE_CHARS = int(os.environ.get("FASS_RAG_MAX_EVIDENCE_CHARS",
                                        str(DEFAULT_MAX_EVIDENCE_CHARS)))
LOG_LEVEL = os.environ.get("FASS_RAG_LOG_LEVEL", "info")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=LOG_LEVEL.upper())
    app.state.retriever = Retriever(DATA_DIR / CORPUS)
    yield
    app.state.retriever = None


app = FastAPI(title="fass-rag", version="0.1.0", lifespan=lifespan)


class SearchRequest(BaseModel):
    query: str
    corpus_id: str | None = None
    component_types: list[str] | None = None
    component_names: list[str] | None = None
    source_kinds: list[str] | None = None
    top_k: int = Field(default=6, ge=1, le=MAX_TOP_K)
    max_evidence_chars: int | None = Field(default=None, ge=1)


def _corpora_index() -> list[dict]:
    """Scan DATA_DIR for corpus dirs with a manifest (PRD 5.5 /v1/corpora)."""
    out = []
    if not DATA_DIR.is_dir():
        return out
    for child in sorted(DATA_DIR.iterdir()):
        manifest = child / "manifest.json"
        if not child.is_dir() or not manifest.is_file():
            continue
        m = json.loads(manifest.read_text())
        kinds = {}
        for kind in KINDS:
            gpath = child / "chunks" / kind / "generations.json"
            if not gpath.is_file():
                continue
            g = json.loads(gpath.read_text())
            active = int(g["active"])
            entry = next((h for h in g["history"] if h["generation"] == active), {})
            kinds[kind] = {"active_generation": active,
                           "total_chunks": entry.get("total_chunks"),
                           "source_kind": kind}
        out.append({
            "corpus_id": m["corpus_id"],
            "version_status": m.get("version_status"),
            "loaded": m["corpus_id"] == app.state.retriever.corpus_id
            if getattr(app.state, "retriever", None) else False,
            "kinds": kinds,
        })
    return out


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(request: Request):
    r = getattr(request.app.state, "retriever", None)
    if r is None:
        return JSONResponse(status_code=503, content={"status": "not-ready"})
    return {
        "status": "ready",
        "corpus_id": r.corpus_id,
        "version_status": r.version_status,
        "kinds": {k: {"chunks": n, "active_generation": r.kind_generations[k]}
                  for k, n in r.kind_counts.items()},
        "index_build_ms": r.index_build_ms,
    }


@app.get("/v1/corpora")
def corpora():
    return {"corpora": _corpora_index(), "loaded_corpus_id": CORPUS}


@app.post("/v1/search")
def search(req: SearchRequest):
    r = app.state.retriever
    try:
        result = r.search(
            req.query,
            corpus_id=req.corpus_id,
            component_types=req.component_types,
            component_names=req.component_names,
            source_kinds=req.source_kinds,
            top_k=req.top_k,
            max_evidence_chars=req.max_evidence_chars or MAX_EVIDENCE_CHARS,
        )
    except CorpusMismatch as e:
        return JSONResponse(status_code=404, content={
            "error": f"corpus {e.requested!r} not loaded",
            "corpus_match": "missing",
            "requested_corpus_id": e.requested,
            "loaded_corpus_id": e.loaded,
        })
    return result.response()


@app.get("/v1/components/{component_type}/{component_name}")
def component(component_type: str, component_name: str):
    r = app.state.retriever
    chunks = r.get_component(component_type, component_name)
    if not chunks:
        return JSONResponse(status_code=404, content={
            "error": f"no chunks for component {component_type}/{component_name}",
            "corpus_id": r.corpus_id,
        })
    return {
        "corpus_id": r.corpus_id,
        "version_status": r.version_status,
        "component_type": component_type,
        "component_name": component_name,
        "chunks": [c.response(1.0) for c in chunks],
    }


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level=LOG_LEVEL)


if __name__ == "__main__":
    main()
