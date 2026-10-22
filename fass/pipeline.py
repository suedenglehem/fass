"""End-to-end pipeline (PRD section 9):

instruction -> NLU adapter -> schema validation -> semantic validation
-> capability resolution -> deterministic builder -> shell renderer
-> response
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from fass.builder import build_argv, render_posix
from fass.explain import summarize
from fass.nlu import get_adapter
from fass.nlu.base import AdapterError
from fass.planner import PlanError, plan_execution
from fass.probe.ffprobe import (
    MediaInfo,
    ffmpeg_available,
    ffprobe_available,
    probe_ffmpeg,
    probe_media,
    preflight,
)
from fass.registry import REGISTRY
from fass.schema import AssetMap, EditPlan, ParseResult, ParseStatus


@dataclass
class GenerationResponse:
    status: str  # ready | needs_clarification | unsupported | error
    plan: dict | None = None
    arguments: list[str] | None = None
    command: str | None = None
    summary: list[str] = field(default_factory=list)
    defaults: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation_level: str = "none"  # none | static | preflight
    checks: list[dict] = field(default_factory=list)
    clarifications: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    adapter: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _used_filters(ep) -> list[str]:
    chain = f"{ep.vf or ''}{ep.filter_complex or ''}"
    used = []
    for name in ("scale", "pad", "hue", "overlay", "format"):
        if name in chain:
            used.append(name)
    return used


def generate(
    instruction: str,
    assets: AssetMap,
    output_filename: str | None = None,
    previous_plan: EditPlan | None = None,
    media: MediaInfo | None = None,
    adapter_name: str = "auto",
    preflight_check: bool = False,
) -> GenerationResponse:
    resp = GenerationResponse(status="")
    try:
        adapter = get_adapter(adapter_name)
    except AdapterError as e:
        resp.status = "error"
        resp.errors = [str(e)]
        return resp
    resp.adapter = adapter.name

    try:
        result: ParseResult = adapter.parse(instruction, assets, previous_plan, media)
    except Exception as e:  # provider/transport/schema failures
        resp.status = "error"
        resp.errors = [f"parser error: {e}"]
        return resp

    resp.clarifications = [c.model_dump() for c in result.clarifications]
    resp.unsupported = [u.model_dump() for u in result.unsupported]
    resp.defaults.extend(result.defaults)

    if result.status is not ParseStatus.READY or result.plan is None:
        resp.status = result.status.value
        return resp
    edit = result.plan

    # Optional local inspection (plan0.md section 2): only for files that
    # exist on this machine — the MVP never uploads media.
    if media is None and ffprobe_available():
        candidate = Path(assets[edit.input_id].path)
        if candidate.exists():
            media = probe_media(str(candidate))

    try:
        ep = plan_execution(edit, assets, media, output_filename)
    except PlanError as e:
        resp.status = "error"
        resp.errors = list(e.errors)
        return resp

    argv = build_argv(ep)
    resp.status = "ready"
    resp.arguments = argv
    resp.command = render_posix(argv)
    resp.plan = edit.model_dump()
    resp.summary = summarize(edit, ep, media)
    resp.defaults.extend(ep.defaults)
    resp.warnings.extend(ep.warnings)

    if not Path(assets[edit.input_id].path).exists():
        resp.warnings.append(
            "input file was not found on this machine — the command references it exactly as given"
        )

    # Validation levels (PRD FR-5): static by default; preflight when the
    # local build can be inspected.
    resp.validation_level = "static"
    if (preflight_check or media is not None) and ffmpeg_available():
        ff = probe_ffmpeg()
        if ff is not None:
            encoders = [REGISTRY["video_codecs"][edit.output.video_codec]["encoder"]]
            if edit.output.audio_mode.value == "encode":
                encoders.append(REGISTRY["audio_codecs"][REGISTRY["matrix"][edit.output.container]["audio_codec"]]["encoder"])
            checks = preflight(media, ff, encoders, _used_filters(ep))
            resp.checks = [dataclasses.asdict(c) for c in checks]
            if all(c.ok for c in checks):
                resp.validation_level = "preflight"
            else:
                missing = [f"{c.name} ({c.detail})" for c in checks if not c.ok]
                resp.warnings.append("local FFmpeg build check failed: " + "; ".join(missing))
    elif preflight_check and not ffmpeg_available():
        resp.warnings.append("--preflight requested but ffmpeg is not installed here")

    return resp
