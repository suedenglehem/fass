"""Planner: editing plan -> execution plan (plan0.md section 3).

Keeps *what the user wants* (EditPlan) separate from *how to do it*
(stream selection, encoders, filters, option placement). Every default
and warning is produced here, so nothing is hidden inside model output
or inside the rendered command.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fass.probe.ffprobe import MediaInfo
from fass.registry import (
    REGISTRY,
    audio_bsf_for_copy,
    audio_for,
    atempo_limits,
    bsf_for_copy,
    capability_check,
    extension_for,
    input_format_for,
    output_device_for,
    output_device_parts,
    output_schemes,
    pixel_format_error,
    sample_rates,
    source_spec_parts,
    stream_output_info,
    stream_scheme,
    video_presets_for,
    video_profiles_for,
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


def build_video_chain(operations: list, defaults: list[str]):
    """Build the video filter chain in user-specified order.

    Returns (mode, chain, video_out_label):
      mode "vf"     -> chain is a -vf expression, label is None
      mode "complex"-> chain is a -filter_complex graph, label is the
                      output pad to map
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
        return "vf", ",".join(parts) or None, None

    segs: list[str] = []
    prev: str | None = None
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
        elif o.type == "watermark":
            wm_scale = ""
            if o.scale_percent != 100:
                wm_scale = f",scale=iw*{o.scale_percent}/100:ih*{o.scale_percent}/100"
            x, y = _overlay_position(o)
            segs.append(f"[1:v]format=rgba{wm_scale}[wm]")
            segs.append(f"{src}[wm]overlay={x}:{y}[{out}]")
        prev = out
    return "complex", ";".join(segs), f"v{len(operations) - 1}"


# ---------------------------------------------------------------- planning

