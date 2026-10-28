"""Optional local inspection helpers.

A filename alone cannot tell you whether a video has audio, what its
duration is, or whether a requested encoder is installed. These helpers
run ffprobe / ffmpeg locally (never on the server against uploads in the
MVP) so the planner can make remux decisions and range checks honest.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

_HDR_PIX_FMTS = {
    "p010le", "p012le", "p016le", "p010be", "p016be",
    "x2rgb10le", "x2rgb12le", "x2rgb16le",
}

# `ffmpeg -filters` rows: "  T.C hue    V->V   Adjust the hue..." — the flags
# column is 3 chars from [TSC] with '.' placeholders; legend rows use "=" as
# the second column, which is how they are told apart.
_FILTER_FLAG = re.compile(r"^[TSC.-]+$")

# `ffmpeg -muxers` rows: "  E mp4   MP4 (MPEG-4 Part 14)" — the flags column
# is built from [D.E]; the legend rows (" D. = Demuxing supported") carry "="
# as the name column.
_MUX_FLAG = re.compile(r"^[DE.]+$")
_BSF_FLAG = re.compile(r"^[VAS.]+$")
_BSF_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


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
    subtitle_streams: int = 0
    rotation: int | None = None
    hdr: bool = False
    # first video stream's packet pts (M12 compare): True/False from a
    # bounded packet probe (probe_has_pts); None = not probed
    has_pts: bool | None = None
    # measured source settings (bps / Hz) — the same-value conversion
    # warning compares requested targets against these
    video_bitrate: int | None = None
    audio_bitrate: int | None = None
    audio_sample_rate: int | None = None


@dataclass
class FfmpegInfo:
    version: str | None = None
    # the full `ffmpeg -version` minus the lib* lines (the user's
    # `ffmpeg -version | grep -v '^lib'`): version, built-with, configuration
    build_info: str | None = None
    encoders: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    muxers: set[str] = field(default_factory=set)
    bsfs: set[str] = field(default_factory=set)


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


def parse_ffprobe_json(payload: dict, *, require_video: bool = True
                       ) -> MediaInfo | None:
    """Parse `ffprobe -print_format json -show_format -show_streams` output.

    ``require_video`` (default) rejects audio-only files — the main input of
    a normal plan is video media. Combiner plans (M10.3) probe audio-only
    inputs with ``require_video=False``."""
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if require_video and video is None:
        return None
    if video is None and audio is None:
        return None
    fmt = payload.get("format") or {}
    duration = None
    try:
        duration = float(fmt.get("duration") or (video or audio).get("duration"))
    except (TypeError, ValueError):
        duration = None

    fps = None
    if video is not None:
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
        if "/" in rate:
            num, _, den = rate.partition("/")
            try:
                fps = float(num) / float(den) if float(den) else None
            except ValueError:
                fps = None

    rotation = None
    if video is not None:
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

    def _bps_int(value) -> int | None:
        try:
            v = int(str(value).strip())
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    # per-stream bitrate first; containers that don't record it (some
    # .ts/.mkv) fall back to the format-level total — the 10% comparison
    # tolerance absorbs the audio share
    video_bitrate = _bps_int(video.get("bit_rate")) if video else None
    if video_bitrate is None and video is not None:
        video_bitrate = _bps_int(fmt.get("bit_rate"))
    audio_bitrate = _bps_int(audio.get("bit_rate")) if audio else None
    audio_sample_rate = _bps_int(audio.get("sample_rate")) if audio else None

    if video is None:
        pix_fmt, hdr = None, False
    else:
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
        video_codec=video.get("codec_name") if video else None,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        pix_fmt=pix_fmt,
        fps=fps,
        has_audio=audio is not None,
        audio_codec=audio.get("codec_name") if audio else None,
        video_streams=sum(1 for s in streams if s.get("codec_type") == "video"),
        audio_streams=sum(1 for s in streams if s.get("codec_type") == "audio"),
        subtitle_streams=sum(
            1 for s in streams if s.get("codec_type") == "subtitle"
        ),
        rotation=rotation,
        hdr=hdr,
        video_bitrate=video_bitrate,
        audio_bitrate=audio_bitrate,
        audio_sample_rate=audio_sample_rate,
    )


def probe_media(path: str, *, require_video: bool = True) -> MediaInfo | None:
    """ffprobe a local file. Returns None when ffprobe is missing or the
    file cannot be read. ``require_video=False`` accepts audio-only files
    (combiner inputs, M10.3)."""
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
    info = parse_ffprobe_json(payload, require_video=require_video)
    if info:
        info.path = path
    return info


def probe_has_pts(path: str, sample: int = 8, timeout: float = 30.0) -> bool | None:
    """Does the first video stream carry packet timestamps?

    ``setpts=PTS-STARTPTS`` is only meaningful on streams that HAVE pts —
    a raw bitstream (a bare .hevc Annex B file, for instance) demuxes
    without any (ffprobe reports N/A for every packet), so the comparison
    graph probes before it decides (M12 compare, Netflix vmaf docs order).
    The probe is bounded: it reads the first ``sample`` packets of
    ``ffprobe -v error -select_streams v:0 -show_entries
    packet=pts,pts_time,dts,dts_time -of csv=p=0`` and stops — pts
    presence is a stream-level property, and a bounded read keeps the
    cost flat even on multi-hour files. Returns True when a sampled
    packet has pts, False when sampled packets have none, None when
    ffprobe is missing or the file cannot be read (unknown — the caller
    keeps the docs' default).
    """
    if not ffprobe_available():
        return None
    try:
        p = subprocess.Popen(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts,pts_time,dts,dts_time",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    watchdog = threading.Timer(timeout, p.kill)
    watchdog.daemon = True
    watchdog.start()
    saw_packet = False
    has_pts = False
    try:
        for i, line in enumerate(p.stdout or []):
            if i >= sample:
                break
            if not line.strip():
                continue
            saw_packet = True
            first = line.split(",", 1)[0].strip()
            if first in ("", "N/A", "-1"):
                continue
            try:
                if int(first) >= 0:
                    has_pts = True
            except ValueError:
                pass
    finally:
        watchdog.cancel()
        p.kill()
        p.wait()
    return has_pts if saw_packet else None


_ffmpeg_info_cache: FfmpegInfo | None = None
_ffmpeg_info_probed = False


def probe_ffmpeg() -> FfmpegInfo | None:
    """Inspect the local FFmpeg build for encoders, filters, muxers and
    bitstream filters.

    Muxer and bsf availability is build-specific (e.g. the ``showinfo``
    bsf only exists from FFmpeg 7.1), so it is probed, never assumed.
    The build does not change within a process, so the probe is cached
    (M10.1: the planner consults filter presence on every request)."""
    global _ffmpeg_info_cache, _ffmpeg_info_probed
    if _ffmpeg_info_probed:
        return _ffmpeg_info_cache
    _ffmpeg_info_probed = True
    if not ffmpeg_available():
        return None
    info = FfmpegInfo()
    ver = _run(["ffmpeg", "-hide_banner", "-version"])
    if ver:
        info.version = ver.splitlines()[0]
        info.build_info = "\n".join(
            line for line in ver.splitlines() if not line.startswith("lib"))
    enc = _run(["ffmpeg", "-hide_banner", "-encoders"])
    if enc:
        info.encoders = {line.split()[1] for line in enc.splitlines() if len(line.split()) > 1}
    fil = _run(["ffmpeg", "-hide_banner", "-filters"])
    if fil:
        names = set()
        for line in fil.splitlines():
            parts = line.split()
            if len(parts) >= 2 and _FILTER_FLAG.match(parts[0]) and parts[1] != "=":
                names.add(parts[1])
        info.filters = names
    mux = _run(["ffmpeg", "-hide_banner", "-muxers"])
    if mux:
        # Rows look like "  E mp4             MP4 (MPEG-4 Part 14)"; legend
        # rows (" D. = Demuxing supported") carry "=" as the name column.
        names = set()
        for line in mux.splitlines():
            parts = line.split()
            if len(parts) >= 2 and _MUX_FLAG.match(parts[0]) and parts[1] != "=":
                names.add(parts[1])
        info.muxers = names
    bsf = _run(["ffmpeg", "-hide_banner", "-bsfs"])
    if bsf:
        names = set()
        for line in bsf.splitlines():
            parts = line.split()
            if not parts:
                continue
            # 6.1 prints bare names; other builds use a flag column
            # ("  V name   description") — take the second token then.
            name = parts[1] if (len(parts) >= 2 and _BSF_FLAG.match(parts[0])) else parts[0]
            if _BSF_NAME.match(name):
                names.add(name)
        info.bsfs = names
    _ffmpeg_info_cache = info
    return info


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def preflight(media: MediaInfo | None, ff: FfmpegInfo | None,
              encoders: list[str], filters: list[str],
              muxers: list[str] | None = None) -> list[Check]:
    """Local capability preflight (PRD FR-5, optional level).

    ``muxers`` are the muxers the plan will ask FFmpeg to write with; each
    is checked against the probed ``-muxers`` set — availability is
    build-specific, so it is never assumed."""
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
    for name in muxers or []:
        ok = name in ff.muxers
        checks.append(Check(f"muxer {name}", ok, "available" if ok else "NOT available in this FFmpeg build"))
    return checks
