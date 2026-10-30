"""Versioned structured editing-plan schema (PRD FR-2).

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
    audio_encoder_name,
    audio_for,
    bsf_errors,
    capability_errors,
    codec_option_errors,
    comparison_filters,
    combiner_domain,
    combiner_filters,
    filter_expr_problems,
    filter_options_problems,
    filtergraph_problems,
    format_option_errors,
    input_schemes,
    is_source_spec,
    output_schemes,
    pixel_format_error,
    stream_output_info,
    stream_scheme,
    subtitle_dump_codec,
    video_encoder_name,
    video_profiles_for,
)

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- audio

class AudioMode(str, Enum):
    """What to do with the audio track: re-encode it (default), copy it
    as-is (``-c:a copy`` — the input's codec and bitrate pass through
    untouched, so the container must be able to carry it), or remove it."""

    ENCODE = "encode"
    COPY = "copy"
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
    stream_copy: bool = Field(
        default=False,
        description="Stream-copy cut (no re-encode): -ss/-t with forced "
                    "-c copy. Video starts at the first keyframe at/after "
                    "start_seconds (keyframe-accurate), audio is "
                    "sample-accurate. Default False = frame-accurate "
                    "re-encode (PRD FR-4). FFmpeg has no 'trim' "
                    "bitstream filter — this is how a 'trim' request is "
                    "served in a copy context.",
    )

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


class BitstreamFilterSpec(BaseModel):
    """A user-requested bitstream filter applied to the COMPRESSED stream
    without re-encoding (registry 'bitstream_filters.specs' table).

    Applying a bitstream filter forces stream copy for that stream's codec;
    the filter must match the probed stream codec (planner-checked).
    Diagnostic filters (trace_headers, showinfo) only write to the log.
    'trim' is a plan-level pseudo-entry meaning a stream-copy cut — it
    switches TrimSpec to copy mode and is normalized away at validation.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid(self) -> "BitstreamFilterSpec":
        for err in bsf_errors(self.name, self.options):
            raise ValueError(err)
        return self

# ---------------------------------------------------------------- output

