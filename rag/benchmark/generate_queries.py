"""M11.1 part B — benchmark query generator (PRD 9.1).

Deterministic: reads fass/registry/registry.json, the ACTIVE corpus
generations (rag/corpus/ffmpeg-{6.1.1,9.0.1}), and the hand-authored sets
(queries_nl.json, queries_insufficient.json) and emits queries.jsonl plus
dataset.json into this directory.

Categories (PRD 9.1):
  exact        one query per registry filter, against a corpus that documents
               it (9.0.1 preferred); a filter documented in no corpus would be
               queried against 9.0.1 with expect_top1 null.
  option       registry filter + option/value from the description example
               when parseable, else the first corpus-documented option of
               that filter (canonical short name). Video and audio are both
               covered so the set comfortably exceeds the PRD's 50 M10.0
               filters (grouped/argument-less filters have no option to
               query and are skipped).
  nl           62 hand-authored mixed natural-language queries (queries_nl.json).
  mismatch     10 cases derived from the 9.0.1-vs-6.1.1 corpus diff (filters
               and options documented only in 9.0.1, queried against 6.1.1);
               candidates are additionally filtered for TEXT-level novelty
               — a name/option present anywhere in 6.1.1 prose is not novel
               (it would leak into the returned chunks).
  insufficient 5 queries with no answer in the corpus (queries_insufficient.json).

Presence is heading-aware: parser 1.2 stores comma-grouped manual headings
(e.g. 'setpts, asetpts') with component_name null, so a registry filter counts
as documented when its name appears in a heading leaf OR as a chunk
component_name. Grouped queries carry the group anchor in relevant_anchors so
the runner can accept the group chunk as a top-1 hit.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS_ROOT = ROOT / "rag" / "corpus"
REGISTRY = ROOT / "fass" / "registry" / "registry.json"
CORPORA = ("ffmpeg-9.0.1", "ffmpeg-6.1.1")
KINDS = ("manual", "build-help")

EXAMPLE_RE = re.compile(r"'([^']+)'")
PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^:\s]+)")
# word runs exactly as the benchmark's no-leak regex sees them: dashes do
# NOT delimit ('h-full' contains the token 'full'), so a candidate novel
# token must be absent as a whole run to stay leak-free
RUN_RE = re.compile(r"[A-Za-z0-9_]+")


# -- corpus inventory ---------------------------------------------------------

def _active_chunks(corpus_dir: Path, kind: str) -> list[dict]:
    gens = json.loads((corpus_dir / "chunks" / kind / "generations.json").read_text())
    path = corpus_dir / "chunks" / kind / f"gen-{int(gens['active'])}" / "chunks.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _heading_names(leaf: str) -> list[str]:
    return [p.strip() for p in leaf.split(",") if p.strip()]


class Inventory:
    """Heading-aware view of one corpus's active generations."""

    def __init__(self, corpus_dir: Path):
        self.names: dict[str, dict] = {}   # name -> {type, anchor, grouped}
        self.options: dict[str, set[str]] = {}  # name -> lowercase option tokens
        self.words: set[str] = set()       # every word run in the corpus text
        for kind in KINDS:
            for c in _active_chunks(corpus_dir, kind):
                self.words.update(w.lower() for w in RUN_RE.findall(c["text"]))
                leaf = c["heading_path"][-1] if c.get("heading_path") else ""
                anchor = c.get("anchor")
                ct = c.get("component_type")
                cname = c.get("component_name")
                level = c.get("level", 0)
                # component_name entries win over heading-group entries
                if cname and level <= 4:
                    self.names.setdefault(cname, {
                        "type": ct, "anchor": anchor, "grouped": False})
                elif ct in ("filter", None) and leaf and level in (2, 3):
                    for n in _heading_names(leaf):
                        self.names.setdefault(n, {
                            "type": ct, "anchor": anchor,
                            "grouped": "," in leaf})
                # options: explicit component_name chunks and heading groups
                if c.get("option_names"):
                    target = cname
                    if not target and ct in ("filter", None) and leaf and level in (2, 3):
                        target = _heading_names(leaf)[0] if "," not in leaf else None
                    if target:
                        for o in c["option_names"]:
                            for p in o.split(","):
                                # ffmpeg option names have no whitespace; the
                                # parser occasionally stores "name type" pairs
                                # (Muxers Options lists) — keep the name
                                tok = p.strip().split()[0] if p.strip() else ""
                                if tok:
                                    self.options.setdefault(
                                        target, set()).add(tok.lower())

    def has(self, name: str) -> bool:
        return name in self.names

    def anchor(self, name: str) -> str | None:
        return self.names.get(name, {}).get("anchor")

    def grouped(self, name: str) -> bool:
        return self.names.get(name, {}).get("grouped", False)

    def first_option(self, name: str) -> str | None:
        """Canonical form: the short alias after the comma when stored that way."""
        for o in sorted(self.options.get(name, ())):
            return o.split(",")[0].strip()
        return None


