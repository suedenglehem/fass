#!/usr/bin/env python3
"""M11.4 §9.2 NLU ablation harness (PRD ffmpeg-assistant-rag-prd.md).

Four arms on the same datasets:
  1. rules                 — deterministic rules adapter (the default)
  2. llm                   — LLM + current prompt (names + descriptions only)
  3. llm_schemas           — LLM + registry option schemas rendered into the prompt
  4. llm_schemas_evidence  — arm 3 + retrieved evidence from the fass-rag service

Metrics (PRD §9.2): status accuracy, operation-multiset accuracy, parameter
accuracy (trim ±0.01 s, container, video_codec, audio_mode,
video_bitrate_kbps, output_destination), clarification accuracy, unsupported
detection, p95 latency, token counts (chars/4 estimate — consistent across
arms, which is what the comparison needs), citation precision (arm 4 only:
a cited chunk is "relevant" if its component name is used in the resulting
plan, or it shares >= 2 content tokens with the instruction).

Plans are compared semantically — never command wording (the same stance as
tests/test_benchmark.py).

The LLM arms talk to an OpenAI-compatible endpoint (tr4 vLLM default:
http://127.0.0.1:8091, model qwen3.8-27b) at the NLU level only — the
pipeline's planner/builder are deliberately bypassed so the ablation
measures the prompt effect on the parse output. The evidence arm talks to
the fass-rag service (default http://127.0.0.1:8100) and degrades
fail-open to arm 3 when a search fails; affected entries are counted and
reported, never silently dropped.

Usage:
  .venv/bin/python scripts/m11_ablation.py --arms rules          # offline check
  .venv/bin/python scripts/m11_ablation.py --mock                # wiring smoke
  .venv/bin/python scripts/m11_ablation.py                       # all 4 arms
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fass.config import RagConfig  # noqa: E402
from fass.nlu.llm import LLMAdapter, _extract_json  # noqa: E402
from fass.pipeline import generate  # noqa: E402
from fass.rag.client import search  # noqa: E402
from fass.rag.evidence import format_evidence_block  # noqa: E402
from fass.registry import REGISTRY  # noqa: E402
from fass.schema import Asset, EditPlan, ParseResult as Envelope, guess_kind  # noqa: E402

TRIM_TOLERANCE = 0.01
ARMS = ("rules", "llm", "llm_schemas", "llm_schemas_evidence")


# ------------------------------------------------------------ datasets

def load_datasets(paths: list[str], limit: int | None) -> list[dict]:
    entries = []
    for p in paths:
        path = REPO / p if not Path(p).is_absolute() else Path(p)
        if not path.exists():
            print(f"warning: dataset not found: {path} — skipped", file=sys.stderr)
            continue
        for e in json.loads(path.read_text()):
            e = dict(e)
            e["_dataset"] = path.name
            entries.append(e)
    if limit:
        entries = entries[:limit]
    return entries


def assets_from_entry(entry: dict) -> dict:
    spec = entry.get("assets") or {}
    out = {"main": Asset(path=spec.get("input", "clip.mp4"),
                         kind=guess_kind(spec.get("input", "clip.mp4")))}
    if spec.get("watermark"):
        from fass.schema import AssetKind

        out["watermark"] = Asset(path=spec["watermark"], kind=AssetKind.IMAGE)
    for aid, path in spec.items():
        if aid not in ("input", "watermark"):
            out[aid] = Asset(path=path, kind=guess_kind(path))
    return out


# ------------------------------------------------- arm 3: schemas block

def _opt_spec(opt: dict) -> str:
    kind = opt.get("kind", "string")
    if kind in ("choice", "flagset"):
        vals = "|".join(opt.get("choices") or [])
        return f"{kind}({vals})"
    if kind in ("int", "number") and opt.get("range") is not None:
        lo, hi = opt["range"]
        return f"{kind}[{lo:g}..{hi:g}]"
    if kind == "bool":
        return "bool"
    return kind


def render_schemas_block() -> str:
    """Registry option schemas (M10.0) rendered for the LLM prompt.

    Only schema-bearing filters get lines — pre-M10 entries pass options
    through and stay description-only, exactly like the current prompt."""
    lines = ["Filter option schemas (kind + range; use key=value in the "
             "filter expr, options exactly as documented):"]
    for domain in ("video", "audio"):
        for name, spec in REGISTRY["filters"][domain].items():
            opts = spec.get("options")
            if not opts:
                continue
            rendered = ", ".join(
                f"{o}={_opt_spec(v)}" for o, v in sorted(opts.items())
            )
            lines.append(f"[{domain}] {name}: {rendered}")
    return "\n" + "\n".join(lines)


# -------------------------------------------------------------- runners

@dataclass
class ArmResult:
    arm: str
    id: str
    status: str
    plan: dict | None
    clarifications: list = field(default_factory=list)
    unsupported: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    latency_s: float = 0.0
    prompt_chars: int = 0
    completion_chars: int = 0
    evidence_chunks: int = 0
    citations: list = field(default_factory=list)
    error: str | None = None


def run_rules(entry: dict, assets: dict) -> ArmResult:
    t0 = time.monotonic()
    previous = entry.get("previous_plan")
    r = generate(
        entry["instruction"], assets,
        previous_plan=EditPlan.model_validate(previous) if previous else None,
        adapter_name="rules",
    )
    return ArmResult(
        arm="rules", id=entry["id"], status=r.status,
        plan=r.plan,  # GenerationResponse.plan is already a dict
        clarifications=r.clarifications, unsupported=r.unsupported,
        errors=r.errors, latency_s=round(time.monotonic() - t0, 3),
    )


def run_llm(entry: dict, assets: dict, adapter: LLMAdapter,
            schemas: bool, rag_cfg: RagConfig | None) -> ArmResult:
    arm = "llm_schemas_evidence" if (schemas and rag_cfg is not None) \
        else ("llm_schemas" if schemas else "llm")
    system = adapter._system(assets)
    if schemas:
        system += SCHEMAS_BLOCK
    user = f"Instruction: {entry['instruction']}"
    previous = entry.get("previous_plan")
    if previous:
        if isinstance(previous, dict):
            # Mirror fass/nlu/llm.py: the adapter serializes the previous
            # EditPlan with model_dump_json().
            previous = EditPlan.model_validate(previous).model_dump_json()
        user += ("\nPrevious plan (this is a revision — change only what "
                 "the instruction changes):\n" + previous)
    evidence_chunks = 0
    citations = []
    if rag_cfg is not None:
        try:
            res = search(rag_cfg, entry["instruction"])
        except Exception as e:  # noqa: BLE001 — fail-open, arm degrades to 3
            print(f"  {entry['id']}: retrieval failed ({e}); "
                  "degrading to no-evidence", file=sys.stderr)
            res = None
        if res is not None and res.ok:
            block = format_evidence_block(res, budget=rag_cfg.max_evidence_chars)
            if block:
                user += "\n" + block
                evidence_chunks = len(res.chunks)
                citations = [
                    {"chunk_id": c.get("chunk_id"),
                     "component_name": c.get("component_name"),
                     "component_type": c.get("component_type"),
                     "text": c.get("text", "")}
                    for c in res.chunks
                ]
    prompt_chars = len(system) + len(user)
    t0 = time.monotonic()
    try:
        text = adapter._chat_openai(system, user)
    except Exception as e:  # noqa: BLE001 — provider failure is a metric, not a crash
        return ArmResult(arm=arm, id=entry["id"], status="error", plan=None,
                         errors=[f"provider: {e}"],
                         latency_s=round(time.monotonic() - t0, 3),
                         prompt_chars=prompt_chars, error=str(e))
    latency = round(time.monotonic() - t0, 3)
    try:
        env = Envelope.model_validate(_extract_json(text))
    except Exception as e:  # noqa: BLE001 — schema-invalid output = the metric
        return ArmResult(arm=arm, id=entry["id"], status="error", plan=None,
                         errors=[f"schema: {e}"], latency_s=latency,
                         prompt_chars=prompt_chars,
                         completion_chars=len(text), error=str(e))
    return ArmResult(
        arm=arm, id=entry["id"], status=env.status.value,
        plan=env.plan.model_dump() if env.plan is not None else None,
        clarifications=[c.model_dump() for c in env.clarifications],
        unsupported=[u.model_dump() for u in env.unsupported],
        latency_s=latency, prompt_chars=prompt_chars,
        completion_chars=len(text), evidence_chunks=evidence_chunks,
        citations=citations,
    )


def mock_chat(self, system: str, user: str) -> str:
    """Deterministic stub (offline wiring check): always clarifies."""
    return json.dumps({
        "status": "needs_clarification", "plan": None,
        "clarifications": [{"question": "which edit exactly?", "hint": None}],
        "unsupported": [], "defaults": [],
    })


# -------------------------------------------------------------- scoring

_INSTR_TOKENS = re.compile(r"[a-z0-9]{3,}")


def _ops_multiset(plan: dict) -> collections.Counter:
    return collections.Counter(o["type"] for o in plan.get("operations", []))


def _plan_component_names(plan: dict) -> set[str]:
    names = set()
    for o in plan.get("operations", []):
        names.add(o.get("type", ""))
        if o.get("type") == "filter":
            names.add(str(o.get("expr", "")).split("=")[0])
    fg = plan.get("filtergraph")
    if fg:
        for m in re.finditer(r"(?<![\w.])\w+?=", str(fg)):
            names.add(m.group(0).rstrip("="))
    return names


def _citation_relevant(cit: dict, plan: dict | None, instruction: str) -> bool:
    name = cit.get("component_name")
    if name and plan is not None and name in _plan_component_names(plan):
        return True
    itok = set(_INSTR_TOKENS.findall(instruction.lower()))
    ctok = set(_INSTR_TOKENS.findall(cit.get("text", "").lower()))
    return len(itok & ctok) >= 2


def score_entry(entry: dict, res: ArmResult) -> dict:
    exp = entry["expected"]
    out: dict = {"status_ok": res.status == exp["status"]}
    if exp["status"] != "ready":
        if exp["status"] == "unsupported":
            out["unsupported_ok"] = (
                res.status == "unsupported" and res.plan is None)
        if exp["status"] == "needs_clarification":
            out["clarification_ok"] = res.status == "needs_clarification"
        return out
    if res.status != "ready" or res.plan is None:
        return out
    plan = res.plan
    if "ops" in exp:
        out["ops_ok"] = _ops_multiset(plan) == collections.Counter(exp["ops"])
    checked = passed = 0
    if "trim" in exp:
        checked += 1
        et = exp["trim"]
        if et is None:
            if plan.get("trim") is None:
                passed += 1
        elif plan.get("trim") and all(
            abs(plan["trim"][k] - et[k]) <= TRIM_TOLERANCE
            for k in ("start_seconds", "duration_seconds")
        ):
            passed += 1
    for key in ("container", "video_codec", "audio_mode", "video_bitrate_kbps"):
        if key in exp:
            checked += 1
            got = plan.get("output", {}).get(key)
            if got == exp[key]:
                passed += 1
    if "destination" in exp:
        checked += 1
        if plan.get("output_destination") == exp["destination"]:
            passed += 1
    if checked:
        out["params_checked"] = checked
        out["params_passed"] = passed
    return out


def _mean(vals: list[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else None


def _p95(vals: list[float]) -> float:
    if not vals:
        return None
    vals = sorted(vals)
    return round(vals[min(len(vals) - 1, int(0.95 * len(vals)))], 3)


def aggregate(results: list[ArmResult], entries: list[dict]) -> dict:
    by_arm: dict[str, list[dict]] = {}
    entry_by_id = {e["id"]: e for e in entries}
    for res in results:
        scores = score_entry(entry_by_id[res.id], res)
        scores["citation_precision"] = None
        if res.citations:
            relevant = sum(
                1 for c in res.citations
                if _citation_relevant(c, res.plan, entry_by_id[res.id]["instruction"])
            )
            scores["citation_precision"] = relevant / len(res.citations)
        by_arm.setdefault(res.arm, []).append(scores)

    out = {}
    for arm, scores in by_arm.items():
        res = [r for r in results if r.arm == arm]
        out[arm] = {
            "n": len(scores),
            "status_acc": _mean([s["status_ok"] for s in scores]),
            "ops_acc": _mean([s["ops_ok"] for s in scores if "ops_ok" in s]),
            "param_acc": _mean([
                s["params_passed"] / s["params_checked"]
                for s in scores if s.get("params_checked")
            ]),
            "clarification_acc": _mean([
                s["clarification_ok"]
                for s in scores if "clarification_ok" in s
            ]),
            "unsupported_acc": _mean([
                s["unsupported_ok"] for s in scores if "unsupported_ok" in s
            ]),
            "p95_latency_s": _p95([r.latency_s for r in res]),
            "mean_latency_s": _mean([r.latency_s for r in res]),
            "mean_prompt_tokens": _mean([r.prompt_chars / 4 for r in res]),
            "mean_completion_tokens": _mean([r.completion_chars / 4 for r in res]),
            "provider_errors": sum(1 for r in res if r.error),
            "evidence_entries": sum(1 for r in res if r.evidence_chunks),
            "mean_evidence_chunks": _mean([
                r.evidence_chunks for r in res if r.evidence_chunks
            ]),
            "citation_precision": _mean([
                s["citation_precision"] for s in scores
                if s["citation_precision"] is not None
            ]),
        }
    return out


# --------------------------------------------------------------- report

def write_report(out_dir: Path, entries: list[dict],
                 results: list[ArmResult], metrics: dict, args) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets = sorted({e["_dataset"] for e in entries})
    payload = {
        "arms": list(metrics),
        "llm": {"host": args.host, "port": args.port, "model": args.model},
        "rag_url": args.rag_url,
        "datasets": datasets,
        "metrics": metrics,
        "results": [
            {k: v for k, v in vars(r).items()} for r in results
        ],
    }
    (out_dir / "report.json").write_text(json.dumps(payload, indent=1))

    lines = [
        "# §9.2 NLU ablation",
        "",
        f"LLM: {args.model} @ http://{args.host}:{args.port} | "
        f"RAG: {args.rag_url} | datasets: {', '.join(datasets)}",
        "",
        "| arm | n | status | ops | params | clarif. | unsupported | "
        "p95 (s) | prompt tok | compl. tok | errors | evid. entries | cit. prec. |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, m in metrics.items():
        lines.append(
            f"| {arm} | {m['n']} | {m['status_acc']} | {m['ops_acc']} | "
            f"{m['param_acc']} | {m['clarification_acc']} | {m['unsupported_acc']} | "
            f"{m['p95_latency_s']} | {m['mean_prompt_tokens']} | "
            f"{m['mean_completion_tokens']} | {m['provider_errors']} | "
            f"{m['evidence_entries']} | {m['citation_precision']} |"
        )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------- main

SCHEMAS_BLOCK = render_schemas_block()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--datasets", default="")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--model", default="qwen3.8-27b")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--rag-url", default="http://127.0.0.1:8100")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--out-dir", default="/tmp/ablation")
    ap.add_argument("--cache", default="",
                    help="JSONL cache of completed arms (default "
                         "<out-dir>/results.jsonl) — a re-run with the same "
                         "out-dir skips completed (dataset, id, arm) pairs")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mock", action="store_true",
                    help="deterministic LLM stub (offline wiring check)")
    args = ap.parse_args()

    arms = args.arms.split(",")
    if any(a not in ARMS for a in arms):
        ap.error(f"unknown arm(s); valid: {', '.join(ARMS)}")

    if args.datasets:
        dataset_paths = [p for p in args.datasets.split(",") if p]
    else:
        dataset_paths = ["tests/benchmark/requests.json"]
        if (REPO / "tests/benchmark/ablation_heldout.json").exists():
            dataset_paths.append("tests/benchmark/ablation_heldout.json")
    entries = load_datasets(dataset_paths, args.limit or None)
    print(f"datasets: {dataset_paths} — {len(entries)} entries")

    adapter = None
    rag_cfg = None
    if any(a != "rules" for a in arms):
        adapter = LLMAdapter(provider="openai", host=args.host, port=args.port,
                             model=args.model, api_key=args.api_key or None,
                             timeout=180.0)
        if args.mock:
            adapter._chat_openai = mock_chat.__get__(adapter)
            print("mock LLM armed (offline wiring check)")
        if "llm_schemas_evidence" in arms:
            rag_cfg = RagConfig(enabled=True, url=args.rag_url,
                                timeout_ms=3000, top_k=args.top_k)

    # Per-arm JSONL cache: a crash (or re-run) resumes where it stopped —
    # rows whose config fingerprint (model/host/port/rag) differs are
    # ignored, so a re-run with a new LLM never reads stale results.
    cache_path = Path(args.cache) if args.cache \
        else Path(args.out_dir) / "results.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_sig = (f"{args.model}@{args.host}:{args.port}"
               f"|rag={args.rag_url}:topk={args.top_k}"
               + ("|mock" if args.mock else ""))
    cache: dict[tuple[str, str, str], ArmResult] = {}
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"cache: skipping truncated line in {cache_path}",
                      file=sys.stderr)
                continue
            if row.get("_cfg") != cfg_sig:
                continue
            del row["_cfg"]
            dataset = row.pop("_dataset")
            cache[(dataset, row["id"], row["arm"])] = ArmResult(**row)
        if cache:
            print(f"cache: {len(cache)} completed arms reused from {cache_path}")
    cache_f = cache_path.open("a")

    results: list[ArmResult] = []
    total = len(entries) * len(arms)
    done = 0
    for entry in entries:
        assets = assets_from_entry(entry)
        for arm in arms:
            key = (entry["_dataset"], entry["id"], arm)
            cached = key in cache
            if cached:
                res = cache[key]
            elif arm == "rules":
                res = run_rules(entry, assets)
            elif arm == "llm":
                res = run_llm(entry, assets, adapter, schemas=False,
                              rag_cfg=None)
            elif arm == "llm_schemas":
                res = run_llm(entry, assets, adapter, schemas=True,
                              rag_cfg=None)
            else:
                res = run_llm(entry, assets, adapter, schemas=True,
                              rag_cfg=rag_cfg)
            if not cached:
                cache[key] = res
                cache_f.write(json.dumps(
                    {"_cfg": cfg_sig, "_dataset": entry["_dataset"],
                     "id": res.id, "arm": res.arm, **asdict(res)}) + "\n")
                cache_f.flush()
            results.append(res)
            done += 1
            flag = "!" if res.error or res.status == "error" else ""
            note = " (cached)" if cached else ""
            print(f"[{done}/{total}] {entry['_dataset']} {entry['id']} "
                  f"{arm} -> {res.status} {res.latency_s}s {flag}{note}")

    cache_f.close()
    metrics = aggregate(results, entries)
    write_report(Path(args.out_dir), entries, results, metrics, args)
    print(f"\nreport: {Path(args.out_dir)}/report.md")
    for arm, m in metrics.items():
        print(f"  {arm:24s} status={m['status_acc']} ops={m['ops_acc']} "
              f"params={m['param_acc']} p95={m['p95_latency_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
