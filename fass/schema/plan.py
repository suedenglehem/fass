"""Versioned structured editing-plan schema (PRD FR-2, plan0.md section 1).

The plan describes what the user *wants*, independent of FFmpeg syntax:
no raw flags, no filter expressions, no file paths. Assets are referenced
by identifier only; application code binds identifiers to local paths
through the asset map. The schema is strict (``extra="forbid"``) so model
or parser output can never smuggle in unknown fields.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fass.registry import (
    input_schemes,
    is_source_spec,
    output_schemes,
    stream_output_info,
    stream_scheme,
)

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- audio

class AudioMode(str, Enum):
    """Keep audio by encoding it (default) or remove it entirely."""

    ENCODE = "encode"
    REMOVE = "remove"

# ---------------------------------------------------------------- trim

class TrimSpec(BaseModel):
    """A kept interval of the timeline.

    Accepts either ``duration_seconds`` or ``end_seconds``; after
    normalization the two representations are mutually exclusive
    (PRD FR-2) and only ``duration_seconds`` survives.
    """

    model_config = ConfigDict(extra="forbid")

    start_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    end_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _normalize(self) -> "TrimSpec":
        if self.duration_seconds is not None and self.end_seconds is not None:
            raise ValueError("provide either duration_seconds or end_seconds, not both")
        if self.end_seconds is not None:
            if self.end_seconds <= self.start_seconds:
                raise ValueError("end_seconds must be after start_seconds")
            self.duration_seconds = self.end_seconds - self.start_seconds
            self.end_seconds = None
        return self

    @property
    def is_bounded(self) -> bool:
        return self.duration_seconds is not None

# ---------------------------------------------------------------- output

class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: Literal["mp4", "mov", "mkv", "webm", "mpegts", "flv", "rtsp", "srt"]
    video_codec: Literal["h264", "h265", "vp9"]
    audio_mode: AudioMode = AudioMode.ENCODE
    video_bitrate_kbps: int | None = Field(
        default=None, ge=100, le=200000,
        description="Target video bitrate in kbps (e.g. 10000 for 10 Mbps). "
                    "Overrides the quality preset with CBR-style flags "
                    "(-b:v/-maxrate at target, -bufsize 2x).",
    )
    pixel_format: Literal["yuv420p", "yuv420p10le", "yuv444p"] | None = Field(
        default=None,
        description="Output pixel format (the scaling/conversion component). "
                    "None keeps the codec's default (yuv420p). Allowed values "
                    "depend on the codec — see the registry matrix.",
    )
    video_profile: Literal["baseline", "main", "high"] | None = Field(
        default=None,
        description="Encoder profile (H.264 in the MVP): 'baseline' for "
                    "maximum compatibility.",
    )
    video_preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    ] | None = Field(
        default=None,
        description="x264 speed/size preset (H.264 in the MVP).",
    )

# ---------------------------------------------------------------- operations

class ResizeOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["resize"] = "resize"
    width: int | None = Field(default=None, ge=16, le=8192)
    height: int | None = Field(default=None, ge=16, le=8192)
    aspect_ratio: Literal["preserve", "exact"] = "preserve"

    @model_validator(mode="after")
    def _has_dimension(self) -> "ResizeOp":
        if self.width is None and self.height is None:
            raise ValueError("resize needs a width and/or height")
        return self


class GrayscaleOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["grayscale"] = "grayscale"


class CropOp(BaseModel):
    """Crop to exact output dimensions, centered (FFmpeg's crop default)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["crop"] = "crop"
    width: int = Field(ge=16, le=8192)
    height: int = Field(ge=16, le=8192)


class SpeedOp(BaseModel):
    """Change playback speed. factor > 1 is faster, < 1 is slower.

    The builder scales video timestamps (setpts) and audio tempo (atempo);
    a trim's source duration is divided by the factor in the planner."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["speed"] = "speed"
    factor: float = Field(ge=0.25, le=8)


class RotateOp(BaseModel):
    """Rotate by a quarter turn. 90/270 swap the output dimensions."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["rotate"] = "rotate"
    angle: Literal[90, 180, 270] = 90
    clockwise: bool = True


class AudioResampleOp(BaseModel):
    """Audio resampler: target sample rate and/or channel count."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_resample"] = "audio_resample"
    sample_rate: int | None = Field(default=None, ge=8000, le=192000)
    channels: int | None = Field(default=None, ge=1, le=6)

    @model_validator(mode="after")
    def _has_target(self) -> "AudioResampleOp":
        if self.sample_rate is None and self.channels is None:
            raise ValueError("audio_resample needs a sample_rate and/or channels")
        return self


class AudioReplaceOp(BaseModel):
    """Replace the output audio with the first audio stream of another
    input (an audio file bound in the asset map). The output still runs
    with ``-shortest``: it ends when the shorter of the video and the
    replacement audio ends."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_replace"] = "audio_replace"
    asset_id: str = Field(min_length=1, max_length=64)


