"""Plain-language summaries derived from the validated plan and the
builder's decisions (FR-6). Nothing is explained that was not actually
decided."""

from __future__ import annotations

from fass.planner import ExecutionPlan, fmt_num
from fass.probe.ffprobe import MediaInfo
from fass.registry import REGISTRY
from fass.schema import EditPlan

_POS_WORDS = {
    "top_left": "top-left",
    "top_right": "top-right",
    "bottom_left": "bottom-left",
    "bottom_right": "bottom-right",
    "center": "center",
}


def summarize(edit: EditPlan, ep: ExecutionPlan, media: MediaInfo | None = None) -> list[str]:
    lines: list[str] = []
    out = edit.output

    if ep.mode == "copy":
        lines.append("Remux (stream copy): no re-encoding — the input streams already match the target")
    else:
        vc = REGISTRY["video_codecs"][out.video_codec]
        crf = vc.get("preset", {}).get("crf")
        quality = f", CRF {crf}" if crf is not None else ""
        lines.append(f"Encode: {out.video_codec} ({vc['encoder']}{quality}) in {out.container.upper()}")

    trim = edit.trim
    if trim is not None:
        if trim.is_bounded:
            end = trim.start_seconds + trim.duration_seconds
            lines.append(
                f"Trim: keep {fmt_num(trim.start_seconds)}s to {fmt_num(end)}s "
                f"({fmt_num(trim.duration_seconds)}s of the video)"
            )
        else:
            lines.append(f"Trim: keep from {fmt_num(trim.start_seconds)}s to the end")
        lines.append("Trim re-encodes for frame-accurate cuts (MVP policy)")

    for op in edit.operations:
        if op.type == "resize":
            if op.height and not op.width:
                lines.append(
                    f"Resize: output height {op.height}px, width automatic — aspect ratio preserved "
                    "(no padding or cropping)"
                )
            elif op.width and not op.height:
                lines.append(
                    f"Resize: output width {op.width}px, height automatic — aspect ratio preserved"
                )
            else:
                how = "letterboxed, no stretching" if op.aspect_ratio == "preserve" else "stretched to exact size"
                lines.append(f"Resize: {op.width}x{op.height} ({how})")
        elif op.type == "grayscale":
            lines.append("Grayscale: video made black and white (audio unaffected)")
        elif op.type == "watermark":
            size = "original image size" if op.scale_percent == 100 else f"scaled to {op.scale_percent}%"
            lines.append(
                f"Watermark: image at {_POS_WORDS[op.position]}, {op.margin_px}px margin, {size}, "
                "shown throughout the output"
            )

    if out.audio_mode.value == "remove":
        lines.append("Audio: removed")
    elif ep.mode == "copy":
        lines.append("Audio: kept (copied)")
    else:
        ac = REGISTRY["audio_codecs"][REGISTRY["matrix"][out.container]["audio_codec"]]
        lines.append(f"Audio: kept, encoded as {ac['encoder'].upper()} {ac['bitrate_kbps']}k")

    if media is not None:
        lines.append(f"Input: {media.width}x{media.height}, {media.duration:g}s, {media.video_codec}"
                     + ("" if media.has_audio else ", no audio stream"))
    return lines