def _registry_filters() -> list[tuple[str, str, str]]:
    """[(kind, name, description)] in registry order: video then audio."""
    reg = json.loads(REGISTRY.read_text())
    out = []
    for kind in ("video", "audio"):
        for name, entry in reg["filters"][kind].items():
            out.append((kind, name, entry.get("description", "")))
    return out


# -- option-level query building ----------------------------------------------

def _option_query(kind: str, name: str, desc: str, inv: Inventory,
                  corpus_id: str) -> dict | None:
    """name + value/units: from the description example when parseable, else
    the first corpus-documented option of the filter."""
    for example in EXAMPLE_RE.findall(desc):
        if "=" not in example:
            continue
        ex_name, args = example.split("=", 1)
        if ex_name != name:
            continue
        if "(" in args:                      # expressions (ow-iw)/2 — skip
            continue
        pairs = PAIR_RE.findall(args)
        if pairs:
            opt, val = pairs[0]
            return _record(f"option-{corpus_id[7:]}-{name}", "option", corpus_id,
                           f"{name} {opt} {val}", name, inv,
                           note=f"description example {example!r}")
        first = args.split(":")[0].strip()
        if first:
            return _record(f"option-{corpus_id[7:]}-{name}", "option", corpus_id,
                           f"{name} {first}", name, inv,
                           note=f"positional example {example!r}")
        return None
    opt = inv.first_option(name)
    if opt:
        return _record(f"option-{corpus_id[7:]}-{name}", "option", corpus_id,
                       f"{name} {opt}", name, inv, note="corpus first option")
    return None


def _record(id: str, category: str, corpus_id: str, query: str,
            expect_name: str | None, inv: Inventory | None,
            novel: list[str] | None = None, relevant: list[str] | None = None,
            note: str = "") -> dict:
    rec = {
        "id": id,
        "category": category,
        "corpus": corpus_id,
        "query": query,
        "expect_top1": expect_name,
        "relevant": relevant if relevant is not None else (
            [expect_name] if expect_name else []),
        "relevant_anchors": [],
        "novel_tokens": novel or [],
        "note": note,
    }
    if expect_name and inv is not None and inv.has(expect_name):
        a = inv.anchor(expect_name)
        if a:
            rec["relevant_anchors"] = [a]
    return rec


# -- mismatch derivation --------------------------------------------------------

def _mismatch_queries(inv9: Inventory, inv6: Inventory,
                      reg_names: list[str]) -> list[dict]:
    # type == "filter" only: ct None also holds section headings, sources and
    # demuxers (e.g. 'CUDA Video Filters', 'lrc'), which are not mismatch cases
    # Text-level novelty: a '9.0.1-only' name/option that already appears
    # in 6.1.1 prose (e.g. 'ffv1' in h-full, 'shaping' in ass/aresample)
    # would leak against the 6.1.1 corpus, so it is not a mismatch case.
    def novel9(names: set[str]) -> set[str]:
        return {n for n in names if n.lower() not in inv6.words}

    f9 = {n for n, e in inv9.names.items() if e["type"] == "filter"}
    f6 = {n for n, e in inv6.names.items() if e["type"] == "filter"}
    e9 = {n for n, e in inv9.names.items() if e["type"] == "encoder"}
    e6 = {n for n, e in inv6.names.items() if e["type"] == "encoder"}
    out: list[dict] = []
    filters_only = sorted(novel9(f9) - f6)
    if filters_only:
        idx = sorted({0, len(filters_only) // 3, 2 * len(filters_only) // 3,
                      len(filters_only) - 1})
        for i in idx:
            n = filters_only[i]
            out.append(_record(f"mm-filter-{n}", "mismatch", "ffmpeg-6.1.1",
                               n, None, None, novel=[n], relevant=[],
                               note="filter documented only in 9.0.1"))
    encs_only = sorted(novel9(e9) - e6)
    for n in encs_only[:2]:
        out.append(_record(f"mm-enc-{n}", "mismatch", "ffmpeg-6.1.1",
                           n, None, None, novel=[n], relevant=[],
                           note="encoder documented only in 9.0.1"))
    taken = 0
    for n in reg_names:  # registry order keeps the pick deterministic
        if taken >= 4:
            break
        diff = sorted(o for o in inv9.options.get(n, set())
                      - inv6.options.get(n, set()) if o not in inv6.words)
        if diff:
            opt = diff[0]
            out.append(_record(f"mm-opt-{n}-{opt}", "mismatch", "ffmpeg-6.1.1",
                               f"{n} {opt}", n, inv6, novel=[opt],
                               relevant=[n],
                               note="option documented only in 9.0.1"))
            taken += 1
    return out[:10]


