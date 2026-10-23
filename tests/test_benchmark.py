"""Milestone-1 language benchmark (plan0.md: "50 representative requests").

Each entry in tests/benchmark/requests.json is a natural-language request
with the expected outcome derived from the PRD (section 13 scenarios,
clarification policy, scope). The test runs the deterministic rules
adapter through the full pipeline and compares the *plan* — status,
operation multiset, trim interval (±0.01s), and output spec fields —
never command wording (plan0.md section 6).

Expectations follow the PRD, not the adapter: a mismatch is a bug report
against fass/nlu/rules.py, not a reason to weaken the expectation.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from fass.schema import EditPlan

REQUESTS = json.loads((Path(__file__).parent / "benchmark" / "requests.json").read_text())

TRIM_TOLERANCE = 0.01


def _ops_multiset(plan: dict) -> collections.Counter:
    return collections.Counter(o["type"] for o in plan.get("operations", []))


def _describe(r) -> str:
    return (
        f"clarifications={r.clarifications} unsupported={r.unsupported} errors={r.errors}"
    )


@pytest.mark.parametrize("entry", REQUESTS, ids=[e["id"] for e in REQUESTS])
def test_benchmark(gen, entry):
    assets_spec = entry.get("assets") or {}
    previous = entry.get("previous_plan")
    r = gen(
        entry["instruction"],
        input_path=assets_spec.get("input", "clip.mp4"),
        watermark=assets_spec.get("watermark"),
        previous_plan=EditPlan.model_validate(previous) if previous else None,
    )
    exp = entry["expected"]
    eid = entry["id"]

    assert r.status == exp["status"], (
        f"{eid}: status {r.status!r} != expected {exp['status']!r} "
        f"for {entry['instruction']!r} — {_describe(r)}"
    )

    if exp["status"] != "ready":
        if exp["status"] == "unsupported":
            assert r.plan is None, f"{eid}: unsupported must never carry a partial plan"
        return

    plan = r.plan
    assert plan is not None, f"{eid}: ready response must carry a plan"

    if "ops" in exp:
        got = _ops_multiset(plan)
        want = collections.Counter(exp["ops"])
        assert got == want, (
            f"{eid}: op multiset {dict(got)} != expected {dict(want)} "
            f"for {entry['instruction']!r}"
        )

    if "trim" in exp:
        exp_trim = exp["trim"]
        if exp_trim is None:
            assert plan["trim"] is None, f"{eid}: expected no trim, got {plan['trim']}"
        else:
            assert plan["trim"] is not None, f"{eid}: expected a trim, got None"
            assert abs(plan["trim"]["start_seconds"] - exp_trim["start_seconds"]) <= TRIM_TOLERANCE, (
                f"{eid}: trim start {plan['trim']['start_seconds']} != "
                f"{exp_trim['start_seconds']} (±{TRIM_TOLERANCE})"
            )
            assert abs(plan["trim"]["duration_seconds"] - exp_trim["duration_seconds"]) <= TRIM_TOLERANCE, (
                f"{eid}: trim duration {plan['trim']['duration_seconds']} != "
                f"{exp_trim['duration_seconds']} (±{TRIM_TOLERANCE})"
            )

    for key in ("container", "video_codec", "audio_mode", "video_bitrate_kbps"):
        if key in exp:
            got = plan["output"].get(key)
            want = exp[key]
            if key == "audio_mode":
                got = got.value if hasattr(got, "value") else got
            assert got == want, (
                f"{eid}: output.{key} {got!r} != expected {want!r}"
            )
