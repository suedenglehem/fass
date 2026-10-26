"""M11.1 part B — benchmark runner (PRD 9.1 gates).

Two passes per query:

* RANKING pass — ``Retriever.search(top_k=8, max_evidence_chars=inf)``
  directly. The 4000-char evidence budget is payload control (PRD 9.5),
  not a ranking input, so exact-top-1 and Recall@8 are measured without
  budget-induced omissions.
* SERVICE pass — one TestClient per corpus, contract defaults (top_k=6,
  4000-char budget): client-side latency (cold = first request after
  boot, warm = the rest), citation traceability, version labeling,
  contract shape.

Gates (PRD 9.1):
  exact_top1_min            0.95  one query per registry filter, top-1 hit
  recall8_min               0.90  labeled sections (names and/or anchors)
  citation_traceability_min 1.00  every returned chunk carries url + anchor
  version_labeling_min      1.00  corpus_id / version_status / corpus_match
  no_fabricated_syntax      all   every exact_match verifiable: a query
                                  token equals the component name or one of
                                  its documented option_names
  mismatch_no_leak          all   novel 9.x tokens absent from the chunks
                                  returned against the 6.1.1 corpus
  persistence               ok    second boot of the same corpus reports
                                  identical /readyz identity
  warm_p95_ms_max           300   (i7 CPU reference, PRD 9.1)
  cold_first_ms_max         1000

Parsing fixtures 100% is covered by the existing parser test suite, not
re-run here. evidence_status calibration is recorded as a finding, not a
gate (see dataset.json notes: lexical 'supported' is over-claimed for
out-of-domain queries).

Run: ``python rag/benchmark/run_benchmark.py`` — writes results.json and
exits non-zero when any gate fails.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
import sys
import time
from contextlib import contextmanager
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS_ROOT = ROOT / "rag" / "corpus"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from rag.retrieval import Retriever, _tokenize  # noqa: E402

# TestClient routes each request through httpx's logger at INFO; the
# benchmark only cares about gate numbers, not per-request logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("fastapi").setLevel(logging.WARNING)

REQUIRED_CHUNK_KEYS = {
    "chunk_id", "title", "heading_path", "url", "anchor", "component_type",
    "component_name", "option_names", "text", "score", "parent_id",
}


@contextmanager
def _service(corpus_id: str, corpus_root: Path):
    """Boot the fass-rag app with one corpus pinned (env is read at import)."""
    os.environ["FASS_RAG_DATA_DIR"] = str(corpus_root)
    os.environ["FASS_RAG_CORPUS"] = corpus_id
    import rag.server as server
    importlib.reload(server)
    with TestClient(server.app) as client:
        yield client


def _p95(values: list[float]) -> float:
    vs = sorted(values)
    return vs[max(0, math.ceil(0.95 * len(vs)) - 1)]


def run_benchmark(queries_path: Path = HERE / "queries.jsonl",
                  corpus_root: Path = CORPUS_ROOT,
                  out_path: Path | None = None) -> dict:
    if out_path is None:
        out_path = HERE / "results.json"
    records = [json.loads(line) for line in
               Path(queries_path).read_text().splitlines() if line.strip()]
    corpus_ids = sorted({r["corpus"] for r in records})

    # ---------------- ranking pass ---------------------------------------
    retrievers = {cid: Retriever(corpus_root / cid) for cid in corpus_ids}
    rank: dict[str, object] = {}
    for r in records:
        rank[r["id"]] = retrievers[r["corpus"]].search(
            r["query"], top_k=8, max_evidence_chars=10 ** 9)

    # ---------------- service pass ---------------------------------------
    svc: dict[str, dict] = {}
    latency: dict[str, dict] = {
        cid: {"cold_first_ms": None, "warm": []} for cid in corpus_ids}
    for cid in corpus_ids:
        with _service(cid, corpus_root) as client:
            for i, r in enumerate(rec for rec in records
                                  if rec["corpus"] == cid):
                t0 = time.perf_counter()
                resp = client.post("/v1/search", json={"query": r["query"]})
                ms = (time.perf_counter() - t0) * 1000
                assert resp.status_code == 200, (r["id"], resp.text[:200])
                svc[r["id"]] = resp.json()
                if i == 0:
                    latency[cid]["cold_first_ms"] = round(ms, 2)
                else:
                    latency[cid]["warm"].append(ms)

    # ---------------- persistence (PRD 9.1 offline/restart) ---------------
    persistence = {}
    for boot in (1, 2):
        with _service("ffmpeg-9.0.1", corpus_root) as client:
            ready = client.get("/readyz").json()
            persistence[f"boot_{boot}"] = {
                "corpus_id": ready["corpus_id"],
                "version_status": ready["version_status"],
                "kinds": ready["kinds"],
            }
    persistence["identical"] = (persistence["boot_1"] == persistence["boot_2"])

    # ---------------- gates ----------------------------------------------
    # exact top-1: top chunk is the expected component (or, for grouped
    # headings where component_name is null, the stored group anchor)
    exact = [r for r in records if r["category"] == "exact"]
    exact_misses = []
    for r in exact:
        if r["expect_top1"] is None:
            continue
        top = rank[r["id"]].chunks[0] if rank[r["id"]].chunks else None
        if not top or not (top["component_name"] == r["expect_top1"]
                           or top["anchor"] in r["relevant_anchors"]):
            exact_misses.append(r["id"])
    exact_rate = 1.0 - len(exact_misses) / len(exact) if exact else 1.0

    # recall@8: any top-8 chunk matches a labeled name or anchor
    labeled = [r for r in records if r["relevant"] or r["relevant_anchors"]]
    recall_misses = []
    for r in labeled:
        want_names = {n.lower() for n in r["relevant"]}
        want_anchors = set(r["relevant_anchors"])
        hit = any((c["component_name"] and
                   c["component_name"].lower() in want_names)
                  or c["anchor"] in want_anchors
                  for c in rank[r["id"]].chunks)
        if not hit:
            recall_misses.append(r["id"])
    recall = 1.0 - len(recall_misses) / len(labeled) if labeled else 1.0

    # citation traceability + contract shape (service pass)
    cite_total = cite_bad = shape_bad = 0
    for r in records:
        for c in svc[r["id"]]["chunks"]:
            cite_total += 1
            if not c.get("url") or not c.get("anchor"):
                cite_bad += 1
            if not REQUIRED_CHUNK_KEYS.issubset(c):
                shape_bad += 1
    cite_rate = 1.0 - cite_bad / cite_total if cite_total else 1.0

    # version labeling (service pass)
    ver_bad = [r["id"] for r in records
               if svc[r["id"]]["corpus_id"] != r["corpus"]
               or svc[r["id"]]["version_status"] != "pinned-release"
               or svc[r["id"]]["corpus_match"] != "exact"]
    ver_rate = 1.0 - len(ver_bad) / len(records) if records else 1.0

    # no fabricated syntax: every exact_match in insufficient/mismatch
    # responses must be verifiable against the corpus (query token ==
    # component name or one of its documented option_names)
    unverified = []
    for r in records:
        if r["category"] not in ("insufficient", "mismatch"):
            continue
        res = rank[r["id"]]
        if not res.exact_matches:
            continue
        cores = {t["core"] for t in _tokenize(r["query"])}
        for em in res.exact_matches:
            name = em["name"].lower()
            opts: set[str] = set()
            for c in res.chunks:
                if c["component_name"] and \
                        c["component_name"].lower() == name:
                    for o in c["option_names"]:
                        for p in o.split(","):
                            if p.strip():
                                opts.add(p.strip().split()[0].lower())
            if name not in cores and not (cores & opts):
                unverified.append(
                    {"id": r["id"], "type": em["type"], "name": em["name"]})

    # mismatch no-leak: novel tokens absent from returned 6.1.1 chunks
    leak_pairs = leak_hits = 0
    leaks = []
    for r in records:
        if r["category"] != "mismatch":
            continue
        for tok in r["novel_tokens"]:
            leak_pairs += 1
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok)
                             + r"(?![A-Za-z0-9_])", re.I)
            for c in rank[r["id"]].chunks:
                if pat.search(c["text"]):
                    leak_hits += 1
                    leaks.append({"id": r["id"], "token": tok,
                                  "chunk": c["chunk_id"]})
                    break
    no_leak = 1.0 - leak_hits / leak_pairs if leak_pairs else 1.0

    # latency
    latency_out = {}
    for cid in corpus_ids:
        warm = latency[cid]["warm"]
        latency_out[cid] = {
            "cold_first_ms": latency[cid]["cold_first_ms"],
            "warm_n": len(warm),
            "warm_p95_ms": round(_p95(warm), 2) if warm else None,
            "warm_max_ms": round(max(warm), 2) if warm else None,
        }
    warm_p95_max = max(v["warm_p95_ms"] for v in latency_out.values())
    cold_max = max(v["cold_first_ms"] for v in latency_out.values())

    status_dist = {cat: dict(Counter(
        rank[r["id"]].evidence_status
        for r in records if r["category"] == cat))
        for cat in sorted({r["category"] for r in records})}

    gates = {
        "exact_top1_min": (0.95, round(exact_rate, 4)),
        "recall8_min": (0.90, round(recall, 4)),
        "citation_traceability_min": (1.0, round(cite_rate, 4)),
        "version_labeling_min": (1.0, round(ver_rate, 4)),
        "no_fabricated_syntax": (1.0, 1.0 if not unverified else 0.0),
        "mismatch_no_leak": (1.0, round(no_leak, 4)),
        "persistence": ("identical", int(persistence["identical"])),
        "warm_p95_ms_max": (300, warm_p95_max),
        "cold_first_ms_max": (1000, cold_max),
    }
    gate_table = {}
    for name, (thresh, actual) in gates.items():
        if name == "persistence":
            ok = bool(actual)  # threshold "identical" means boots must match
        elif name in ("exact_top1_min", "recall8_min",
                      "citation_traceability_min", "version_labeling_min",
                      "no_fabricated_syntax", "mismatch_no_leak"):
            ok = actual >= thresh
        else:
            ok = actual <= thresh
        gate_table[name] = {
            "threshold": thresh, "actual": actual, "pass": bool(ok)}

    counts = Counter(r["category"] for r in records)
    results = {
        "generated_by": "rag/benchmark/run_benchmark.py",
        "prd": "ffmpeg-assistant-rag-prd.md section 9.1",
        "queries": str(Path(queries_path).resolve().relative_to(ROOT)),
        "corpora": {cid: str((corpus_root / cid).resolve().relative_to(ROOT))
                    for cid in corpus_ids},
        "counts": dict(sorted(counts.items())),
        "total": len(records),
        "gates": gate_table,
        "latency_ms": latency_out,
        "index_build_ms": {cid: retrievers[cid].index_build_ms
                           for cid in corpus_ids},
        "persistence": persistence,
        "findings": {
            "evidence_status_by_category": status_dist,
            "exact_top1_misses": exact_misses,
            "recall8_misses": recall_misses,
            "unverified_exact_matches": unverified,
            "mismatch_leaks": leaks,
            "version_labeling_violations": ver_bad,
            "contract_shape_violations": shape_bad,
            "notes": [
                "parsing-fixture 100% gate is covered by the existing parser "
                "test suite (tests/test_rag_parser.py), not re-run here",
                "evidence_status 'supported' over-claiming on out-of-domain "
                "queries is recorded, not gated — no lexical threshold can "
                "separate (see dataset.json notes)",
            ],
        },
    }
    Path(out_path).write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    return results


def main() -> int:
    results = run_benchmark()
    print(f"queries: {results['total']} ({results['counts']})")
    failed = []
    for name, g in results["gates"].items():
        mark = "PASS" if g["pass"] else "FAIL"
        print(f"  [{mark}] {name:<28} threshold={g['threshold']} "
              f"actual={g['actual']}")
        if not g["pass"]:
            failed.append(name)
    for cid, lat in results["latency_ms"].items():
        print(f"  latency {cid}: cold={lat['cold_first_ms']}ms "
              f"warm_p95={lat['warm_p95_ms']}ms (n={lat['warm_n']}, "
              f"max={lat['warm_max_ms']}ms)")
    if failed:
        print("GATES FAILED:", ", ".join(failed))
        return 1
    print("all gates passed ->", HERE / "results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
