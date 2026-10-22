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
