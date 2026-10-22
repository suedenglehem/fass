"""Optional local inspection helpers (plan0.md section 2).

A filename alone cannot tell you whether a video has audio, what its
duration is, or whether a requested encoder is installed. These helpers
run ffprobe / ffmpeg locally (never on the server against uploads in the
MVP) so the planner can make remux decisions and range checks honest.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field

_HDR_PIX_FMTS = {
    "p010le", "p012le", "p016le", "p010be", "p016be",
    "x2rgb10le", "x2rgb12le", "x2rgb16le",
}


@dataclass
class MediaInfo:
    path: str
    duration: float | None = None
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    fps: float | None = None
    has_audio: bool = False
    audio_codec: str | None = None
    video_streams: int = 0
    audio_streams: int = 0
    rotation: int | None = None
    hdr: bool = False


@dataclass
class FfmpegInfo:
    version: str | None = None
    encoders: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(cmd: list[str], timeout: float = 30.0) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if p.returncode != 0:
            return None
        return p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_ffprobe_json(payload: dict) -> MediaInfo | None:
    """Parse `ffprobe -print_format json -show_format -show_streams` output."""
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return None
    fmt = payload.get("format") or {}
    duration = None
    try:
        duration = float(fmt.get("duration") or video.get("duration"))
    except (TypeError, ValueError):
        duration = None

    fps = None
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            fps = float(num) / float(den) if float(den) else None
        except ValueError:
            fps = None

    rotation = None
    for sd in video.get("side_data_list") or []:
        if "displaymatrix" in sd or sd.get("rotation"):
            try:
                rotation = int(float(sd.get("rotation")))
            except (TypeError, ValueError):
                rotation = None
    if rotation is None:
        tags = video.get("tags") or {}
        for key in ("rotate", "rotation"):
            if tags.get(key) not in (None, ""):
                try:
                    rotation = int(float(tags[key]))
                except ValueError:
                    rotation = None

    pix_fmt = video.get("pix_fmt")
    tags = {k.lower(): v for k, v in (video.get("tags") or {}).items()}
    hdr = pix_fmt in _HDR_PIX_FMTS or tags.get("color_space") == "bt2020nc" or \
        (video.get("color_space") in ("bt2020nc", "bt2020ncl"))
    # color_* top-level stream fields
    for k in ("color_space", "color_primaries", "color_trc"):
        if video.get(k) in ("bt2020nc", "bt2020ncl", "smpte2084"):
            hdr = True

    return MediaInfo(
        path=payload.get("_path", ""),
        duration=duration,
        video_codec=video.get("codec_name"),
        width=video.get("width"),
        height=video.get("height"),
        pix_fmt=pix_fmt,
        fps=fps,
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        video_streams=sum(1 for s in streams if s.get("codec_type") == "video"),
        audio_streams=sum(1 for s in streams if s.get("codec_type") == "audio"),
        rotation=rotation,
        hdr=hdr,
    )


def probe_media(path: str) -> MediaInfo | None:
    """ffprobe a local file. Returns None when ffprobe is missing or the
    file cannot be read."""
    if not ffprobe_available():
        return None
    out = _run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path]
    )
    if out is None:
        return None
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return None
    info = parse_ffprobe_json(payload)
    if info:
        info.path = path
    return info


def probe_ffmpeg() -> FfmpegInfo | None:
    """Inspect the local FFmpeg build for encoders and filters."""
    if not ffmpeg_available():
        return None
    info = FfmpegInfo()
    ver = _run(["ffmpeg", "-hide_banner", "-version"])
    if ver:
        info.version = ver.splitlines()[0]
    enc = _run(["ffmpeg", "-hide_banner", "-encoders"])
    if enc:
        info.encoders = {line.split()[1] for line in enc.splitlines() if len(line.split()) > 1}
    fil = _run(["ffmpeg", "-hide_banner", "-filters"])
    if fil:
        info.filters = {line.split()[-1] for line in fil.splitlines() if line.strip() and line.split()[0] in "TS.-"}
    return info


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def preflight(media: MediaInfo | None, ff: FfmpegInfo | None,
              encoders: list[str], filters: list[str]) -> list[Check]:
    """Local capability preflight (PRD FR-5, optional level)."""
    checks: list[Check] = []
    if ff is None:
        checks.append(Check("ffmpeg build", False, "ffmpeg not found locally — checks limited to static validation"))
        return checks
    checks.append(Check("ffmpeg build", True, ff.version or "unknown version"))
    for name in encoders:
        ok = name in ff.encoders
        checks.append(Check(f"encoder {name}", ok, "available" if ok else "NOT available in this FFmpeg build"))
    for name in filters:
        ok = name in ff.filters
        checks.append(Check(f"filter {name}", ok, "available" if ok else "NOT available in this FFmpeg build"))
    return checks
