"""Citation derivation (M11.2, PRD §6.4).

After ``plan_execution`` the pipeline derives the response's
``evidence.citations`` from facts it already has — the components the
plan actually uses, and the chunks the RAG search retrieved.
Citations are never model-generated (the model is never asked for
them), so they always trace to stored evidence AND to the actual
command.

Rules (PRD §6.4):
1. Collect used components in first-use order: filters (from the
   plan's graphs — the pipeline's ``_used_filters()``), encoders
   (from the output spec), bitstream filters, format-option keys.
2. Match each against the retrieved chunks' ``component_name`` /
   ``option_names``. One citation per matched component, the
   highest-scoring matching chunk.
3. A used filter with no retrieved evidence but a registry ``doc_ref``
   gets a citation with ``source: "registry_doc_ref"`` — this is the
   static-citation path that works with RAG disabled (PRD §6.5).
4. Retrieved chunks the plan did not use are dropped (precision, no
   "evidence dump" noise).
"""

from __future__ import annotations

from fass.registry import audio_encoder_name, doc_ref, filter_aliases, \
    video_encoder_name


def used_components(edit, used_filters: list[str]) -> list[tuple[str, str]]:
    """The plan's used components as ``(kind, name)`` pairs in first-use
    order: ``filter`` (the plan's graph order), then the output
    encoders, bitstream filters and format-option keys.

    ``edit`` is the EditPlan, ``used_filters`` the pipeline's
    ``_used_filters(ep)`` output (filter names only, deduped, in the
    order the graphs use them)."""
    out = edit.output
    comps: list[tuple[str, str]] = [("filter", n) for n in used_filters]
    if out.video_codec:
        enc = video_encoder_name(out.video_codec, out.gpu, out.qsv)
        if enc:
            comps.append(("encoder", enc))
    if out.audio_codec:
        enc = audio_encoder_name(out.audio_codec)
        if enc:
            comps.append(("encoder", enc))
    if edit.bitstream_filters:
        comps.extend(("bsf", b.name) for b in edit.bitstream_filters)
    comps.extend(("format", k) for k in (out.format_options or {}))

    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for kind, name in comps:
        if (kind, name) not in seen:
            seen.add((kind, name))
            ordered.append((kind, name))
    return ordered


def _filter_match(name: str, chunk: dict) -> bool:
    """A filter chunk matches the plan's filter when the chunk's
    canonical component name is the filter (or either side is an
    alias of the other — 2.5.0 registry)."""
    cname = (chunk.get("component_name") or "").lower()
    if not cname:
        return False
    if cname == name:
        return True
    return bool(name in filter_aliases(cname)
                or cname in filter_aliases(name))


def _matches(kind: str, name: str, chunk: dict) -> bool:
    """PRD §6.4.2 — component vs a chunk's component_name /
    option_names."""
    cname = (chunk.get("component_name") or "").lower()
    ctype = (chunk.get("component_type") or "").lower()
    if kind == "filter":
        return ctype == "filter" and _filter_match(name, chunk)
    if kind in ("encoder", "bsf"):
        return ctype == kind and cname == name.lower()
    if kind == "format":
        return name.lower() in [
            str(o).lower() for o in (chunk.get("option_names") or [])]
    return False


def chunk_citation(chunk: dict, component: str) -> dict:
    """The response citation for one retrieved chunk (PRD §6.4 shape)."""
    text = (chunk.get("text") or "").strip()
    return {
        "chunk_id": chunk.get("chunk_id"),
        "title": chunk.get("title"),
        "url": chunk.get("url"),
        "anchor": chunk.get("anchor"),
        "component": component,
        "source": "retrieved",
        "excerpt_chars": len(text),
    }


def derive_citations(edit, used_filters: list[str],
                     rag_result) -> list[dict]:
    """The response citations (PRD §6.4), one per used component that
    carries evidence:

    - a retrieved chunk matched the component → ``source "retrieved"``
      (the highest-scoring match; ties keep the service's order);
    - otherwise a used filter with a registry ``doc_ref`` →
      ``source "registry_doc_ref"`` (works with RAG disabled).

    ``rag_result`` is the client's ``RagSearchResult`` or None (RAG
    off). Retrieved chunks the plan did not use are dropped by
    construction."""
    chunks: list[dict] = []
    if rag_result is not None and rag_result.ok:
        chunks = rag_result.chunks or []

    citations: list[dict] = []
    for kind, name in used_components(edit, used_filters):
        component = f"{kind}:{name}"
        best: dict | None = None
        best_score: float | None = None
        for chunk in chunks:
            if not _matches(kind, name, chunk):
                continue
            score = chunk.get("score")
            score = float(score) if score is not None else 0.0
            if best is None or score > best_score:
                best, best_score = chunk, score
        if best is not None:
            citations.append(chunk_citation(best, component))
        elif kind == "filter":
            ref = doc_ref(name)
            if ref:
                citations.append({
                    "chunk_id": None,
                    "title": ref.get("title") or name,
                    "url": ref.get("url"),
                    "anchor": ref.get("anchor"),
                    "component": component,
                    "source": "registry_doc_ref",
                    "excerpt_chars": None,
                })
    return citations