class VariantSpec(BaseModel):
    """One level of an ABR ladder (M13.3): a resolution + video bitrate,
    with optional per-level audio bitrate / H.264 profile / level.

    The planner resolves unset fields against the default ladder table for
    the given height, computes the width from the input aspect ratio (kept
    even), and caps every level at the input height (it never upscales).
    """

    model_config = ConfigDict(extra="forbid")

    width: int | None = Field(
        default=None, ge=16, le=8192,
        description="Level width in pixels; unset → derived from the input "
                    "aspect ratio at the given height (kept even).",
    )
    height: int | None = Field(
        default=None, ge=16, le=8192,
        description="Level height in pixels (e.g. 360 for '360p'); unset → "
                    "derived from the input aspect ratio at the given width.",
    )
    video_bitrate_kbps: int | None = Field(
        default=None, ge=100, le=200000,
        description="Level video bitrate in kbps; emitted as -b:v/-minrate/"
                    "-maxrate with -bufsize 2x (CBR-style, the ABR form); "
                    "unset → the default table's row for the height.",
    )
    audio_bitrate_kbps: int | None = Field(
        default=None, ge=32, le=3840,
        description="Level audio bitrate in kbps (e.g. 64); unset → the "
                    "default table's row for the height.",
    )
    video_profile: Literal["baseline", "main", "high"] | None = Field(
        default=None,
        description="H.264 profile for this level (baseline/main/high); "
                    "unset → the default table's row for the height.",
    )
    video_level: str | None = Field(
        default=None, max_length=8,
        description="H.264 level for this level, e.g. '3.0' or '31'; unset "
                    "→ the default table's row for the height.",
    )

    @model_validator(mode="after")
    def _needs_a_dimension(self) -> "VariantSpec":
        if self.width is None and self.height is None:
            raise ValueError(
                "a ladder level needs a width and/or a height "
                "(e.g. 360p, or 640x360)"
            )
        return self


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["video", "audio", "subtitles"] = Field(
        default="video",
        description="Output mode: 'video' (video + audio), 'audio' (audio "
                    "track only, e.g. extracting audio to mp3/wav/m4a) or "
                    "'subtitles' (dump the input's subtitle track to a "
                    ".srt/.vtt/.ass file).",
    )
    container: Literal[
        "mp4", "mov", "mkv", "webm", "mpegts", "flv", "rtsp", "srt", "hls",
        "dash", "tee",
        "avi", "mxf", "vob", "asf", "3gp", "dv", "gif", "webp", "ogv",
        "mp3", "mp2", "m4a", "aac", "ogg", "flac", "wav", "aiff", "mka",
        "opus", "srt_file", "vtt_file", "ass_file",
    ]
    video_codec: Literal[
        "copy", "h264", "h265", "av1", "av1_svt", "vp9", "vp8", "theora",
        "vvc", "prores", "mpeg2", "mpeg4", "mjpeg", "dv", "gif", "webp",
    ] | None = Field(
        default=None,
        description="Video codec to encode with, or 'copy' — stream-copy the "
                    "input's video as-is (-c:v copy): the input's codec and "
                    "bitrate pass through untouched, so the container must "
                    "carry the input's codec and no video setting (bitrate, "
                    "profile, preset, pixel format, GPU/QSV, codec options, "
                    "ABR ladder, video filters) may be requested.",
    )
    audio_codec: Literal[
        "aac", "fdk_aac", "opus", "mp2", "mp3", "ac3", "eac3", "alac",
        "flac", "vorbis", "pcm_s16le", "pcm_s24le", "pcm_f32le",
    ] | None = None
    audio_mode: AudioMode = AudioMode.ENCODE
    video_bitrate_kbps: int | None = Field(
        default=None, ge=100, le=200000,
        description="Target video bitrate in kbps (e.g. 10000 for 10 Mbps). "
                    "Overrides the quality preset with CBR-style flags "
                    "(-b:v/-maxrate at target, -bufsize 2x).",
    )
    audio_bitrate_kbps: int | None = Field(
        default=None, ge=32, le=3840,
        description="Target audio bitrate in kbps (e.g. 128 for 'mp2 128k'). "
                    "Overrides the codec preset's -b:a; lossless/PCM codecs "
                    "take no bitrate and a requested one is noted.",
    )
    pixel_format: Literal[
        "yuv420p", "yuv420p10le", "yuv444p", "yuvj420p",
        "yuv422p10le", "yuv444p10le", "rgb24", "yuva420p",
    ] | None = Field(
        default=None,
        description="Output pixel format (the scaling/conversion component). "
                    "None keeps the codec's default. Allowed values depend "
                    "on the codec — see the registry matrix.",
    )
    video_profile: Literal[
        "baseline", "main", "high",
        "proxy", "lt", "standard", "hq", "4444", "4444xq",
    ] | None = Field(
        default=None,
        description="Encoder profile: H.264 'baseline' for maximum "
                    "compatibility; ProRes family (proxy..4444xq).",
    )
    video_preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    ] | None = Field(
        default=None,
        description="x264 speed/size preset (H.264 only).",
    )
    gpu: bool = Field(
        default=False,
        description="Encode on an NVIDIA GPU (M6): h264 -> h264_nvenc, "
                    "h265 -> hevc_nvenc. All other codecs are CPU-only; "
                    "x264 speed presets and the 'baseline' profile do not "
                    "apply.",
    )
    qsv: bool = Field(
        default=False,
        description="Encode on Intel Quick Sync (M8): h264 -> h264_qsv, "
                    "h265 -> hevc_qsv (profiles baseline/main/high; "
                    "hevc main/main10). Mutually exclusive with gpu. The "
                    "local FFmpeg build has QSV; the docker build does "
                    "not (preflight reports it).",
    )
    video_codec_options: dict[str, str] = Field(
        default_factory=dict,
        description="Private codec options for the video encoder "
                    "(registry codec_options table for the selected "
                    "encoder), e.g. {'crf': '18'} for libx264, "
                    "{'preset': 'hq', 'tune': 'll'} for h264_nvenc, "
                    "{'profile': '4444', 'quant_mat': 'hq'} for "
                    "prores_ks, {'x265-params': 'rdoq=2'} for libx265. "
                    "Emitted as -<key> <value> right after -c:v. Do not "
                    "put 'preset'/'profile' here when video_preset / "
                    "video_profile are set, and not a constant-quality "
                    "option (crf/cq/qp, rc=constqp) together with a "
                    "target video bitrate.",
    )
    audio_codec_options: dict[str, str] = Field(
        default_factory=dict,
        description="Private codec options for the audio encoder "
                    "(registry codec_options table for the selected "
                    "encoder), e.g. {'dialnorm': '-27'} for ac3/eac3, "
                    "{'ch_mode': 'mid_side'} for flac, "
                    "{'vbr': 'constrained', 'fec': '1'} for libopus, "
                    "{'vbr': '4'} for libfdk_aac. Emitted as "
                    "-<key> <value> right after -c:a.",
    )
    format_options: dict[str, str] = Field(
        default_factory=dict,
        description="Output container/format options from the registry "
                    "'format_options' table, e.g. {'hls_time': '6', "
                    "'movflags': 'faststart', 'mpegts_flags': "
                    "'resend_headers'} — emitted as -<key> <value> before "
                    "the output path. Keys and values are validated against "
                    "the table. For the tee container (M13.5) the keys route "
                    "into their branch (hls_* / tee keys into the [f=hls] "
                    "branch, dash keys into [f=dash]) and ride inside the "
                    "bracket template instead of as separate flags.",
    )
    variants: list[VariantSpec] | None = Field(
        default=None, max_length=8,
        description="ABR ladder levels (M13.3): when set, the output is an "
                    "adaptive-bitrate ladder — one encode per level, an HLS "
                    "variant playlist per level (stream_%v.m3u8) or a single "
                    "DASH .mpd with auto adaptation sets. An empty list "
                    "means 'ladder requested, fill the default table'; a "
                    "filled list is the user's explicit levels. HLS/DASH "
                    "containers only; always a re-encode.",
    )

    @model_validator(mode="after")
    def _variants_consistency(self) -> "OutputSpec":
        if self.variants is None:
            return self
        if self.container not in ("hls", "dash"):
            raise ValueError(
                "an ABR ladder (variants) only makes sense for segmented "
                f"outputs — the container must be 'hls' or 'dash', not "
                f"'{self.container}'"
            )
        if self.mode != "video":
            raise ValueError(
                "an ABR ladder (variants) is a video feature — an "
                "audio-only or subtitle output has no video levels"
            )
        if self.video_codec is not None and self.video_codec not in (
            "h264", "h265", "copy",
        ):
            raise ValueError(
                "an ABR ladder re-encodes video with per-level profile/"
                f"level flags — only an h264 or h265 ladder is supported, "
                f"not {self.video_codec}"
            )
        # 'copy' + ladder is a planner error (a ladder re-encodes every
        # level) — kept out of the schema so it surfaces as a plan error.
        if self.gpu or self.qsv:
            raise ValueError(
                "an ABR ladder is a CPU encode — clear gpu/qsv; per-level "
                "NVENC/QSV ladders are not supported"
            )
        if self.pixel_format is not None:
            raise ValueError(
                "an ABR ladder sets the pixel format per encoder; clear "
                "pixel_format and let the ladder use the codec default"
            )
        if self.variants:
            if self.video_bitrate_kbps is not None:
                raise ValueError(
                    "an ABR ladder sets a bitrate per level — clear the "
                    "top-level video bitrate (the levels carry their own)"
                )
            if self.audio_bitrate_kbps is not None:
                raise ValueError(
                    "an ABR ladder sets an audio bitrate per level — clear "
                    "the top-level audio bitrate (the levels carry their "
                    "own)"
                )
            if any(
                v.video_profile is not None
                and self.video_profile is not None
                and v.video_profile != self.video_profile
                for v in self.variants
            ):
                raise ValueError(
                    "a per-level video_profile conflicts with a top-level "
                    "video_profile — set the profile only per level or "
                    "only top-level"
                )
        return self

    @model_validator(mode="after")
    def _mode_consistency(self) -> "OutputSpec":
        out = self
        if out.gpu and out.qsv:
            raise ValueError(
                "gpu and qsv are mutually exclusive — pick one hardware path"
            )
        video_only_fields = (
            ("video_bitrate_kbps", out.video_bitrate_kbps),
            ("pixel_format", out.pixel_format),
            ("video_profile", out.video_profile),
            ("video_preset", out.video_preset),
            ("gpu", out.gpu),
            ("qsv", out.qsv),
            ("video_codec_options", out.video_codec_options or None),
        )
        if out.mode == "video":
            if out.video_codec is None:
                raise ValueError("a video output needs a video_codec")
        elif out.mode == "audio":
            if out.video_codec is not None:
                raise ValueError("an audio-only output has no video_codec")
            if out.audio_mode is not AudioMode.ENCODE:
                raise ValueError(
                    "an audio-only output must keep (encode) its audio; "
                    "'copy' leaves no audio work for the pipeline to do "
                    "and 'remove' would produce an empty file"
                )
            for name, val in video_only_fields:
                if val not in (None, False):
                    raise ValueError(
                        f"{name} does not apply to an audio-only output"
                    )
        else:  # subtitles
            if subtitle_dump_codec(out.container) is None:
                raise ValueError(
                    "a subtitle dump output needs a subtitle file container "
                    "(srt_file, vtt_file or ass_file)"
                )
            if out.video_codec is not None or out.audio_codec is not None:
                raise ValueError(
                    "a subtitle dump output has no video or audio codec"
                )
            for name, val in video_only_fields:
                if val not in (None, False):
                    raise ValueError(
                        f"{name} does not apply to a subtitle dump output"
                    )
            if out.audio_codec_options:
                raise ValueError(
                    "audio_codec_options does not apply to a subtitle dump "
                    "output"
                )
        return self

    @model_validator(mode="after")
    def _capability(self) -> "OutputSpec":
        for err in capability_errors(self):
            raise ValueError(err)
        # 'copy' names no encoder — the codec-specific tables below do not
        # apply; the planner reports "copying the video cannot ..." for
        # every video setting requested alongside a copy.
        codec_specific = self.video_codec not in (None, "copy")
        if self.mode == "video" and self.video_profile is not None \
                and codec_specific:
            allowed = video_profiles_for(self.video_codec)
            if self.video_profile not in allowed:
                raise ValueError(
                    f"video_profile '{self.video_profile}' does not apply to "
                    f"{self.video_codec.upper()} "
                    f"(allowed: {', '.join(allowed) or 'none'})"
                )
        if self.mode == "video" and self.pixel_format is not None \
                and codec_specific:
            pf_err = pixel_format_error(self.video_codec, self.pixel_format)
            if pf_err:
                raise ValueError(pf_err)
        if self.video_codec_options and self.mode == "video" \
                and codec_specific:
            enc = video_encoder_name(self.video_codec, self.gpu, self.qsv)
            for err in codec_option_errors(enc, self.video_codec_options):
                raise ValueError(err)
            if self.video_preset and "preset" in self.video_codec_options:
                raise ValueError(
                    "video_preset and the 'preset' codec option are both "
                    "set — use video_preset for x264 speed presets, and "
                    "the 'preset' codec option only for the selected "
                    "encoder's own presets (nvenc, QSV, prores …)"
                )
            if self.video_profile and "profile" in self.video_codec_options:
                raise ValueError(
                    "video_profile and the 'profile' codec option are both "
                    "set — use video_profile for baseline/main/high (and "
                    "ProRes profiles), the 'profile' codec option for "
                    "extended values (high444p, main10 …)"
                )
            # A target bitrate conflicts with constant-quality rate control
            # (crf/cq/qp, rc=constqp) — but not with cbr/vbr modes, which
            # take the bitrate as their rate.
            rc_val = self.video_codec_options.get("rc")
            if self.video_bitrate_kbps is not None and (
                {"crf", "cq", "qp"} & self.video_codec_options.keys()
                or rc_val == "constqp"
            ):
                raise ValueError(
                    "a target video bitrate and a constant-quality codec "
                    "option (crf/cq/qp, or rc=constqp) conflict — pick one"
                )
            if self.gpu and self.video_bitrate_kbps is None:
                if rc_val is not None and rc_val.startswith("cbr") \
                        and "cq" not in self.video_codec_options:
                    raise ValueError(
                        "rc=cbr* on the GPU needs a target video bitrate "
                        "(video_bitrate_kbps) — cbr has no quality fallback"
                    )
        if self.audio_codec_options:
            # with no explicit audio codec, the container default applies
            # (e.g. aac in mp4) — its options must validate too
            acodec = self.audio_codec or audio_for(self.container)
            if acodec is None:
                # a container that carries no audio (e.g. an animated GIF)
                # has no audio codec for options to apply to
                raise ValueError(
                    f"audio codec options need an audio codec, but a "
                    f"{self.container} file carries no audio")
            enc = audio_encoder_name(acodec)
            for err in codec_option_errors(enc, self.audio_codec_options):
                raise ValueError(err)
        for err in format_option_errors(self.container, self.format_options):
            raise ValueError(err)
        return self

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


