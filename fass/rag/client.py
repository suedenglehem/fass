"""Fail-open HTTP client for the fass-rag service (M11.2, PRD §4.4/§6.2).

One call per LLM-NLU generation:

1. ``GET /v1/corpora`` — the corpus list + the loaded corpus, used for
   client-side corpus selection (the server serves exactly one corpus and
   reports ``corpus_match`` hardcoded to "exact"; the real quality label
   is computed by the query builder). If this lookup fails, the search
   still proceeds with the service default and a warning.
2. ``POST /v1/search`` — the deterministic request from query.py.

**Every failure degrades to a RAG-less generation** (PRD §4.4): timeouts,
connection errors, non-200 responses and malformed bodies all return
``ok=False`` with a human-readable warning; this module never raises to
the caller. A 404 corpus mismatch is reported with the service's own
"not loaded" text, not treated as a search hit of any kind.

Stdlib only (urllib). The opener is injectable so the fail-open paths
are unit-testable without a running service.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from fass.config import RagConfig, resolved_rag

from .query import RagQuery, build_query


@dataclass
class RagSearchResult:
    """The outcome of one RAG call (ok or degraded).

    ``corpus_match`` / ``corpus_warnings`` come from the query builder
    (the client knows the probed build; the server only knows what it
    loaded). ``evidence_status`` / ``chunks`` / ``retrieval_ms`` come
    from the service. When ``ok`` is False, ``chunks`` is empty and the
    generation proceeds without evidence — the response's evidence block
    records the ``warnings`` and status "unavailable"."""

    ok: bool
    query: RagQuery | None = None
    corpus_id: str | None = None
    corpus_match: str = "none"
    evidence_status: str = ""
    chunks: list = field(default_factory=list)
    retrieval_ms: int = 0
    warnings: list = field(default_factory=list)


def _short_error(e: Exception) -> str:
    """One warning phrase for a transport error (never the raw traceback)."""
    reason = getattr(e, "reason", None)
    if isinstance(reason, BaseException):
        reason = getattr(reason, "strerror", None) or type(reason).__name__
    if reason:
        return str(reason)
    return type(e).__name__


def search(
    cfg: RagConfig,
    instruction: str,
    media=None,
    probed_version: str | None = None,
    *,
    opener=None,
) -> RagSearchResult:
    """Run the two-step RAG call. Never raises; see the module docstring."""
    opener = opener or urllib.request.urlopen
    base = cfg.url.rstrip("/")
    headers = {"Accept": "application/json"}
    if cfg.auth_token:
        headers["Authorization"] = f"Bearer {cfg.auth_token}"
    timeout = cfg.timeout_ms / 1000.0
    warnings: list[str] = []

    # Step 1 — corpus metadata (for client-side corpus selection).
    available: list[str] | None = None
    loaded: str | None = None
    try:
        req = urllib.request.Request(base + "/v1/corpora", headers=headers)
        with opener(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        available = [c.get("corpus_id") for c in body.get("corpora", [])
                     if c.get("corpus_id")]
        loaded = body.get("loaded_corpus_id")
    except Exception as e:  # noqa: BLE001 — fail-open, every failure
        warnings.append(f"RAG corpus lookup failed ({_short_error(e)}); "
                        f"using the service default corpus")

    q = build_query(
        instruction, media=media, probed_version=probed_version,
        available_corpora=available, loaded_corpus=loaded, rag=cfg)
    warnings.extend(q.corpus_warnings)

    # Step 2 — the search itself.
    body = json.dumps(q.to_request()).encode("utf-8")
    req = urllib.request.Request(
        base + "/v1/search", data=body, method="POST",
        headers={**headers, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("not a JSON object")
        return RagSearchResult(
            ok=True,
            query=q,
            corpus_id=payload.get("corpus_id") or q.corpus_id,
            corpus_match=q.corpus_match,
            evidence_status=payload.get("evidence_status", ""),
            chunks=payload.get("chunks") or [],
            retrieval_ms=int(payload.get("timing_ms") or 0),
            warnings=warnings,
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                payload = {}
            if payload.get("corpus_match") == "missing":
                warnings.append(
                    f"RAG corpus {q.corpus_id!r} is not loaded by the service "
                    f"(it has {payload.get('loaded_corpus_id')!r})")
            else:
                warnings.append("RAG service returned 404 for the search")
        else:
            warnings.append(f"RAG service returned HTTP {e.code}")
    except Exception as e:  # noqa: BLE001 — fail-open, every failure
        warnings.append(f"RAG search failed ({_short_error(e)})")
    return RagSearchResult(
        ok=False, query=q, corpus_id=q.corpus_id,
        corpus_match=q.corpus_match, warnings=warnings)


def retrieve(
    cfg: RagConfig | None,
    instruction: str,
    media=None,
    probed_version: str | None = None,
    *,
    opener=None,
) -> RagSearchResult | None:
    """The pipeline's single RAG entry point.

    Returns None when RAG is off — the rules path and disabled RAG never
    touch the service (PRD §6.5). Otherwise the resolved config's
    env/file values decide."""
    resolved = cfg if cfg is not None else resolved_rag()
    if not resolved.enabled:
        return None
    return search(resolved, instruction, media=media,
                  probed_version=probed_version, opener=opener)
