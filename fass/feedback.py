"""Consented feedback logging (PRD FR-7).

Failures are recorded by category — incorrect intent, missing
clarification, unsupported capability, builder bug, environment
mismatch — because each category has a different fix. The instruction
text itself is stored only with separate explicit consent.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

CATEGORIES = [
    "wrong_intent",
    "missing_clarification",
    "unsupported_capability",
    "builder_bug",
    "environment_mismatch",
    "other",
]

_HOMES = [str(Path.home())]


def _redact(text: str) -> str:
    """Replace local home paths with ~ so diagnostics stay shareable."""
    out = text
    for h in _HOMES:
        out = out.replace(h, "~")
    out = re.sub(r"(?<![\w])(/[\w.~-]+){3,}", "<path>", out)
    return out


def feedback_path() -> Path:
    p = Path(os.environ.get("FASS_FEEDBACK_FILE", str(Path.home() / ".fass" / "feedback.jsonl")))
    return p


def log_feedback(
    useful: bool | None,
    category: str | None = None,
    note: str | None = None,
    ffmpeg_error: str | None = None,
    instruction: str | None = None,
    consent: bool = False,
) -> Path:
    if category and category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "useful": useful,
        "category": category,
        "note": _redact(note) if note else None,
        "ffmpeg_error": _redact(ffmpeg_error) if ffmpeg_error else None,
        "consent": bool(consent),
    }
    if instruction and consent:
        entry["instruction"] = _redact(instruction)
    path = feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return path
