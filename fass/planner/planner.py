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

from fass.probe.ffprobe import MediaInfo, ffmpeg_available, probe_ffmpeg
from fass.registry import (
    REGISTRY,
    audio_bsf_for_copy,
    audio_for,
    atempo_limits,
    bsf_for_copy,
    bsf_spec,
    capability_errors,
    extension_for,
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
class ExecutionPlan:
    mode: str  # "copy" (remux) | "reencode" | "device"
    inputs: list[InputSpec]
    vf: str | None  # simple -vf chain (no watermark)
    filter_complex: str | None  # labeled graph (watermark present)
    maps: list[str]
    output_opts: list[str]
    output_path: str
    af: str | None = None  # audio filter chain (-af): speed changes
    defaults: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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


def _overlay_position(op: WatermarkOp) -> tuple[str, str]:
    m = op.margin_px
    return {
        "top_left": (str(m), str(m)),
        "top_right": (f"W-w-{m}", str(m)),
        "bottom_left": (str(m), f"H-h-{m}"),
        "bottom_right": (f"W-w-{m}", f"H-h-{m}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }[op.position]


def build_video_chain(operations: list, defaults: list[str],
                      sub_exprs: dict[str, str] | None = None):
    """Build the video filter chain in user-specified order.

    Returns (mode, chain, video_out_label):
      mode "vf"     -> chain is a -vf expression, label is None
      mode "complex"-> chain is a -filter_complex graph, label is the
                      output pad to map

    ``sub_exprs`` maps a burned-subtitle asset id to its complete filter
    argument ('subtitles=…' / 'ass=…'); the file is opened by the filter
    itself, so no extra input is needed (M5).
    """
    if not operations:
        return "none", None, None
    if not any(o.type == "watermark" for o in operations):
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
    prev: str | None = None
    last_out: str | None = None
    for i, o in enumerate(operations):
        out = f"v{i}"
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

    out = edit.output
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

    input_asset = assets.get(edit.input_id)
    if input_asset is None:
        errors.append(f"asset id '{edit.input_id}' is not in the asset map")
    elif input_asset.kind not in {
            "video": (AssetKind.VIDEO, AssetKind.SOURCE),
            "audio": (AssetKind.VIDEO, AssetKind.SOURCE, AssetKind.AUDIO),
            "subtitles": (AssetKind.VIDEO, AssetKind.SOURCE),
        }[out.mode]:
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
                          "watermark", "subtitles", "filter")
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
            out_path, defaults, warnings
        )
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
                         out_path: str, defaults: list[str], warnings: list[str]) -> ExecutionPlan:
    inputs = [_main_input(in_path)]
    if edit.trim is not None:
        # -ss as an input option: frame-accurate when followed by decoding.
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    # Input order is fixed: main=0, watermark=1 (build_video_chain hardcodes
    # [1:v]), soft-subtitle input before the replacement audio (audio stays
    # last, as audio_idx below assumes).
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
    if ar_op:
        # replacement audio: first audio stream of the extra input, never the
        # input's own track (and never an optional map)
        maps.append(f"{audio_idx}:a:0")
    elif not audio_remove and not (edit.filtergraph and not fg_labels):
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
    af = _audio_filter_chain(edit) if not audio_remove else None

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
    elif audio_remove:
        opts.append("-an")
    else:
        # A named audio_codec overrides the container's default (validated
        # against the matrix in plan_execution); the bitrate override works
        # the same way the audio-mode branch does.
        acodec = edit.output.audio_codec or audio_for(edit.output.container)
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
        # Audio resampler targets (-ar / -ac); inert when the output has no
        # audio stream.
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