def _audio_compatible(metadata: MediaInfo, container: str,
                      audio_mode: AudioMode) -> bool:
    if audio_mode == AudioMode.REMOVE:
        return True
    if not metadata.has_audio:
        return True
    allowed = REGISTRY["matrix"][container]["audio"]
    return metadata.audio_codec in allowed


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
    errors.extend(capability_check(edit.output.container, edit.output.video_codec))
    if len(edit.operations) > MAX_OPERATIONS:
        errors.append(f"at most {MAX_OPERATIONS} operations are supported per request")

    out = edit.output
    if out.pixel_format is not None:
        pf_err = pixel_format_error(out.video_codec, out.pixel_format)
        if pf_err:
            errors.append(pf_err)
    if out.video_profile is not None and not video_profiles_for(out.video_codec):
        errors.append(
            f"encoder profiles are only supported for H.264 in the MVP "
            f"('{out.video_profile}' does not apply to {out.video_codec.upper()})"
        )
    if out.video_preset is not None and out.video_codec != "h264":
        errors.append(
            f"encoder presets are only supported for H.264 (x264) in the MVP "
            f"('{out.video_preset}' does not apply to {out.video_codec.upper()})"
        )
    resample = edit.ops_of("audio_resample")
    if resample and out.audio_mode == AudioMode.REMOVE:
        errors.append("the audio is removed, so it cannot be resampled")

    input_asset = assets.get(edit.input_id)
    if input_asset is None:
        errors.append(f"asset id '{edit.input_id}' is not in the asset map")
    elif input_asset.kind not in (AssetKind.VIDEO, AssetKind.SOURCE):
        errors.append(f"asset '{edit.input_id}' is not a video or a source")

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

    if errors:
        raise PlanError(errors)

    crop_err = _crop_too_big(edit, metadata) if metadata is not None else None
    if crop_err:
        raise PlanError([crop_err])

    in_path = input_asset.path
    wm_path = wm_assets[0].path if wm_assets else None
    ar_path = ar_assets[0].path if ar_assets else None

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
        video_ops = sorted({
            o.type for o in edit.operations
            if o.type in ("resize", "crop", "rotate", "speed", "grayscale", "watermark")
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

        real_out = os.path.realpath(out_path)
        if real_out == os.path.realpath(in_path):
            # Only reachable for an explicit -o (the default was de-collided above).
            raise PlanError(["output path resolves to the input file — refusing to overwrite the source"])
        if wm_path and real_out == os.path.realpath(wm_path):
            raise PlanError(["output path resolves to the watermark file — refusing to overwrite it"])
        if ar_path and real_out == os.path.realpath(ar_path):
            raise PlanError(["output path resolves to the replacement-audio file — refusing to overwrite it"])

    # -- remux vs re-encode (plan0.md section 3) ---------------------------
    has_trim = edit.trim is not None
    has_ops = bool(edit.operations)
    # PRD FR-4: trimming re-encodes. An explicit target bitrate also forces
    # a re-encode: stream copy cannot honor a bitrate the input does not have.
    needs_reencode = has_trim or has_ops or edit.output.video_bitrate_kbps is not None

    if not needs_reencode:
        if metadata and metadata.video_codec == edit.output.video_codec and \
                _audio_compatible(metadata, edit.output.container, edit.output.audio_mode):
            mode = "copy"
        else:
            mode = "reencode"
            if metadata:
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

    if device is not None:
        ep = _build_device_plan(edit, in_path, out_path, device, ar_path, defaults)
    elif mode == "copy":
        ep = _build_copy_plan(edit, in_path, out_path, metadata)
    else:
        ep = _build_reencode_plan(
            edit, in_path, wm_path, ar_path, out_path, defaults, warnings
        )
    ep.defaults.extend(defaults)
    ep.warnings.extend(warnings)
    if muxer:
        # explicit muxer for the stream destination (registry streaming.output);
        # the destination token is already last in the argv (builder contract)
        ep.output_opts = ["-f", muxer] + ep.output_opts
    _metadata_warnings(ep, edit, metadata)
    return ep


def _container_from_path(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    for name, info in REGISTRY["containers"].items():
        if ext in info["extensions"]:
            return name
    return None


_FFPROBE_CODEC = {"hevc": "h265"}  # ffprobe name -> fass codec name


def _build_copy_plan(edit: EditPlan, in_path: str, out_path: str,
                     metadata: MediaInfo | None) -> ExecutionPlan:
    audio_remove = edit.output.audio_mode == AudioMode.REMOVE
    maps = ["0:v:0"]
    opts: list[str] = []
    if audio_remove:
        opts.append("-an")
    if not audio_remove:
        maps.append("0:a?")
    opts += ["-c", "copy"]
    # Bitstream filter (no re-encode): remuxing MP4/MOV into MPEG-TS requires
    # H.264/HEVC to be re-stamped into Annex-B form. The source codec comes
    # from the probe when one exists, else from the plan.
    if metadata is not None and metadata.video_codec:
        codec = _FFPROBE_CODEC.get(metadata.video_codec, metadata.video_codec)
    else:
        codec = edit.output.video_codec
    bsf = bsf_for_copy(_container_from_path(in_path) or "", edit.output.container, codec)
    defaults: list[str] = []
    if bsf:
        opts += ["-bsf:v", bsf]
        defaults.append(
            f"remuxing to MPEG-TS needs bitstream filter '{bsf}' — the compressed "
            "stream is re-stamped to Annex-B without re-encoding"
        )
    # Audio direction: AAC in MPEG-TS is ADTS-framed; MP4/MOV need the ADTS
    # header stripped. The codec name only comes from the probe.
    if not audio_remove and metadata is not None and metadata.audio_codec:
        absf = audio_bsf_for_copy(
            _container_from_path(in_path) or "",
            edit.output.container,
            metadata.audio_codec,
        )
        if absf:
            opts += ["-bsf:a", absf]
            defaults.append(
                f"remuxing AAC to MP4/MOV needs bitstream filter '{absf}' — the "
                "ADTS header is stripped from the compressed stream without re-encoding"
            )
    return ExecutionPlan(
        mode="copy",
        inputs=[_main_input(in_path)],
        vf=None,
        filter_complex=None,
        maps=maps,
        output_opts=opts,
        output_path=out_path,
        defaults=defaults,
    )


def _build_reencode_plan(edit: EditPlan, in_path: str, wm_path: str | None,
                         audio_path: str | None,
                         out_path: str, defaults: list[str], warnings: list[str]) -> ExecutionPlan:
    inputs = [_main_input(in_path)]
    if edit.trim is not None:
        # -ss as an input option: frame-accurate when followed by decoding.
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    # Input order is fixed: main=0, watermark=1 (build_video_chain hardcodes
    # [1:v]), replacement audio last.
    if wm_path:
        inputs.append(InputSpec(wm_path))
    if audio_path:
        inputs.append(InputSpec(audio_path))
    audio_idx = len(inputs) - 1 if audio_path else None

    chain_mode, chain, vlabel = build_video_chain(edit.operations, defaults)
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
    elif not audio_remove:
        maps.append("0:a?")

    # Speed change: video timestamps are scaled in the filter chain
    # (setpts, built above); the audio side is an atempo chain on -af.
    # -af is inert when the output has no audio stream, so it is only
    # emitted when audio survives.
    speed_ops = edit.ops_of("speed")
    speed_op = speed_ops[0] if speed_ops else None
    af = _atempo_chain(speed_op) if speed_op is not None and not audio_remove else None

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
    opts += ["-c:v", vc["encoder"]]
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
    else:
        preset = vc.get("preset", {})
        if "crf" in preset:
            opts += ["-crf", str(preset["crf"])]
        if "bitrate" in preset:
            opts += ["-b:v", preset["bitrate"]]
    if edit.output.video_profile is not None:
        opts += ["-profile:v", edit.output.video_profile]
        defaults.append(f"encoder profile {edit.output.video_profile} (maximum-compatibility setting)")
    if edit.output.video_preset is not None:
        opts += ["-preset", edit.output.video_preset]
        defaults.append(f"encoder preset {edit.output.video_preset}")
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
        opts += ["-c:a", ac["encoder"], "-b:a", f"{ac['bitrate_kbps']}k", "-shortest"]
        defaults.append(
            f"audio replaced with the first audio stream of '{audio_path}'; "
            "-shortest: output ends when the shorter of video and replacement audio ends"
        )
    elif audio_remove:
        opts.append("-an")
    else:
        ac = REGISTRY["audio_codecs"][audio_for(edit.output.container)]
        opts += ["-c:a", ac["encoder"], "-b:a", f"{ac['bitrate_kbps']}k"]
        # Audio resampler targets (-ar / -ac); inert when the output has no
        # audio stream.
        for ro in edit.ops_of("audio_resample"):
            if ro.sample_rate is not None:
                opts += ["-ar", str(ro.sample_rate)]
                defaults.append(f"audio sample rate {ro.sample_rate} Hz")
            if ro.channels is not None:
                opts += ["-ac", str(ro.channels)]
                defaults.append(f"audio channels {ro.channels}")

    opts += REGISTRY["matrix"][edit.output.container].get("flags", [])

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
