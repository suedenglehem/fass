"""Load and query the versioned capability registry (PRD FR-3)."""

from __future__ import annotations

import json
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
