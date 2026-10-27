"""Planner: editing plan -> execution plan.

Keeps *what the user wants* (EditPlan) separate from *how to do it*
(stream selection, encoders, filters, option placement). Every default
and warning is produced here, so nothing is hidden inside model output
or inside the rendered command.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from fass.execute import resolve_input_name
from fass.probe.ffprobe import (MediaInfo, ffmpeg_available, probe_ffmpeg,
                                probe_has_pts, probe_media)
from fass.registry import (
    REGISTRY,
    audio_bsf_for_copy,
    audio_for,
    atempo_limits,
    bsf_for_copy,
    bsf_spec,
    capability_errors,
    combiner_domain,
    combiner_filters,
    diagnostic_output,
    extension_for,
    filter_spec,
    filtergraph_output_labels,
    gpu_coder,
    input_format_for,
    output_device_for,
    output_device_parts,
    output_schemes,
    qsv_coder,
    soft_subtitle_codec,
    source_spec_parts,
    stream_output_info,
    stream_scheme,
    subtitle_burn_expr,
    subtitle_dump_codec,
    subtitle_input_format,
)
from fass.schema import (
    AssetKind,
    AudioMode,
    AudioResampleOp,
    CropOp,
    EditPlan,
    ResizeOp,
    RotateOp,
    SpeedOp,
    SubtitleOp,
    WatermarkOp,
)

MAX_OPERATIONS = 8


class PlanError(Exception):
    """Semantic / capability validation failure (PRD FR-5)."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(errors))


@dataclass
class InputSpec:
    path: str
    pre_opts: list[str] = field(default_factory=list)  # e.g. ["-ss", "10"]


@dataclass
class Pass:
    """One ffmpeg invocation of a multi-pass execution (M10.5).

    A two-pass plan (stabilization) runs the passes in order, in the same
    working directory: the detect pass writes its side-file (the .trf) as
    a bare name, and the transform pass reads it back — no path rewriting
    between passes. The ExecutionPlan's top-level fields (vf/maps/...)
    mirror the FINAL pass, so single-pass consumers (builder, summarize,
    preflight) work unchanged."""

    purpose: str  # e.g. "stabilize-detect" | "stabilize-transform"
    inputs: list[InputSpec]
    output_path: str
    vf: str | None = None
    filter_complex: str | None = None
    af: str | None = None
    maps: list[str] = field(default_factory=list)
    output_opts: list[str] = field(default_factory=list)
    loglevel: str = "error"


@dataclass
class ExecutionPlan:
    mode: str  # "copy" (remux) | "reencode" | "device"
    inputs: list[InputSpec]
    vf: str | None  # simple -vf chain (no watermark)
    filter_complex: str | None  # labeled graph (watermark present)
    maps: list[str]
    output_opts: list[str]
    output_path: str
    af: str | None = None  # audio filter chain (-af): speed changes
    loglevel: str = "error"  # builder's -loglevel; diagnostic plans raise it to info
    defaults: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # M10.5: multi-pass execution. None for single-pass plans; when set,
    # the passes run in order and the top-level fields above mirror the
    # final (transform+encode) pass.
    passes: list[Pass] | None = None
    # side-files a multi-pass plan writes next to the output (bare names)
    intermediates: list[str] = field(default_factory=list)


def _main_input(path: str) -> InputSpec:
    """Main input spec.

    Generic source specs ('lavfi:testsrc=…', 'v4l2:/dev/video0;video_size=…')
    get -f <format> plus their ';key=value' options as separate option
    tokens (registry sources); raw udp/rtp/tcp stream demuxes get an
    explicit -f (registry streaming.input_format); everything else is
    probed by ffmpeg."""
    parts = source_spec_parts(path)
    if parts is not None:
        fmt, spec, opts = parts
        pre = ["-f", fmt]
        pre += [token for k, v in opts for token in (f"-{k}", v)]
        return InputSpec(spec, pre_opts=pre)
    fmt = input_format_for(path)
    return InputSpec(path, pre_opts=["-f", fmt] if fmt else [])


def even(n: int) -> int:
    """Snap to an even value (yuv420p requires even dimensions)."""
    return n + (n % 2)


def fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


_ffmpeg_version_cache: tuple[int, ...] | None = None
_ffmpeg_version_probed = False


def local_ffmpeg_version() -> tuple[int, ...] | None:
    """Numeric version of the local FFmpeg build (e.g. (9, 0, 1)), probed
    once per process. None when FFmpeg is not on PATH or the version line
    cannot be parsed."""
    global _ffmpeg_version_cache, _ffmpeg_version_probed
    if _ffmpeg_version_probed:
        return _ffmpeg_version_cache
    _ffmpeg_version_probed = True
    if not ffmpeg_available():
        return None
    m = re.search(r"ffmpeg version (\d+(?:\.\d+)+)",
                  probe_ffmpeg().version or "")
    _ffmpeg_version_cache = (
        tuple(int(p) for p in m.group(1).split(".")) if m else None
    )
    return _ffmpeg_version_cache


def _version_note(subject: str, min_version: str) -> str | None:
    """Warning text when the local build predates a min_version feature —
    or its availability is unverified (no local FFmpeg). None when the
    probed build is at least min_version: no warning on 9.0.1 for a
    7.1-gated feature."""
    ver = local_ffmpeg_version()
    if ver is None:
        return (f"{subject} requires FFmpeg {min_version}+ — the local "
                "FFmpeg build could not be probed, so its availability "
                "is unverified")
    need = tuple(int(p) for p in min_version.split("."))
    if ver >= need:
        return None
    return (f"{subject} requires FFmpeg {min_version}+ — the local build "
            f"is {'.'.join(map(str, ver))}, which predates it")


# Semantic traps (M10.1): filters whose effect is not what the plain name
# suggests. The plan is built as requested; the warning states the trap.
_TRAP_NOTES = {
    "asetrate": (
        "asetrate rewrites the sample-rate metadata — it shifts pitch AND "
        "playback speed (unlike atempo, which preserves pitch)"),
    "decimate": (
        "decimate drops frames in a cycle pattern — the output has fewer "
        "frames (shorter, choppier), not a slower playback"),
    "chromakey": (
        "chromakey keys the chosen color to transparency — an output "
        "without an alpha channel (e.g. H.264 in MP4) renders the keyed "
        "area black; composite with overlay or write an alpha-capable format"),
    "colorspace": (
        "colorspace rewrites color-space metadata tags — it labels the "
        "stream, it does not convert pixel values"),
    "colormatrix": (
        "colormatrix rewrites color-matrix metadata tags — it labels the "
        "stream, it does not convert pixel values"),
    "loop": (
        "loop repeats a frame range — the output runs longer than the "
        "input; bound it with a trim"),
    "fps": (
        "fps drops or duplicates frames to reach the target rate — "
        "playback speed is unchanged (use a speed change for that)"),
    "framerate": (
        "framerate drops or duplicates frames to reach the target rate "
        "(scene-change aware) — playback speed is unchanged"),
    "setdar": (
        "setdar changes the display-aspect-ratio metadata — pixels are not "
        "rescaled (use resize for that)"),
    "setsar": (
        "setsar changes the sample-aspect-ratio metadata — pixels are not "
        "rescaled (use resize for that)"),
    "silenceremove": (
        "silenceremove drops leading/interior silences — output timestamps "
        "shift; the kept audio is unchanged"),
    "rotate": (
        "arbitrary-angle rotation pads the frame (fillcolor) — the output "
        "dimensions change"),
    "trim": (
        "the trim filter cuts inside the filter chain — a plan-level trim "
        "is usually what you want"),
}

# Diagnostic filters (M10.2): they write results to FFmpeg's log and change
# nothing. A plan made only of them runs against the null muxer with the
# log level raised to info (the results are info-level messages).
_DIAG_NOTES = {
    "silencedetect": "silencedetect writes silence intervals to the log — the stream is unchanged",
    "volumedetect": "volumedetect writes mean/max volume to the log — the stream is unchanged",
    "astats": "astats writes per-window audio statistics to the log",
    "ashowinfo": "ashowinfo writes per-frame audio packet info to the log",
    "blackdetect": "blackdetect writes black intervals to the log",
    "cropdetect": "cropdetect writes crop recommendations to the log",
    "freezedetect": "freezedetect writes frozen-frame intervals to the log",
    "idet": "idet writes interlaced/progressive/repeat frame counts to the log",
    "showinfo": "showinfo writes per-frame metadata to the log — verbose on long files",
    "bbox": "bbox writes bounding boxes of frame-to-frame changes to the log",
    "blackdetect_vulkan": "blackdetect_vulkan writes black intervals to the log (needs a GPU)",
    "psnr": "psnr writes the mean/max signal-to-noise ratio to the log — the stream is unchanged",
    "ssim": "ssim writes the structural similarity score to the log — the stream is unchanged",
    "libvmaf": "libvmaf writes the pooled VMAF score to the log — the stream is unchanged",
}


def _split_top_level_commas(s: str) -> list[str]:
    """Split a filter chain on commas outside brackets and quotes."""
    parts: list[str] = []
    cur: list[str] = []
    depth, quote = 0, None
    for ch in s:
        if quote is not None:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def _chain_filter_names(expr: str) -> list[str]:
    """Filter instance names in a chain expression ('a=x,b=c' → ['a','b']).
    The schema validated the expression, so this is a light parse: top-level
    commas, then the leading identifier of each instance."""
    names = []
    for inst in _split_top_level_commas(expr):
        m = re.match(r"\s*([a-z][a-z0-9_]*)", inst)
        if m:
            names.append(m.group(1))
    return names


# ---------------------------------------------------------------- filters

def _scale_expr(op: ResizeOp, defaults: list[str]) -> str:
    if op.height is not None and op.width is None:
        h = even(op.height)
        if h != op.height:
            defaults.append(f"height snapped from {op.height} to {h} (even dimensions required for yuv420p)")
        return f"scale=-2:{h}"
    if op.width is not None and op.height is None:
        w = even(op.width)
        if w != op.width:
            defaults.append(f"width snapped from {op.width} to {w} (even dimensions required for yuv420p)")
        return f"scale={w}:-2"
    w, h = even(op.width), even(op.height)
    if op.aspect_ratio == "exact":
        if (w, h) != (op.width, op.height):
            defaults.append(f"dimensions snapped to {w}x{h} (even dimensions required for yuv420p)")
        return f"scale={w}:{h}"
    # both given, aspect preserved -> scale down then letterbox (no stretching)
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    )


def _crop_expr(op: CropOp, defaults: list[str]) -> str:
    w, h = even(op.width), even(op.height)
    if (w, h) != (op.width, op.height):
        defaults.append(
            f"crop dimensions snapped to {w}x{h} (even dimensions required for yuv420p)"
        )
    # crop=w:h is centered by FFmpeg's default x=(iw-ow)/2, y=(ih-oh)/2 —
    # the plan carries no offset, so "crop to WxH" always means the center.
    return f"crop={w}:{h}"


_ROTATE_FILTERS = {
    (90, True): "transpose=1",   # 90° clockwise
    (270, True): "transpose=2",  # 270° clockwise == 90° counter-clockwise
    (90, False): "transpose=2",
    (270, False): "transpose=1",
    (180, True): "hflip,vflip",  # direction is irrelevant at 180°
    (180, False): "hflip,vflip",
}


def _rotate_expr(op: RotateOp) -> str:
    return _ROTATE_FILTERS[(op.angle, op.clockwise)]


def _setpts_expr(op: SpeedOp) -> str:
    # factor 2 -> PTS/2 (half the duration); factor 0.5 -> PTS/0.5 (twice)
    return f"setpts=PTS/{fmt_num(op.factor)}"


def _atempo_chain(op: SpeedOp) -> str:
    """atempo accepts 0.5-2.0 per instance; wider factors chain instances
    (4x -> atempo=2,atempo=2; 0.25x -> atempo=0.5,atempo=0.5)."""
    lo, hi = atempo_limits()
    f = op.factor
    parts: list[float] = []
    while f > hi:
        parts.append(hi)
        f /= hi
    while f < lo:
        parts.append(lo)
        f /= lo
    parts.append(f)
    return ",".join("atempo=" + fmt_num(p) for p in parts)


def _audio_filter_chain(edit: EditPlan) -> str | None:
    """The -af chain: speed changes (atempo) plus audio filter operations,
    in user-specified order. None when the output audio is untouched."""
    parts: list[str] = []
    for o in edit.operations:
        if o.type == "speed":
            parts.append(_atempo_chain(o))
        elif o.type == "filter" and o.domain == "audio":
            parts.append(o.expr)
    return ",".join(parts) if parts else None


