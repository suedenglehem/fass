"""Shape validation for the PRD §9.2 held-out ablation dataset.

tests/benchmark/ablation_heldout.json is the held-out paraphrase/jargon
set (plus ambiguity, unsupported, and adversarial sets) the §9.2 NLU
ablation runs the four arms against. This test validates the *shape* of
the dataset — counts, ids, entry structure, the operation vocabulary
derived from fass/schema/plan.py, and the held-out property that no
instruction is a literal phrase-table string from fass/nlu/rules.py.

It runs no NLU, no ffmpeg, no network, no yaml: pure data validation,
fast and deterministic. The 'class' key marks each entry's set and is
read only here — the benchmark harness reads requests.json, not this
file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from fass.schema import plan as plan_schema

DATA = json.loads((Path(__file__).parent / "benchmark" / "ablation_heldout.json").read_text())
RULES_SOURCE = (Path(__file__).parent.parent / "fass" / "nlu" / "rules.py").read_text()

# The statuses the benchmark harness (tests/test_benchmark.py) accepts.
ALLOWED_STATUSES = {"ready", "needs_clarification", "unsupported", "error"}

# Expected-field vocabulary the harness actually checks (plus "note",
# the free-form field this dataset adds for ambiguity/unsupported).
ALLOWED_EXPECTED_KEYS = {
    "status", "ops", "trim", "container", "video_codec", "audio_mode",
    "video_bitrate_kbps", "destination",
}
ALLOWED_ENTRY_KEYS = {"id", "instruction", "assets", "expected", "class", "note"}

CLASSES = {"paraphrase", "ambiguity", "unsupported", "adversarial"}
REQUIRED_COUNTS = {"ambiguity": 20, "unsupported": 20, "adversarial": 10}
MIN_PARAPHRASE = 60

ID_RE = re.compile(r"^abl-\d{3}$")


def _operation_vocabulary() -> set[str]:
    """The plan schema's operation vocabulary, derived from
    fass/schema/plan.py (the discriminated Operation union), not a list
    kept in sync by hand."""
    union = get_args(plan_schema.Operation)[0]
    ops: set[str] = set()
    for cls in get_args(union):
        ops |= set(get_args(cls.model_fields["type"].annotation))
    assert ops, "operation vocabulary derived from plan.py is empty"
    return ops


OP_VOCAB = _operation_vocabulary()


def test_dataset_nonempty_and_entry_shape():
    assert isinstance(DATA, list) and DATA, "dataset must be a nonempty list"
    for entry in DATA:
        assert isinstance(entry, dict), f"entry not an object: {entry!r}"
        extra = set(entry) - ALLOWED_ENTRY_KEYS
        assert not extra, f"{entry.get('id')}: unexpected entry keys {extra}"


def test_class_counts():
    counts = {c: 0 for c in CLASSES}
    for entry in DATA:
        cls = entry.get("class")
        assert cls in CLASSES, f"{entry.get('id')}: class {cls!r} not in {sorted(CLASSES)}"
        counts[cls] += 1
    assert counts["paraphrase"] >= MIN_PARAPHRASE, (
        f"paraphrase set has {counts['paraphrase']} entries, needs >= {MIN_PARAPHRASE}")
    for cls, want in REQUIRED_COUNTS.items():
        assert counts[cls] == want, f"{cls} set has {counts[cls]} entries, needs exactly {want}"


def test_ids_unique_format_and_order():
    ids = [e["id"] for e in DATA]
    for i, eid in enumerate(ids, start=1):
        assert ID_RE.fullmatch(eid), f"entry {i}: id {eid!r} does not match abl-NNN"
        assert eid == f"abl-{i:03d}", f"entry {i}: id {eid!r} out of order"
    assert len(set(ids)) == len(ids), "ids must be unique"


def test_instructions_are_nonempty_strings():
    for entry in DATA:
        ins = entry.get("instruction")
        assert isinstance(ins, str) and ins.strip(), f"{entry['id']}: instruction must be a non-empty string"


def test_expected_nonempty_and_keys():
    for entry in DATA:
        exp = entry.get("expected")
        assert isinstance(exp, dict) and exp, f"{entry['id']}: expected must be a non-empty dict"
        extra = set(exp) - ALLOWED_EXPECTED_KEYS
        assert not extra, f"{entry['id']}: expected keys {extra} are not read by the benchmark harness"


def test_status_vocabulary():
    for entry in DATA:
        status = entry["expected"].get("status")
        assert status in ALLOWED_STATUSES, (
            f"{entry['id']}: status {status!r} not in {sorted(ALLOWED_STATUSES)}")


def test_ops_subset_of_plan_schema_vocabulary():
    for entry in DATA:
        exp = entry["expected"]
        if "ops" not in exp:
            continue
        ops = exp["ops"]
        assert isinstance(ops, list), f"{entry['id']}: ops must be a list"
        bad = set(ops) - OP_VOCAB
        assert not bad, f"{entry['id']}: ops {bad} outside the plan.py operation vocabulary {sorted(OP_VOCAB)}"


def test_ready_entries_carry_ops_and_nonready_do_not():
    for entry in DATA:
        exp = entry["expected"]
        if exp["status"] == "ready":
            assert "ops" in exp, f"{entry['id']}: ready entry must fully specify its expected ops"
        else:
            assert "ops" not in exp, f"{entry['id']}: {exp['status']} entry must not carry ops"


def test_trim_shape():
    for entry in DATA:
        trim = entry["expected"].get("trim")
        if trim is None:
            continue
        assert isinstance(trim, dict), f"{entry['id']}: trim must be an object"
        for key in ("start_seconds", "duration_seconds"):
            v = trim.get(key)
            assert isinstance(v, (int, float)) and not isinstance(v, bool), (
                f"{entry['id']}: trim.{key} must be a number")
        assert trim["start_seconds"] >= 0 and trim["duration_seconds"] > 0, (
            f"{entry['id']}: trim interval invalid: {trim}")


def test_assets_shape():
    for entry in DATA:
        assets = entry.get("assets")
        if assets is None:
            continue
        assert isinstance(assets, dict), f"{entry['id']}: assets must be an object"
        for aid, path in assets.items():
            assert isinstance(aid, str) and aid, f"{entry['id']}: bad asset id {aid!r}"
            assert isinstance(path, str) and path.strip(), f"{entry['id']}: asset {aid} path must be a non-empty string"


def test_note_only_on_ambiguity_and_unsupported():
    for entry in DATA:
        note = entry.get("note")
        if note is None:
            continue
        assert isinstance(note, str) and note.strip(), f"{entry['id']}: note must be a non-empty string"
        assert entry["class"] in ("ambiguity", "unsupported"), (
            f"{entry['id']}: note is only allowed on ambiguity/unsupported entries")


def test_no_instruction_is_a_literal_rules_phrase():
    """The held-out property: every instruction is a human paraphrase,
    never a phrase lifted verbatim from the frozen rules table."""
    for entry in DATA:
        ins = entry["instruction"].strip().lower()
        assert ins not in RULES_SOURCE, (
            f"{entry['id']}: instruction {entry['instruction']!r} occurs verbatim "
            f"in fass/nlu/rules.py — the held-out set must not reuse literal phrase-table forms")


def test_adversarial_entries_embed_injection_text():
    """The adversarial set's point: each instruction carries injection
    markers, and its expectation is the benign parse outcome (the
    injection changes nothing)."""
    markers = re.compile(
        r"ignore previous instructions|system:|disregard all safety|"
        r"new instructions:|system override|assistant must comply|"
        r"inject:|\[INST\]|no-self-control", re.I)
    for entry in DATA:
        if entry["class"] != "adversarial":
            continue
        assert markers.search(entry["instruction"]), (
            f"{entry['id']}: adversarial entry must embed an injection attempt")
        assert entry["expected"]["status"] in ("ready", "unsupported"), (
            f"{entry['id']}: adversarial expectation must be the benign parsed outcome "
            f"(ready) or unsupported — the injection must not steer a clarification")
        if entry["expected"]["status"] == "ready":
            assert "ops" in entry["expected"], (
                f"{entry['id']}: benign outcome must be fully specified")
