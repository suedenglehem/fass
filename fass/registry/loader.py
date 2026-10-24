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
        return f"container '{container}' is not in the MVP container set"
    if video_codec not in row["video"]:
        allowed = ", ".join(row["video"])
        return (
            f"video codec '{video_codec}' is not supported in {container.upper()} "
            f"(supported: {allowed})"
        )
    return None


def default_codec(container: str) -> str:
    return REGISTRY["matrix"][container]["default_video"]


def audio_for(container: str) -> str:
    return REGISTRY["matrix"][container]["audio_codec"]


def extension_for(container: str) -> str:
    return REGISTRY["containers"][container]["extensions"][0]


def capability_check(container: str, video_codec: str) -> list[str]:
    """Capability-level validation (PRD FR-5). Returns a list of errors."""
    errors = []
    row = REGISTRY["matrix"].get(container)
    if row is None:
        errors.append(f"container '{container}' is not supported")
        return errors
    if video_codec not in row["video"]:
        errors.append(
            f"video codec '{video_codec}' is not supported in {container.upper()} "
            f"(supported: {', '.join(row['video'])})"
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


def stream_output_info(url: str) -> tuple[str, str] | None:
    """(container, muxer) for a stream *output* destination, else None."""
    scheme = stream_scheme(url)
    if scheme is None:
        return None
    info = _streaming()["output"].get(scheme)
    if info is None:
        return None
    return info["container"], info["muxer"]


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
