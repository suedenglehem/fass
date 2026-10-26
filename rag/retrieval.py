"""Lexical retrieval core for the fass-rag service (joint PRD 5.4 Retrieval).

Lexical-first gate (PRD 5.4): stdlib sqlite3 + FTS5 only — no embeddings, no
hybrid fusion, no reciprocal rank fusion, no network. Scores are ranking
signals, never probabilities of correctness.

Pipeline (PRD 5.4, lexical half): corpus/component filters -> 30 FTS5 lexical
candidates -> exact component/option boosts (FFmpeg identifiers: tokens
starting with '-', tokens equal to a component_name, tokens appearing in
option_names, dotted ``component.option`` forms) -> dedupe -> parent/adjacent
expansion -> top_k (max 8) bounded by the caller's character budget.

Evidence-status thresholds (documented decision, PRD 5.4 leaves them open):
    insufficient  no candidate chunks after filters
    supported     a returned chunk carries an exact component/option token
                  match, OR the best raw bm25 hit is >= STRONG_BM25
    partial       candidates exist but none is an exact match and none is a
                  strong hit

Exact lookup by (component_type, component_name) returns every chunk of that
component. ``mentioned_components`` lists {type, name} of corpus-known
components whose names appear in the returned chunk texts.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("manual", "build-help")
LEXICAL_CANDIDATES = 30          # PRD 5.4: 30 lexical candidates
MAX_TOP_K = 8                    # PRD 5.4: up to 8 final chunks
DEFAULT_TOP_K = 6
DEFAULT_MAX_EVIDENCE_CHARS = 4000  # PRD 9.5 evidence budget

# Scoring: raw bm25 (SQLite ``rank`` is negative; base = -rank) plus boosts.
STRONG_BM25 = 5.0                # base >= this counts as a strong lexical hit
EXACT_COMPONENT_BOOST = 10.0     # query token == component_name
EXACT_OPTION_BOOST = 6.0         # query token == an option_name
EXACT_DOTTED_BOOST = 12.0        # token "component.option" matches both
DASH_TOKEN_BOOST = 4.0           # "-map" style token present in chunk text
EXPANSION_SCORE_FACTOR = 0.9     # parent/adjacent chunks rank below their seed

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")
_MAX_QUERY_TOKENS = 16


class CorpusMismatch(Exception):
    """Requested corpus_id is not the corpus loaded by this Retriever."""

    def __init__(self, requested: str, loaded: str):
        self.requested = requested
        self.loaded = loaded
        super().__init__(f"corpus {requested!r} not loaded (loaded {loaded!r})")


@dataclass
class Chunk:
    chunk_id: str
    corpus_id: str
    source_url: str
    anchor: str
    heading_path: list
    component_type: str | None
    component_name: str | None
    option_names: list
    text: str
    parent_id: str | None
    content_hash: str
    version_status: str
    source_kind: str
    level: int
    oversized: bool
    seq: int = 0

    def response(self, score: float) -> dict:
        """Chunk shape per PRD 5.5 (title = component_name or last heading;
        url = source_url without its #anchor; stored ids kept verbatim)."""
        return {
            "chunk_id": self.chunk_id,
            "title": self.component_name or (self.heading_path[-1] if self.heading_path else ""),
            "heading_path": self.heading_path,
            "url": self.source_url.split("#", 1)[0],
            "anchor": self.anchor,
            "component_type": self.component_type,
            "component_name": self.component_name,
            "option_names": self.option_names,
            "text": self.text,
            "score": round(score, 4),
            "parent_id": self.parent_id,
        }


@dataclass
class _Hit:
    chunk: Chunk
    base: float          # bm25-derived base score (0 for expansion chunks)
    boost: float         # exact-match boosts
    exact: bool          # exact component/option token match fired
    expansion: bool = False

    @property
    def score(self) -> float:
        return self.base + self.boost


@dataclass
class SearchResult:
    query: str
    corpus_id: str
    version_status: str
    corpus_match: str
    evidence_status: str
    chunks: list = field(default_factory=list)
    exact_matches: list = field(default_factory=list)
    mentioned_components: list = field(default_factory=list)
    omitted: dict = field(default_factory=dict)
    timing_ms: int = 0

    def response(self) -> dict:
        return {
            "query": self.query,
            "corpus_id": self.corpus_id,
            "version_status": self.version_status,
            "corpus_match": self.corpus_match,
            "evidence_status": self.evidence_status,
            "chunks": self.chunks,
            "exact_matches": self.exact_matches,
            "mentioned_components": self.mentioned_components,
            "omitted": self.omitted,
            "timing_ms": self.timing_ms,
        }


def _tokenize(query: str) -> list[dict]:
    """Extract FFmpeg-aware tokens. Each token: core (lower, dashes stripped),
    dotted split (component part / option part), dash flag."""
    tokens = []
    for raw in _TOKEN_RE.findall(query):
        core = raw.lower().lstrip("-")
        if not core:
            continue
        parts_all = [p for p in re.split(r"[_.\-]+", core) if p]
        tokens.append({
            "raw": raw,
            "core": core,
            "dash": raw.startswith("-"),
            "parts": [p for p in parts_all if len(p) >= 2],  # for MATCH
            "parts_all": parts_all,                          # for dotted boosts
            "dotted": "." in core,
        })
    return tokens[:_MAX_QUERY_TOKENS]


def _fts_quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _match_query(tokens: list[dict]) -> str:
    """FTS5 MATCH string: quoted phrases, prefix-expanded, OR-joined."""
    phrases = []
    for t in tokens:
        for p in t["parts"] or [t["core"]]:
            phrases.append(_fts_quote(p) + ("*" if len(p) >= 3 else ""))
    return " OR ".join(dict.fromkeys(phrases))  # dedupe, keep order


class Retriever:
    """Loads one corpus directory (manifest + active generation of BOTH
    kinds) into an in-memory FTS5 index and answers lexical searches."""

    def __init__(self, corpus_dir: str | Path):
        t0 = time.perf_counter()
        self.corpus_dir = Path(corpus_dir)
        manifest_path = self.corpus_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"no manifest.json under {self.corpus_dir}")
        self.manifest = json.loads(manifest_path.read_text())
        self.corpus_id = self.manifest["corpus_id"]
        self.version_status = self.manifest.get("version_status", "pinned-release")

        self.chunks: list[Chunk] = []
        self.kind_counts: dict[str, int] = {}
        self.kind_generations: dict[str, int] = {}
        for kind in KINDS:
            kind_dir = self.corpus_dir / "chunks" / kind
            gens = json.loads((kind_dir / "generations.json").read_text())
            active = int(gens["active"])
            self.kind_generations[kind] = active
            rows = []
            path = kind_dir / f"gen-{active}" / "chunks.jsonl"
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                rows.append(Chunk(
                    chunk_id=d["chunk_id"],
                    corpus_id=d["corpus_id"],
                    source_url=d["source_url"],
                    anchor=d["anchor"],
                    heading_path=list(d["heading_path"]),
                    component_type=d["component_type"],
                    component_name=d["component_name"],
                    option_names=list(d["option_names"]),
                    text=d["text"],
                    parent_id=d["parent_id"],
                    content_hash=d["content_hash"],
                    version_status=d["version_status"],
                    source_kind=d["source_kind"],
                    level=d["level"],
                    oversized=d["oversized"],
                ))
            self.kind_counts[kind] = len(rows)
            self.chunks.extend(rows)

        for seq, c in enumerate(self.chunks):
            c.seq = seq

        self._by_id = {c.chunk_id: c for c in self.chunks}
        self._by_component: dict[tuple[str, str], list[Chunk]] = {}
        self._known_components: dict[str, tuple[str, str]] = {}  # lower name -> (type, name)
        for c in self.chunks:
            if c.component_type and c.component_name:
                self._by_component.setdefault(
                    (c.component_type, c.component_name.lower()), []).append(c)
                self._known_components.setdefault(c.component_name.lower(),
                                                  (c.component_type, c.component_name))

        # check_same_thread=False: the FastAPI threadpool serves endpoints off
        # the thread that built the index (tests/test_rag_server.py).
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5("
            "text, title, options, "
            "chunk_id UNINDEXED, corpus_id UNINDEXED, source_url UNINDEXED, "
            "anchor UNINDEXED, component_type UNINDEXED, component_name UNINDEXED, "
            "parent_id UNINDEXED, version_status UNINDEXED, source_kind UNINDEXED, "
            "option_names_json UNINDEXED, heading_path_json UNINDEXED, "
            "level UNINDEXED, oversized UNINDEXED, content_hash UNINDEXED, "
            "seq UNINDEXED)"
        )
        self._conn.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    c.text,
                    " ".join(filter(None, [c.component_name,
                                           c.heading_path[-1] if c.heading_path else None])),
                    " ".join(c.option_names),
                    c.chunk_id, c.corpus_id, c.source_url, c.anchor,
                    c.component_type, c.component_name, c.parent_id,
                    c.version_status, c.source_kind,
                    json.dumps(c.option_names), json.dumps(c.heading_path),
                    c.level, int(c.oversized), c.content_hash, c.seq,
                )
                for c in self.chunks
            ],
        )
        self._conn.commit()
        self.index_build_ms = int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    # Exact lookup (PRD 5.5 GET /v1/components/{type}/{name})
    # ------------------------------------------------------------------
    def get_component(self, component_type: str, component_name: str) -> list[Chunk]:
        return list(self._by_component.get((component_type, component_name.lower()), []))

    # ------------------------------------------------------------------
    # Search (PRD 5.4)
    # ------------------------------------------------------------------
    def search(self, query: str, corpus_id: str | None = None,
               component_types: list[str] | None = None,
               component_names: list[str] | None = None,
               source_kinds: list[str] | None = None,
               top_k: int = DEFAULT_TOP_K,
               max_evidence_chars: int = DEFAULT_MAX_EVIDENCE_CHARS) -> SearchResult:
        t0 = time.perf_counter()
        if corpus_id is not None and corpus_id != self.corpus_id:
            raise CorpusMismatch(corpus_id, self.corpus_id)
        top_k = max(1, min(int(top_k), MAX_TOP_K))

        tokens = _tokenize(query)
        match = _match_query(tokens)
        where, params = [], []
        if component_types:
            where.append("component_type IN (%s)" % ",".join("?" * len(component_types)))
            params.extend(component_types)
        if component_names:
            where.append("lower(component_name) IN (%s)"
                         % ",".join("?" * len(component_names)))
            params.extend([n.lower() for n in component_names])
        if source_kinds:
            where.append("source_kind IN (%s)" % ",".join("?" * len(source_kinds)))
            params.extend(source_kinds)
        sql = ("SELECT chunk_id, rank FROM chunks WHERE chunks MATCH ?"
               + "".join(" AND " + w for w in where)
               + " ORDER BY rank LIMIT ?")
        rows = self._conn.execute(sql, [match] + params + [LEXICAL_CANDIDATES]) if match else []

        hits = [self._score(c, tokens, rank) for c, rank in
                ((self._by_id[r[0]], r[1]) for r in rows)]
        # An explicit component_names filter is an exact component request
        # (PRD 5.5 example: NL query + component_names -> supported).
        if component_names:
            wanted = {n.lower() for n in component_names}
            for h in hits:
                if h.chunk.component_name and h.chunk.component_name.lower() in wanted:
                    h.boost += EXACT_COMPONENT_BOOST
                    h.exact = True
        hits = self._dedupe(hits)
        hits.sort(key=lambda h: (-h.score, h.chunk.seq))

        selected, omitted = self._select(hits, top_k, max_evidence_chars)
        selected = self._expand(selected, top_k, max_evidence_chars, omitted)

        return SearchResult(
            query=query,
            corpus_id=self.corpus_id,
            version_status=self.version_status,
            corpus_match="exact",
            evidence_status=self._evidence_status(hits, selected),
            chunks=[h.chunk.response(self._norm(h.score)) for h in selected],
            exact_matches=self._exact_matches(selected),
            mentioned_components=self._mentioned(selected),
            omitted=omitted,
            timing_ms=int((time.perf_counter() - t0) * 1000),
        )

    # -- scoring ---------------------------------------------------------
    def _score(self, c: Chunk, tokens: list[dict], rank: float) -> _Hit:
        base = -float(rank)
        boost, exact = 0.0, False
        # option_names may store alias groups ("I, i") — split to single tokens
        opts = {p.strip().lower() for o in c.option_names for p in o.split(",") if p.strip()}
        name = c.component_name.lower() if c.component_name else None
        for t in tokens:
            if name and t["core"] == name:
                boost += EXACT_COMPONENT_BOOST
                exact = True
            if t["core"] in opts:
                boost += EXACT_OPTION_BOOST
                exact = True
            if t["dotted"] and len(t["parts_all"]) >= 2:
                comp, opt = t["parts_all"][0], t["parts_all"][1]
                if name == comp and opt in opts:
                    boost += EXACT_DOTTED_BOOST
                    exact = True
            if t["dash"] and ("-" + t["core"]).lower() in c.text.lower():
                boost += DASH_TOKEN_BOOST
        return _Hit(chunk=c, base=base, boost=boost, exact=exact)

    @staticmethod
    def _norm(score: float) -> float:
        """Ranking signal in [0, 1); not a probability (PRD 5.4)."""
        return score / (score + 4.0)

    @staticmethod
    def _dedupe(hits: list[_Hit]) -> list[_Hit]:
        best: dict[str, _Hit] = {}
        for h in hits:
            key = h.chunk.content_hash
            if key not in best or h.score > best[key].score:
                best[key] = h
        return list(best.values())

    # -- selection & expansion ------------------------------------------
    def _select(self, hits: list[_Hit], top_k: int,
                max_chars: int) -> tuple[list[_Hit], dict]:
        selected, omitted_chunks, omitted_chars = [], 0, 0
        used = 0
        for h in hits:
            n = len(h.chunk.text)
            if len(selected) >= top_k or used + n > max_chars:
                omitted_chunks += 1
                omitted_chars += n
                continue
            selected.append(h)
            used += n
        return selected, {"chunks": omitted_chunks, "chars": omitted_chars}

    def _expand(self, selected: list[_Hit], top_k: int, max_chars: int,
                omitted: dict) -> list[_Hit]:
        """Parent/adjacent expansion (PRD 5.4): for the top seeds, pull in the
        parent chunk and the adjacent chunk of the same document, bounded by
        top_k and the character budget. Budget-skipped expansions are counted
        in ``omitted``."""
        used = sum(len(h.chunk.text) for h in selected)
        out: list[_Hit] = []
        expanded_ids = {h.chunk.chunk_id for h in selected}
        for i, h in enumerate(selected):
            out.append(h)
            if i >= 3:
                continue
            extras: list[Chunk] = []
            parent = self._by_id.get(h.chunk.parent_id) if h.chunk.parent_id else None
            if parent is not None:
                extras.append(parent)
            prev = self._adjacent(h.chunk)
            if prev is not None:
                extras.append(prev)
            for e in extras:
                if e.chunk_id in expanded_ids or len(out) >= top_k:
                    continue
                if used + len(e.text) > max_chars:
                    omitted["chunks"] += 1
                    omitted["chars"] += len(e.text)
                    continue
                out.append(_Hit(chunk=e, base=h.score * EXPANSION_SCORE_FACTOR,
                                boost=0.0, exact=False, expansion=True))
                expanded_ids.add(e.chunk_id)
                used += len(e.text)
        return out

    def _adjacent(self, c: Chunk) -> Chunk | None:
        """Previous chunk of the same document (insertion order)."""
        if not c.source_url:
            return None
        return next((x for x in self.chunks
                     if x.seq == c.seq - 1 and x.source_url == c.source_url), None)

    # -- status & components ---------------------------------------------
    def _evidence_status(self, hits: list[_Hit], selected: list[_Hit]) -> str:
        if not hits:
            return "insufficient"
        if any(h.exact for h in selected):
            return "supported"
        if hits and hits[0].base >= STRONG_BM25:
            return "supported"
        return "partial"

    @staticmethod
    def _exact_matches(selected: list[_Hit]) -> list[dict]:
        seen, out = set(), []
        for h in selected:
            if h.exact and h.chunk.component_type and h.chunk.component_name:
                key = (h.chunk.component_type, h.chunk.component_name)
                if key not in seen:
                    seen.add(key)
                    out.append({"type": key[0], "name": key[1]})
        return out

    def _mentioned(self, selected: list[_Hit]) -> list[dict]:
        if not self._known_components:
            return []
        names = sorted(self._known_components, key=len, reverse=True)
        pattern = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b", re.I)
        seen, out = set(), []
        for h in selected:
            for m in pattern.finditer(h.chunk.text):
                key = self._known_components[m.group(1).lower()]
                if key not in seen:
                    seen.add(key)
                    out.append({"type": key[0], "name": key[1]})
        return out
