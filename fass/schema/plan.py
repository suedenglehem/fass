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


class AudioReplaceOp(BaseModel):
    """Replace the output audio with the first audio stream of another
    input (an audio file bound in the asset map). The output still runs
    with ``-shortest``: it ends when the shorter of the video and the
    replacement audio ends."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_replace"] = "audio_replace"
    asset_id: str = Field(min_length=1, max_length=64)


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
    Union[ResizeOp, GrayscaleOp, WatermarkOp, AudioReplaceOp],
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
        if sum(1 for o in self.operations if o.type == "watermark") > 1:
            raise ValueError("the MVP supports at most one watermark")
        if sum(1 for o in self.operations if o.type == "audio_replace") > 1:
            raise ValueError("the MVP supports at most one audio replacement")
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


_REMOTE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://|^//")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac",
    ".opus", ".aiff", ".aif", ".wma",
}


def guess_kind(path: str) -> AssetKind:
    import os

    # URLs may carry query/fragment suffixes — drop them before the
    # extension check ("http://x/live.mp4?token=…" is a video).
    bare = path.split("?", 1)[0].split("#", 1)[0]
    ext = os.path.splitext(bare)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return AssetKind.IMAGE
    if ext in AUDIO_EXTENSIONS:
        return AssetKind.AUDIO
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
        if _REMOTE.match(p):
            if stream_scheme(p) not in input_schemes():
                allowed = ", ".join(input_schemes())
                raise ValueError(
                    "asset URLs must use an allowlisted streaming scheme "
                    f"({allowed})"
                )
        return self


AssetMap = dict[str, Asset]