class AudioAddOp(BaseModel):
    """Keep the output's own audio track and add the first audio stream of
    another input (a video or audio file bound in the asset map) as an
    additional, selectable audio track (e.g. a second language from a
    second file). The track is muxed in: stream-copied in a remux plan,
    encoded with the output's audio settings in a re-encode plan. The
    output ends when the main input ends."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_add"] = "audio_add"
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


class FilterOp(BaseModel):
    """Apply FFmpeg filter(s) by expression, from the registry 'filters'
    allowlist: one filter instance ('loudnorm') or a comma-joined chain of
    same-domain filters ('eq=contrast=1.2, fade=t=out:st=9:d=1.5').

    domain 'video' appends to the -vf chain (video outputs only);
    domain 'audio' appends to the -af chain. A full labeled graph (multiple
    outputs, mixed domains) goes to EditPlan.filtergraph instead — it
    cannot be combined with a watermark or burned subtitles, which own the
    filter graph."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["filter"] = "filter"
    domain: Literal["video", "audio"] = "video"
    expr: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _valid_expr(self) -> "FilterOp":
        for err in filter_expr_problems(self.expr):
            raise ValueError(f"filter expression rejected: {err}")
        return self


class CombinerOp(BaseModel):
    """Join the main input with additional inputs into one output using a
    multi-input filter (the registry 'multi_input' table): xfade, acrossfade,
    concat, hstack, vstack, overlay (video) or amix, amerge, join (audio).

    ``asset_ids`` are the extra inputs, in order — the main input stays
    first (input pad 0). The planner probes each extra input, builds a
    labeled -filter_complex graph and maps its outputs explicitly. At most
    one combiner per plan; it cannot be combined with a watermark, replaced
    audio, subtitles, a trim or a custom filtergraph (planner)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["combine"] = "combine"
    filter: str = Field(min_length=1, max_length=64)
    asset_ids: list[str] = Field(min_length=1, max_length=8)
    options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_combiner(self) -> "CombinerOp":
        if combiner_domain(self.filter) is None:
            known = ", ".join(sorted(combiner_filters()))
            raise ValueError(
                f"'{self.filter}' is not a multi-input filter "
                f"(supported: {known})")
        for err in filter_options_problems(self.filter, self.options):
            raise ValueError(f"combiner options rejected: {err}")
        return self


class CompareOp(BaseModel):
    """Measure the quality difference between a reference clip and a
    distorted clip with a comparison metric (the registry
    'needs_reference' table): psnr, ssim or libvmaf.

    Convention (the user decision of 2026-10-28): the MAIN asset is the
    REFERENCE — the source in the input folder — and the DISTORTED clip
    is the transcoded file in the output folder. ``distorted`` is the
    asset id of that file (the rules adapter injects it after scanning
    the output folder, or the application binds it explicitly); when it
    is set, ``reference`` is the main asset's id. When ``distorted`` is
    absent the op keeps the M10.4 explicit-reference shape: ``reference``
    is the asset id of a second video asset and the main input is the
    distorted clip.

    The command line follows the Netflix vmaf documentation
    (resource/doc/ffmpeg.md) in both shapes: the reference is probed and
    becomes the FIRST input ('-i reference -i distorted'), the distorted
    clip the second; the planner builds a labeled -filter_complex graph
    whose metric pads take '[distorted][reference]' —
    '[1:v]setpts=PTS-STARTPTS[c1]; [0:v]setpts=PTS-STARTPTS[c0];
    [c1][c0]psnr[cmp]'-style, the distorted pad feeding the metric first
    ('setpts=PTS-STARTPTS' only on pads whose probe says the stream
    carries packet pts). It runs against the null muxer: nothing is
    written to a file, the metric score lands in the run log (M10.4,
    diagnostic pattern). At most one comparison per plan; it is a
    standalone analysis — it cannot be combined with a watermark,
    replaced audio, subtitles, a trim or a custom filtergraph (planner)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["compare"] = "compare"
    metric: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1, max_length=64)
    distorted: str | None = Field(default=None, min_length=1, max_length=64)
    options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_metric(self) -> "CompareOp":
        if self.metric not in comparison_filters():
            known = ", ".join(sorted(comparison_filters()))
            raise ValueError(
                f"'{self.metric}' is not a comparison metric "
                f"(supported: {known})")
        for err in filter_options_problems(self.metric, self.options):
            raise ValueError(f"comparison options rejected: {err}")
        return self