class SubtitleOp(BaseModel):
    """Add subtitles from a bound subtitle file (srt / ass / ssa / vtt).

    ``mode="burn"`` hard-codes them into the video with the libass filters
    (``subtitles=`` / ``ass=``) — forces a video re-encode. ``mode="soft"``
    muxes them as a selectable track (mp4/mov → mov_text, mkv → srt) and is
    compatible with stream-copy remuxing. The planner validates both the
    container support and the asset kind; the builder emits the flags."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["subtitles"] = "subtitles"
    asset_id: str = Field(min_length=1, max_length=64)
    mode: Literal["burn", "soft"] = "burn"


class WatermarkOp(BaseModel):
    """One static image overlay. Applied with the rest of the video chain;
    the builder uses a labeled filter graph so the image (a single-frame
    input) never bounds the output duration."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["watermark"] = "watermark"
    asset_id: str = Field(min_length=1, max_length=64)
    position: Literal[
        "top_left", "top_right", "bottom_left", "bottom_right", "center"
    ] = "bottom_right"
    margin_px: int = Field(default=16, ge=0, le=1000)
    scale_percent: int = Field(default=100, ge=10, le=100)


Operation = Annotated[
    Union[
        ResizeOp, GrayscaleOp, WatermarkOp, AudioReplaceOp, SubtitleOp,
        CropOp, SpeedOp, RotateOp, AudioResampleOp,
    ],
    Field(discriminator="type"),
]

# ---------------------------------------------------------------- plan

class EditPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    input_id: str = Field(default="main", min_length=1, max_length=64)
    output: OutputSpec
    trim: TrimSpec | None = None
    operations: list[Operation] = Field(default_factory=list, max_length=8)
    output_destination: str | None = Field(
        default=None, max_length=1024,
        description="Stream destination for the output (M3b). Only stream "
                    "schemes are allowed (rtmp://, rtmps://, rtsp://, rtsps://"
                    ", srt://, udp://, rtp://, tcp://); the scheme fixes the "
                    "output container (see the registry).",
    )

    @model_validator(mode="after")
    def _destination_is_stream(self) -> "EditPlan":
        d = self.output_destination
        if d is None:
            return self
        d = d.strip()
        if not d or any(c in d for c in "\x00\n\r\t"):
            raise ValueError("output destination must be a nonempty stream URL")
        if stream_output_info(d) is None:
            allowed = ", ".join(s + "://" for s in output_schemes())
            raise ValueError(
                f"output destination must be a stream URL ({allowed}); "
                "plain filenames are set by the application, not the plan"
            )
        self.output_destination = d
        return self

    @model_validator(mode="after")
    def _at_most_one_watermark(self) -> "EditPlan":
        for op_type in ("watermark", "audio_replace", "subtitles", "crop",
                        "speed", "rotate", "audio_resample"):
            if sum(1 for o in self.operations if o.type == op_type) > 1:
                raise ValueError(f"the MVP supports at most one {op_type} operation")
        return self

    def ops_of(self, op_type: str) -> list:
        return [o for o in self.operations if o.type == op_type]

# ---------------------------------------------------------------- NLU envelope

class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3)
    hint: str | None = None


class UnsupportedPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fragment: str
    reason: str


class ParseStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"


class ParseResult(BaseModel):
    """Envelope every NLU adapter returns (plan0.md section 5)."""

    model_config = ConfigDict(extra="forbid")

    status: ParseStatus
    plan: EditPlan | None = None
    clarifications: list[Clarification] = Field(default_factory=list)
    unsupported: list[UnsupportedPart] = Field(default_factory=list)
    defaults: list[str] = Field(default_factory=list)

# ---------------------------------------------------------------- assets

class AssetKind(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    SUBTITLES = "subtitles"
    SOURCE = "source"  # a generic ffmpeg source spec (lavfi, v4l2, x11grab, …)


_REMOTE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://|^//")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac",
    ".opus", ".aiff", ".aif", ".wma",
}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}


def guess_kind(path: str) -> AssetKind:
    import os

    # A generic ffmpeg source spec ('lavfi:testsrc=…', 'v4l2:/dev/video0')
    # wins over the extension heuristic — it has no file semantics at all.
    if is_source_spec(path):
        return AssetKind.SOURCE
    # URLs may carry query/fragment suffixes — drop them before the
    # extension check ("http://x/live.mp4?token=…" is a video).
    bare = path.split("?", 1)[0].split("#", 1)[0]
    ext = os.path.splitext(bare)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return AssetKind.IMAGE
    if ext in AUDIO_EXTENSIONS:
        return AssetKind.AUDIO
    if ext in SUBTITLE_EXTENSIONS:
        return AssetKind.SUBTITLES
    return AssetKind.VIDEO


class Asset(BaseModel):
    """A media source bound by the application (never by the model): either
    a local file path or a URL with an allowlisted streaming scheme (M3b).
    fass never fetches or executes anything — the user's local ffmpeg reads
    the source when the rendered command runs."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    kind: AssetKind = AssetKind.VIDEO
    label: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _valid_source(self) -> "Asset":
        p = self.path.strip()
        if not p or any(c in p for c in "\x00\n\r\t"):
            raise ValueError("asset path must be a nonempty local path")
        if self.kind == AssetKind.SOURCE:
            if not is_source_spec(p):
                raise ValueError(
                    "a source asset must be an allowlisted ffmpeg source spec "
                    "(e.g. 'lavfi:testsrc=duration=5', 'v4l2:/dev/video0')"
                )
            return self
        if _REMOTE.match(p):
            if stream_scheme(p) not in input_schemes():
                allowed = ", ".join(input_schemes())
                raise ValueError(
                    "asset URLs must use an allowlisted streaming scheme "
                    f"({allowed})"
                )
        return self


AssetMap = dict[str, Asset]
