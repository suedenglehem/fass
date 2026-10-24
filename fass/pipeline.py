"""End-to-end pipeline (PRD section 9):

instruction -> NLU adapter -> schema validation -> semantic validation
-> capability resolution -> deterministic builder -> shell renderer
-> response
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fass.builder import build_argv, render_posix
from fass.config import load_config
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
from fass.registry import (
    REGISTRY,
    is_source_spec,
    soft_subtitle_codec,
    stream_output_info,
    stream_scheme,
)
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
    for name in ("scale", "pad", "hue", "overlay", "format",
                 "crop", "transpose", "hflip", "vflip", "setpts",
                 "subtitles", "ass"):
        if name in chain:
            used.append(name)
    return used


def _reconcile_output_destination(
    edit: EditPlan,
    output_filename: str | None,
    nlu_defaults: list[str],
    resp: GenerationResponse,
) -> None:
    """Apply an output that is a stream URL to the plan (M3b).

    The NLU adapters never see the -o / output field, so a stream URL
    passed there is a destination exactly like one named in the
    instruction. The destination's scheme is authoritative for the
    container when the plan's container was only defaulted; an explicitly
    requested, conflicting container stays a contradiction the planner
    reports.
    """
    if not output_filename:
        return
    if edit.output_destination is not None:
        if output_filename != edit.output_destination \
                and stream_output_info(output_filename) is None:
            # an inline stream destination plus a file output: a
            # contradiction, not a silent choice of one
            raise PlanError([
                f"the instruction names the stream destination "
                f"'{edit.output_destination}' but the output is a file "
                f"('{output_filename}') — name one, not both"
            ])
        return
    info = stream_output_info(output_filename)
    if info is None:
        return  # a filename, or a URL the planner will reject with a message
    dest_container, _ = info
    edit.output_destination = output_filename
    if edit.output.container != dest_container:
        defaulted = any("container" in d and "default" in d for d in nlu_defaults)
        if not defaulted:
            return  # explicit, conflicting container -> the planner's PlanError
        edit.output.container = dest_container
        resp.defaults.append(
            f"output container set to {dest_container} by the output destination {output_filename}"
        )


def _rewrite_stream_host(url: str, host_address: str, listen: bool) -> str:
    """Rewrite the stream host placeholder 'host' (M6, docker deployment).

    'host' means "the docker host", and the right address depends on the
    direction: a udp/rtp *listen* input binds 0.0.0.0 so docker's port
    forwarding can deliver the datagrams; every other pull or push address
    becomes the configured host address (the docker bridge gateway by
    default). URLs with any other hostname pass through untouched."""
    if stream_scheme(url) is None:
        return url
    parts = urlsplit(url)
    if parts.hostname != "host":
        return url
    target = "0.0.0.0" if (listen and parts.scheme in ("udp", "rtp")) else host_address
    netloc = target if not parts.port else f"{target}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _apply_stream_hosts(assets, output_filename, edit, resp) -> str | None:
    """Apply the deployment's stream-host rewrite to every stream URL the
    request carries. No-op unless streaming is enabled in the config."""
    cfg = load_config()
    sc = cfg.streaming
    if not sc.enabled or not sc.host_address:
        return output_filename
    ha = sc.host_address
    for a in assets.values():
        new = _rewrite_stream_host(a.path, ha, listen=True)
        if new != a.path:
            a.path = new
            if new.startswith("udp://0.0.0.0") or new.startswith("rtp://0.0.0.0"):
                resp.defaults.append(
                    f"input stream 'host' binds to {new} (the container listens on "
                    "all interfaces; forward the host port in the config's port mappings)"
                )
            else:
                resp.defaults.append(
                    f"input stream 'host' resolved to {new} (the configured docker host address)"
                )
    if output_filename:
        new = _rewrite_stream_host(output_filename, ha, listen=False)
        if new != output_filename:
            resp.defaults.append(
                f"output stream 'host' resolved to {new} (the configured docker host address)"
            )
            output_filename = new
    if edit.output_destination:
        new = _rewrite_stream_host(edit.output_destination, ha, listen=False)
        if new != edit.output_destination:
            edit.output_destination = new
            resp.defaults.append(
                f"output stream 'host' resolved to {new} (the configured docker host address)"
            )
    return output_filename


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

    # Stream-host rewrite (M6): the docker deployment's 'host' placeholder
    # becomes the configured docker host address / 0.0.0.0 bind.
    output_filename = _apply_stream_hosts(assets, output_filename, edit, resp)

    # Optional local inspection (plan0.md section 2): only for files that
    # exist on this machine — the MVP never uploads media.
    if media is None and ffprobe_available():
        candidate = Path(assets[edit.input_id].path)
        if candidate.exists():
            media = probe_media(str(candidate))

    try:
        _reconcile_output_destination(edit, output_filename, result.defaults, resp)
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

    main_path = assets[edit.input_id].path
    # A stream URL or a source spec (lavfi:/dev/video0) never "exists" as a
    # file — that is its nature, not a problem.
    if stream_scheme(main_path) is None and not is_source_spec(main_path) \
            and not Path(main_path).exists():
        resp.warnings.append(
            "input file was not found on this machine — the command references it exactly as given"
        )

    # Validation levels (PRD FR-5): static by default; preflight when the
    # local build can be inspected.
    resp.validation_level = "static"
    if (preflight_check or media is not None) and ffmpeg_available():
        ff = probe_ffmpeg()
        if ff is not None:
            if edit.output.gpu:
                # GPU plan (M6): the nvenc coder (the planner already
                # rejected codecs without a GPU form)
                enc = REGISTRY["gpu"]["coders"][edit.output.video_codec]["encoder"]
            else:
                enc = REGISTRY["video_codecs"][edit.output.video_codec]["encoder"]
            encoders = [enc]
            if edit.output.audio_mode.value == "encode":
                encoders.append(REGISTRY["audio_codecs"][REGISTRY["matrix"][edit.output.container]["audio_codec"]]["encoder"])
            for op in edit.operations:
                if op.type == "subtitles" and op.mode == "soft":
                    enc = soft_subtitle_codec(edit.output.container)
                    if enc:
                        encoders.append(enc)
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