class StabilizeOp(BaseModel):
    """Stabilize shaky handheld footage with the two-pass vidstab
    transform (registry filters vidstabdetect + vidstabtransform).

    The planner builds a two-pass execution: pass 1 analyzes the camera
    motion (vidstabdetect, stream discarded through the null muxer) and
    writes a transform side-file; pass 2 re-encodes the clip with
    vidstabtransform reading that side-file. The side-file name is derived
    from the output filename (a bare name, written next to the output).
    M10.5 takes the filters' built-in defaults; per-filter options
    (shakiness, smoothing, ...) are not exposed yet."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["stabilize"] = "stabilize"


class ToneMapOp(BaseModel):
    """Tone-map HDR video (HDR10/PQ, HLG, BT.2020) down to SDR (BT.709, 8-bit).

    The planner builds the fixed tone-mapping chain
    (``format=gbrpf32le,tonemap=tonemap=hable:desat=0,format=yuv420p`` —
    registry filter ``tonemap``) and lands the output on the codec's 8-bit
    pixel format; the tone-mapping curve parameters are the filter's
    built-in defaults (M12). The op is a video-chain edit (like grayscale):
    it composes with resize/crop/rotate/speed/watermark/subtitles in
    user-specified order, and it forces a video re-encode."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tonemap"] = "tonemap"


