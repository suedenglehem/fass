"""§9.4 RAG budget measurements (PRD §9.4).

Runs real queries against a live fass-rag service and reports the budget
lines:

  * retrieval p95 — server-side retrieval_ms, warm (excluding the first
    query after service start; pass --cold to flag the first query as the
    cold measurement)
  * evidence block size — max chars of format_evidence_block (budget 4000)
  * RAG process memory — RSS of the service (budget 4 GB), via --pid

Usage (from the repo root):
  FASS_RAG_URL=http://127.0.0.1:8100 \
    .venv/bin/python scripts/m11_rag_budgets.py [--n 40] [--cold] [--pid N]

The RAG layer is fail-open: a dead service is reported as a measurement
failure, not an exception.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fass.config import resolved_rag  # noqa: E402
from fass.rag import client  # noqa: E402
from fass.rag import evidence  # noqa: E402
from fass.rag.evidence import format_evidence_block  # noqa: E402

# The budget bounds the block BODY; the fixed preamble/markers are extra.
WRAP = len(evidence.PREAMBLE) + len(evidence.OPEN) + len(evidence.CLOSE)

BUDGET_RETRIEVAL_P95_WARM_MS = 300.0
BUDGET_RETRIEVAL_P95_COLD_MS = 1000.0
BUDGET_EVIDENCE_CHARS = 4000
BUDGET_RAG_MEMORY_MB = 4096


def _p95(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(0.95 * (len(xs) - 1)))))
    return xs[k]


def _rss_mb(pid: int) -> float | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return None


def _ok(verdict: bool, label: str, actual: str, budget: str) -> None:
    print(f"  [{'PASS' if verdict else 'FAIL'}] {label}: {actual} (budget {budget})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40, help="number of queries")
    ap.add_argument("--cold", action="store_true",
                    help="service just started — first query counts as cold")
    ap.add_argument("--pid", type=int, default=0,
                    help="fass-rag server pid for the memory budget")
    ap.add_argument("--out", default="", help="also write JSON here")
    args = ap.parse_args()

    cfg = resolved_rag()
    if not cfg.enabled:
        print("RAG disabled — set FASS_RAG_ENABLED=1 + FASS_RAG_URL")
        return 2

    reqs = json.loads((REPO / "tests/benchmark/requests.json").read_text())
    queries = [e["instruction"] for e in reqs][: args.n]

    times: list[tuple[str, float, float, int, str]] = []  # q, server_ms, client_ms, ev_chars, status
    for i, q in enumerate(queries):
        t0 = time.perf_counter()
        res = client.search(cfg, q)
        t1 = time.perf_counter()
        block = (format_evidence_block(res, budget=cfg.max_evidence_chars)
                 if res.ok else "") or ""
        body = max(0, len(block) - WRAP)
        times.append((q, float(res.retrieval_ms), (t1 - t0) * 1000.0,
                      body, "ok" if res.ok else "fail"))
        tag = "cold" if (args.cold and i == 0) else ""
        print(f"  [{i + 1:2d}/{args.n}] {tag:4s} server={res.retrieval_ms:7.1f}ms "
              f"client={(t1 - t0) * 1000:7.1f}ms ev_body={body:4d}ch "
              f"chunks={len(res.chunks)} {q[:48]!r}")

    fails = [t for t in times if t[4] != "ok"]
    if fails:
        print(f"\n{len(fails)}/{args.n} queries failed (fail-open) — "
              "service unhealthy; no budget verdicts.")
        return 1

    server = [t[1] for t in times]
    warm = server[1:] if args.cold else server
    p95_warm = _p95(warm)
    p95_cold = server[0] if args.cold else float("nan")
    max_ev = max(t[3] for t in times)

    print(f"\nretrieval (server-side), n={args.n}:")
    _ok(p95_warm <= BUDGET_RETRIEVAL_P95_WARM_MS, "p95 warm",
        f"{p95_warm:.1f}ms", f"<={BUDGET_RETRIEVAL_P95_WARM_MS:.0f}ms")
    if args.cold:
        _ok(p95_cold <= BUDGET_RETRIEVAL_P95_COLD_MS, "first-query (cold)",
            f"{p95_cold:.1f}ms", f"<={BUDGET_RETRIEVAL_P95_COLD_MS:.0f}ms")
    _ok(max_ev <= BUDGET_EVIDENCE_CHARS, "evidence body max",
        f"{max_ev}ch (+{WRAP}ch fixed wrapper)", f"<={BUDGET_EVIDENCE_CHARS}ch")

    if args.pid:
        rss = _rss_mb(args.pid)
        if rss is None:
            print(f"  [FAIL] rag memory: pid {args.pid} unreadable")
        else:
            _ok(rss <= BUDGET_RAG_MEMORY_MB, "rag process RSS",
                f"{rss:.0f}MB", f"<={BUDGET_RAG_MEMORY_MB}MB")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "n": args.n, "cold": args.cold,
            "p95_warm_ms": p95_warm, "p95_cold_ms": p95_cold,
            "evidence_max_chars": max_ev,
            "client_ms": {"mean": statistics.mean(t[2] for t in times),
                          "p95": _p95([t[2] for t in times])},
            "pid_rss_mb": _rss_mb(args.pid) if args.pid else None,
        }
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