def _crop_too_big(edit: EditPlan, metadata: MediaInfo) -> str | None:
    """A crop must fit the video as it exists at that point in the chain —
    earlier rotate/resize ops change the working dimensions."""
    if not (metadata.width and metadata.height):
        return None
    w, h = float(metadata.width), float(metadata.height)
    for o in edit.operations:
        if o.type == "rotate" and o.angle in (90, 270):
            w, h = h, w
        elif o.type == "resize" and (o.width is not None or o.height is not None):
            if o.width is not None and o.height is None:
                if w:
                    h = round(h * even(o.width) / w)
                w = even(o.width)
            elif o.width is None and o.height is not None:
                if h:
                    w = round(w * even(o.height) / h)
                h = even(o.height)
            else:
                w, h = even(o.width), even(o.height)
        elif o.type == "crop":
            if o.width > w or o.height > h:
                return (
                    f"crop {o.width}x{o.height} is larger than the video at that "
                    f"point in the edit ({w:g}x{h:g})"
                )
    return None


def _overlay_xy(position: str, margin: int) -> tuple[str, str]:
    """The overlay x/y expressions for a named corner/center position."""
    m = str(margin)
    return {
        "top_left": (m, m),
        "top_right": (f"W-w-{m}", m),
        "bottom_left": (m, f"H-h-{m}"),
        "bottom_right": (f"W-w-{m}", f"H-h-{m}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }[position]


def _overlay_position(op: WatermarkOp) -> tuple[str, str]:
    return _overlay_xy(op.position, op.margin_px)


def build_video_chain(operations: list, defaults: list[str],
                      sub_exprs: dict[str, str] | None = None,
                      start_label: str | None = None):
    """Build the video filter chain in user-specified order.

    Returns (mode, chain, video_out_label):
      mode "vf"     -> chain is a -vf expression, label is None
      mode "complex"-> chain is a -filter_complex graph, label is the
                      output pad to map

    ``sub_exprs`` maps a burned-subtitle asset id to its complete filter
    argument ('subtitles=…' / 'ass=…'); the file is opened by the filter
    itself, so no extra input is needed (M5).

    ``start_label`` chains the first segment off an existing labeled pad
    (e.g. a combiner filter's '[cv]' output, M10.3) instead of the main
    input's '[0:v]' — the chain then always runs in complex mode.
    """
    if not operations:
        return "none", None, None
    if not any(o.type == "watermark" for o in operations) and not start_label:
        parts = []
        for o in operations:
            if o.type == "resize":
                parts.append(_scale_expr(o, defaults))
            elif o.type == "grayscale":
                parts.append("hue=s=0")
            elif o.type == "crop":
                parts.append(_crop_expr(o, defaults))
            elif o.type == "rotate":
                parts.append(_rotate_expr(o))
            elif o.type == "speed":
                parts.append(_setpts_expr(o))
            elif o.type == "filter" and o.domain == "video":
                # FilterOp.expr is label-free (schema-validated) — it splices
                # straight into the -vf chain in user-specified order.
                parts.append(o.expr)
            elif o.type == "subtitles" and sub_exprs:
                parts.append(sub_exprs[o.asset_id])
            # a soft subtitle is muxed as a track, not filtered — no segment
        return "vf", ",".join(parts) or None, None

    segs: list[str] = []
    prev: str | None = start_label
    last_out: str | None = None
    offset = 1 if start_label else 0  # the combiner pad already took v0's place
    for i, o in enumerate(operations):
        out = f"v{i + offset}"
        src = f"[{prev}]" if prev is not None else "[0:v]"
        if o.type == "resize":
            segs.append(f"{src}{_scale_expr(o, defaults)}[{out}]")
        elif o.type == "grayscale":
            segs.append(f"{src}hue=s=0[{out}]")
        elif o.type == "crop":
            segs.append(f"{src}{_crop_expr(o, defaults)}[{out}]")
        elif o.type == "rotate":
            segs.append(f"{src}{_rotate_expr(o)}[{out}]")
        elif o.type == "speed":
            segs.append(f"{src}{_setpts_expr(o)}[{out}]")
        elif o.type == "filter" and o.domain == "video":
            segs.append(f"{src}{o.expr}[{out}]")
        elif o.type == "subtitles":
            if not sub_exprs:
                # soft subtitle: muxed as a track, not a filter — skip the
                # segment AND the pad, so the graph stays connected
                continue
            segs.append(f"{src}{sub_exprs[o.asset_id]}[{out}]")
        elif o.type == "watermark":
            wm_scale = ""
            if o.scale_percent != 100:
                wm_scale = f",scale=iw*{o.scale_percent}/100:ih*{o.scale_percent}/100"
            x, y = _overlay_position(o)
            segs.append(f"[1:v]format=rgba{wm_scale}[wm]")
            segs.append(f"{src}[wm]overlay={x}:{y}[{out}]")
        prev = out
        last_out = out
    return "complex", ";".join(segs), last_out


# ---------------------------------------------------------------- planning

def _audio_compatible(metadata: MediaInfo, container: str,
                      audio_mode: AudioMode) -> bool:
    if audio_mode == AudioMode.REMOVE:
        return True
    if not metadata.has_audio:
        return True
    allowed = REGISTRY["matrix"][container]["audio"]
    return metadata.audio_codec in allowed


def _matrix_flags(container: str, format_options: dict[str, str]) -> list[str]:
    """The container's matrix flags (-movflags +faststart & co.), minus any
    flag the plan sets explicitly through format_options — the explicit
    value wins instead of emitting two of the same option."""
    flags = list(REGISTRY["matrix"].get(container, {}).get("flags", []))
    if not format_options:
        return flags
    skip = {f"-{k}" for k in format_options}
    out: list[str] = []
    i = 0
    while i < len(flags):
        if flags[i] in skip:
            i += 2  # drop the whole -flag value pair
        else:
            out.append(flags[i])
            i += 1
    return out


def _codec_option_tokens(options: dict[str, str]) -> list[str]:
    """'-<key> <value>' argv tokens for a codec-options dict (M8), sorted
    by key so the argv is deterministic."""
    return [tok for k, v in sorted(options.items()) for tok in (f"-{k}", str(v))]


def _combiner_segments(combine: dict, defaults: list[str],
                       warnings: list[str]) -> tuple[list[str], str | None,
                                                     str | None, str]:
    """Labeled -filter_complex segments for a multi-input filter (M10.3).

    ``combine`` is the plan_execution context: {'op', 'domain',
    'main_media', 'extra_medias'} — the main input is pad 0, the extras
    pads 1..N-1, in the plan's asset order. Returns (segments, video_out,
    audio_out, audio_source): a video combiner outputs video and an audio
    combiner outputs audio (the main's video stays stream-mapped);
    audio_source is 'graph' (the audio comes from the graph's pad),
    'main' (the main input's own audio stream survives, stream-mapped) or
    'none' (the output carries no audio). Raises PlanError on semantic
    problems (unknown duration, mismatched layouts, …).
    """
    op = combine["op"]
    name = op.filter
    domain = combine["domain"]
    main = combine["main_media"]
    medias = [main] + combine["extra_medias"]
    opts = dict(op.options)
    n = len(medias)
    segs: list[str] = []

    if name == "xfade":
        if n != 2:
            raise PlanError(["xfade mixes exactly two inputs"])
        duration = float(opts.get("duration", "1"))
        if duration <= 0:
            raise PlanError(["xfade's duration must be positive"])
        transition = opts.get("transition", "fade")
        dur0 = main.duration
        if not dur0 or dur0 <= 0:
            raise PlanError([
                "xfade's offset is computed from the main input's duration, "
                "which is unknown — provide probe metadata or a file ffprobe "
                "can read"])
        offset = dur0 - duration
        if offset < 0:
            raise PlanError([
                f"the main input is {fmt_num(dur0)}s long — a "
                f"{fmt_num(duration)}s crossfade would start before it ends; "
                "the transition duration must not exceed the main input's "
                "duration"])
        # xfade needs identical rate/format/size/timebase on both pads:
        # normalize the extra input to the main's. (fps takes no format
        # option in 6.x builds — format is its own filter.)
        norm = "settb=AVTB"
        if main.fps and main.pix_fmt:
            norm = f"fps={fmt_num(main.fps)},format={main.pix_fmt},settb=AVTB"
            defaults.append(
                f"xfade: both inputs normalized to {fmt_num(main.fps)} fps, "
                f"{main.pix_fmt} (the main input's rate)")
        scale = ""
        if (main.width and main.height
                and medias[1].width and medias[1].height
                and (medias[1].width, medias[1].height)
                != (main.width, main.height)):
            scale = f"scale={main.width}:{main.height},"
            warnings.append(
                f"xfade: input 1 is {medias[1].width}x{medias[1].height}, "
                f"the main is {main.width}x{main.height} — input 1 is "
                "scaled to the main's size")
        segs = [
            f"[0:v]{norm}[c0]",
            f"[1:v]{scale}{norm}[c1]",
            f"[c0][c1]xfade=transition={transition}:"
            f"duration={fmt_num(duration)}:offset={fmt_num(offset)}[cv]",
        ]
        defaults.append(
            f"xfade offset {fmt_num(offset)}s = main duration {fmt_num(dur0)}s "
            "− transition duration (computed — the crossfade starts so both "
            "inputs play until the end)")
        if medias[1].has_audio:
            warnings.append(
                "the second input's audio is dropped by the crossfade — only "
                "the main input's audio is kept (say 'mix the audio' to blend "
                "it)")
        return segs, "cv", None, "main"

    if name == "acrossfade":
        if n != 2:
            raise PlanError(["acrossfade mixes exactly two inputs"])
        d = float(opts.get("duration", "1"))
        if d <= 0:
            raise PlanError(["acrossfade's duration must be positive"])
        if "duration" not in opts:
            defaults.append(f"acrossfade duration {fmt_num(d)}s (defaulted)")
        segs = [f"[0:a][1:a]acrossfade=d={fmt_num(d)}[ca]"]
        return segs, None, "ca", "graph"

    if name == "concat":
        has_audio = [m.has_audio for m in medias]
        v = 1 if domain == "video" else 0
        if "v" in opts and int(opts["v"]) != v:
            raise PlanError([
                f"concat in a {'video' if v else 'audio'}-domain plan must "
                f"keep v={v} (the plan's concat options say v={opts['v']})"])
        if "a" in opts:
            a = int(opts["a"])
        elif all(has_audio):
            a = 1
        elif not any(has_audio):
            a = 0
        else:
            missing = [i for i, has in enumerate(has_audio) if not has]
            which = ("input " if len(missing) == 1 else "inputs ") \
                + ", ".join(str(i) for i in missing)
            raise PlanError([
                "the inputs' audio does not line up — " + which +
                (" has" if len(missing) == 1 else " have")
                + " no audio. Say 'without the audio' to concatenate "
                "video only, or add audio to that clip first"])
        if a and not all(has_audio):
            raise PlanError([
                "concat can only keep audio when every input has it — the "
                "inputs here do not match (say 'without the audio')"])
        # The concat filter takes each input's streams in order
        # ([0:v][0:a][1:v][1:a]), not all videos then all audios — and it
        # needs identical video dimensions, so scale extras to the main's.
        segs: list[str] = []
        pads: list[str] = []
        for i in range(n):
            if v:
                m = medias[i]
                if i and (main.width and main.height
                          and m.width and m.height
                          and (m.width, m.height)
                          != (main.width, main.height)):
                    segs.append(
                        f"[{i}:v]scale={main.width}:{main.height}[s{i}]")
                    warnings.append(
                        f"concat: input {i} is {m.width}x{m.height}, the "
                        f"main is {main.width}x{main.height} — input {i} is "
                        "scaled to the main's size")
                    pads.append(f"[s{i}]")
                else:
                    pads.append(f"[{i}:v]")
            if a:
                pads.append(f"[{i}:a]")
        outs = ("[cv]" if v else "") + ("[ca]" if a else "")
        segs += ["".join(pads) + f"concat=n={n}:v={v}:a={a}" + outs]
        if a == 0 and any(has_audio):
            warnings.append(
                "audio is dropped in the concatenation (not every input "
                "carries audio)")
        return segs, ("cv" if v else None), ("ca" if a else None), \
            ("graph" if a else "none")

    if name in ("hstack", "vstack"):
        vertical = name == "vstack"
        dims = [(m.width, m.height) for m in medias]
        if any(w is None or h is None for w, h in dims):
            raise PlanError([
                f"{name} needs the inputs' dimensions, which could not be "
                "probed — re-probe the files or use a file ffprobe can read"])
        w0, h0 = dims[0]
        norm_segs: list[str] = []
        pads = ["[0:v]"]
        for i in range(1, n):
            w, h = dims[i]
            if (vertical and w != w0) or (not vertical and h != h0):
                scale = f"scale={w0}:-2" if vertical else f"scale=-2:{h0}"
                norm_segs.append(f"[{i}:v]{scale}[s{i}]")
                warnings.append(
                    f"{name}: input {i} is {w}x{h}, the main is "
                    f"{w0}x{h0} — input {i} is scaled to the main's "
                    f"{'width' if vertical else 'height'} (aspect preserved)")
                pads.append(f"[s{i}]")
            else:
                pads.append(f"[{i}:v]")
        segs = norm_segs + ["".join(pads) + f"{name}=inputs={n}[cv]"]
        if any(m.has_audio for m in medias[1:]):
            warnings.append(
                "the second input's audio is dropped by the stack — only "
                "the main input's audio is kept")
        return segs, "cv", None, "main"

    if name == "overlay":
        if n != 2:
            raise PlanError(["overlay takes the main input plus one overlay input"])
        x = opts.get("x", "0")
        y = opts.get("y", "0")
        segs = [f"[0:v][1:v]overlay=x={x}:y={y}[cv]"]
        if medias[1].has_audio:
            warnings.append(
                "the overlay input's audio is dropped — only the main "
                "input's audio is kept")
        return segs, "cv", None, "main"

    if name in ("amix", "amerge", "join"):
        pads = "".join(f"[{i}:a]" for i in range(n))
        if name == "amix":
            duration = opts.get("duration", "longest")
            segs = [f"{pads}amix=inputs={n}:duration={duration}[ca]"]
        elif name == "amerge":
            segs = [f"{pads}amerge=inputs={n}[ca]"]
        else:
            layout = opts.get("channel_layout")
            if not layout:
                raise PlanError([
                    "'join' needs a target channel layout — say e.g. 'join "
                    "the audio tracks to 5.1' or 'to stereo'"])
            segs = [f"{pads}join=inputs={n}:channel_layout={layout}[ca]"]
        return segs, None, "ca", "graph"

    raise PlanError([f"combiner filter '{name}' is not supported by the planner"])


def plan_execution(
    edit: EditPlan,
    assets: dict,
    metadata: MediaInfo | None = None,
    output_filename: str | None = None,
) -> ExecutionPlan:
    """Turn a validated editing plan into an execution plan.

    Raises PlanError with all semantic/capability problems found.
    """
    errors: list[str] = []
    errors.extend(capability_errors(edit.output))
    if len(edit.operations) > MAX_OPERATIONS:
        errors.append(f"at most {MAX_OPERATIONS} operations are supported per request")

    # Diagnostic analysis (M10.2): a plan made only of diagnostic filters
    # (registry 'diagnostic': true) analyzes the input and writes nothing —
    # the results are info-level log messages. Trim is allowed (analyze a
    # segment); a filtergraph or bitstream filters are other plan shapes and
    # keep the plan a normal output plan, and an explicitly requested
    # audio/subtitle output is a file the user wants, not an analysis.
    out = edit.output
    filter_ops = [o for o in edit.operations if o.type == "filter"]
    compare_ops = edit.ops_of("compare")
    diag_names = [n for o in filter_ops for n in _chain_filter_names(o.expr)]
    diag_names += [o.metric for o in compare_ops]
    diag_names = list(dict.fromkeys(diag_names))
    filter_diagnostic = (
        bool(filter_ops)
        and all(o.type == "filter" for o in edit.operations)
        and all((filter_spec(n) or {}).get("diagnostic") for n in diag_names)
    )
    diagnostic = (
        out.mode == "video"
        and (filter_diagnostic or bool(compare_ops))
        and not edit.filtergraph
        and not (edit.bitstream_filters or [])
    )
    # A reference metric in a plain -vf/-af chain can never work — it needs
    # the second (reference) pad a comparison plan or a custom filtergraph
    # provides.
    for o in filter_ops:
        for name in _chain_filter_names(o.expr):
            if (filter_spec(name) or {}).get("needs_reference"):
                errors.append(
                    f"the {name} filter needs a reference input — it cannot "
                    "run on the input alone; say 'compare ... against "
                    "<file>' (a comparison plan)")
    ar_ops = edit.ops_of("audio_replace")
    # A remove followed by a replace still produces audio — the replacement
    # track stands in for the removed one.
    audio_survives = out.audio_mode != AudioMode.REMOVE or bool(ar_ops)
    # The per-codec checks (video_profile, pixel_format) are the schema's
    # job (OutputSpec._capability); pixel_format/video_profile/video_preset/
    # gpu are schema-forbidden outside video mode, so the remaining checks
    # are safe for every mode.
    if out.video_preset is not None and out.video_codec != "h264":
        errors.append(
            f"encoder presets are only supported for H.264 (x264) in the MVP "
            f"('{out.video_preset}' does not apply to {out.video_codec.upper()})"
        )
    if out.gpu:
        coder = gpu_coder(out.video_codec)
        if coder is None:
            errors.append(
                f"GPU encoding is not available for {out.video_codec.upper()} — "
                "it is CPU-only (libvpx-vp9); use H.264 or H.265 on the GPU"
            )
        else:
            if out.video_preset is not None:
                errors.append(
                    f"the x264 speed preset '{out.video_preset}' does not apply to "
                    f"GPU encoding ({coder['encoder']}) — drop the preset"
                )
            if out.video_profile is not None \
                    and out.video_profile not in coder["profiles"]:
                errors.append(
                    f"GPU {out.video_codec.upper()} supports the "
                    f"{' and '.join(coder['profiles'])} profiles — "
                    f"'{out.video_profile}' has no GPU form"
                )
    if out.qsv:
        coder = qsv_coder(out.video_codec)
        if coder is None:
            errors.append(
                f"QSV encoding is not available for {out.video_codec.upper()} — "
                "it is CPU-only; use H.264 or H.265 with QSV"
            )
        else:
            if out.video_preset is not None:
                errors.append(
                    f"the x264 speed preset '{out.video_preset}' does not apply "
                    f"to QSV encoding ({coder['encoder']}) — drop the preset "
                    "(QSV has its own 'preset' codec option: 0-7, "
                    "veryfast..veryslow)"
                )
            if out.video_profile is not None \
                    and out.video_profile not in coder["profiles"]:
                errors.append(
                    f"QSV {out.video_codec.upper()} supports the "
                    f"{' and '.join(coder['profiles'])} profiles — "
                    f"'{out.video_profile}' has no QSV form"
                )
    resample = edit.ops_of("audio_resample")
    if resample and not audio_survives:
        errors.append("the audio is removed, so it cannot be resampled")
    if not audio_survives and any(
            o.type == "filter" and o.domain == "audio" for o in edit.operations):
        errors.append("the audio is removed, so it cannot be filtered")
    # A video container that carries no audio (e.g. an animated GIF) drops
    # the input's track — operations that act on that track are impossible.
    if out.mode == "video" and not REGISTRY["matrix"].get(
            out.container, {}).get("audio"):
        if resample:
            errors.append(
                f"a {out.container} file carries no audio, so it cannot be "
                "resampled"
            )
        if ar_ops:
            errors.append(
                f"a {out.container} file carries no audio, so the audio "
                "track cannot be replaced"
            )

    input_asset = assets.get(edit.input_id)
    allowed_kinds = {
        "video": (AssetKind.VIDEO, AssetKind.SOURCE),
        "audio": (AssetKind.VIDEO, AssetKind.SOURCE, AssetKind.AUDIO),
        "subtitles": (AssetKind.VIDEO, AssetKind.SOURCE),
    }[out.mode]
    if diagnostic and AssetKind.AUDIO not in allowed_kinds:
        # audio-domain diagnostics run fine on an audio file (-af … -f null -)
        allowed_kinds = allowed_kinds + (AssetKind.AUDIO,)
    if input_asset is None:
        errors.append(f"asset id '{edit.input_id}' is not in the asset map")
    elif input_asset.kind not in allowed_kinds:
        errors.append(f"asset '{edit.input_id}' " + {
            "video": "is not a video or a source",
            "audio": "is not a video, source or audio file",
            "subtitles": ("is not a video or a source (subtitles are dumped "
                          "from a video's subtitle stream)"),
        }[out.mode])

    wm_assets: list = []
    for o in edit.ops_of("watermark"):
        a = assets.get(o.asset_id)
        if a is None:
            errors.append(f"watermark asset id '{o.asset_id}' is not in the asset map")
        elif a.kind != AssetKind.IMAGE:
            errors.append(f"watermark asset '{o.asset_id}' is not an image")
        else:
            wm_assets.append(a)

    ar_assets: list = []
    for o in edit.ops_of("audio_replace"):
        a = assets.get(o.asset_id)
        if a is None:
            errors.append(f"audio-replacement asset id '{o.asset_id}' is not in the asset map")
        elif a.kind != AssetKind.AUDIO:
            errors.append(f"audio-replacement asset '{o.asset_id}' is not an audio file")
        else:
            ar_assets.append(a)

    sub_ops = edit.ops_of("subtitles")
    sub_assets: list = []
    for o in sub_ops:
        a = assets.get(o.asset_id)
        if a is None:
            errors.append(f"subtitles asset id '{o.asset_id}' is not in the asset map")
        elif a.kind != AssetKind.SUBTITLES:
            errors.append(
                f"subtitles asset '{o.asset_id}' is not a subtitle file "
                "(srt/ass/ssa/vtt)"
            )
        elif subtitle_input_format(a.path) is None:
            errors.append(
                f"subtitles asset '{a.path}' is not a supported subtitle format "
                "(srt/ass/ssa/vtt)"
            )
        elif stream_scheme(a.path) is None and ":" in a.path:
            errors.append(
                f"subtitles file '{a.path}' contains ':' — FFmpeg reads that as a "
                "stream protocol and cannot open it; rename the file"
            )
        else:
            sub_assets.append(a)
        if o.mode == "soft":
            enc = soft_subtitle_codec(edit.output.container)
            if enc is None:
                errors.append(
                    f"selectable (soft) subtitles have no codec in "
                    f"{edit.output.container.upper()} output — burn them in instead, "
                    "or write MP4 / MOV / MKV"
                )

    # -- combiner inputs (M10.3): resolve + probe the extra inputs ----------
    # The API probes only the main input, so the extras (and the main, when
    # no metadata came in) are probed here — they need a real duration and
    # stream layout for the graph math.
    combine = None
    for op in edit.ops_of("combine"):
        spec = filter_spec(op.filter) or {}
        domain = combiner_domain(op.filter)
        min_total = spec.get("multi_input") or 2
        total = 1 + len(op.asset_ids)
        if total < min_total:
            errors.append(
                f"{op.filter} takes at least {min_total} inputs — the plan "
                f"has {total}")
        if spec.get("multi_input") == 2 and total != 2:
            errors.append(
                f"{op.filter} mixes exactly two inputs — the plan has {total}")
        seen_ids = {edit.input_id}
        extra_assets: list = []
        for aid in op.asset_ids:
            if aid in seen_ids:
                errors.append(f"combiner input '{aid}' duplicates an input")
                continue
            seen_ids.add(aid)
            a = assets.get(aid)
            if a is None:
                errors.append(f"combiner asset id '{aid}' is not in the asset map")
                continue
            if a.kind not in (AssetKind.VIDEO, AssetKind.AUDIO):
                errors.append(
                    f"combiner input '{aid}' must be a media file "
                    "(image, subtitle and stream inputs are not supported yet)")
                continue
            if stream_scheme(a.path) is not None:
                errors.append(
                    f"combiner input '{aid}' is a stream — combiner inputs "
                    "must be local files (they are probed for duration and "
                    "layout)")
                continue
            extra_assets.append(a)
        if not errors and domain is not None and len(extra_assets) == len(op.asset_ids):
            main_media = metadata
            if main_media is None and stream_scheme(input_asset.path) is None:
                main_media = probe_media(
                    resolve_input_name(input_asset.path), require_video=False)
            extra_medias: list[MediaInfo] = []
            for a in extra_assets:
                m = probe_media(resolve_input_name(a.path), require_video=False)
                if m is None:
                    errors.append(
                        f"combiner input '{a.path}' could not be probed — is "
                        "the file readable?")
                    continue
                extra_medias.append(m)
                have = m.video_streams > 0 if domain == "video" else m.has_audio
                if not have:
                    errors.append(
                        f"combiner input '{a.path}' has no {domain} stream — "
                        f"{op.filter} needs {domain} from every input")
            if not errors:
                if main_media is None:
                    errors.append(
                        f"the main input's {domain} streams could not be "
                        "verified — provide probe metadata or a file "
                        "ffprobe can read")
                else:
                    have = (main_media.video_streams > 0
                            if domain == "video" else main_media.has_audio)
                    if not have:
                        errors.append(
                            f"the main input has no {domain} stream — "
                            f"{op.filter} needs {domain} from every input")
                    else:
                        combine = {
                            "op": op, "domain": domain,
                            "main_media": main_media,
                            "extra_assets": extra_assets,
                            "extra_medias": extra_medias,
                        }

    if combine is not None:
        if edit.trim is not None:
            errors.append(
                "trimming cannot be combined with a multi-input filter — "
                "the cut point would fight the transition offset (trim the "
                "clip first, then combine)")
        if wm_assets:
            errors.append(
                "a watermark cannot be added in the same pass as a "
                "multi-input filter — combine first, then add the watermark")
        if ar_assets:
            errors.append(
                "replacing the audio cannot be combined with a multi-input "
                "filter in one pass — combine first, then replace the audio")
        if sub_ops:
            errors.append(
                "subtitles cannot be combined with a multi-input filter in "
                "one pass — combine first, then add the subtitles")
        if out.mode == "audio":
            errors.append(
                "an audio-only output cannot carry a multi-input filter in "
                "the MVP — export a video container (e.g. mp4) that keeps "
                "the main video with the combined audio")
        if out.mode == "subtitles":
            errors.append(
                "a subtitle dump cannot carry a multi-input filter")
        if out.audio_mode == AudioMode.REMOVE and combine["domain"] == "audio":
            errors.append(
                "an audio combiner produces the output audio — it cannot be "
                "removed in the same plan")

    # -- comparison reference (M10.4): resolve + probe the reference input --
    # The comparison is a standalone analysis in the Netflix vmaf docs'
    # order: the reference clip is input 0, the main (distorted) input is
    # input 1, and the metric (psnr/ssim/libvmaf) compares them frame by
    # frame into the log (its pads take [distorted][reference]).
    compare = None
    for op in edit.ops_of("compare"):
        if op.reference == edit.input_id:
            errors.append(
                "the comparison reference must be a different file than the "
                "main input (the metric measures the difference between the "
                "two)")
            continue
        a = assets.get(op.reference)
        if a is None:
            errors.append(
                f"comparison reference asset id '{op.reference}' is not in "
                "the asset map")
            continue
        if a.kind not in (AssetKind.VIDEO, AssetKind.SOURCE):
            errors.append(
                f"comparison reference '{a.path}' must be a media file "
                "(image, subtitle and stream references are not supported "
                "yet)")
            continue
        if stream_scheme(a.path) is not None:
            errors.append(
                f"comparison reference '{a.path}' is a stream — the "
                "reference must be a local file (it is probed for layout)")
            continue
        main_media = metadata
        if main_media is None and stream_scheme(input_asset.path) is None:
            main_media = probe_media(
                resolve_input_name(input_asset.path), require_video=True)
        ref_media = probe_media(resolve_input_name(a.path), require_video=True)
        if ref_media is None:
            errors.append(
                f"comparison reference '{a.path}' could not be probed — is "
                "the file readable?")
            continue
        if ref_media.video_streams == 0:
            errors.append(
                f"comparison reference '{a.path}' has no video stream — "
                f"{op.metric} compares video frame by frame")
            continue
        if main_media is None:
            errors.append(
                "the main input's video streams could not be verified — "
                "provide probe metadata or a file ffprobe can read")
            continue
        if main_media.video_streams == 0:
            errors.append(
                f"the main input has no video stream — {op.metric} compares "
                "video frame by frame")
            continue
        # 'setpts=PTS-STARTPTS' is only meaningful on streams that carry
        # packet pts — a raw bitstream (a bare .hevc Annex B) demuxes
        # without any, so probe both inputs before the graph is written.
        # An unreadable file stays None (unknown) and keeps the docs'
        # default.
        if ref_media.has_pts is None:
            ref_media.has_pts = probe_has_pts(
                resolve_input_name(a.path))
        if main_media.has_pts is None:
            main_media.has_pts = probe_has_pts(
                resolve_input_name(input_asset.path))
        compare = {
            "op": op, "main_media": main_media,
            "ref_asset": a, "ref_media": ref_media,
        }

    if compare is not None:
        if edit.trim is not None:
            errors.append(
                "trimming cannot be combined with a comparison — the cut "
                "point would shift the main input's frames against the "
                "reference timeline (trim the clip first, then compare)")
        if wm_assets:
            errors.append(
                "a watermark cannot be added in the same pass as a "
                "comparison — comparisons write nothing; add the watermark "
                "first, then compare the result")
        if ar_assets:
            errors.append(
                "replacing the audio cannot be combined with a comparison "
                "in one pass — comparisons are standalone analyses")
        if sub_ops:
            errors.append(
                "subtitles cannot be combined with a comparison in one "
                "pass — comparisons are standalone analyses")
        if out.mode != "video":
            errors.append(
                "a comparison is a video analysis — it cannot produce an "
                f"{out.mode} output (psnr/ssim/libvmaf compare video frame "
                "by frame)")
        for other in edit.operations:
            if other.type not in ("compare",):
                errors.append(
                    f"operation '{other.type}' cannot be combined with a "
                    "comparison — comparisons are standalone analyses "
                    "(apply the edit first, then compare the result "
                    "against the reference)")
                break

    # -- stabilization (M10.5): two-pass vidstab transform ------------------
    # Pass 1 analyzes the motion (vidstabdetect -> a .trf side-file, the
    # stream discarded through the null muxer); pass 2 re-encodes with
    # vidstabtransform reading that side-file. Everything that breaks the
    # 1:1 frame correspondence between the two passes, or that owns the
    # video filter chain, conflicts. Allowed alongside: resize, grayscale,
    # crop, codec options and the audio side (resample, audio filters).
    if edit.ops_of("stabilize"):
        if out.mode != "video":
            errors.append(
                "stabilization produces a video clip — it cannot target an "
                f"{out.mode} output (export a video container, e.g. mp4)")
        if edit.output_destination:
            errors.append(
                "stabilization is a two-pass transform — it writes an "
                "intermediate transform file next to the output, which a "
                "stream destination has no place for (stabilize to a file "
                "first, then stream it)")
        if edit.trim is not None:
            errors.append(
                "trimming cannot be combined with stabilization — the two "
                "passes must analyze and transform the same frames (trim "
                "the clip first, then stabilize)")
        if edit.filtergraph:
            errors.append(
                "stabilization runs as its own two-pass transform — a "
                "custom filtergraph is not compatible (use one or the "
                "other)")
        if combine is not None:
            errors.append(
                "stabilization cannot be combined with a multi-input filter "
                "in one pass — combine the inputs first, then stabilize")
        if wm_assets:
            errors.append(
                "a watermark cannot be added in the same pass as "
                "stabilization — stabilize first, then add the watermark")
        if ar_ops:
            errors.append(
                "replacing the audio cannot be combined with stabilization "
                "in one pass — stabilize first, then replace the audio")
        if sub_ops:
            errors.append(
                "subtitles cannot be combined with stabilization in one "
                "pass — stabilize first, then add the subtitles")
        if edit.ops_of("speed"):
            errors.append(
                "a speed change breaks the 1:1 frame correspondence the "
                "stabilization passes rely on — set the speed first, then "
                "stabilize")
        for o in edit.bitstream_filters or []:
            errors.append(
                f"the bitstream filter '{o.name}' runs on the compressed "
                "stream (stream copy) — stabilization re-encodes the "
                "video, so the two are not compatible")
            break
        for o in filter_ops:
            if o.domain == "video":
                errors.append(
                    f"the video filter '{o.expr}' is not compatible with "
                    "stabilization — stabilization runs as its own "
                    "two-pass transform (audio filters are fine)")
                break
        if any((filter_spec(n) or {}).get("diagnostic")
               for o in filter_ops for n in _chain_filter_names(o.expr)):
            errors.append(
                "diagnostic filters (analysis only — no file written) "
                "cannot be combined with stabilization — stabilize first, "
                "then run the analysis on the result")

    # -- mode-specific operation restrictions --------------------------------
    if out.mode == "audio":
        for o in edit.operations:
            if o.type in ("resize", "grayscale", "watermark", "subtitles",
                          "crop", "rotate"):
                errors.append(
                    f"operation '{o.type}' is a video edit — an audio-only output "
                    "has no video (supported: speed change, audio resample, "
                    "audio replace, audio filter)"
                )
            elif o.type == "filter" and o.domain == "video":
                errors.append("a video filter does not apply to an audio-only output")
    elif out.mode == "subtitles":
        for o in edit.operations:
            errors.append(
                f"subtitle dumping takes no operations — it writes the input's "
                f"subtitle stream as-is (drop the '{o.type}' operation)"
            )
    if edit.filtergraph:
        if out.mode == "audio":
            errors.append(
                "a filtergraph is a video filter graph — it cannot target an "
                "audio-only output (use an audio filter operation instead)"
            )
        elif out.mode == "subtitles":
            errors.append(
                "a subtitle dump output has no video to filter — drop the filtergraph"
            )
        else:
            # The graph replaces the built video chain, so operations the
            # chain would have built are dead weight and get rejected.
            chain_ops = sorted({
                o.type for o in edit.operations
                if o.type in ("resize", "grayscale", "crop", "rotate", "speed",
                              "watermark", "subtitles")
                or (o.type == "filter" and o.domain == "video")
            })
            for t in chain_ops:
                errors.append(
                    f"operation '{t}' is part of the video filter chain — the "
                    "filtergraph replaces that chain (drop the operation or "
                    "the filtergraph)"
                )
            # A labeled graph gets explicit -map entries; an unlabeled one
            # falls back to FFmpeg's auto-map, which cannot run alongside the
            # explicit audio/subtitle maps those streams need.
            if not filtergraph_output_labels(edit.filtergraph) and (
                    ar_ops or any(o.mode == "soft" for o in sub_ops)):
                errors.append(
                    "this filtergraph has no output label — add one (e.g. end "
                    "it with '[v]') so its output can be mapped alongside the "
                    "replacement audio / subtitles"
                )

    if errors:
        raise PlanError(errors)

    crop_err = _crop_too_big(edit, metadata) if metadata is not None else None
    if crop_err:
        raise PlanError([crop_err])

    in_path = input_asset.path
    wm_path = wm_assets[0].path if wm_assets else None
    ar_path = ar_assets[0].path if ar_assets else None
    sub_path = sub_assets[0].path if sub_assets else None

    defaults: list[str] = []
    warnings: list[str] = []

    # -- filter disclosures (M10.1): presence, version, GPU, semantic traps --
    # The registry validated the names and options; this is what the plan
    # tells the user about them on THIS build.
    ff = probe_ffmpeg()
    disclosed: set[str] = set()
    for o in edit.operations:
        if o.type != "filter":
            continue
        for name in _chain_filter_names(o.expr):
            if name in disclosed:
                continue
            disclosed.add(name)
            spec = filter_spec(name)
            if spec is None:
                continue
            if ff is not None and name not in ff.filters:
                warnings.append(
                    f"filter '{name}' is not available in the local FFmpeg "
                    "build — the command will fail to start")
            if spec.get("min_version"):
                note = _version_note(f"filter '{name}'", spec["min_version"])
                if note:
                    warnings.append(note)
            if spec.get("gpu"):
                warnings.append(
                    f"filter '{name}' needs a GPU at runtime — it fails "
                    "without one (the plan's GPU flag selects the encoder, "
                    "not the filter)")
            note = _TRAP_NOTES.get(name)
            if note:
                warnings.append(note)
    for op in compare_ops:
        # comparison metric disclosures: presence (libvmaf is not in every
        # build) and min_version; no GPU/trap notes apply to a metric
        if op.metric in disclosed:
            continue
        disclosed.add(op.metric)
        spec = filter_spec(op.metric)
        if spec is None:
            continue
        if ff is not None and op.metric not in ff.filters:
            warnings.append(
                f"filter '{op.metric}' is not available in the local "
                "FFmpeg build — the command will fail to start (psnr and "
                "ssim are available on every build)")
        elif spec.get("min_version"):
            note = _version_note(f"filter '{op.metric}'", spec["min_version"])
            if note:
                warnings.append(note)

    # -- diagnostic analysis (M10.2): null muxer, log level info, no file --
    if diagnostic:
        ep = _build_diagnostic_plan(edit, in_path, defaults, warnings,
                                    output_filename, diag_names,
                                    compare=compare)
        ep.defaults.extend(defaults)
        ep.warnings.extend(warnings)
        _metadata_warnings(ep, edit, metadata)
        return ep

    # -- output destination: stream URL (M3b), device (M4), or file --------
    dest = output_filename or edit.output_destination
    muxer: str | None = None
    device: str | None = None
    if dest and stream_scheme(dest) is not None:
        # A stream destination: its scheme fixes the container and muxer.
        info = stream_output_info(dest)
        if info is None:
            raise PlanError([
                f"output destination '{dest}' is not a supported stream URL "
                f"(allowed: {', '.join(s + '://' for s in output_schemes())})"
            ])
        dest_container, muxer = info
        if edit.output.container != dest_container:
            raise PlanError([
                f"stream destination '{dest}' carries {dest_container.upper()} "
                f"(muxer '{muxer}'); the plan requests {edit.output.container.upper()} — "
                f"name one or the other, not both"
            ])
        out_path = dest
        warnings.append(
            f"output streams to {dest} — data is sent to a remote endpoint; "
            "nothing is saved locally"
        )
    elif dest and output_device_for(dest) is not None:
        # Output device (alsa/pulse): audio only — a plan that produces video
        # cannot target one, and nothing is ever saved to a file.
        parts = output_device_parts(dest)
        if parts[2]:
            raise PlanError([
                f"device output '{dest}' must not carry options "
                "(write e.g. 'alsa:hw:0' without ';key=value')"
            ])
        device, out_path = parts[0], parts[1]
        if out.mode == "subtitles":
            raise PlanError([
                "a subtitle dump writes a .srt/.vtt/.ass file — it cannot "
                "target an audio device"
            ])
        video_ops = sorted({
            o.type for o in edit.operations
            if o.type in ("resize", "crop", "rotate", "speed", "grayscale",
                          "watermark", "subtitles", "filter", "combine")
        })
        if video_ops:
            raise PlanError([
                f"output device '{dest}' carries audio only — remove the video "
                f"edits ({', '.join(video_ops)}) or use a file/stream output"
            ])
        if edit.output.audio_mode == AudioMode.REMOVE and not edit.ops_of("audio_replace"):
            raise PlanError([
                f"output device '{dest}' needs audio, but the plan removes the audio"
            ])
        warnings.append(
            f"output goes to the local audio device {dest} — nothing is saved to a file"
        )
    else:
        if edit.output.container == "rtsp" or edit.output.container == "srt":
            raise PlanError([
                f"{edit.output.container.upper()} output can only go to a "
                f"{edit.output.container}:// stream destination — it has no file form"
            ])
        if dest and "://" in dest:
            raise PlanError([
                f"output destination '{dest}' is a URL but not a supported "
                f"stream scheme (allowed: {', '.join(s + '://' for s in output_schemes())}); "
                "use a plain filename for file output"
            ])
        out_path = dest
        if not out_path:
            stem = os.path.splitext(os.path.basename(in_path))[0] or "output"
            ext = extension_for(edit.output.container)
            out_path = f"{stem}.{ext}"
            # A defaulted name must never resolve to the source file.
            if os.path.realpath(out_path) == os.path.realpath(in_path):
                out_path = f"{stem}_out.{ext}"
            defaults.append(f"output filename defaulted to '{out_path}'")

        if out.container == "hls" and out_path:
            # HLS on disk is the .m3u8 playlist; the muxer writes the media
            # segments next to it. Force the extension and the explicit muxer.
            muxer = "hls"
            if os.path.splitext(out_path)[1].lower() != ".m3u8":
                base, _ = os.path.splitext(out_path)
                out_path = f"{base}.m3u8"
                defaults.append(
                    "HLS output uses a .m3u8 playlist file (the muxer writes "
                    "the media segments next to it)"
                )

        real_out = os.path.realpath(out_path)
        if real_out == os.path.realpath(in_path):
            # Only reachable for an explicit -o (the default was de-collided above).
            raise PlanError(["output path resolves to the input file — refusing to overwrite the source"])
        if wm_path and real_out == os.path.realpath(wm_path):
            raise PlanError(["output path resolves to the watermark file — refusing to overwrite it"])
        if ar_path and real_out == os.path.realpath(ar_path):
            raise PlanError(["output path resolves to the replacement-audio file — refusing to overwrite it"])
        if sub_path and real_out == os.path.realpath(sub_path):
            raise PlanError(["output path resolves to the subtitles file — refusing to overwrite it"])
        if out_path and out_path.startswith("/input/"):
            warnings.append(
                f"output '{out_path}' lands in the input folder — later "
                "commands can reference it by bare name (chained processings)"
            )

    # -- remux vs re-encode ------------------------------------------------
    has_trim = edit.trim is not None
    # A stream-copy cut (TrimSpec.stream_copy) does not re-encode: it is
    # -ss/-t on the compressed stream (video keyframe-accurate). FFmpeg has
    # no 'trim' bitstream filter — this is how a 'trim' request is served
    # in a copy context.
    copy_trim = has_trim and edit.trim.stream_copy
    # Soft subtitles only mux a track — they touch no frame, so they do not
    # force a re-encode (M5). Burning is a filter chain and does. Any other
    # operation (filters, resample, replace, ...) touches a stream and does.
    forced_ops = [o for o in edit.operations
                  if not (o.type == "subtitles" and o.mode == "soft")]

    if out.mode == "subtitles":
        # A dump is always a stream copy of the subtitle track.
        if metadata is not None and metadata.subtitle_streams == 0:
            raise PlanError(["the input has no subtitle stream to dump"])
        mode = "copy"
    elif out.mode == "audio":
        if metadata is not None and not metadata.has_audio and not ar_assets:
            raise PlanError(["the input has no audio stream to extract"])
        # PRD FR-4: trimming re-encodes — except a stream-copy cut
        # (TrimSpec.stream_copy), which is -ss/-t on the compressed stream;
        # an explicit target bitrate forces a re-encode (stream copy cannot
        # honor a bitrate the input lacks); any operation
        # (atempo/resample/filter/replace) needs decoding.
        needs_reencode = (has_trim and not copy_trim or bool(forced_ops)
                          or out.audio_bitrate_kbps is not None
                          or bool(out.audio_codec_options))
        if not needs_reencode:
            if metadata and metadata.audio_codec == out.audio_codec:
                mode = "copy"
            else:
                mode = "reencode"
                if metadata:
                    warnings.append(
                        f"input audio is {metadata.audio_codec}; encoding to "
                        f"{out.audio_codec} to match the requested output"
                    )
                elif stream_scheme(in_path) is not None:
                    defaults.append(
                        "input is a remote stream — it can't be probed locally, so the "
                        "audio is re-encoded to guarantee the requested codec/container"
                    )
                else:
                    defaults.append(
                        "no media metadata provided — the audio is re-encoded to "
                        "guarantee the requested codec/container (supply probe "
                        "metadata to allow a lossless remux when compatible)"
                    )
        else:
            mode = "reencode"
    else:
        # PRD FR-4: trimming re-encodes. An explicit target bitrate also
        # forces a re-encode: stream copy cannot honor a bitrate the input
        # does not have. A filtergraph decodes the video, so it re-encodes too.
        # A container the registry marks reencode (MXF: the mxf muxer takes
        # an encoded master and rejects stream-copied h264) re-encodes too.
        force_reencode = bool(
            REGISTRY["containers"].get(out.container, {}).get("reencode"))
        # Encoder-specific parameters cannot be honored by a stream copy:
        # codec options, profile/preset and pixel format each name an
        # encoder setting a remuxed stream would silently ignore (M8).
        encoder_params = (bool(edit.output.video_codec_options)
                          or (bool(edit.output.audio_codec_options)
                              and edit.output.audio_mode == AudioMode.ENCODE)
                          or edit.output.video_profile is not None
                          or edit.output.video_preset is not None
                          or edit.output.pixel_format is not None)
        needs_reencode = (has_trim and not copy_trim or bool(forced_ops)
                          or bool(edit.filtergraph)
                          or edit.output.video_bitrate_kbps is not None
                          or encoder_params
                          or force_reencode)
        if force_reencode and not (has_trim or bool(forced_ops)
                                   or bool(edit.filtergraph)
                                   or edit.output.video_bitrate_kbps is not None):
            defaults.append(
                f"{out.container} is a master format — the video is re-encoded "
                "into it (a stream copy of the matching codec would still be "
                "rejected by the muxer)"
            )
        if not needs_reencode:
            if metadata and metadata.video_codec == edit.output.video_codec and \
                    _audio_compatible(metadata, edit.output.container, edit.output.audio_mode) and \
                    (edit.output.audio_codec is None
                     or metadata.audio_codec == edit.output.audio_codec):
                mode = "copy"
            else:
                mode = "reencode"
                if metadata:
                    if metadata.video_codec == edit.output.video_codec:
                        want = edit.output.audio_codec \
                            or audio_for(edit.output.container)
                        if want is None:
                            warnings.append(
                                f"input audio is {metadata.audio_codec}; the "
                                f"{edit.output.container} output carries no "
                                "audio, so the track is dropped"
                            )
                        else:
                            warnings.append(
                                f"input audio is {metadata.audio_codec}; encoding to "
                                f"{want} to match the requested output"
                            )
                    else:
                        warnings.append(
                            f"input video is {metadata.video_codec}; re-encoding to "
                            f"{edit.output.video_codec} to match the requested output"
                        )
                elif stream_scheme(in_path) is not None:
                    defaults.append(
                        "input is a remote stream — it can't be probed locally, so the output is "
                        "re-encoded to guarantee the requested codec/container"
                    )
                else:
                    defaults.append(
                        "no media metadata provided — re-encoding to guarantee the requested "
                        "codec/container (supply probe metadata to allow a lossless remux when compatible)"
                    )
        else:
            mode = "reencode"

    # -- bitstream filters + stream-copy trim ------------------------------
    # Bitstream filters run on the COMPRESSED stream: they force a stream
    # copy and cannot coexist with a re-encode. ('trim' is not an FFmpeg
    # bitstream filter — the schema normalized it to TrimSpec.stream_copy.)
    user_bsfs = edit.bitstream_filters or []
    vbsfs = [b for b in user_bsfs
             if (bsf_spec(b.name) or {}).get("kind") in ("video", "both")]
    abufs = [b for b in user_bsfs
             if (bsf_spec(b.name) or {}).get("kind") == "audio"]
    if user_bsfs and out.mode == "subtitles":
        raise PlanError([
            "bitstream filters cannot apply to a subtitle dump — the dump "
            "decodes the subtitle stream itself"
        ])
    if user_bsfs and device is not None:
        raise PlanError([
            f"bitstream filters run on the compressed stream, but output to "
            f"audio device '{device}' always decodes and re-encodes to "
            "pcm_s16le — the filter would have nothing to run on"
        ])
    if vbsfs and out.mode != "video":
        raise PlanError([
            "bitstream filter(s) " + ", ".join(b.name for b in vbsfs)
            + " need a video stream, but this output is audio-only"
        ])
    if vbsfs and out.mode == "video" and mode == "reencode":
        raise PlanError([
            "bitstream filter(s) " + ", ".join(b.name for b in vbsfs)
            + " run on the compressed video stream and force a stream copy, "
            "but this plan re-encodes the video — drop the re-encoding edit "
            "(or the filter)"
        ])
    if copy_trim and out.mode in ("video", "audio") and mode == "reencode":
        raise PlanError([
            "a stream-copy trim requires the input codec to survive the "
            "remux, but this plan re-encodes — drop the re-encoding edit "
            "(or ask for a normal, re-encoding trim)"
        ])
    audio_removed = out.audio_mode == AudioMode.REMOVE
    if abufs:
        if out.mode == "video" and (audio_removed or mode == "reencode"):
            raise PlanError([
                "bitstream filter(s) " + ", ".join(b.name for b in abufs)
                + " run on the input's compressed audio stream, but this "
                "plan " + ("removes the audio" if audio_removed
                           else "re-encodes the audio")
            ])
        elif out.mode == "audio" and (ar_path is not None or mode == "reencode"):
            raise PlanError([
                "bitstream filter(s) " + ", ".join(b.name for b in abufs)
                + " run on the input's compressed audio stream, but this "
                "plan " + ("replaces the audio" if ar_path is not None
                           else "re-encodes the audio")
            ])
    # Codec match (bsfs are codec-specific) and availability disclosure.
    # Spec codecs are FFmpeg names; ffprobe reports "h265" for "hevc".
    for b in user_bsfs:
        spec = bsf_spec(b.name) or {}
        kind = spec.get("kind")
        stream = "audio" if kind == "audio" else "video"
        codecs = spec.get("codecs")
        if codecs:
            probed = None
            if metadata is not None:
                probed = (metadata.audio_codec if stream == "audio"
                          else metadata.video_codec)
            if probed is None:
                warnings.append(
                    f"bitstream filter '{b.name}' requires a "
                    + " / ".join(codecs)
                    + f" {stream} stream — the input could not be probed, "
                    "so the codec is not verified"
                )
            elif {v: k for k, v in _FFPROBE_CODEC.items()}.get(
                    probed, probed) not in codecs and probed not in codecs:
                raise PlanError([
                    f"bitstream filter '{b.name}' works on "
                    + " / ".join(codecs)
                    + f" streams, but the input {stream} is {probed}"
                ])
        if spec.get("min_version"):
            note = _version_note(f"bitstream filter '{b.name}'",
                                 spec["min_version"])
            if note:
                warnings.append(note)
        for k, opt in (spec.get("options") or {}).items():
            if k in b.options and opt.get("min_version"):
                note = _version_note(f"option '{k}' for '{b.name}'",
                                     opt["min_version"])
                if note:
                    warnings.append(note)
        if spec.get("diagnostic"):
            warnings.append(
                f"bitstream filter '{b.name}' is diagnostic — it only writes "
                "to the log; the output stream is unchanged"
            )

    if edit.output.gpu and mode == "copy":
        warnings.append(
            "the GPU was requested, but this output is a stream copy "
            "(no re-encode) — the GPU will not be used"
        )
    if edit.output.qsv and mode == "copy":
        warnings.append(
            "Quick Sync (QSV) was requested, but this output is a stream copy "
            "(no re-encode) — QSV will not be used"
        )
    if device is not None:
        ep = _build_device_plan(edit, in_path, out_path, device, ar_path, defaults)
    elif out.mode == "subtitles":
        ep = _build_subtitle_plan(edit, in_path, out_path, defaults)
    elif out.mode == "audio":
        ep = _build_audio_plan(edit, in_path, out_path, metadata, mode,
                               ar_path, defaults)
    elif mode == "copy":
        sub_enc = soft_subtitle_codec(edit.output.container) if sub_path else None
        ep = _build_copy_plan(edit, in_path, out_path, metadata,
                              sub_path=sub_path, sub_enc=sub_enc)
    else:
        sub_input, sub_exprs, sub_enc = None, None, None
        if sub_path:
            so = sub_ops[0]
            if so.mode == "soft":
                sub_enc = soft_subtitle_codec(edit.output.container)
                sub_input = sub_path
            else:
                # burn: the libass filter opens the file itself — no input
                sub_exprs = {so.asset_id: subtitle_burn_expr(sub_path)}
        ep = _build_reencode_plan(
            edit, in_path, wm_path, ar_path, sub_input, sub_exprs, sub_enc,
            out_path, defaults, warnings, combine=combine
        )
        if edit.ops_of("stabilize"):
            _attach_stabilize_passes(ep, out_path, defaults)
    ep.defaults.extend(defaults)
    ep.warnings.extend(warnings)
    if muxer:
        # explicit muxer for the stream destination (registry streaming.output);
        # the destination token is already last in the argv (builder contract)
        ep.output_opts = ["-f", muxer] + ep.output_opts
    if out.format_options and device is None:
        # Registry-validated container/format options (hls_time, movflags,
        # mpegts_flags, ...) — emitted as -<key> <value> before the output path.
        ep.output_opts += [tok for k, v in sorted(out.format_options.items())
                           for tok in (f"-{k}", v)]
    _metadata_warnings(ep, edit, metadata)
    return ep


def _container_from_path(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    for name, info in REGISTRY["containers"].items():
        if ext in info["extensions"]:
            return name
    return None


_FFPROBE_CODEC = {"hevc": "h265"}  # ffprobe name -> fass codec name


def _bsf_arg(b) -> str:
    """One -bsf:v/-bsf:a argument: the filter name, or name=k=v:k2=v2 —
    FFmpeg joins bitstream-filter options with colons inside the argument
    (multiple filters = repeated -bsf flags, chain order = argument order)."""
    if not b.options:
        return b.name
    return b.name + "=" + ":".join(f"{k}={b.options[k]}" for k in sorted(b.options))


def _build_diagnostic_plan(edit: EditPlan, in_path: str, defaults: list[str],
                           warnings: list[str], output_filename: str | None,
                           diag_names: list[str],
                           compare: dict | None = None
                           ) -> ExecutionPlan:
    """Diagnostic analysis (M10.2): decode through the diagnostic filters and
    discard the stream ('-f null -', registry diagnostic_output). The filters
    write their results to the log at info level, so the builder's -loglevel
    is raised; no file is written, so an output filename is ignored (noted).
    A stream destination or audio device is a contradiction — there is no
    data to carry.

    ``compare`` (M10.4) switches the plan to a comparison in the Netflix
    vmaf docs' command order: the reference clip is input 0, the main
    (distorted) input 1, and the graph is the labeled metric chain from
    ``_comparison_segments`` (pads [distorted][reference]) — the simple
    vf/af chains do not apply."""
    dest = output_filename or edit.output_destination
    if dest and stream_scheme(dest) is not None:
        raise PlanError([
            f"diagnostic analysis writes nothing — the stream destination "
            f"'{dest}' has no data to carry; drop it"])
    if dest and output_device_for(dest) is not None:
        raise PlanError([
            f"diagnostic analysis writes nothing — it cannot target the "
            f"audio device '{dest}'"])
    if output_filename:
        defaults.append(
            f"output filename '{output_filename}' ignored — a diagnostic "
            "run writes no file")
    main = _main_input(in_path)
    if compare is not None:
        # The Netflix vmaf documentation's command order: the reference is
        # input 0, the main (distorted) input 1 — '-i reference
        # -i distorted'; the metric's pads are [distorted][reference].
        inputs = [InputSpec(compare["ref_asset"].path), main]
    else:
        inputs = [main]
    if edit.trim is not None:
        main.pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    opts: list[str] = []
    if edit.trim is not None and edit.trim.is_bounded:
        opts += ["-t", fmt_num(edit.trim.duration_seconds)]
    diag = diagnostic_output()
    opts += ["-f", diag.get("muxer", "null")]
    fcomplex: str | None = None
    vf = af = None
    if compare is not None:
        fcomplex = ";".join(_comparison_segments(compare, defaults, warnings))
        warnings.append(
            "diagnostic analysis — nothing is written to a file; "
            f"the {compare['op'].metric} score is in the run log "
            f"(command order: the reference '{compare['ref_asset'].path}' "
            "is the first input, the main (distorted) input the second — "
            "compared frame by frame)")
    else:
        vf = ",".join(o.expr for o in edit.operations
                      if o.type == "filter" and o.domain == "video")
        af = ",".join(o.expr for o in edit.operations
                      if o.type == "filter" and o.domain == "audio")
        warnings.append(
            "diagnostic analysis — nothing is written to a file; the results "
            "are in the run log (log level raised to info)")
    for name in dict.fromkeys(diag_names):
        note = _DIAG_NOTES.get(name)
        if note:
            warnings.append(note)
    return ExecutionPlan(
        mode="diagnostic",
        inputs=inputs,
        vf=vf or None,
        filter_complex=fcomplex,
        # a labeled filtergraph output is not auto-mapped — the metric pad
        # must be connected to the (null) output explicitly
        maps=["[cmp]"] if compare is not None else [],
        output_opts=opts,
        output_path=diag.get("path", "-"),
        af=af or None,
        loglevel="info",
    )


def vmaf_model_path() -> str | None:
    """libvmaf model location from the environment (M10.4): a full path in
    FASS_VMAF_MODEL, else a model under FASS_VMAF_MODEL_DIR — vmaf_v0.6.1.pkl
    (legacy) or vmaf_v0.6.1.json (the official Netflix/vmaf distribution is
    JSON-only for v0.6.1) — else None (the filter's built-in model/version
    applies)."""
    model = os.environ.get("FASS_VMAF_MODEL")
    if model:
        return model
    model_dir = os.environ.get("FASS_VMAF_MODEL_DIR")
    if model_dir:
        for name in ("vmaf_v0.6.1.pkl", "vmaf_v0.6.1.json"):
            candidate = os.path.join(model_dir, name)
            if os.path.exists(candidate):
                return candidate
        return os.path.join(model_dir, "vmaf_v0.6.1.pkl")
    return None


def _comparison_segments(compare: dict, defaults: list[str],
                         warnings: list[str]) -> list[str]:
    """Labeled -filter_complex segments for a comparison (M10.4).

    ``compare`` is the plan_execution context: {'op', 'main_media',
    'ref_asset', 'ref_media'}. The command line follows the Netflix vmaf
    documentation (resource/doc/ffmpeg.md): the reference is input 0
    ('-i reference -i distorted'), and the metric's pads take
    '[distorted][reference]' — so the main (distorted) input, pad 1,
    feeds the metric first. The metric needs identical frame dimensions
    on both pads, so the distorted input is scaled to the reference's
    size when they differ (the doc's convention: the reference is the
    ground truth); rate and pixel-format mismatches are only noted
    (converting them would resample the measured signal and distort the
    metric). 'setpts=PTS-STARTPTS' on a pad aligns the timelines the way
    the doc's example does (filters synchronize on timestamps, not
    frames) — but only for streams that HAVE packet pts: a raw bitstream
    demuxes without any, so each pad is re-based only when its probe says
    the stream carries pts (has_pts True or unknown; a confirmed
    has_pts=False skips the filter and is noted). The last segment feeds
    the two labeled pads into the metric and labels the output [cmp]."""
    op = compare["op"]
    main = compare["main_media"]
    ref = compare["ref_media"]
    metric = op.metric
    segs: list[str] = []
    c1: list[str] = []
    if (main.width and main.height and ref.width and ref.height
            and (ref.width, ref.height) != (main.width, main.height)):
        c1.append(f"scale={ref.width}:{ref.height}:flags=bicubic")
        warnings.append(
            f"the main input is {main.width}x{main.height}, the reference "
            f"is {ref.width}x{ref.height} — the main (distorted) input is "
            "scaled to the reference's size (the metric compares identical "
            "dimensions)")
    if main.has_pts is False:
        warnings.append(
            "the main input carries no packet timestamps (raw stream) — "
            "'setpts=PTS-STARTPTS' was skipped for it; frames are compared "
            "in arrival order")
    elif main.has_pts is not False:
        c1.append("setpts=PTS-STARTPTS")
    if c1:
        segs.append(f"[1:v]{','.join(c1)}[c1]")
    p0 = "[c1]" if c1 else "[1:v]"
    if ref.has_pts is False:
        warnings.append(
            "the reference carries no packet timestamps (raw stream) — "
            "'setpts=PTS-STARTPTS' was skipped for it; frames are compared "
            "in arrival order")
    if ref.has_pts is not False:
        segs.append("[0:v]setpts=PTS-STARTPTS[c0]")
    c0 = "[c0]" if ref.has_pts is not False else "[0:v]"
    if (main.fps and ref.fps and abs(main.fps - ref.fps) > 0.01):
        warnings.append(
            f"the reference runs at {fmt_num(ref.fps)} fps, the main input "
            f"at {fmt_num(main.fps)} fps — frames are compared in arrival "
            "order (no rate conversion; the metric scores the overlapping "
            "span)")
    if (main.pix_fmt and ref.pix_fmt and main.pix_fmt != ref.pix_fmt):
        warnings.append(
            f"the reference is {ref.pix_fmt}, the main input is "
            f"{main.pix_fmt} — the metric converts internally")
    opts = dict(op.options)
    if metric == "libvmaf" and "model" not in opts:
        model = vmaf_model_path()
        if model:
            # the filter's model option is a specification
            # ('version=vmaf_v0.6.1' or 'path=<file>'); a bare path is
            # rejected by the filter
            opts["model"] = f"path={model}"
            defaults.append(
                f"libvmaf model: {model} (FASS_VMAF_MODEL / "
                "FASS_VMAF_MODEL_DIR)")
    tail = ""
    if opts:
        tail = "=" + ":".join(f"{k}={v}" for k, v in sorted(opts.items()))
    segs.append(f"{p0}{c0}{metric}{tail}[cmp]")
    return segs


def _build_copy_plan(edit: EditPlan, in_path: str, out_path: str,
                     metadata: MediaInfo | None, sub_path: str | None = None,
                     sub_enc: str | None = None) -> ExecutionPlan:
    audio_remove = edit.output.audio_mode == AudioMode.REMOVE
    inputs = [_main_input(in_path)]
    maps = ["0:v:0"]
    opts: list[str] = []
    if edit.trim is not None:
        # Stream-copy cut (TrimSpec.stream_copy): -ss before the input is
        # keyframe-accurate for video (nothing is decoded) and sample-accurate
        # for audio; -t bounds the output.
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    if edit.trim is not None and edit.trim.is_bounded:
        opts += ["-t", fmt_num(edit.trim.duration_seconds)]
    if audio_remove:
        opts.append("-an")
    if not audio_remove:
        maps.append("0:a?")
    opts += ["-c", "copy"]
    if sub_path:
        # Soft subtitles: a selectable track muxed next to the copied
        # streams. -c:s after -c copy overrides that stream only (the
        # subtitle is still encoded — there is nothing to copy).
        inputs.append(InputSpec(sub_path))
        maps.append(f"{len(inputs) - 1}:s:0")
        opts += ["-c:s", sub_enc]
    # Bitstream filters (no re-encode). The auto filter a container pair
    # needs runs first, then the user-requested filters, chained in request
    # order; a user filter identical to the auto one is not duplicated.
    # The source codec comes from the probe when one exists, else from the
    # plan.
    if metadata is not None and metadata.video_codec:
        codec = _FFPROBE_CODEC.get(metadata.video_codec, metadata.video_codec)
    else:
        codec = edit.output.video_codec
    auto_v = bsf_for_copy(_container_from_path(in_path) or "",
                          edit.output.container, codec)
    auto_a = None
    # Audio direction: AAC in MPEG-TS is ADTS-framed; MP4/MOV need the ADTS
    # header stripped. The codec name only comes from the probe.
    if not audio_remove and metadata is not None and metadata.audio_codec:
        auto_a = audio_bsf_for_copy(
            _container_from_path(in_path) or "",
            edit.output.container,
            metadata.audio_codec,
        )
    defaults: list[str] = []
    user_bsfs = edit.bitstream_filters or []
    vchain: list[str] = []
    if auto_v:
        vchain.append(auto_v)
        defaults.append(
            f"remuxing to MPEG-TS needs bitstream filter '{auto_v}' — the compressed "
            "stream is re-stamped to Annex-B without re-encoding"
        )
    for b in user_bsfs:
        if (bsf_spec(b.name) or {}).get("kind") in ("video", "both") \
                and b.name not in vchain:
            vchain.append(_bsf_arg(b))
            defaults.append(
                f"bitstream filter '{b.name}' applied to the compressed video "
                "stream (no re-encode)"
            )
    for name in vchain:
        opts += ["-bsf:v", name]
    if not audio_remove:
        achain: list[str] = []
        if auto_a:
            achain.append(auto_a)
            defaults.append(
                f"remuxing AAC to MP4/MOV needs bitstream filter '{auto_a}' — the "
                "ADTS header is stripped from the compressed stream without re-encoding"
            )
        for b in user_bsfs:
            if (bsf_spec(b.name) or {}).get("kind") == "audio" \
                    and b.name not in achain:
                achain.append(_bsf_arg(b))
                defaults.append(
                    f"bitstream filter '{b.name}' applied to the compressed audio "
                    "stream (no re-encode)"
                )
        for name in achain:
            opts += ["-bsf:a", name]
    if sub_path:
        defaults.append(
            f"subtitles muxed as a selectable {sub_enc} track from '{sub_path}' "
            "(soft — no re-encode)"
        )
    return ExecutionPlan(
        mode="copy",
        inputs=inputs,
        vf=None,
        filter_complex=None,
        maps=maps,
        output_opts=opts,
        output_path=out_path,
        defaults=defaults,
    )


def _build_reencode_plan(edit: EditPlan, in_path: str, wm_path: str | None,
                         audio_path: str | None, sub_path: str | None,
                         sub_exprs: dict[str, str] | None, sub_enc: str | None,
                         out_path: str, defaults: list[str], warnings: list[str],
                         combine: dict | None = None) -> ExecutionPlan:
    inputs = [_main_input(in_path)]
    if edit.trim is not None:
        # -ss as an input option: frame-accurate when followed by decoding.
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    # Input order is fixed: main=0, combiner extras in the plan's asset
    # order (the graph pads reference them by index), then — mutually
    # exclusive with a combiner — watermark=1 (build_video_chain hardcodes
    # [1:v]), soft-subtitle input before the replacement audio (audio stays
    # last, as audio_idx below assumes).
    if combine:
        inputs += [InputSpec(a.path) for a in combine["extra_assets"]]
    if wm_path:
        inputs.append(InputSpec(wm_path))
    sub_idx = None
    if sub_path:
        # SOFT mode only: a selectable track mapped from this input. Burn
        # needs no input — the libass filter opens the file itself.
        inputs.append(InputSpec(sub_path))
        sub_idx = len(inputs) - 1
    if audio_path:
        inputs.append(InputSpec(audio_path))
    audio_idx = len(inputs) - 1 if audio_path else None

    fg_labels: list[str] = []
    if edit.filtergraph:
        # A full filtergraph replaces the built chain: its labeled output
        # pads are mapped explicitly. An unlabeled graph emits no -map at
        # all — FFmpeg auto-maps the graph output plus audio/subtitles
        # (plan_execution rejects that mix when explicit audio/subtitle
        # maps are required).
        vf, fcomplex = None, edit.filtergraph
        fg_labels = filtergraph_output_labels(edit.filtergraph)
        maps = [f"[{l}]" for l in fg_labels]
    else:
        ca_label: str | None = None
        chain_ops = [o for o in edit.operations if o.type != "combine"]
        if combine:
            # The combiner consumes the extra inputs as labeled pads; any
            # remaining video operations chain after its output pad.
            segs, cv_label, ca_label, audio_source = _combiner_segments(
                combine, defaults, warnings)
            chain_mode, chain, vlabel = build_video_chain(
                chain_ops, defaults, start_label=cv_label)
            # The combiner always runs in complex mode; there is no -vf.
            vf, fcomplex = None, (
                ";".join(segs) + (";" + chain if chain else ""))
            if cv_label is not None:
                # video combiner: the graph's video pad is the output video
                maps = [f"[{vlabel or cv_label}]"]
            elif vlabel is not None:
                # audio combiner + video ops: chain runs off the main [0:v]
                maps = [f"[{vlabel}]"]
            else:
                # audio combiner alone: video is the main input's own stream
                maps = ["0:v:0"]
        else:
            chain_mode, chain, vlabel = build_video_chain(
                edit.operations, defaults, sub_exprs=sub_exprs)
            if chain_mode == "complex":
                vf, fcomplex = None, chain
                maps = [f"[{vlabel}]"]
            else:
                vf, fcomplex = (chain or None), None
                maps = ["0:v:0"]

    ar_op = edit.ops_of("audio_replace")
    audio_remove = edit.output.audio_mode == AudioMode.REMOVE and not ar_op
    # A container that carries no audio (e.g. an animated GIF) maps no audio
    # stream at all — the input's audio track is dropped.
    carries_audio = bool(
        REGISTRY["matrix"].get(edit.output.container, {}).get("audio"))
    if ar_op:
        # replacement audio: first audio stream of the extra input, never the
        # input's own track (and never an optional map)
        maps.append(f"{audio_idx}:a:0")
    elif combine and audio_source == "graph" and carries_audio:
        # audio combiner (or concat with audio): the graph's audio pad is
        # the output audio
        maps.append(f"[{ca_label}]")
    elif combine and audio_source == "main" and not audio_remove and carries_audio:
        # video combiner: the combined video replaces the frame source, the
        # audio stays the main input's own stream
        maps.append("0:a?")
    elif (not audio_remove and not (edit.filtergraph and not fg_labels)
          and not (combine and audio_source == "none")
          and carries_audio):
        # combine with audio_source == "none" (concat without audio): no audio
        # explicit maps: keep the optional audio map; auto-map (unlabeled
        # graph) already picks the audio stream on its own
        maps.append("0:a?")

    if sub_idx is not None:
        # Soft subtitles: the first subtitle stream of the subtitle input.
        # No -shortest — subtitles may legitimately be shorter than the video.
        maps.append(f"{sub_idx}:s:0")

    # Audio side of speed changes (atempo) plus audio filter operations:
    # one -af chain, in user-specified order. -af is inert when the output
    # has no audio stream, so it is only emitted when audio survives.
    speed_ops = edit.ops_of("speed")
    speed_op = speed_ops[0] if speed_ops else None
    af = _audio_filter_chain(edit) \
        if not audio_remove and carries_audio else None

    opts: list[str] = []
    if edit.trim is not None and edit.trim.is_bounded:
        dur = edit.trim.duration_seconds
        if speed_op is not None:
            # -t bounds the *output* timeline; at Nx speed the cut interval
            # (source seconds) produces dur/N output seconds.
            dur = dur / speed_op.factor
            defaults.append(
                f"output duration {fmt_num(dur)}s: the trimmed "
                f"{fmt_num(edit.trim.duration_seconds)}s of source at "
                f"{fmt_num(speed_op.factor)}x speed"
            )
        opts += ["-t", fmt_num(dur)]

    vc = REGISTRY["video_codecs"][edit.output.video_codec]
    vco = edit.output.video_codec_options
    if edit.output.gpu:
        # GPU encoding (M6): the nvenc coder for the requested codec
        # (validated above — VP9 and the x264-only options are rejected).
        v_encoder = REGISTRY["gpu"]["coders"][edit.output.video_codec]["encoder"]
        defaults.append(
            f"GPU encoding: {edit.output.video_codec} via {v_encoder} (NVIDIA)"
        )
    elif edit.output.qsv:
        # QSV encoding (M8): the Quick Sync coder (validated above).
        v_encoder = REGISTRY["qsv"]["coders"][edit.output.video_codec]["encoder"]
        defaults.append(
            f"QSV encoding: {edit.output.video_codec} via {v_encoder} (Intel Quick Sync)"
        )
    else:
        v_encoder = vc["encoder"]
    opts += ["-c:v", v_encoder]
    # Per-encoder private codec options (M8): -<key> <value> right after -c:v
    # (registry codec_options table; validated in the plan schema).
    if vco:
        opts += _codec_option_tokens(vco)
        defaults.append(
            f"video codec options for {v_encoder}: "
            + ", ".join(f"-{k} {v}" for k, v in sorted(vco.items()))
        )
    kbps = edit.output.video_bitrate_kbps
    if kbps is not None:
        # CBR-style target overrides the quality preset (registry bitrate_policy):
        # -b:v and -maxrate at the target; -bufsize 2x where the encoder takes it.
        opts += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k"]
        if edit.output.video_codec in ("h264", "h265"):
            opts += ["-bufsize", f"{2 * kbps}k"]
        details = f"-b:v/-maxrate {kbps}k"
        if edit.output.video_codec in ("h264", "h265"):
            details += f", -bufsize {2 * kbps}k"
        defaults.append(f"target video bitrate {kbps} kbps (CBR-style: {details})")
    elif edit.output.gpu:
        # nvenc quality control: -rc vbr -cq N is the GPU form of CRF N.
        # User rate-control codec options (cq/rc) win: the registry default
        # is suppressed and noted.
        crf = vc.get("preset", {}).get("crf")
        user_rc = any(k in vco for k in ("cq", "rc"))
        if crf is not None and not user_rc:
            opts += ["-rc", "vbr", "-cq", str(crf)]
            defaults.append(
                f"GPU constant-quality mode: -rc vbr -cq {crf} (the nvenc form of CRF {crf})"
            )
        elif user_rc:
            defaults.append(
                "GPU rate control from the codec options (cq/rc) — the "
                f"registry default -rc vbr -cq {crf} is not emitted"
            )
    elif edit.output.qsv:
        # QSV has no CRF/CQ-style quality option in its private table;
        # without a target bitrate the encoder falls back to a built-in
        # default far below fass's quality targets, so a 5 Mbps CBR-style
        # target is applied and noted.
        opts += ["-b:v", "5000k", "-maxrate", "5000k"]
        defaults.append(
            "QSV default target bitrate 5000k (CBR-style: -b:v/-maxrate) — "
            "no target bitrate was given and QSV has no CRF-style knob"
        )
    else:
        preset = vc.get("preset", {})
        if "crf" in preset:
            if "crf" in vco:
                defaults.append(
                    "constant quality from the crf codec option — the "
                    f"registry default CRF {preset['crf']} is not emitted"
                )
            else:
                opts += ["-crf", str(preset["crf"])]
        if "bitrate" in preset:
            opts += ["-b:v", preset["bitrate"]]
    if edit.output.video_profile is not None:
        opts += ["-profile:v", edit.output.video_profile]
        defaults.append(f"encoder profile {edit.output.video_profile} (maximum-compatibility setting)")
    if edit.output.video_preset is not None:
        opts += ["-preset", edit.output.video_preset]
        defaults.append(f"encoder preset {edit.output.video_preset}")
    # MXF (master wrapper): ffmpeg 9.x's mxf muxer index table requires
    # frame offsets within ±127, so the container caps the GOP below that —
    # x264's default of 250 fails at the trailer on newer builds (6.1.1
    # tolerated it). Only CPU h264 is capped: mpeg2's default GOP is already
    # small, prores takes no GOP option, and nvenc/QSV default GOPs are
    # already under 127.
    gop_cap = REGISTRY["containers"].get(edit.output.container, {}).get("gop")
    if gop_cap is not None and not edit.output.gpu and not edit.output.qsv \
            and edit.output.video_codec == "h264":
        opts += ["-g", str(gop_cap)]
        defaults.append(
            f"GOP capped at {gop_cap} frames — the mxf muxer's index table "
            "requires frame offsets within ±127"
        )
    # Pixel-format conversion (scaler component): the codec default unless
    # the plan names one (validated against the registry matrix in plan_execution).
    pix_fmt = edit.output.pixel_format or vc["pix_fmt"]
    opts += ["-pix_fmt", pix_fmt]
    if pix_fmt != vc["pix_fmt"]:
        defaults.append(
            f"pixel format {pix_fmt} instead of the {edit.output.video_codec} "
            f"default {vc['pix_fmt']}"
        )

    if ar_op:
        # Replacement audio is encoded to the container's audio codec (no
        # stream copy); -shortest ends the output when the shorter of the
        # video and the replacement audio ends.
        ac = REGISTRY["audio_codecs"][audio_for(edit.output.container)]
        opts += ["-c:a", ac["encoder"]]
        if edit.output.audio_codec_options:
            opts += _codec_option_tokens(edit.output.audio_codec_options)
            defaults.append(
                f"audio codec options for {ac['encoder']}: "
                + ", ".join(f"-{k} {v}" for k, v in
                            sorted(edit.output.audio_codec_options.items()))
            )
        if ac.get("bitrate_kbps") is not None:
            opts += ["-b:a", f"{ac['bitrate_kbps']}k"]
        opts.append("-shortest")
        defaults.append(
            f"audio replaced with the first audio stream of '{audio_path}'; "
            "-shortest: output ends when the shorter of video and replacement audio ends"
        )
    elif audio_remove or (combine and audio_source == "none"):
        # A concat without audio maps no audio stream either — -an keeps
        # the output explicitly audio-free.
        opts.append("-an")
    else:
        # A named audio_codec overrides the container's default (validated
        # against the matrix in plan_execution); the bitrate override works
        # the same way the audio-mode branch does. A container that carries
        # no audio at all (e.g. an animated GIF) resolves to None — the
        # input's audio track is simply dropped.
        acodec = edit.output.audio_codec or audio_for(edit.output.container)
        if acodec is None:
            opts.append("-an")
            defaults.append(
                f"a {edit.output.container} file carries no audio — the "
                "input's audio track is dropped"
            )
        else:
            ac = REGISTRY["audio_codecs"][acodec]
            opts += ["-c:a", ac["encoder"]]
            if edit.output.audio_codec_options:
                opts += _codec_option_tokens(edit.output.audio_codec_options)
                defaults.append(
                    f"audio codec options for {ac['encoder']}: "
                    + ", ".join(f"-{k} {v}" for k, v in
                                sorted(edit.output.audio_codec_options.items()))
                )
            kbps = edit.output.audio_bitrate_kbps or ac.get("bitrate_kbps")
            if kbps is not None:
                opts += ["-b:a", f"{kbps}k"]
            # Audio resampler targets (-ar / -ac); inert when the output has
            # no audio stream.
            for ro in edit.ops_of("audio_resample"):
                if ro.sample_rate is not None:
                    opts += ["-ar", str(ro.sample_rate)]
                    defaults.append(f"audio sample rate {ro.sample_rate} Hz")
                if ro.channels is not None:
                    opts += ["-ac", str(ro.channels)]
                    defaults.append(f"audio channels {ro.channels}")

    if sub_exprs:
        # Burn: the escaped filter expression is already in the chain; this
        # note explains what the opaque token does.
        defaults.append(
            "subtitles burned into the video (hard-coded, not selectable — "
            "the filter opens the subtitle file itself, no extra input)"
        )
    if sub_idx is not None:
        # Soft: -c:s encodes the subtitle track (nothing to copy); it
        # overrides the stream defaults for subtitles only.
        opts += ["-c:s", sub_enc]
        defaults.append(
            f"subtitles muxed as a selectable {sub_enc} track from '{sub_path}' "
            "(soft — no re-encode)"
        )

    opts += _matrix_flags(edit.output.container, edit.output.format_options)

    return ExecutionPlan(
        mode="reencode",
        inputs=inputs,
        vf=vf,
        filter_complex=fcomplex,
        maps=maps,
        output_opts=opts,
        output_path=out_path,
        af=af,
    )


def _attach_stabilize_passes(ep: ExecutionPlan, out_path: str,
                             defaults: list[str]) -> None:
    """M10.5: turn the re-encode plan into the two-pass vidstab execution.

    The final pass's video chain becomes the transform pass:
    vidstabtransform prepended (it reads the side-file), the remaining
    chain (resize/grayscale/crop, ...) after it. The detect pass
    re-decodes the same input with vidstabdetect and discards the stream
    through the null muxer (the M10.2 diagnostic pattern) — only the .trf
    side-file it writes survives. The side-file is a BARE name derived
    from the output filename, and both passes run in the same working
    directory (the output's), so nothing is rewritten between them."""
    stem = os.path.splitext(os.path.basename(out_path))[0] or "output"
    trf = f"{stem}.vidstab.trf"
    stab = f"vidstabtransform=input={trf}"
    ep.vf = stab if not ep.vf else stab + "," + ep.vf
    detect = Pass(
        purpose="stabilize-detect",
        inputs=ep.inputs[:1],  # the main input, exactly as pass 2 feeds it
        vf=f"vidstabdetect=result={trf}",
        output_opts=["-f", "null"],
        output_path="-",
    )
    transform = Pass(
        purpose="stabilize-transform",
        inputs=ep.inputs,
        output_path=ep.output_path,
        vf=ep.vf,
        filter_complex=ep.filter_complex,
        af=ep.af,
        maps=list(ep.maps),
        output_opts=list(ep.output_opts),
        loglevel=ep.loglevel,
    )
    ep.passes = [detect, transform]
    ep.intermediates = [trf]
    defaults.append(
        "two-pass stabilization: pass 1 analyzes the camera motion "
        f"(vidstabdetect, stream discarded) and writes '{trf}' next to the "
        "output; pass 2 re-encodes the stabilized clip (vidstabtransform)")


def _build_audio_plan(edit: EditPlan, in_path: str, out_path: str,
                      metadata: MediaInfo | None, mode: str,
                      ar_path: str | None, defaults: list[str]) -> ExecutionPlan:
    """Audio-only output: extract the audio track (and re-encode it when the
    codec/container requires — the decision was made in plan_execution)."""
    inputs = [_main_input(in_path)]
    if edit.trim is not None:
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    if ar_path:
        inputs.append(InputSpec(ar_path))
    audio_idx = len(inputs) - 1 if ar_path else None
    maps = [f"{audio_idx}:a:0" if ar_path else "0:a:0"]

    opts: list[str] = []
    speed_op = edit.ops_of("speed")[0] if edit.ops_of("speed") else None
    if edit.trim is not None and edit.trim.is_bounded:
        dur = edit.trim.duration_seconds
        if speed_op is not None:
            dur = dur / speed_op.factor
            defaults.append(
                f"output duration {fmt_num(dur)}s: the trimmed "
                f"{fmt_num(edit.trim.duration_seconds)}s of source at "
                f"{fmt_num(speed_op.factor)}x speed"
            )
        opts += ["-t", fmt_num(dur)]

    if mode == "copy":
        opts += ["-c", "copy"]
        codec = metadata.audio_codec if metadata is not None \
            else edit.output.audio_codec
        absf = audio_bsf_for_copy(
            _container_from_path(in_path) or "",
            edit.output.container,
            codec,
        )
        achain: list[str] = []
        if absf:
            achain.append(absf)
            defaults.append(
                f"remuxing {codec} to {edit.output.container} needs bitstream "
                f"filter '{absf}' — the compressed stream is re-stamped without "
                "re-encoding"
            )
        for b in edit.bitstream_filters or []:
            if (bsf_spec(b.name) or {}).get("kind") == "audio" \
                    and b.name not in achain:
                achain.append(_bsf_arg(b))
                defaults.append(
                    f"bitstream filter '{b.name}' applied to the compressed audio "
                    "stream (no re-encode)"
                )
        for name in achain:
            opts += ["-bsf:a", name]
    else:
        ac = REGISTRY["audio_codecs"][edit.output.audio_codec]
        opts += ["-c:a", ac["encoder"]]
        if edit.output.audio_codec_options:
            opts += _codec_option_tokens(edit.output.audio_codec_options)
            defaults.append(
                f"audio codec options for {ac['encoder']}: "
                + ", ".join(f"-{k} {v}" for k, v in
                            sorted(edit.output.audio_codec_options.items()))
            )
        kbps = edit.output.audio_bitrate_kbps or ac.get("bitrate_kbps")
        if kbps is not None:
            opts += ["-b:a", f"{kbps}k"]
            if edit.output.audio_bitrate_kbps is None:
                defaults.append(
                    f"audio bitrate {kbps} kbps (codec default for "
                    f"{edit.output.audio_codec})"
                )
        else:
            defaults.append(
                f"{edit.output.audio_codec} carries no bitrate "
                "(lossless/PCM) — none applied"
            )
        for ro in edit.ops_of("audio_resample"):
            if ro.sample_rate is not None:
                opts += ["-ar", str(ro.sample_rate)]
                defaults.append(f"audio sample rate {ro.sample_rate} Hz")
            if ro.channels is not None:
                opts += ["-ac", str(ro.channels)]
                defaults.append(f"audio channels {ro.channels}")
    if ar_path:
        defaults.append(f"audio replaced with the first audio stream of '{ar_path}'")

    af = _audio_filter_chain(edit)
    opts += _matrix_flags(edit.output.container, edit.output.format_options)

    return ExecutionPlan(
        mode=mode,
        inputs=inputs,
        vf=None,
        filter_complex=None,
        maps=maps,
        output_opts=opts,
        output_path=out_path,
        af=af,
    )


def _build_subtitle_plan(edit: EditPlan, in_path: str, out_path: str,
                         defaults: list[str]) -> ExecutionPlan:
    """Subtitle dump: write the input's first subtitle stream to a file.

    No -c:s: the srt/vtt/ass muxer decodes the AVSubtitle stream itself —
    text formats are written by the muxer, not an encoder."""
    inputs = [_main_input(in_path)]
    if edit.trim is not None:
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    opts: list[str] = []
    if edit.trim is not None and edit.trim.is_bounded:
        opts += ["-t", fmt_num(edit.trim.duration_seconds)]
    codec = subtitle_dump_codec(edit.output.container)
    defaults.append(
        f"the first subtitle stream is written as {codec} "
        "(the muxer decodes the subtitle stream itself — no codec flag)"
    )
    return ExecutionPlan(
        mode="copy",
        inputs=inputs,
        vf=None,
        filter_complex=None,
        maps=["0:s:0"],
        output_opts=opts,
        output_path=out_path,
    )


def _build_device_plan(edit: EditPlan, in_path: str, out_path: str,
                       device: str, ar_path: str | None,
                       defaults: list[str]) -> ExecutionPlan:
    """Output to a local audio device (alsa/pulse): audio only, pcm_s16le.

    plan_execution has already rejected plans with video edits or removed
    audio, so this builds the audio path alone (trim, optional replacement,
    optional resample)."""
    inputs = [_main_input(in_path)]
    if edit.trim is not None:
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    if ar_path:
        inputs.append(InputSpec(ar_path))
    maps = [f"{len(inputs) - 1}:a:0" if ar_path else "0:a?"]

    opts: list[str] = []
    if edit.trim is not None and edit.trim.is_bounded:
        opts += ["-t", fmt_num(edit.trim.duration_seconds)]
    for ro in edit.ops_of("audio_resample"):
        if ro.sample_rate is not None:
            opts += ["-ar", str(ro.sample_rate)]
        if ro.channels is not None:
            opts += ["-ac", str(ro.channels)]
    opts += ["-acodec", "pcm_s16le", "-f", device]
    if ar_path:
        defaults.append(f"audio replaced with the first audio stream of '{ar_path}'")
    defaults.append(f"audio device output: encoded as pcm_s16le for '{device}'")
    return ExecutionPlan(
        mode="device",
        inputs=inputs,
        vf=None,
        filter_complex=None,
        maps=maps,
        output_opts=opts,
        output_path=out_path,
    )


def _metadata_warnings(ep: ExecutionPlan, edit: EditPlan, metadata: MediaInfo | None) -> None:
    if metadata is None:
        return
    if edit.output.mode == "video":
        if metadata.hdr:
            ep.warnings.append(
                "input looks like HDR; the SDR presets will convert it — check the output before distributing"
            )
        if metadata.rotation:
            ep.warnings.append(
                f"input carries rotation metadata ({metadata.rotation}°); the decoded geometry may differ "
                "from the stored dimensions"
            )
        if metadata.video_streams > 1 or metadata.audio_streams > 1:
            ep.warnings.append(
                "additional streams are excluded (only the first video and first audio stream are used)"
            )
        if metadata.subtitle_streams > 0 and not any(
                o.type == "subtitles" for o in edit.operations):
            ep.warnings.append(
                f"the input has {metadata.subtitle_streams} subtitle stream(s); they are "
                "not carried over (ask to keep or re-add subtitles to include them)"
            )
    elif edit.output.mode == "audio":
        if metadata.audio_streams > 1:
            ep.warnings.append(
                "additional audio streams are excluded (only the first audio stream is used)"
            )
    trim = edit.trim
    if trim is not None and metadata.duration is not None:
        if trim.start_seconds > metadata.duration:
            ep.warnings.append(
                f"trim start ({trim.start_seconds:.1f}s) is beyond the input duration "
                f"({metadata.duration:.1f}s) — the output will be empty"
            )
        elif trim.is_bounded and trim.start_seconds + trim.duration_seconds > metadata.duration + 0.25:
            ep.warnings.append(
                "trim interval extends past the end of the input; the output will be shorter "
                "than requested"
            )