# -- main -----------------------------------------------------------------------

def generate(corpus_root: Path = CORPUS_ROOT,
             out_dir: Path = HERE) -> dict:
    reg = _registry_filters()
    inv = {cid: Inventory(corpus_root / cid) for cid in CORPORA}
    c91, c611 = inv["ffmpeg-9.0.1"], inv["ffmpeg-6.1.1"]

    records: list[dict] = []
    # exact: one per registry filter, against a documenting corpus
    for kind, name, _desc in reg:
        if c91.has(name):
            rec = _record(f"exact-901-{name}", "exact", "ffmpeg-9.0.1", name,
                          name, c91)
        elif c611.has(name):
            rec = _record(f"exact-611-{name}", "exact", "ffmpeg-6.1.1", name,
                          name, c611)
        else:
            rec = _record(f"exact-901-{name}", "exact", "ffmpeg-9.0.1", name,
                          None, None, note="absent from both corpora")
        records.append(rec)
    # option: every registry filter with a parseable option
    for kind, name, desc in reg:
        corpus_id = "ffmpeg-9.0.1" if c91.has(name) else "ffmpeg-6.1.1"
        rec = _option_query(kind, name, desc, inv[corpus_id], corpus_id)
        if rec:
            records.append(rec)
    # nl + insufficient (hand-authored)
    nl = json.loads((HERE / "queries_nl.json").read_text())["queries"]
    for q in nl:
        records.append({
            "id": q["id"], "category": "nl", "corpus": "ffmpeg-9.0.1",
            "query": q["query"], "expect_top1": None,
            "relevant": q.get("relevant", []),
            "relevant_anchors": q.get("relevant_anchors", []),
            "novel_tokens": [], "note": q.get("note", ""),
        })
    for q in json.loads((HERE / "queries_insufficient.json").read_text())["queries"]:
        records.append({
            "id": q["id"], "category": "insufficient",
            "corpus": q.get("corpus", "ffmpeg-9.0.1"), "query": q["query"],
            "expect_top1": None, "relevant": [], "relevant_anchors": [],
            "novel_tokens": [], "note": q.get("note", ""),
        })
    # mismatch: corpus diff
    records.extend(_mismatch_queries(c91, c611,
                                     [n for _k, n, _d in reg]))

    # deterministic order: category, then id
    order = {"exact": 0, "option": 1, "nl": 2, "mismatch": 3,
             "insufficient": 4}
    records.sort(key=lambda r: (order[r["category"]], r["id"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "queries.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records))

    counts: dict[str, int] = {}
    for r in records:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    grouped = sorted(n for n, e in c91.names.items()
                     if e["grouped"] and n in {x[1] for x in reg})
    dataset = {
        "generated_by": "rag/benchmark/generate_queries.py",
        "prd": "ffmpeg-assistant-rag-prd.md section 9.1",
        "registry": str(REGISTRY.relative_to(ROOT)),
        "corpora": {cid: str((corpus_root / cid).relative_to(ROOT))
                    for cid in CORPORA},
        "counts": counts,
        "total": len(records),
        "gates": {
            "exact_top1_min": 0.95,
            "recall8_min": 0.90,
            "citation_traceability_min": 1.0,
            "version_labeling_min": 1.0,
            "no_fabricated_syntax": "all exact_matches verifiable",
            "mismatch_no_leak": "novel tokens absent from returned chunks",
            "persistence": "restart preserves corpus identity",
            "warm_p95_ms_max": 300,
            "cold_first_ms_max": 1000,
        },
        "notes": [
            f"parser 1.2 stores comma-grouped headings with component_name "
            f"null; registry filters affected in 9.0.1 docs: {grouped}. "
            f"Exact top-1 for those rests on bm25 alone (no exact boost); "
            f"parser 1.3 should split grouped headings.",
            "evidence_status 'supported' is over-claimed for out-of-domain "
            "queries (bm25 cannot separate; measured 0-16 overlap). M11.2 "
            "advisory layer must not treat lexical 'supported' as ground "
            "truth; the no-fabrication gate checks exact_match "
            "verifiability instead.",
            "recall8 residual (~21/232) is all-NL and every case is a "
            "genuine lexical gap — inflection (silent/silence, "
            "timestamp(s)), compounding (time base/timebase), common-word "
            "dilution (frame rate, stream), sibling-component dominance "
            "(fade/afade, thumbnail/thumbnail_cuda), or pure synonymy "
            "(twice as fast/setpts). No boost rescues them without harming "
            "precision; the M11.2 advisory layer must tolerate them.",
        ],
    }
    (out_dir / "dataset.json").write_text(json.dumps(dataset, indent=1) + "\n")
    return dataset


def main() -> int:
    d = generate()
    print(json.dumps(d["counts"], indent=1))
    print("total:", d["total"], "->", HERE / "queries.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
