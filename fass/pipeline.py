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
from fass.config import load_config, resolved_rag
from fass.execute import resolve_input_name
from fass.explain import summarize
from fass.nlu import get_adapter
from fass.nlu.base import AdapterError
from fass.planner import PlanError, plan_execution
from fass.rag.citations import chunk_citation, derive_citations
from fass.rag.client import RagSearchResult, retrieve as rag_retrieve
from fass.rag.evidence import format_evidence_block
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
    # M11.2: documentation evidence (PRD §6.4) — citations derived from
    # the plan's used components; None when there is nothing to cite.
    evidence: dict | None = None
    # The LLM's verbatim reply — what the model returned before any
    # processing (JSON extraction / schema validation / plan building) —
    # so the UI can show it. None for the rules adapter and when the model
    # never produced an answer (transport failure).
    raw_llm: str | None = None

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


def build_evidence(edit, ep, rag_result) -> dict | None:
    """The response's evidence block (PRD §6.4) or None when there is
    nothing to cite. Citations are derived from the plan's used
    components — never model-generated."""
    citations = derive_citations(edit, _used_filters(ep), rag_result)
    if rag_result is not None:
        # RAG was consulted: report its outcome even when degraded —
        # a failed search is status "unavailable" (PRD §4.4), with the
        # static registry_doc_ref citations the plan can still show.
        warnings = list(rag_result.warnings)
        if rag_result.ok:
            # PRD §9.3: evidence the plan did not use cannot affect the
            # command — say so, once per unused retrieved filter.
            cited = {c["component"] for c in citations}
            seen: set[str] = set()
            for chunk in rag_result.chunks:
                ctype = chunk.get("component_type")
                cname = chunk.get("component_name")
                if ctype != "filter" or not cname:
                    continue
                key = f"filter:{cname}"
                if key not in cited and key not in seen:
                    seen.add(key)
                    warnings.append(
                        f"evidence for {key} was not used by the plan; "
                        "the command is unaffected by it")
        return {
            "status": ("unavailable" if not rag_result.ok
                       else rag_result.evidence_status or "insufficient"),
            "corpus_id": rag_result.corpus_id,
            "corpus_match": rag_result.corpus_match,
            "retrieval_ms": rag_result.retrieval_ms,
            "citations": citations,
            "warnings": warnings,
        }
    if citations:
        # RAG off: static doc_ref citations only (PRD §6.5) — they work
        # with RAG disabled and cost nothing at request time.
        return {"status": "static", "corpus_id": None, "corpus_match": None,
                "retrieval_ms": None, "citations": citations,
                "warnings": []}
    return None


def _degraded_rag(e: Exception) -> RagSearchResult:
    """PRD §9.3 — the client is contractually fail-open (it never
    raises); if it ever does, degrade to the same shape instead of
    failing the generation."""
    return RagSearchResult(
        ok=False, query=None, corpus_id=None, corpus_match="none",
        warnings=[f"RAG retrieval failed: {e}"])


