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
from fass.registry import REGISTRY, capability_check, audio_for, extension_for
from fass.schema import AssetKind, AudioMode, EditPlan, ResizeOp, WatermarkOp

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
    mode: str  # "copy" (remux) | "reencode"
    inputs: list[InputSpec]
    vf: str | None  # simple -vf chain (no watermark)
    filter_complex: str | None  # labeled graph (watermark present)
    maps: list[str]
    output_opts: list[str]
    output_path: str
    defaults: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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

    input_asset = assets.get(edit.input_id)
    if input_asset is None:
        errors.append(f"asset id '{edit.input_id}' is not in the asset map")
    elif input_asset.kind != AssetKind.VIDEO:
        errors.append(f"asset '{edit.input_id}' is not a video")

    wm_assets: list = []
    for o in edit.ops_of("watermark"):
        a = assets.get(o.asset_id)
        if a is None:
            errors.append(f"watermark asset id '{o.asset_id}' is not in the asset map")
        elif a.kind != AssetKind.IMAGE:
            errors.append(f"watermark asset '{o.asset_id}' is not an image")
        else:
            wm_assets.append(a)

    if errors:
        raise PlanError(errors)

    in_path = input_asset.path
    wm_path = wm_assets[0].path if wm_assets else None

    defaults: list[str] = []
    warnings: list[str] = []

    # -- output path -------------------------------------------------------
    explicit_output = bool(output_filename)
    if explicit_output:
        out_path = output_filename
    else:
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
            else:
                defaults.append(
                    "no media metadata provided — re-encoding to guarantee the requested "
                    "codec/container (supply probe metadata to allow a lossless remux when compatible)"
                )
    else:
        mode = "reencode"

    ep = _build_copy_plan(edit, in_path, out_path) if mode == "copy" else _build_reencode_plan(
        edit, in_path, wm_path, out_path, defaults, warnings
    )
    ep.defaults.extend(defaults)
    ep.warnings.extend(warnings)
    _metadata_warnings(ep, edit, metadata)
    return ep


def _build_copy_plan(edit: EditPlan, in_path: str, out_path: str) -> ExecutionPlan:
    audio_remove = edit.output.audio_mode == AudioMode.REMOVE
    maps = ["0:v:0"]
    opts: list[str] = []
    if audio_remove:
        opts.append("-an")
    if not audio_remove:
        maps.append("0:a?")
    opts += ["-c", "copy"]
    return ExecutionPlan(
        mode="copy",
        inputs=[InputSpec(in_path)],
        vf=None,
        filter_complex=None,
        maps=maps,
        output_opts=opts,
        output_path=out_path,
    )


def _build_reencode_plan(edit: EditPlan, in_path: str, wm_path: str | None,
                         out_path: str, defaults: list[str], warnings: list[str]) -> ExecutionPlan:
    inputs = [InputSpec(in_path)]
    if edit.trim is not None:
        # -ss as an input option: frame-accurate when followed by decoding.
        inputs[0].pre_opts += ["-ss", fmt_num(edit.trim.start_seconds)]
    if wm_path:
        inputs.append(InputSpec(wm_path))

    chain_mode, chain, vlabel = build_video_chain(edit.operations, defaults)
    if chain_mode == "complex":
        vf, fcomplex = None, chain
        maps = [f"[{vlabel}]"]
    else:
        vf, fcomplex = (chain or None), None
        maps = ["0:v:0"]

    audio_remove = edit.output.audio_mode == AudioMode.REMOVE
    if not audio_remove:
        maps.append("0:a?")

    opts: list[str] = []
    if edit.trim is not None and edit.trim.is_bounded:
        opts += ["-t", fmt_num(edit.trim.duration_seconds)]

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
    opts += ["-pix_fmt", vc["pix_fmt"]]

    if audio_remove:
        opts.append("-an")
    else:
        ac = REGISTRY["audio_codecs"][audio_for(edit.output.container)]
        opts += ["-c:a", ac["encoder"], "-b:a", f"{ac['bitrate_kbps']}k"]

    opts += REGISTRY["matrix"][edit.output.container].get("flags", [])

    return ExecutionPlan(
        mode="reencode",
        inputs=inputs,
        vf=vf,
        filter_complex=fcomplex,
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
