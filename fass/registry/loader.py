"""Load and query the versioned capability registry (PRD FR-3)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

REG_PATH = Path(__file__).parent / "registry.json"

REGISTRY: dict = json.loads(REG_PATH.read_text(encoding="utf-8"))


class RegistryError(Exception):
    """Raised when a requested capability is outside the supported matrix."""


def load_registry() -> dict:
    return REGISTRY


def matrix_error(container: str, video_codec: str) -> str | None:
    """Return a human-readable error if container/codec is not a supported
    combination, else None."""
    row = REGISTRY["matrix"].get(container)
    if row is None:
        return f"container '{container}' is not in the supported container set"
    if video_codec not in row["video"]:
        allowed = ", ".join(row["video"]) or "audio/subtitles only"
        return (
            f"video codec '{video_codec}' is not supported in {container.upper()} "
            f"(supported: {allowed})"
        )
    return None


def matrix_row(container: str) -> dict | None:
    return REGISTRY["matrix"].get(container)


def video_codecs_for(container: str) -> list[str]:
    row = REGISTRY["matrix"].get(container)
    return list(row["video"]) if row else []


def audio_codecs_for(container: str) -> list[str]:
    row = REGISTRY["matrix"].get(container)
    return list(row["audio"]) if row else []


def audio_codec_for(container: str, audio_codec: str | None) -> str | None:
    """The output audio codec for a container: the explicit choice when the
    matrix allows it, else the container's default (None for video-only /
    subtitle containers)."""
    row = REGISTRY["matrix"].get(container)
    if row is None:
        return audio_codec
    allowed = row["audio"]
    if audio_codec is not None:
        return audio_codec if audio_codec in allowed else None
    return row["audio_codec"]


def audio_container_for_codec(audio_codec: str) -> str | None:
    """Natural file container for an audio codec with no explicit container
    (mp3 → mp3, aac → m4a, flac → flac, pcm → wav, …)."""
    return REGISTRY["audio_container_for_codec"].get(audio_codec)


def default_codec(container: str) -> str:
    return REGISTRY["matrix"][container]["default_video"]


def audio_for(container: str) -> str:
    return REGISTRY["matrix"][container]["audio_codec"]


def extension_for(container: str) -> str:
    return REGISTRY["containers"][container]["extensions"][0]


def capability_check(container: str, video_codec: str) -> list[str]:
    """Capability-level validation (PRD FR-5), video mode. Returns a list
    of errors."""
    return capability_errors(_SimpleSpec("video", container, video_codec))


class _SimpleSpec:
    """Duck-typed OutputSpec stand-in for capability_errors."""
    __slots__ = ("mode", "container", "video_codec", "audio_codec")

    def __init__(self, mode, container, video_codec, audio_codec=None):
        self.mode = mode
        self.container = container
        self.video_codec = video_codec
        self.audio_codec = audio_codec


def capability_errors(spec) -> list[str]:
    """Mode-aware capability validation (PRD FR-5) for an OutputSpec-shaped
    object (mode / container / video_codec / audio_codec). Returns a list of
    errors — empty when the combination is inside the supported matrix."""
    errors: list[str] = []
    row = REGISTRY["matrix"].get(spec.container)
    if row is None:
        errors.append(f"container '{spec.container}' is not supported")
        return errors
    if spec.mode == "subtitles":
        if spec.container not in REGISTRY["subtitles"]["dump_codecs"]:
            errors.append(
                f"subtitle dumping needs a subtitle file container "
                f"(srt, vtt or ass — '{spec.container}' is not one)"
            )
        if spec.video_codec is not None:
            errors.append("a subtitle dump output has no video codec")
        if spec.audio_codec is not None:
            errors.append("a subtitle dump output has no audio codec")
        return errors
    if spec.mode == "audio":
        if spec.video_codec is not None:
            errors.append("an audio-only output has no video codec")
        if spec.audio_codec is None:
            errors.append(
                f"audio-only output to {spec.container.upper()} needs an "
                f"audio codec (supported: {', '.join(row['audio']) or 'none'})"
            )
        elif spec.audio_codec not in row["audio"]:
            errors.append(
                f"audio codec '{spec.audio_codec}' is not supported in "
                f"{spec.container.upper()} (supported: {', '.join(row['audio'])})"
            )
        return errors
    # video mode
    if spec.video_codec is None:
        errors.append(
            f"video output to {spec.container.upper()} needs a video codec "
            f"(supported: {', '.join(row['video']) or 'none'})"
        )
    elif spec.video_codec == "copy":
        pass  # stream copy carries the INPUT's codec — the planner checks
        # the container carries it once the input is probed
    elif spec.video_codec not in row["video"]:
        errors.append(
            f"video codec '{spec.video_codec}' is not supported in "
            f"{spec.container.upper()} (supported: {', '.join(row['video'])})"
        )
    if spec.audio_codec is not None and spec.audio_codec not in row["audio"]:
        errors.append(
            f"audio codec '{spec.audio_codec}' is not supported in "
            f"{spec.container.upper()} (supported: {', '.join(row['audio']) or 'none'})"
        )
    return errors


def _streaming() -> dict:
    return REGISTRY["streaming"]


def input_schemes() -> list[str]:
    """URL schemes allowed on input assets (stream/remote sources, M3b)."""
    return list(_streaming()["input_schemes"])


def stream_scheme(url: str) -> str | None:
    """Lowercased URL scheme, or None if the string carries no scheme."""
    # scheme = the chars before the first ":" — must be [a-z][a-z0-9+.-]*
    # and must be followed by "://" so that "C:\..." or "12:30" never match.
    i = url.find(":")
    if i <= 0:
        return None
    scheme = url[:i]
    if not (scheme[0].isalpha() and all(
            c.isalnum() or c in "+.-" for c in scheme)):
        return None
    if not url.startswith(scheme + "://"):
        return None
    return scheme.lower()


def container_from_extension(ext: str) -> str | None:
    """Container whose extension list contains ``ext`` (lowercase, no dot).
    Stream-only containers (rtsp, srt) never match — they are protocol
    endpoints, not files."""
    ext = ext.lower().lstrip(".")
    for name, c in REGISTRY["containers"].items():
        if c.get("file") is False:
            continue
        if ext in c["extensions"]:
            return name
    return None


def stream_output_info(url: str) -> tuple[str, str] | None:
    """(container, muxer) for a stream *output* destination, else None.

    Schemes with a fixed container (rtmp, srt, udp, …) return it directly;
    http/https derive the container from the URL path extension
    (.mp4 → mp4, .ts → mpegts, .m3u8 → hls, .mp3 → mp3, …) falling back to
    the scheme's default (mpegts) when the path has no recognized
    extension — HTTP live pushing conventionally carries MPEG-TS."""
    scheme = stream_scheme(url)
    if scheme is None:
        return None
    info = _streaming()["output"].get(scheme)
    if info is None:
        return None
    if "muxer" in info:
        return info["container"], info["muxer"]
    # container_from: "extension" (http/https)
    from urllib.parse import urlsplit
    path = urlsplit(url).path
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    container = container_from_extension(ext) if ext else None
    if container is None:
        container = info["default_container"]
    return container, REGISTRY["containers"][container]["muxer"]


def input_format_for(url: str) -> str | None:
    """Explicit -f value for a stream *input* that ffmpeg would otherwise
    mis-probe (raw udp/rtp/tcp demux), else None (ffmpeg auto-detects)."""
    scheme = stream_scheme(url)
    if scheme is None:
        return None
    return _streaming()["input_format"].get(scheme)


def output_schemes() -> list[str]:
    """URL schemes allowed on stream *output* destinations."""
    return list(_streaming()["output"])


def all_muxers() -> set[str]:
    """Every muxer name fass may emit: the containers' muxers plus the
    fixed-container stream muxers. Used by the executor's argv
    allowlist (validated before a process is spawned)."""
    muxers = {c["muxer"] for c in REGISTRY["containers"].values()}
    for info in _streaming()["output"].values():
        if "muxer" in info:
            muxers.add(info["muxer"])
    return muxers


def stream_output_schemes() -> list[str]:
    """Schemes whose output container is FIXED (a bare filename of that
    container's kind is a contradiction, not a stream)."""
    return [s for s, i in _streaming()["output"].items() if "muxer" in i]


def diagnostic_output() -> dict:
    """The diagnostic analysis destination (M10.2): {'muxer': 'null',
    'path': '-'} — the null muxer discards the decoded stream, results go
    to the log. Used by the planner's diagnostic mode and by the argv
    gate's -f vocabulary."""
    return REGISTRY.get("diagnostic_output", {})


# ---------------------------------------------------------------- M4 helpers

def pixel_formats_for(video_codec: str) -> list[str]:
    """Output pixel formats the codec supports (scaling/conversion matrix)."""
    return list(REGISTRY["pixel_formats"].get(video_codec, []))


def pixel_format_error(video_codec: str, pixel_format: str) -> str | None:
    allowed = pixel_formats_for(video_codec)
    if pixel_format not in allowed:
        return (
            f"pixel format '{pixel_format}' is not supported by "
            f"{video_codec.upper()} (supported: {', '.join(allowed)})"
        )
    return None


def video_profiles_for(video_codec: str) -> list[str]:
    return list(REGISTRY["video_profiles"].get(video_codec, []))


def video_presets_for(video_codec: str) -> list[str]:
    return list(REGISTRY["video_presets"].get(video_codec, []))


def gpu_coder(video_codec: str) -> dict | None:
    """The nvenc coder for a codec when GPU encoding is requested (M6);
    None when the codec has no GPU form (VP9 is CPU-only)."""
    return REGISTRY["gpu"]["coders"].get(video_codec)


def sample_rates() -> list[int]:
    return list(REGISTRY["audio_resample"]["sample_rates"])


def channel_layouts() -> list[int]:
    return list(REGISTRY["audio_resample"]["channel_layouts"])


def speed_limits() -> tuple[float, float]:
    s = REGISTRY["speed"]
    return float(s["min"]), float(s["max"])


def atempo_limits() -> tuple[float, float]:
    s = REGISTRY["speed"]
    return float(s["atempo_min"]), float(s["atempo_max"])


def bsf_for_copy(source_container: str, target_container: str,
                 video_codec: str) -> str | None:
    """Bitstream filter a remux needs (None = plain stream copy).

    MP4/MOV -> MPEG-TS requires re-stamping H.264/HEVC into Annex-B form."""
    if target_container != "mpegts" or source_container not in ("mp4", "mov"):
        return None
    return REGISTRY["bitstream_filters"]["mp4_mov_to_mpegts"].get(video_codec)


def audio_bsf_for_copy(source_container: str, target_container: str,
                       audio_codec: str) -> str | None:
    """Audio bitstream filter a remux needs (None = plain stream copy).

    MPEG-TS -> MP4/MOV requires stripping the ADTS header from AAC; MPEG-TS
    carries AAC in ADTS form while MP4/MOV store it in raw (LOAS) form."""
    if source_container != "mpegts" or target_container not in ("mp4", "mov"):
        return None
    return REGISTRY["bitstream_filters"]["mpegts_to_mp4_mov"].get(audio_codec)


def bsf_specs() -> dict[str, dict]:
    """{name: spec} of every user-requestable bitstream filter.

    'trim' is a pseudo-entry (copy_cut): FFmpeg has no trim bsf — it stands
    for a stream-copy cut (-ss/-t with -c copy)."""
    return REGISTRY["bitstream_filters"]["specs"]


def bsf_spec(name: str) -> dict | None:
    return REGISTRY["bitstream_filters"]["specs"].get(name)


def bsf_errors(name: str, options: dict[str, str]) -> list[str]:
    """Validate a user-requested bitstream filter name and its options
    against the registry table: the name must exist, every option key must
    be valid for that filter and every value must match its kind."""
    spec = bsf_spec(name)
    if spec is None:
        allowed = ", ".join(sorted(bsf_specs()))
        return [f"unknown bitstream filter '{name}' (valid: {allowed})"]
    if spec.get("copy_cut"):
        if options:
            return [
                f"bitstream filter '{name}' takes no options — the cut comes "
                "from the trim start/duration"
            ]
        return []
    errors = []
    for key in sorted(options):
        opt = spec.get("options", {}).get(key)
        if opt is None:
            allowed = ", ".join(sorted(spec.get("options", {}))) or "(none)"
            errors.append(
                f"bitstream filter '{name}': option '{key}' is not valid "
                f"(valid: {allowed})"
            )
            continue
        err = _validate_option_value(f"{name}.{key}", opt, str(options[key]))
        if err:
            errors.append(err)
    return errors


# ---------------------------------------------------------------- source specs

# '<format>:<spec>' plus optional ';key=value' device options. The spec may
# itself contain ':' (lavfi parameter lists, x11grab display names), so only
# the FIRST colon splits format from spec; semicolons always start options.
_SOURCE_SPEC = re.compile(
    r"^(?P<fmt>[a-z0-9][a-z0-9_]*):(?P<spec>[^\s;]+)"
    r"(?P<opts>(?:;[a-z0-9][a-z0-9_]*=[^\s;]+)*)$"
)


def source_input_formats() -> list[str]:
    """Allowlisted generic input source formats (lavfi, v4l2, …)."""
    return list(REGISTRY["sources"]["input"])


def is_source_spec(path: str) -> bool:
    """True if the path is an allowlisted '<format>:<spec>' source, not a file.

    Stream URLs ('rtmp://…') are NOT source specs — they carry '://' and are
    handled by the streaming block. A local filename with a colon whose
    prefix is not allowlisted stays a filename."""
    m = _SOURCE_SPEC.match(path)
    return bool(m and m.group("fmt") in source_input_formats())


def source_spec_parts(path: str) -> tuple[str, str, list[tuple[str, str]]] | None:
    """(format, spec, [(key, value), …]) for an allowlisted source spec."""
    m = _SOURCE_SPEC.match(path)
    if not m or m.group("fmt") not in source_input_formats():
        return None
    opts: list[tuple[str, str]] = []
    for kv in m.group("opts").split(";"):
        if kv:
            k, v = kv.split("=", 1)
            opts.append((k, v))
    return m.group("fmt"), m.group("spec"), opts


def output_device_formats() -> list[str]:
    """Allowlisted output device formats (alsa, pulse) — audio only."""
    return list(REGISTRY["sources"]["output"])


def output_device_parts(path: str) -> tuple[str, str, list[tuple[str, str]]] | None:
    """(format, spec, options) if the *output* path is an allowlisted device
    spec ('alsa:hw:0', 'pulse:default'), else None (plain filename or URL)."""
    m = _SOURCE_SPEC.match(path)
    if m and m.group("fmt") in output_device_formats():
        opts: list[tuple[str, str]] = []
        for kv in m.group("opts").split(";"):
            if kv:
                k, v = kv.split("=", 1)
                opts.append((k, v))
        return m.group("fmt"), m.group("spec"), opts
    return None


def output_device_for(path: str) -> str | None:
    """Device format for an allowlisted output device spec, else None."""
    parts = output_device_parts(path)
    return parts[0] if parts else None


# ---------------------------------------------------------------- M5 subtitles

_SUB_FILTER_ESCAPES = re.compile(r"([\\'\":=,;])")


def subtitle_input_format(path: str) -> str | None:
    """FFmpeg input format name for a subtitle file path (registry-driven),
    or None if the extension is not a supported subtitle format."""
    ext = os.path.splitext(path.split("?", 1)[0].split("#", 1)[0])[1].lower()
    fmt = ext.lstrip(".").lower()
    return fmt if fmt in REGISTRY["subtitles"]["input_formats"] else None


def subtitle_burn_filter(path: str) -> str | None:
    """libass filter name for burning a subtitle file: 'subtitles' (srt/vtt)
    or 'ass' (ass/ssa); None for unsupported extensions."""
    fmt = subtitle_input_format(path)
    if fmt is None:
        return None
    return REGISTRY["subtitles"]["burn_filters"].get(fmt)


def soft_subtitle_codec(container: str) -> str | None:
    """Encoder for a soft (selectable) subtitle track in the container
    (mp4/mov → mov_text, mkv → srt), or None when the container has no
    subtitle codec (webm, mpegts, flv, rtsp, srt)."""
    return REGISTRY["subtitles"]["soft_codecs"].get(container)


def subtitle_burn_expr(path: str) -> str:
    """Complete filter argument for burning ``path``: ``<filter>=<escaped>``.

    The filename crosses TWO FFmpeg unescape layers before the file is
    opened: the filter-graph parser (av_get_token) and the filter-option
    parser (av_opt_set_from_string). Each layer treats backslash, quotes,
    and (at the graph level) ','/';' as special. Escaping the same
    character set once per layer is verified round-trip on ffmpeg 6.1.1
    (hostile names: spaces, quotes, commas, semicolons, backslashes, '=').
    """
    escaped = _SUB_FILTER_ESCAPES.sub(r"\\\1", _SUB_FILTER_ESCAPES.sub(r"\\\1", path))
    return f"{subtitle_burn_filter(path)}={escaped}"


def subtitle_dump_codec(container: str) -> str | None:
    """Encoder a subtitle dump (mode \"subtitles\") writes for the container
    (srt_file → srt, vtt_file → webvtt, ass_file → ass), or None."""
    return REGISTRY["subtitles"]["dump_codecs"].get(container)


# ------------------------------------------------------- format options (M7)

# Which registry option-group a container uses (the "mp4" group carries the
# movflags/brand/fragmentation options of the whole MPEG-4 family).
_FORMAT_OPTION_GROUPS = {
    "mp4": "mp4", "mov": "mp4", "m4a": "mp4", "3gp": "mp4",
    "mpegts": "mpegts", "hls": "hls", "dash": "dash", "avi": "avi", "flv": "flv",
    "mp3": "mp3", "wav": "wav",
    # tee (M13.5) accepts what its branches accept: the hls + dash option
    # sets plus its own group (the planner routes each key into its branch)
    "tee": ["hls", "dash", "tee"],
}

_RATE = re.compile(r"^\d+(\.\d+)?[kKmM]?$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_INT = re.compile(r"^-?(0[xX][0-9a-fA-F]+|\d+)$")


def _int_value(value: str) -> int:
    """int() that also accepts hex (PIDs); base-0 would misread '008' as octal."""
    if value.lstrip("-").lower().startswith("0x"):
        return int(value, 16)
    return int(value)
_RATIONAL = re.compile(r"^-?\d+(/\d+)?$")
_BRAND = re.compile(r"^[a-z0-9]{4}$")
_META_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*$")


def format_option_specs(container: str) -> dict[str, dict]:
    """{key: spec} of every '-<key> value' option the container accepts: the
    generic set plus the container's own group (mp4/mpegts/hls/avi/flv/mp3/wav).
    A container whose mapping is a list (tee: hls + dash + tee) unions those
    groups' options — tee accepts what its branches accept."""
    specs = dict(REGISTRY["format_options"]["generic"])
    group = _FORMAT_OPTION_GROUPS.get(container)
    for g in [group] if isinstance(group, str) else list(group or []):
        specs.update(REGISTRY["format_options"].get(g, {}))
    return specs


def _validate_option_value(key: str, spec: dict, value: str) -> str | None:
    kind = spec["kind"]
    lo, hi = spec.get("range", (None, None))
    if kind == "rate":
        if not _RATE.match(value):
            return f"{key}: '{value}' is not a bitrate (e.g. 1000k)"
    elif kind == "number":
        if not _NUMBER.match(value):
            return f"{key}: '{value}' is not a number (seconds)"
        v = float(value)
    elif kind == "int":
        if not _INT.match(value):
            return f"{key}: '{value}' is not an integer"
        v = _int_value(value)
    elif kind == "rational":
        if not _RATIONAL.match(value):
            return f"{key}: '{value}' is not a rational (e.g. 30000/1001)"
        num, _, den = value.partition("/")
        d = int(den) if den else 1
        if d == 0:
            return f"{key}: rational denominator must be non-zero"
        v = int(num) / d
    else:
        v = None
    if kind in ("number", "int", "rational") and v is not None:
        if lo is not None and v < lo:
            return f"{key}: {v} is below the minimum {lo}"
        if hi is not None and v > hi:
            return f"{key}: {v} is above the maximum {hi}"
    if kind == "bool":
        if value not in ("0", "1"):
            return f"{key}: expected 0 or 1, got '{value}'"
    elif kind == "choice":
        if value not in spec["values"]:
            return (
                f"{key}: '{value}' is not one of "
                f"{', '.join(spec['values'])}"
            )
    elif kind == "flagset":
        # FFmpeg flag syntax: flags joined with '+', each optionally prefixed
        # with '+' (set) or '-' (clear) — e.g. '+faststart-use_mmf'.
        parts = [p for p in value.split("+") if p]
        if not parts:
            return f"{key}: empty flag list"
        bad = [p for p in parts if p.lstrip("+-") not in spec["values"]]
        if bad:
            return (
                f"{key}: unknown flag(s) {', '.join(bad)} "
                f"(valid: {', '.join(spec['values'])})"
            )
    elif kind == "metadata":
        # comma-joined key=value pairs (one -metadata flag per pair at the
        # command line); a value may contain spaces but not commas
        for pair in value.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                return f"{key}: '{pair}' is not a key=value tag"
            k, _, val = pair.partition("=")
            if not _META_KEY.match(k):
                return f"{key}: invalid metadata tag name '{k}'"
            if not val.strip():
                return f"{key}: tag '{k}' has an empty value"
    elif kind == "brand":
        if not _BRAND.match(value):
            return f"{key}: '{value}' is not a 4-character brand"
    elif kind == "url":
        if " " in value or not value:
            return f"{key}: '{value}' is not a URL"
    elif kind == "string":
        # free text (SEI payloads, setts expressions, unit-type lists):
        # only non-emptiness is checked
        if not value.strip():
            return f"{key}: value must be a non-empty string"
    return None


def format_option_errors(container: str, options: dict[str, str]) -> list[str]:
    """Validate output.format_options against the registry table. Every key
    must exist for the container and every value must match its kind."""
    if not options:
        return []
    specs = format_option_specs(container)
    errors = []
    for key in sorted(options):
        spec = specs.get(key)
        if spec is None:
            allowed = ", ".join(sorted(specs))
            errors.append(
                f"format option '{key}' is not a valid option for "
                f"{container} (valid: {allowed})"
            )
            continue
        err = _validate_option_value(key, spec, str(options[key]))
        if err:
            errors.append(err)
    return errors


# ------------------------------------------------------- codec options (M8)

_VIDEO_CODEC_OPTION_ENCODERS = (
    "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
    "libx264", "libx265", "libvvenc", "prores_ks", "png",
)
_AUDIO_CODEC_OPTION_ENCODERS = (
    "ac3", "eac3", "flac", "libopus", "opus", "libfdk_aac",
)


def qsv_coder(video_codec: str) -> dict | None:
    """The QSV coder for a codec when QSV encoding is requested (M8);
    None when the codec has no QSV form (like the gpu coders)."""
    return REGISTRY["qsv"]["coders"].get(video_codec)


def video_encoder_name(video_codec: str, gpu: bool = False,
                       qsv: bool = False) -> str | None:
    """The FFmpeg encoder a video plan will use: the nvenc coder when gpu,
    the QSV coder when qsv, else the CPU encoder from video_codecs."""
    if gpu:
        coder = REGISTRY["gpu"]["coders"].get(video_codec)
    elif qsv:
        coder = REGISTRY["qsv"]["coders"].get(video_codec)
    else:
        coder = REGISTRY["video_codecs"].get(video_codec)
    return coder["encoder"] if coder else None


def audio_encoder_name(audio_codec: str) -> str | None:
    """The FFmpeg encoder an audio plan will use (audio_codecs table)."""
    coder = REGISTRY["audio_codecs"].get(audio_codec)
    return coder["encoder"] if coder else None


def codec_option_specs(encoder: str) -> dict[str, dict]:
    """{key: spec} of one encoder's private codec options
    (registry codec_options table)."""
    return dict(REGISTRY["codec_options"].get(encoder, {}))


def _codec_option_value_error(key: str, spec: dict, value: str) -> str | None:
    kind = spec["kind"]
    lo, hi = spec.get("range", (None, None))
    if kind == "int":
        if not _INT.match(value):
            return f"{key}: '{value}' is not an integer"
        v = _int_value(value)
    elif kind == "float":
        if not _NUMBER.match(value):
            return f"{key}: '{value}' is not a number"
        v = float(value)
    elif kind == "bool":
        if value not in ("0", "1"):
            return f"{key}: expected 0 or 1, got '{value}'"
        return None
    elif kind == "choice":
        if value in spec["values"]:
            return None
        # the numeric form is accepted when the option carries a range
        if spec.get("range") and _NUMBER.match(value):
            v = float(value)
            if (lo is None or v >= lo) and (hi is None or v <= hi):
                return None
        tail = " or a number in its range" if spec.get("range") else ""
        return (
            f"{key}: '{value}' is not one of "
            f"{', '.join(spec['values'])}{tail}"
        )
    else:  # string — single token (params options are :-separated, no spaces)
        if not value or " " in value:
            return f"{key}: '{value}' must be a non-empty single token (no spaces)"
        return None
    if lo is not None and v < lo:
        return f"{key}: {v} is below the minimum {lo}"
    if hi is not None and v > hi:
        return f"{key}: {v} is above the maximum {hi}"
    return None


def codec_option_errors(encoder: str, options: dict[str, str]) -> list[str]:
    """Validate {key: value} against one encoder's codec_options table.
    Every key must exist for the encoder and every value must match its kind."""
    if not options:
        return []
    specs = codec_option_specs(encoder)
    if not specs:
        return [f"{encoder} has no private codec options in the registry"]
    errors = []
    for key in sorted(options):
        spec = specs.get(key)
        if spec is None:
            allowed = ", ".join(sorted(specs))
            errors.append(
                f"codec option '{key}' is not a valid option for "
                f"{encoder} (valid: {allowed})"
            )
            continue
        err = _codec_option_value_error(key, spec, str(options[key]))
        if err:
            errors.append(err)
    return errors


def _codec_option_key_union(encoder_names: tuple[str, ...]) -> set[str]:
    return set().union(*(codec_option_specs(e) for e in encoder_names))


def video_codec_option_keys() -> set[str]:
    """Option names reachable as video codec options (union over the video
    encoder tables: cpu, nvenc, qsv, prores, png, vvenc)."""
    return _codec_option_key_union(_VIDEO_CODEC_OPTION_ENCODERS)


def audio_codec_option_keys() -> set[str]:
    """Option names reachable as audio codec options (union over the audio
    encoder tables). dvbsub is a subtitle-domain table and is not included."""
    return _codec_option_key_union(_AUDIO_CODEC_OPTION_ENCODERS)


# ------------------------------------------------------- filters (M7)

def filter_names(domain: str) -> set[str]:
    """Allowlisted filter names for 'video' or 'audio'."""
    return set(REGISTRY["filters"].get(domain, {}))


def all_filter_names() -> set[str]:
    return filter_names("video") | filter_names("audio")


# ------------------------------------------------- filter options (M10)

def filter_specs() -> dict[str, dict]:
    """{name: entry} of every allowlisted filter. Entries with an 'options'
    schema (the M10 table) get per-option validation; the older name-only
    entries keep their description-only shape and pass through unchecked."""
    specs: dict[str, dict] = {}
    specs.update(REGISTRY["filters"].get("video", {}))
    specs.update(REGISTRY["filters"].get("audio", {}))
    return specs


def filter_spec(name: str) -> dict | None:
    return filter_specs().get(name)


# ------------------------------------------------- filter docs (M11.2)

def doc_ref(name: str) -> dict | None:
    """The pinned-corpus documentation reference for a filter —
    {url, anchor, title} — video domain first, then audio; None when the
    filter is not in the allowlist (or carries no doc_ref)."""
    for domain in ("video", "audio"):
        spec = REGISTRY["filters"].get(domain, {}).get(name)
        if spec is not None:
            return spec.get("doc_ref")
    return None


def filter_aliases(name: str) -> list[str]:
    """Reviewed natural-language phrases naming this filter (registry
    'aliases'); [] when the filter is unknown or has none."""
    spec = filter_spec(name)
    if spec is None:
        return []
    return list(spec.get("aliases", []))


def doc_ref_map() -> dict[str, dict]:
    """{name: doc_ref} for every allowlisted filter that carries one."""
    return {name: spec["doc_ref"] for name, spec in filter_specs().items()
            if spec.get("doc_ref")}


def combiner_filters() -> dict[str, dict]:
    """Multi-input filters (registry 'multi_input' flag) — the filters that
    join the main input with one or more additional inputs: xfade, acrossfade,
    concat, hstack, vstack, overlay (video) and amix, amerge, join (audio).
    {name: entry} across both domains."""
    return {name: spec for name, spec in filter_specs().items()
            if "multi_input" in spec}


def combiner_domain(name: str) -> str | None:
    """'video' or 'audio' domain of a combiner filter, None if it is not one."""
    for domain in ("video", "audio"):
        spec = REGISTRY["filters"].get(domain, {}).get(name)
        if spec is not None and "multi_input" in spec:
            return domain
    return None


def comparison_filters() -> dict[str, dict]:
    """Comparison metrics (registry 'needs_reference' flag) — the filters
    that measure the quality difference between the main input and a
    reference input: psnr, ssim, libvmaf. {name: entry} across both
    domains."""
    return {name: spec for name, spec in filter_specs().items()
            if spec.get("needs_reference")}


def filter_options_problems(name: str, options: dict[str, str]) -> list[str]:
    """Validate a filter's key=value options against its registry schema —
    the filter-side twin of bsf_errors. A filter without an option schema
    (name-only allowlist entry) accepts any arguments, as before. 'enable'
    is the universal timeline option every filter accepts."""
    spec = filter_spec(name)
    if spec is None or "options" not in spec:
        return []
    valid = dict(spec.get("options", {}))
    valid["enable"] = {"kind": "string"}
    problems = []
    for key in sorted(options):
        opt = valid.get(key)
        if opt is None:
            allowed = ", ".join(sorted(spec.get("options", {}))) or "(none)"
            problems.append(
                f"filter '{name}': option '{key}' is not valid (valid: {allowed})"
            )
            continue
        err = _validate_option_value(f"{name}.{key}", opt, str(options[key]))
        if err:
            problems.append(err)
    return problems


_FILTER_OPT_KEY = re.compile(r"^[a-z0-9_-]+$")


def _parse_filter_args(name: str, args: str) -> tuple[dict[str, str], list[str]]:
    """Parse a filter instance's raw argument string into an ordered
    {key: value} dict. 'key=value' parts are named; bare parts fill the
    schema's positional slots in declaration order (FFmpeg's positional
    order = the order ffmpeg -h prints). Returns (options, problems)."""
    spec = filter_spec(name)
    options: dict[str, str] = {}
    problems: list[str] = []
    if spec is None or "options" not in spec:
        return options, problems
    order = list(spec["options"])
    pos = 0
    for part in _split_top_level(args, ":"):
        part = part.strip()
        if not part:
            problems.append(f"filter '{name}': empty argument (stray ':')")
            continue
        key, sep, val = part.partition("=")
        if sep and _FILTER_OPT_KEY.match(key):
            if key in options:
                problems.append(f"filter '{name}': option '{key}' given twice")
                continue
            options[key] = val
        else:
            if pos >= len(order):
                problems.append(
                    f"filter '{name}': too many positional arguments "
                    f"({len(order)} accepted)"
                )
                continue
            options[order[pos]] = part
            pos += 1
    return options, problems


# Filters that take no argument at all — anything after '=' is a mistake.
_NO_ARG_FILTERS = {"hflip", "vflip", "anull"}

_ARG_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


def _balanced_quotes(s: str) -> bool:
    """Single quotes must pair up, honoring backslash escapes (FFmpeg
    filter-arg quoting)."""
    in_quote = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'":
            in_quote = not in_quote
        i += 1
    return not in_quote


def filtergraph_problems(graph: str) -> list[str]:
    """Public name (schema validators and the executor call this)."""
    return _filtergraph_problems(graph)


def _filtergraph_problems(graph: str) -> list[str]:
    """Validate one filter instance or a full filter graph (filter_complex
    body). Splits top-level ';' (chains) and ',' (instances) honoring
    backslash escapes and '…' quoting; checks every filter name against the
    allowlist and the argument character set. Returns a list of problems —
    empty when the graph is buildable."""
    problems: list[str] = []
    if not graph or not graph.strip():
        return ["filtergraph is empty"]
    if ";" in graph or "," in graph or "[" in graph:
        chains = _split_top_level(graph, ";")
    else:
        chains = [graph]
    for chain in chains:
        _validate_chain(chain, problems)
    if not problems:
        names = {part.split("=", 1)[0].split("[", 1)[0].strip()
                 for part in _split_top_level(graph, ",")}
        if not names:
            problems.append("filtergraph contains no filter")
    return problems


def filtergraph_output_labels(graph: str) -> list[str]:
    """The graph's output pad labels — defined but never consumed as an
    input — in definition order. Empty for an unlabeled graph: FFmpeg
    auto-maps such pads to the output streams, so no -map is needed."""
    if not graph:
        return []
    refs: set[str] = set()
    defs: list[str] = []
    for chain in _split_top_level(graph, ";"):
        body = chain.strip()
        if not body:
            continue
        if body.startswith("["):
            end = body.find("]")
            if end < 0:
                continue
            if _LABEL.match(body[1:end]):
                refs.add(body[1:end])
            body = body[end + 1:]
        for inst in _split_top_level(body, ","):
            s = inst.strip()
            if not s:
                continue
            if s.startswith("["):
                end = s.find("]")
                if end < 0:
                    continue
                if _LABEL.match(s[1:end]):
                    refs.add(s[1:end])
                s = s[end + 1:]
            # trailing labels are quote-safe: a real '[label]' is the last
            # thing in the instance, a quoted "…[x]" ends with "'"
            while s.endswith("]"):
                start = s.rfind("[")
                if start < 0:
                    break
                label = s[start + 1:-1]
                if label and _LABEL.match(label):
                    defs.append(label)
                    s = s[:start]
                elif not label:
                    s = s[:start]
                else:
                    break
    out: list[str] = []
    for d in defs:
        if d not in refs and d not in out:
            out.append(d)
    return out


def _unquoted(s: str) -> str:
    """The string with its quoted spans ('…' and "…") removed, honoring
    backslash escapes — what a filter-arg parser sees as structure."""
    out: list[str] = []
    quote: str | None = None
    esc = False
    for ch in s:
        if quote:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        else:
            out.append(ch)
    return "".join(out)


def filter_expr_problems(expr: str) -> list[str]:
    """Validate a FilterOp expression: a filter instance or same-domain
    chain that gets SPLICED into a larger chain, so it must not carry pad
    labels ('[x]' / '[0:v]') — labels are only meaningful in a full
    filtergraph (EditPlan.filtergraph). Brackets inside quoted arguments
    (drawtext=text='[x]') are fine."""
    problems = _filtergraph_problems(expr)
    if problems:
        return problems
    if "[" in _unquoted(expr) or "]" in _unquoted(expr):
        problems.append(
            "pad labels ('[x]') are not allowed in a filter operation — "
            "they belong to a full filtergraph (EditPlan.filtergraph)"
        )
    return problems


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split on an unescaped, unquoted ``sep`` (also honoring the OTHER
    separator so 'a=1;b' inside a quoted value never splits)."""
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            buf.append(s[i:i + 2])
            i += 2
            continue
        if c == "'":
            in_quote = not in_quote
        if c == sep and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _validate_chain(chain: str, problems: list[str]) -> None:
    chain = chain.strip()
    if not chain:
        problems.append("filtergraph has an empty chain (stray ';')")
        return
    # optional input label
    body = chain
    if body.startswith("["):
        end = body.find("]")
        if end < 0:
            problems.append(f"unterminated label in '{chain[:40]}'")
            return
        label = body[1:end]
        if not _LABEL.match(label):
            problems.append(f"invalid stream label '[{label}]'")
        body = body[end + 1:]
    instances = _split_top_level(body, ",")
    for inst in instances:
        inst = inst.strip()
        if not inst:
            problems.append("filtergraph has an empty filter (stray ',')")
            continue
        _validate_instance(inst, problems)


_LABEL = re.compile(r"^[0-9]+(:[a-zA-Z0-9]+){0,2}$|^[a-zA-Z][a-zA-Z0-9_]*$")


def _strip_trailing_labels(s: str) -> str:
    """Drop '[label]' (or '[]') suffixes — chain output labels."""
    while s.endswith("]"):
        start = s.rfind("[")
        if start < 0:
            break
        label = s[start + 1:-1]
        if not label or _LABEL.match(label):
            s = s[:start]
        else:
            break
    return s


def _validate_instance(inst: str, problems: list[str]) -> None:
    """One filter instance: 'name', 'name=args', plus optional input/output
    '[label]'s — stripped before the name/argument split. Multi-input
    filters (concat, xfade, hstack) carry one input label per pad, so ALL
    leading labels are stripped, not just one."""
    s = inst
    while s.startswith("["):
        end = s.find("]")
        if end < 0:
            problems.append(f"unterminated label in '{s[:40]}'")
            return
        if not _LABEL.match(s[1:end]):
            problems.append(f"invalid stream label '[{s[1:end]}]'")
        s = s[end + 1:]
    s = _strip_trailing_labels(s)
    if "=" in s:
        name, _, args = s.partition("=")
    else:
        name, args = s, ""
    name = name.strip()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", name):
        problems.append(f"'{name}' is not a filter name (in '{inst[:60]}')")
        return
    if name in _NO_ARG_FILTERS and args:
        problems.append(f"filter '{name}' takes no arguments")
        return
    if name not in all_filter_names():
        problems.append(
            f"filter '{name}' is not in the supported filter set"
        )
        return
    if args and _ARG_FORBIDDEN.search(args):
        problems.append(f"filter '{name}' arguments contain control characters")
        return
    if args and not _balanced_quotes(args):
        problems.append(f"filter '{name}' has unbalanced quotes in its arguments")
    if ";" in args:
        problems.append(f"filter '{name}' arguments contain ';' (chain separator)")
    if args:
        # M10: filters with an option schema get their key=value options
        # validated (kinds, ranges, choices) — name-only entries pass through
        options, opt_problems = _parse_filter_args(name, args)
        problems.extend(opt_problems)
        problems.extend(filter_options_problems(name, options))