def _closest_supported(instruction, media, rag_result) -> dict | None:
    """PRD §6.5 — fail-open "closest supported" note for an unsupported
    request: what FFmpeg documents, and what fass does not support yet.
    Never produces a command and never changes ``status``.

    ``rag_result`` is the LLM path's already-fetched result (reused,
    no second call); rules-path callers pass None and the note is
    fetched fail-open — RAG disabled means no note."""
    if rag_result is None:
        cfg = resolved_rag()
        if not cfg.enabled:
            return None
        info = probe_ffmpeg()
        try:
            rag_result = rag_retrieve(
                cfg, instruction, media=media,
                probed_version=info.version if info else None)
        except Exception as e:  # noqa: BLE001 — fail-open (see above)
            rag_result = _degraded_rag(e)
    if rag_result is None or not rag_result.ok or not rag_result.chunks:
        return None
    top = rag_result.chunks[0]
    ctype = top.get("component_type") or "component"
    cname = top.get("component_name") or top.get("title") or "this"
    return {
        "status": "unsupported",
        "corpus_id": rag_result.corpus_id,
        "corpus_match": rag_result.corpus_match,
        "retrieval_ms": rag_result.retrieval_ms,
        "citations": [chunk_citation(top, f"{ctype}:{cname}")],
        "warnings": list(rag_result.warnings),
        "note": (f"FFmpeg documents this as `{ctype}:{cname}`; "
                 f"fass does not support it yet."),
    }


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
    if info is not None:
        dest_container, _ = info
        edit.output_destination = output_filename
        if edit.output.container != dest_container:
            defaulted = any("container" in d and "default" in d
                            for d in nlu_defaults)
            if not defaulted:
                return  # explicit, conflicting container -> the planner's PlanError
            edit.output.container = dest_container
            resp.defaults.append(
                f"output container set to {dest_container} by the output destination {output_filename}"
            )
        return
    # A file destination: its extension is authoritative for the container
    # when the plan's container was only defaulted — an mp4/mov default must
    # not ship -movflags +faststart into an mpegts (.ts) output. Subtitle
    # dumps are excluded: the stream 'srt' container shadows 'srt_file' for
    # the .srt extension.
    if edit.output.mode == "subtitles":
        return
    dest_container = container_from_extension(
        Path(output_filename).suffix.lstrip("."))
    if dest_container is None:
        return  # no extension, or one the registry doesn't map
    if edit.output.container != dest_container:
        defaulted = any("container" in d and "default" in d
                        for d in nlu_defaults)
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
    verbose: bool = False,
) -> GenerationResponse:
    resp = GenerationResponse(status="")
    try:
        adapter = get_adapter(adapter_name)
    except AdapterError as e:
        resp.status = "error"
        resp.errors = [str(e)]
        return resp
    resp.adapter = adapter.name

    # Source-relative NLU needs the input's probed properties BEFORE
    # parse: 'keep the same height as the source file', aspect-ratio math
    # and the same-value warnings all compare against the probe. Only
    # local files can be probed (MVP: no uploads) — a stream input leaves
    # media None and the NLU asks for the explicit value instead.
    probed_id: str | None = None
    if media is None and ffprobe_available():
        for aid in ("main", *assets):
            a = assets.get(aid)
            if a is None:
                continue
            candidate = Path(resolve_input_name(a.path))
            if candidate.exists():
                media = probe_media(str(candidate))
                probed_id = aid
                break

    # RAG retrieval (M11.2 §6.3): LLM path only — the rules adapter never
    # calls the service (PRD §6.5), and RAG is opt-in. Fail-open:
    # retrieve() degrades to a RAG-less generation, it never raises.
    rag_cfg = None
    rag_result = None
    if adapter.name == "llm":
        rag_cfg = resolved_rag()
        if rag_cfg.enabled:
            info = probe_ffmpeg()
            try:
                rag_result = rag_retrieve(
                    rag_cfg, instruction, media=media,
                    probed_version=info.version if info else None)
            except Exception as e:  # noqa: BLE001 — fail-open
                rag_result = _degraded_rag(e)

    # The formatted evidence block goes into the LLM's USER TURN (the
    # system prompt stays stable across requests — PRD §6.3).
    evidence_block = None
    if rag_result is not None and rag_result.ok:
        evidence_block = format_evidence_block(
            rag_result, budget=rag_cfg.max_evidence_chars)

    try:
        if evidence_block:
            result: ParseResult = adapter.parse(
                instruction, assets, previous_plan, media,
                evidence=evidence_block)
        else:
            result: ParseResult = adapter.parse(
                instruction, assets, previous_plan, media)
    except Exception as e:  # provider/transport/schema failures
        resp.status = "error"
        resp.errors = [f"parser error: {e}"]
        # on a schema-validation failure the LLM adapter attached the
        # verbatim reply to the error — a broken plan is exactly when the
        # user wants to see what the model actually returned
        resp.raw_llm = getattr(e, "raw_output", None)
        return resp

    resp.raw_llm = result.raw
    resp.clarifications = [c.model_dump() for c in result.clarifications]
    resp.unsupported = [u.model_dump() for u in result.unsupported]
    resp.defaults.extend(result.defaults)

    if result.status is not ParseStatus.READY or result.plan is None:
        resp.status = result.status.value
        if result.status is ParseStatus.UNSUPPORTED:
            # PRD §6.5: an optional fail-open note naming the component
            # FFmpeg documents; the status above is never changed.
            resp.evidence = _closest_supported(
                instruction, media, rag_result)
        return resp
    edit = result.plan

    # Stream-host rewrite (M6): the docker deployment's 'host' placeholder
    # becomes the configured docker host address / 0.0.0.0 bind.
    output_filename = _apply_stream_hosts(assets, output_filename, edit, resp)

    # Optional local inspection: only for files that
    # exist on this machine — the MVP never uploads media. A bare name
    # resolves against the deployment's input volume, as the executor
    # does at run time. The planner's metadata must describe the plan's
    # INPUT asset, so when the pre-parse probe hit a different asset
    # (e.g. 'main' while the plan's input is another id) the probe is
    # re-pointed at the actual input.
    if media is None and ffprobe_available():
        candidate = Path(resolve_input_name(assets[edit.input_id].path))
        if candidate.exists():
            media = probe_media(str(candidate))
    elif probed_id is not None and edit.input_id != probed_id:
        input_asset = assets.get(edit.input_id)
        if input_asset is not None:
            candidate = Path(resolve_input_name(input_asset.path))
            if candidate.exists():
                media = probe_media(str(candidate)) or media

    try:
        _reconcile_output_destination(edit, output_filename, result.defaults, resp)
        ep = plan_execution(edit, assets, media, output_filename)
    except PlanError as e:
        resp.status = "error"
        resp.errors = list(e.errors)
        return resp

    if verbose:
        # Verbose-log mode (M10.7): the plan's -loglevel is raised to
        # 'verbose' BEFORE the argv is built, so every pass of a multi-pass
        # plan shows it in the displayed command (and the executor writes
        # the run log into {output}/log/ — see execute.start_run).
        ep.loglevel = "verbose"
        for p in ep.passes or []:
            p.loglevel = "verbose"
        resp.defaults.append(
            "verbose log requested — every pass runs with -loglevel verbose "
            "and the run log is written to the output folder's log/ directory")

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
    # PRD §6.4: citations from the plan's used components (retrieved
    # chunks when RAG ran, registry doc_refs otherwise).
    resp.evidence = build_evidence(edit, ep, rag_result)

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
                if edit.output.video_codec != "copy":
                    # "copy" has no encoder to check — the stream passes
                    # through untouched (the planner already validated the
                    # container carries the input codec).
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
                if edit.output.audio_mode.value == "encode" \
                        and audio_for(edit.output.container) is not None:
                    # A no-audio container (e.g. GIF) has no audio encoder —
                    # the planner emitted -an for it.
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
