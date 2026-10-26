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

from fass.builder import build_argv, build_argv_all, render_posix
from fass.config import load_config
from fass.execute import resolve_input_name
from fass.explain import summarize
from fass.nlu import get_adapter
from fass.nlu.base import AdapterError
from fass.planner import PlanError, plan_execution
from fass.probe.ffprobe import (
    Check,
    MediaInfo,
    ffmpeg_available,
    ffprobe_available,
    probe_ffmpeg,
    probe_media,
    preflight,
)
from fass.registry import (
    REGISTRY,
    all_filter_names,
    audio_for,
    container_from_extension,
    is_source_spec,
    soft_subtitle_codec,
    stream_output_info,
    stream_scheme,
)
from fass.registry.loader import _split_top_level
from fass.schema import AssetMap, EditPlan, ParseResult, ParseStatus


@dataclass
class GenerationResponse:
    status: str  # ready | needs_clarification | unsupported | error
    plan: dict | None = None
    arguments: list[str] | None = None
    command: str | None = None
    # M10.5: multi-pass plans (two-pass stabilization). One dict per pass,
    # in execution order (the final pass last); top-level arguments/command
    # always describe the FINAL pass — the one that produces the artifact.
    steps: list[dict] | None = None
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


def _graph_names(graph: str) -> list[str]:
    """Filter instance names in a graph — chains split on ';', instances
    on ',' (quotes/escapes honored, the same split the registry validator
    uses); pad labels are stripped, only the filter name is kept."""
    names: list[str] = []
    for chain in _split_top_level(graph, ";"):
        for inst in _split_top_level(chain.strip(), ","):
            s = inst.strip()
            if s.startswith("["):
                end = s.find("]")
                if end >= 0:
                    s = s[end + 1:]
            name = s.split("=", 1)[0].split("[", 1)[0].strip()
            if name:
                names.append(name)
    return names


def _used_filters(ep) -> list[str]:
    """Filter names the plan's graphs use — parsed from -vf, -af and
    filter_complex (of every pass in a multi-pass plan), restricted to the
    registry's filter vocabulary, so the preflight checks exactly the
    filters the command will run."""
    allowed = all_filter_names()
    used: list[str] = []
    graphs: list[str | None] = []
    for p in (ep.passes or [ep]):
        graphs.extend((p.vf, p.af, p.filter_complex))
    for graph in graphs:
        if not graph:
            continue
        for name in _graph_names(graph):
            if name in allowed and name not in used:
                used.append(name)
    return used


def _output_muxer(ep) -> str | None:
    """Muxer the plan's output destination will be written with: the
    stream scheme's muxer for URLs, the container's muxer for files
    (registry-driven). None for destinations with no muxer (audio devices)."""
    dest = ep.output_path
    info = stream_output_info(dest)
    if info is not None:
        return info[1]
    name = dest.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    container = container_from_extension(name.rsplit(".", 1)[-1])
    if container is not None:
        return REGISTRY["containers"][container]["muxer"]
    return None


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

    # Optional local inspection: only for files that
    # exist on this machine — the MVP never uploads media. A bare name
    # resolves against the deployment's input volume, as the executor
    # does at run time.
    if media is None and ffprobe_available():
        candidate = Path(resolve_input_name(assets[edit.input_id].path))
        if candidate.exists():
            media = probe_media(str(candidate))

    try:
        _reconcile_output_destination(edit, output_filename, result.defaults, resp)
        ep = plan_execution(edit, assets, media, output_filename)
    except PlanError as e:
        resp.status = "error"
        resp.errors = list(e.errors)
        return resp

    argvs = build_argv_all(ep)
    argv = argvs[-1]  # the final pass — the one that produces the artifact
    resp.status = "ready"
    resp.arguments = argv
    resp.command = render_posix(argv)
    if len(argvs) > 1:
        resp.steps = [
            {"pass": i + 1, "purpose": p.purpose, "arguments": a,
             "command": render_posix(a), "output": p.output_path}
            for i, (p, a) in enumerate(zip(ep.passes, argvs))
        ]
    resp.plan = edit.model_dump()
    resp.summary = summarize(edit, ep, media)
    resp.defaults.extend(ep.defaults)
    resp.warnings.extend(ep.warnings)

    main_path = assets[edit.input_id].path
    # A stream URL or a source spec (lavfi:/dev/video0) never "exists" as a
    # file — that is its nature, not a problem. A bare name existing in
    # the deployment's input volume is found through the same resolution
    # the executor applies at run time.
    if stream_scheme(main_path) is None and not is_source_spec(main_path) \
            and not Path(resolve_input_name(main_path)).exists():
        resp.warnings.append(
            "input file was not found on this machine — the command references it exactly as given"
        )

    # Validation levels (PRD FR-5): static by default; preflight when the
    # local build can be inspected. Per output mode: a video plan checks
    # its encoders, an audio plan its audio encoder, a subtitle dump needs
    # a subtitle stream in the input — and every plan checks its output
    # muxer against the build's probed '-muxers' set (availability is
    # build-specific, so it is probed, never assumed).
    resp.validation_level = "static"
    if (preflight_check or media is not None) and ffmpeg_available():
        ff = probe_ffmpeg()
        if ff is not None:
            encoders: list[str] = []
            muxers: list[str] = []
            mux = _output_muxer(ep)
            if mux is not None:
                muxers.append(mux)
            if edit.output.mode == "video":
                if edit.output.gpu:
                    # GPU plan (M6): the nvenc coder (the planner already
                    # rejected codecs without a GPU form)
                    enc = REGISTRY["gpu"]["coders"][edit.output.video_codec]["encoder"]
                elif edit.output.qsv:
                    # QSV plan (M8): the Quick Sync coder (the capability
                    # gate already rejected codecs without a QSV form)
                    enc = REGISTRY["qsv"]["coders"][edit.output.video_codec]["encoder"]
                else:
                    enc = REGISTRY["video_codecs"][edit.output.video_codec]["encoder"]
                encoders.append(enc)
                if edit.output.audio_mode.value == "encode":
                    acodec = edit.output.audio_codec or audio_for(edit.output.container)
                    encoders.append(REGISTRY["audio_codecs"][acodec]["encoder"])
                for op in edit.operations:
                    if op.type == "subtitles" and op.mode == "soft":
                        enc = soft_subtitle_codec(edit.output.container)
                        if enc:
                            encoders.append(enc)
            elif edit.output.mode == "audio":
                encoders.append(REGISTRY["audio_codecs"][edit.output.audio_codec]["encoder"])
            checks = preflight(media, ff, encoders, _used_filters(ep), muxers)
            if edit.output.mode == "subtitles" and media is not None:
                has_subs = media.subtitle_streams > 0
                checks.append(Check(
                    "input subtitle streams", has_subs,
                    f"{media.subtitle_streams} subtitle stream(s) in the input"
                    if has_subs else
                    "the input has no subtitle stream to dump",
                ))
            resp.checks = [dataclasses.asdict(c) for c in checks]
            if all(c.ok for c in checks):
                resp.validation_level = "preflight"
            else:
                missing = [f"{c.name} ({c.detail})" for c in checks if not c.ok]
                resp.warnings.append("local FFmpeg build check failed: " + "; ".join(missing))
    elif preflight_check and not ffmpeg_available():
        resp.warnings.append("--preflight requested but ffmpeg is not installed here")

    return resp