Operation = Annotated[
    Union[
        ResizeOp, GrayscaleOp, WatermarkOp, AudioReplaceOp, AudioAddOp,
        SubtitleOp, CropOp, SpeedOp, RotateOp, AudioResampleOp, FilterOp,
        CombinerOp, CompareOp, StabilizeOp, ToneMapOp,
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
    realtime_input: bool = Field(
        default=False,
        description="Pace the main input's read at native frame rate "
                    "(-re, 'pseudo-live' / 'read at real time'): the file "
                    "is consumed in real time instead of as fast as "
                    "ffmpeg can, so e.g. a live (event) HLS playlist is "
                    "followed by a player while it is still being "
                    "generated. The main input only — auxiliary inputs "
                    "(watermark, replacement audio) are never paced. "
                    "Has no effect on a stream input that is already "
                    "real time.",
    )
    bitstream_filters: list[BitstreamFilterSpec] | None = Field(
        default=None, max_length=8,
        description="Bitstream filters to apply to the compressed stream "
                    "without re-encoding (registry "
                    "'bitstream_filters.specs' table: name + key=value "
                    "options). Forces stream copy for the filtered stream; "
                    "the filter must match the probed stream codec. "
                    "'trim' means a stream-copy cut (see TrimSpec.copy).",
    )
    operations: list[Operation] = Field(default_factory=list, max_length=8)
    output_destination: str | None = Field(
        default=None, max_length=1024,
        description="Stream destination for the output (M3b). Only stream "
                    "schemes are allowed (rtmp://, rtmps://, rtsp://, rtsps://"
                    ", srt://, udp://, rtp://, tcp://, http://, https://); "
                    "the scheme fixes the output container (see the "
                    "registry — http/https derive it from the URL path "
                    "extension, defaulting to mpegts).",
    )
    filtergraph: str | None = Field(
        default=None, max_length=4096,
        description="A full FFmpeg filter graph (filter_complex body): "
                    "labeled chains, e.g. '[0:v]split=2[main][wm];[wm]"
                    "boxblur=2:2[wm2]'. Every filter must be in the "
                    "registry allowlist. Cannot be combined with a watermark "
                    "or burned subtitles.",
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
    def _at_most_one_single_use_op(self) -> "EditPlan":
        # watermarks are exempt (M12.WM): several image overlays may chain
        # in the plan's operation order — the planner binds them to inputs
        # 1..N in that order.
        for op_type in ("audio_replace", "audio_add", "subtitles", "crop",
                        "speed", "rotate", "audio_resample", "combine",
                        "compare", "stabilize", "tonemap"):
            if sum(1 for o in self.operations if o.type == op_type) > 1:
                raise ValueError(f"the MVP supports at most one {op_type} operation")
        return self

    @model_validator(mode="after")
    def _trim_bsf_means_copy(self) -> "EditPlan":
        # 'trim' is not an FFmpeg bitstream filter (it is a decode-time
        # video filter): in the plan it stands for a stream-copy cut, so
        # it switches the trim to copy mode and is dropped from the list.
        if not self.bitstream_filters or not any(
                b.name == "trim" for b in self.bitstream_filters):
            return self
        if self.trim is None:
            raise ValueError(
                "a 'trim' bitstream filter (stream-copy cut) needs a trim "
                "start point — 'trim' by itself keeps nothing cut"
            )
        self.trim.stream_copy = True
        self.bitstream_filters = (
            [b for b in self.bitstream_filters if b.name != "trim"] or None
        )
        return self

    @model_validator(mode="after")
    def _filtergraph_consistent(self) -> "EditPlan":
        g = self.filtergraph
        if g is None:
            return self
        g = g.strip()
        if not g or any(c in g for c in "\x00\n\r\t"):
            raise ValueError("filtergraph must be a nonempty single-line graph")
        for err in filtergraph_problems(g):
            raise ValueError(f"filtergraph rejected: {err}")
        if any(o.type == "watermark" for o in self.operations) or any(
                o.type == "subtitles" and o.mode == "burn"
                for o in self.operations):
            raise ValueError(
                "a custom filtergraph cannot be combined with a watermark or "
                "burned subtitles — they own the filter graph; use one or the other"
            )
        if any(o.type == "combine" for o in self.operations):
            raise ValueError(
                "a custom filtergraph cannot be combined with a multi-input "
                "filter (combine) — both own the filter graph; use one or the other"
            )
        if any(o.type == "compare" for o in self.operations):
            raise ValueError(
                "a custom filtergraph cannot be combined with a comparison "
                "(compare) — the comparison owns the filter graph; use one "
                "or the other"
            )
        self.filtergraph = g
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
    """Envelope every NLU adapter returns."""

    model_config = ConfigDict(extra="forbid")

    status: ParseStatus
    plan: EditPlan | None = None
    clarifications: list[Clarification] = Field(default_factory=list)
    unsupported: list[UnsupportedPart] = Field(default_factory=list)
    defaults: list[str] = Field(default_factory=list)
    # The LLM adapter records the model's VERBATIM reply here, so the UI can
    # show what the model actually returned before any processing (JSON
    # extraction, schema validation, plan building). Deterministic adapters
    # have no raw text and leave it None.
    raw: str | None = None

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
