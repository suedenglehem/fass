"""Deterministic rule-based NLU adapter.

This is the offline development and testing stand-in for the LLM adapter
(command generation is testable without calling a model) and the fallback
when no LLM provider is configured. It returns the same
envelope and produces the same strict schema as the LLM adapter.

Ambiguity is an explicit result: the adapter returns
``ready``, ``needs_clarification``, or ``unsupported`` — never a silent
partial plan.
"""

from __future__ import annotations

import os
import re

from fass.probe.ffprobe import MediaInfo
from fass.registry import (
    default_codec,
    matrix_error,
    output_schemes,
    sample_rates,
    speed_limits,
    stream_output_info,
)
from fass.schema import (
    Asset,
    AssetKind,
    AudioMode,
    AudioReplaceOp,
    AudioResampleOp,
    Clarification,
    CropOp,
    EditPlan,
    GrayscaleOp,
    OutputSpec,
    ParseResult,
    ParseStatus,
    ResizeOp,
    RotateOp,
    SpeedOp,
    SubtitleOp,
    TrimSpec,
    UnsupportedPart,
    WatermarkOp,
)

SUPPORTED_SUMMARY = (
    "Supported: resize (e.g. '720p', '1280x720'), cropping to exact dimensions "
    "('crop to 1280x720'), trim ('cut from 00:10 to 00:30', 'the first 20 "
    "seconds'), speed changes ('make it 2x faster', 'half speed'), rotation "
    "('rotate 90 degrees'), container conversion (mp4, mov, mkv, webm, mpeg-ts), "
    "video encoding (H.264, H.265, VP9) at quality presets or a target bitrate "
    "(e.g. 'cbr 10 Mbps'), pixel format ('10-bit', 'yuv444p'), encoder profile "
    "('baseline profile') and speed preset ('ultrafast preset') for H.264, "
    "grayscale ('black and white'), one image watermark (position, margin, "
    "scale), subtitles from an srt/ass/ssa/vtt file ('add subtitles from "
    "subs.srt' — burned in by default, or soft/selectable on request), audio "
    "keep/remove, audio resampling ('resample audio to 44.1k mono'), and "
    "replacing the audio with another file ('replace the audio with "
    "music.mp3'). Inputs may also be remote streams (http(s)://, "
    "rtmp(s)://, rtsp(s)://, srt://, udp://, rtp://, tcp:// URLs), local "
    "capture sources (lavfi:, v4l2:, alsa:, x11grab: …), and the output may "
    "be a stream ('stream this to rtmp://host:1935/live', 'publish to "
    "rtsp://server:554/live', 'send it to udp://127.0.0.1:5004') or a local "
    "audio device ('play the audio to alsa:hw:0') — the destination's scheme "
    "fixes the output format."
)

# ---------------------------------------------------------------- time

_ASPECTS = {(16, 9), (4, 3), (9, 16), (21, 9), (2, 1), (1, 1), (3, 4), (16, 10)}

# un-named core, safe to repeat inside one pattern
_T = r"\d{1,2}:\d{2}(?::\d{2})?|\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"


def _to_seconds(s: str) -> float | None:
    s = s.strip().lower()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if m.group(3) is not None:
            return a * 3600 + b * 60 + int(m.group(3))  # HH:MM:SS
        # MM:SS — reject leading-zero forms of known aspect ratios ("16:09")
        if (a, b) in _ASPECTS:
            return None  # it is an aspect ratio, not a timecode
        return a * 60 + b
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)$", s)
    if m:
        v, u = float(m.group(1)), m.group(2)[0]
        return v * {"h": 3600, "m": 60, "s": 1}[u]
    return None


def _times_in(text: str) -> list[float]:
    out = []
    for m in re.finditer(rf"(?<![\d:])({_T})(?![\d:])", text, re.I):
        v = _to_seconds(m.group(1))
        if v is not None:
            out.append(v)
    return out


_TRIM_VERB = re.compile(r"\b(cut|trim|trims|trimming|clip|clips|chop|slice|keep|keeps|keeping)\b")
_RANGE = re.compile(
    r"(?:\b(?:seconds?|secs?|minutes?|mins?)\s+)?"
    r"(?P<a>\d{1,4}(?::\d{2})?(?:\.\d+)?)\s*(?:to|through|until|-|–)\s*"
    r"(?P<b>\d{1,4}(?::\d{2})?(?:\.\d+)?)\s*"
    r"(?P<u>seconds?|secs?|minutes?|mins?)?",
    re.I,
)


def _resolve_trim(t: str, input_duration: float | None):
    """Return (TrimSpec|None, note|None, clarification|None)."""
    # 1. "from A to B"
    m = re.search(rf"from\s+({_T})\s+(?:to|until|through|till)\s+({_T})", t, re.I)
    if m:
        a, b = _to_seconds(m.group(1)), _to_seconds(m.group(2))
        if a is not None and b is not None:
            if b <= a:
                return None, None, Clarification(
                    question=f"the end time ({b:g}s) is not after the start time ({a:g}s) — which did you mean?"
                )
            return TrimSpec(start_seconds=a, end_seconds=b), None, None

    # 2. "the first N" / "cut to N"
    m = re.search(rf"(?:the\s+)?first\s+({_T})", t, re.I)
    if m:
        v = _to_seconds(m.group(1))
        if v:
            return TrimSpec(start_seconds=0, end_seconds=v), \
                f"interpreted as keeping the first {v:g}s", None

    m = re.search(rf"\b(?:cut|trim|clip)\w*\s+(?:it\s+)?to\s+({_T})", t, re.I)
    if m:
        v = _to_seconds(m.group(1))
        if v:
            return TrimSpec(start_seconds=0, end_seconds=v), \
                f"interpreted as keeping the first {v:g}s", None

    # 3. "the last N"
    m = re.search(rf"last\s+({_T})", t, re.I)
    if m:
        v = _to_seconds(m.group(1))
        if v:
            if input_duration is not None and input_duration > v:
                return TrimSpec(start_seconds=round(input_duration - v, 3), end_seconds=input_duration), \
                    f"kept the last {v:g}s of the {input_duration:g}s input", None
            return None, None, Clarification(
                question=f"to keep the last {v:g}s I need the input duration — how long is the video, or which start time do you want?",
                hint="run `fass probe <file>` and pass the result, or give a start time",
            )

    # 4. range "A to B seconds" (with a trim verb)
    if _TRIM_VERB.search(t):
        m = _RANGE.search(t)
        if m:
            unit = m.group("u")
            a = _to_seconds(m.group("a") if not unit else f"{m.group('a')} {unit}")
            b = _to_seconds(m.group("b") if not unit else f"{m.group('b')} {unit}")
            # bare numbers in a range are seconds (the documented interpretation)
            if a is None and re.fullmatch(r"\d+(?:\.\d+)?", m.group("a")):
                a = float(m.group("a"))
            if b is None and re.fullmatch(r"\d+(?:\.\d+)?", m.group("b")):
                b = float(m.group("b"))
            if a is not None and b is not None and b > a:
                note = None
                if not unit and ":" not in m.group("a") and ":" not in m.group("b"):
                    note = "assumed seconds for the interval"
                return TrimSpec(start_seconds=a, end_seconds=b), note, None

    # 5. duration + start phrases
    m = re.search(rf"(?:for|length\s+of|next)\s+({_T})", t, re.I)
    duration = _to_seconds(m.group(1)) if m else None
    m = re.search(rf"(?:after|starting(?:\s+at)?|from|begin(?:ning)?(?:\s+at)?)\s+({_T})", t, re.I)
    start = _to_seconds(m.group(1)) if m else None

    if start is not None:
        if duration is not None:
            return TrimSpec(start_seconds=start, duration_seconds=duration), None, None
        times = [x for x in _times_in(t) if x > start]
        if times:
            end = times[0]
            if end > start:
                return TrimSpec(start_seconds=start, end_seconds=end), None, None
        return TrimSpec(start_seconds=start), "kept from the given start time to the end", None

    if duration is not None:
        return TrimSpec(start_seconds=0, duration_seconds=duration), \
            f"kept the first {duration:g}s", None

    # 6. bare "trim/cut N" -> keep the first N (documented interpretation)
    if _TRIM_VERB.search(t):
        times = _times_in(t)
        if len(times) == 2:
            a, b = sorted(times)
            if b > a:
                return TrimSpec(start_seconds=a, end_seconds=b), None, None
        if len(times) == 1:
            return TrimSpec(start_seconds=0, end_seconds=times[0]), \
                f"interpreted 'trim' as keeping the first {times[0]:g}s " \
                "(say 'starting at' to keep from a point to the end)", None

    return None, None, None


# ---------------------------------------------------------------- resize

_RESIZE_PHRASES = [
    ("full hd", 1080), ("fhd", 1080), ("4k", 2160), ("uhd", 2160), ("hd", 720), ("sd", 480),
]


def _detect_resize(t: str, exclude: list[tuple[int, int]] | None = None):
    """Return (ResizeOp|None, clarification|None).

    ``exclude`` lists (start, end) spans already claimed by another detector
    (crop dimensions) — a WxH inside one of them is not a resize target."""
    heights: list[tuple[int, int, int]] = []  # (value, span_start, span_end)
    for m in re.finditer(r"(?<![\d.])(\d{2,5})\s*p\b", t, re.I):
        heights.append((int(m.group(1)), m.start(), m.end()))
    taken: list[tuple[int, int]] = []
    for phrase, value in sorted(_RESIZE_PHRASES, key=lambda p: -len(p[0])):
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", t):
            if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            heights.append((value, m.start(), m.end()))

    wh = None
    for m in re.finditer(r"(?<!\d)(\d{2,5})\s*[x×]\s*(\d{2,5})(?!\d)", t):
        if not any(not (m.end() <= s or m.start() >= e) for s, e in (exclude or [])):
            wh = m
            break
    values = sorted({h for h, _, _ in heights})
    if len(values) > 1:
        return None, Clarification(
            question=f"you mentioned {len(values)} different heights ({', '.join(str(v) + 'p' for v in values)}) — which one?"
        )
    if wh:
        return ResizeOp(width=int(wh.group(1)), height=int(wh.group(2))), None
    if values:
        return ResizeOp(height=values[0]), None
    return None, None


# ---------------------------------------------------------------- crop

def _detect_crop(t: str):
    """Return (CropOp|None, clarification|None, unsupported_frag|None, spans).

    ``spans`` are the (start, end) regions of WxH text the crop claimed —
    resize must not treat them as its own target."""
    spans: list[tuple[int, int]] = []
    if not re.search(r"\bcrop(?:s|ping|ped)?\b", t):
        return None, None, None, spans
    m = re.search(r"(?<!\d)(\d{2,5})\s*[x×]\s*(\d{2,5})(?!\d)", t) \
        or re.search(r"(?<!\d)(\d{2,5})\s+by\s+(\d{2,5})(?!\d)", t)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if not (16 <= w <= 8192 and 16 <= h <= 8192):
            return None, None, f"crop to {w}x{h} (dimensions must be 16 to 8192)", spans
        spans.append((m.start(), m.end()))
        return CropOp(width=w, height=h), None, None, spans
    if re.search(r"(?<!\d)\d{1,2}\s*:\s*\d{1,2}(?!\d)", t) or re.search(r"\baspect\s+ratio\b", t):
        return None, Clarification(
            question="cropping to an aspect ratio (e.g. 16:9) isn't supported directly — "
                     "which exact dimensions (e.g. 'crop to 1280x720')?"
        ), None, spans
    return None, Clarification(
        question="crop to what dimensions? (e.g. 'crop to 1280x720')"
    ), None, spans


# ---------------------------------------------------------------- speed

_SPEED_NUM = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*(?:x|×)\b", re.I)
_SPEED_TIMES = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s+times\s+"
    r"(as\s+fast|as\s+quick|faster|slower|the\s+speed)",
    re.I,
)
_SPEED_WORDS = [
    (re.compile(r"twice\s+(?:as\s+fast|faster|the\s+speed)|at\s+twice\s+(?:the\s+)?speed"
                r"|double\s+(?:the\s+)?speed", re.I), 2.0),
    (re.compile(r"triple\s+(?:the\s+)?speed|three\s+times\s+(?:as\s+fast|faster|the\s+speed)",
                re.I), 3.0),
    (re.compile(r"half\s+(?:the\s+)?(?:speed|as\s+fast)", re.I), 0.5),
    (re.compile(r"quarter\s+(?:the\s+)?(?:speed|as\s+fast)", re.I), 0.25),
]
_SPEED_INTENT = re.compile(
    r"\bspeed(?:ing|up)?\b|\bfast(?:er)?\b|\bslow(?:er)?\b|time[\s-]?lapse|\bplayback\b",
    re.I,
)


def _detect_speed(t: str):
    """Return (SpeedOp|None, clarification|None, unsupported_frag|None).

    'N times slower' / 'Nx slower' invert the factor. A numeric factor with a
    size word after it ('2x bigger', '2x resolution') is a resize request,
    not a speed change."""
    factor: float | None = None
    m = _SPEED_TIMES.search(t)
    if m:
        factor = float(m.group(1))
        if m.group(2).lower().startswith("slower"):
            factor = 1.0 / factor
    if factor is None:
        m = _SPEED_NUM.search(t)
        if m:
            n = float(m.group(1))
            if re.search(rf"(?<![\d.]){re.escape(m.group(1))}\s*(?:x|×)\s*(?:times\s+)?slower\b", t, re.I):
                n = 1.0 / n
            elif re.search(rf"(?<![\d.]){re.escape(m.group(1))}\s*(?:x|×)\s*"
                           r"(?:bigger|larger|size|dimension|resolution|upscale|scale)\b", t, re.I):
                m = None  # a size request, not a speed change
        if m is not None:
            factor = n

    if factor is None:
        for pat, value in _SPEED_WORDS:
            if pat.search(t):
                factor = value
                break

    if factor is not None:
        lo, hi = speed_limits()
        if not (lo <= factor <= hi):
            return None, None, f"speed factor {factor:g}x (supported {lo:g}x to {hi:g}x)"
        return SpeedOp(factor=factor), None, None

    if _SPEED_INTENT.search(t):
        if _detect_preset(t) is not None:
            # "encode faster" — an encoding context makes it a speed *preset*,
            # not a playback change
            return None, None, None
        return None, Clarification(
            question="by what factor? (supported: 0.25x to 8x, e.g. 'make it 2x faster')"
        ), None
    return None, None, None


# ---------------------------------------------------------------- rotate

def _detect_rotate(t: str):
    """Return (RotateOp|None, clarification|None, unsupported_frag|None, note|None)."""
    m_rot = re.search(r"\brotat(?:e|ed|ing)\b", t)
    if not m_rot:
        return None, None, None, None
    window = t[m_rot.start():m_rot.start() + 40]
    clockwise = not re.search(r"counter[\s-]?clockwise|anti[\s-]?clockwise|anticlockwise", t)
    angle, explicit = None, False
    m = re.search(r"(\d{2,3})\s*(?:degrees?|deg\b|°)", window)
    if m:
        angle, explicit = int(m.group(1)), True
    else:
        m = re.search(r"(?<!\d)(\d{2,3})\b", window)
        if m and int(m.group(1)) in (90, 180, 270):
            angle, explicit = int(m.group(1)), True
    if angle is not None and angle not in (90, 180, 270):
        return None, None, \
            f"rotation by {angle}° (only quarter turns — 90, 180 or 270 — are supported)", None
    if angle is None:
        angle = 90
    note = None if explicit else "rotation angle defaulted to 90°"
    return RotateOp(angle=angle, clockwise=clockwise), None, None, note


# ---------------------------------------------------------------- audio resample

def _detect_resample(t: str):
    """Return (AudioResampleOp|None, clarification|None, unsupported_frag|None).

    'NNk' counts as a sample rate only with an audio context word; a
    5-6-digit 'NNNNN hz' or 'NN.N khz' is unambiguous on its own."""
    rate: int | None = None
    m = re.search(r"(?<![\d.])(\d{4,6})\s*(?:hz|hertz)\b", t, re.I)
    if m:
        rate = int(m.group(1))
    if rate is None:
        m = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:khz|k\s*hertz)\b", t, re.I)
        if m:
            rate = int(round(float(m.group(1)) * 1000))
    if rate is None:
        m = re.search(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*k\b", t, re.I)
        if m and re.search(r"\b(?:resampl\w*|sample\s*rate|sampling|audio|sound|mono|stereo)\b", t):
            rate = int(round(float(m.group(1)) * 1000))
    if rate is not None and rate not in sample_rates():
        return None, None, (
            f"sample rate {rate} Hz (supported: {', '.join(str(r) for r in sample_rates())})"
        )

    channels: int | None = None
    if re.search(r"\bmono(?:aural)?\b", t):
        channels = 1
    elif re.search(r"\bstereo\b", t):
        channels = 2
    elif re.search(r"\b5\.1\b|surround\s*(?:sound|audio)|audio\s+surround", t):
        channels = 6

    if rate is None and channels is None:
        if re.search(r"\bresampl\w*|sample\s*rate|sampling\b", t):
            return None, Clarification(
                question="resample the audio to what sample rate and/or channel count? "
                         f"(e.g. '44.1k stereo'; rates: {', '.join(str(r) for r in sample_rates())})"
            ), None
        return None, None, None
    return AudioResampleOp(sample_rate=rate, channels=channels), None, None


# ---------------------------------------------------------------- pixel format / profile / preset

def _detect_pixel_format(t: str) -> str | None:
    m = re.search(r"\b(yuv420p10le|yuv420p10|yuv444p|yuv420p)\b", t, re.I)
    if m:
        name = m.group(1).lower()
        return "yuv420p10le" if name in ("yuv420p10le", "yuv420p10") else name
    if re.search(r"\b10[\s-]?bit\b", t):
        return "yuv420p10le"
    if re.search(r"\b8[\s-]?bit\b", t):
        return "yuv420p"
    if re.search(r"\b4:4:4\b", t):
        return "yuv444p"
    if re.search(r"\b4:2:0\b", t):
        return "yuv420p"
    return None


def _detect_profile(t: str) -> str | None:
    m = re.search(r"\bprofile\s+(baseline|main|high)\b", t, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"\b(baseline|main|high)\s+profile\b", t, re.I)
    if m:
        return m.group(1).lower()
    if re.search(r"(?:max(?:imum)?|most)\s+compatib|\bfor\s+compatib", t, re.I):
        return "baseline"
    return None


def _detect_preset(t: str) -> str | None:
    presets = "|".join((
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow",
    ))
    m = re.search(rf"\bpreset\s+({presets})\b", t, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"\b(ultrafast|superfast|veryfast|veryslow)\b", t, re.I)
    if m:
        return m.group(1).lower()
    # 'fast/slow/…' only counts as a preset near an encoding context — on its
    # own it is a speed request ("make it faster")
    m = re.search(rf"(?:encod\w*|x264|render\w*)[^.;,]{{0,25}}\b({presets})\b", t, re.I)
    if m:
        return m.group(1).lower()
    return None


# ---------------------------------------------------------------- gpu

_GPU_ON = re.compile(r"\bgpu\b|\bnvenc\b|\bnvidia\b|\bhardware\s+accelerat\w*", re.I)
_GPU_OFF = re.compile(r"\bno\s+gpu\b|without\s+gpu\b|\bon\s+cpu\b|\bcpu\s+encod\w*\b|\bno\s+nvidia\b", re.I)


def _detect_gpu(t: str) -> bool | None:
    """'use the gpu' / 'nvenc' / 'nvidia' -> True; 'no gpu' / 'on cpu' ->
    False; nothing said -> None (the plan keeps its current value)."""
    if _GPU_OFF.search(t):
        return False
    if _GPU_ON.search(t):
        return True
    return None


# ---------------------------------------------------------------- codecs / containers

def _detect_codec(t: str) -> str | None:
    if re.search(r"h\.?\s?264|\bavc\b", t, re.I):
        return "h264"
    if re.search(r"h\.?\s?265|\bhevc\b", t, re.I):
        return "h265"
    if re.search(r"\bvp9\b|v-p9", t, re.I):
        return "vp9"
    return None


_EXT_TO_CONTAINER = {
    "mp4": "mp4", "m4v": "mp4", "mov": "mov", "qt": "mov",
    "mkv": "mkv", "webm": "webm",
    "ts": "mpegts", "mts": "mpegts", "m2ts": "mpegts",
}


def _detect_container(t: str) -> tuple[str | None, str | None]:
    """Return (container, unsupported_fragment)."""
    if re.search(r"\bquicktime\b", t, re.I):
        return "mov", None
    m = re.search(r"\b(mp4|mkv|webm|mov|m4v)\b", t, re.I)
    if m:
        return _EXT_TO_CONTAINER[m.group(1)], None
    if re.search(r"\bmpeg[-\s]?ts\b|\.ts\b|\bts\s+file\b|\btransport\s+stream\b|\bmts\b|\bm2ts\b", t, re.I):
        return "mpegts", None
    if re.search(r"\bavi\b", t, re.I):
        return None, "AVI container (not in the MVP container set)"
    return None, None


# ---------------------------------------------------------------- grayscale / audio / watermark

GRAYSCALE_RE = re.compile(r"black\s+and\s+white|\bb&w\b|gr(?:a|e)y\s*scale|desaturat\w*|monochrome", re.I)

_AUDIO_REMOVE = re.compile(
    r"\b(?:remove|strip|drop|delete)\s+(?:the\s+)?audio\b|\bno\s+audio\b|"
    r"without\s+(?:the\s+)?audio\b|\baudio\s+off\b|mute\s+everything",
    re.I,
)
_AUDIO_KEEP = re.compile(
    r"\bkeep(?:s|ing)?\s+(?:the\s+)?audio\b|\bwith\s+(?:the\s+)?audio\b|\bretain(?:s|ing)?\s+(?:the\s+)?audio\b",
    re.I,
)


def _detect_audio(t: str) -> str | None:
    if _AUDIO_REMOVE.search(t) and _AUDIO_KEEP.search(t):
        return "conflict"
    if _AUDIO_REMOVE.search(t):
        return "remove"
    if _AUDIO_KEEP.search(t):
        return "keep"
    return None


_AUDIO_REPLACE = re.compile(
    r"\b(?:replace|swap|change|set|switch|put)\s+(?:the\s+)?(?:audio|music|soundtrack|sound)\s+(?:with|to|from)"
    r"|\b(?:use|put|take|set)\s+.{0,40}?\b(?:as|for)\s+(?:the\s+)?(?:audio|music|soundtrack)\b"
    r"|\b(?:new|different|other|replacement)\s+(?:audio|music|soundtrack)\b"
    r"|\b(?:replace|swap)\s+(?:it|that)\s+(?:with|to|from)\b",
    re.I,
)


def _detect_audio_replace(t: str, assets: dict):
    """Return (AudioReplaceOp|None, clarification|None, note|None)."""
    if not _AUDIO_REPLACE.search(t):
        return None, None, None
    aid, note = _bind_asset(t, assets, AssetKind.AUDIO)
    if aid is None:
        return None, Clarification(
            question="which file should provide the new audio?",
            hint="bind an audio file (e.g. --asset music=song.mp3, or add it in the web UI) and refer to it in the request",
        ), None
    return AudioReplaceOp(asset_id=aid), None, note


_POSITIONS = [
    (re.compile(r"(?:top|upper)[\s-]+right", re.I), "top_right"),
    (re.compile(r"(?:top|upper)[\s-]+left", re.I), "top_left"),
    (re.compile(r"(?:bottom|lower)[\s-]+right", re.I), "bottom_right"),
    (re.compile(r"(?:bottom|lower)[\s-]+left", re.I), "bottom_left"),
    (re.compile(r"\bcenter(?:ed)?\b|\bmiddle\b", re.I), "center"),
]


def _detect_position(t: str) -> str | None:
    for rx, name in _POSITIONS:
        if rx.search(t):
            return name
    return None


def _bind_asset(t: str, assets: dict, kinds: AssetKind):
    """Match a mentioned filename/identifier to an asset; otherwise fall
    back to the sole asset of the requested kind."""
    for aid, asset in assets.items():
        if asset.kind != kinds:
            continue
        base = os.path.basename(asset.path)
        if (base.lower() in t or asset.path.lower() in t
                or (asset.label and asset.label.lower() in t) or aid.lower() in t):
            return aid, None
    wanted = [aid for aid, a in assets.items() if a.kind == kinds]
    if len(wanted) == 1:
        return wanted[0], f"bound the only {kinds.value} asset to the request"
    return None, None


_WMARK = re.compile(r"\bwatermark\w*|\blogo\w*|\bbranding\b", re.I)


def _detect_watermark(t: str, assets: dict):
    """Return (WatermarkOp|None, clarification|None, note|None)."""
    if not _WMARK.search(t):
        return None, None, None
    notes: list[str] = []
    position = _detect_position(t)
    m_margin = re.search(r"(?:margin\s+of\s+|with\s+)?(\d{1,4})\s*(?:px|pixels?|pixel)\b", t, re.I)
    margin = int(m_margin.group(1)) if m_margin else 16
    m_pct = re.search(r"(\d{1,3})\s*%", t)
    scale = int(m_pct.group(1)) if m_pct else 100
    if not m_pct and re.search(r"\bhalf[\s-]?siz(?:e|ed)\b|\bsmall\s+logo\b", t, re.I):
        scale = 50

    aid, note = _bind_asset(t, assets, AssetKind.IMAGE)
    if note:
        notes.append(note)
    if aid is None:
        return None, Clarification(
            question="which file should be used as the watermark?",
            hint="provide it (e.g. --watermark logo.png) and refer to it in the request",
        ), None

    defaults_used = []
    if position is None:
        defaults_used.append("position bottom-right")
    if not m_margin and margin == 16:
        defaults_used.append("margin 16px")
    if not m_pct and scale == 100:
        defaults_used.append("original image size")
    if defaults_used:
        notes.append("watermark defaults: " + ", ".join(defaults_used))

    op = WatermarkOp(
        asset_id=aid,
        position=position or "bottom_right",
        margin_px=margin,
        scale_percent=scale,
    )
    return op, None, "; ".join(notes) or None


# ---------------------------------------------------------------- subtitles

# A bare .srt/.ass/.ssa/.vtt mention is handled by asset binding below; the
# word triggers keep the detector honest about what the user asked for.
_SUBTITLES_RE = re.compile(
    r"\bsub(?:s|titles)?\b|\bcaptions?\b|\bburn\s+(?:in|the)\s+sub\w*", re.I)
# "soften the audio" must NOT count as a soft-subtitle request (it is the
# deferred volume pattern) — soft mode needs the subtitle word next to
# 'soft', on either side: "soft subtitles" or "the subtitles ... soft".
_SUB_SOFT_RE = re.compile(
    r"\bsoft\s+(?:subs|subtitles|captions)\b"
    r"|\b(?:subs|subtitles|captions)\s+(?:should\s+be\s+|as\s+)?soft\b"
    r"|\bselectable\b|\bembed\w*\b", re.I)
_SUB_BURN_RE = re.compile(
    r"\bburn\w*\b|\bhard\s*cod\w*\b|\bhard\s+sub\w*\b|\bhardsub\w*\b", re.I)
_SUB_NEG_RE = re.compile(
    r"\b(?:no|without|drop|remove|strip|get\s+rid\s+of)\s+(?:the\s+)?(?:subtitles?|captions?|subs)\b",
    re.I)


def _detect_subtitles(t: str, assets: dict):
    """Return (SubtitleOp|None, clarification|None, note|None).

    Burn (hard-coded) is the default; soft/selectable only on explicit
    wording. "remove the subtitles" is a negation — the input's own subtitle
    streams are never carried over by default, so there is nothing to do on
    a fresh plan."""
    if not _SUBTITLES_RE.search(t):
        return None, None, None
    if _SUB_NEG_RE.search(t):
        return None, None, None

    if _SUB_SOFT_RE.search(t):
        mode = "soft"
    elif _SUB_BURN_RE.search(t):
        mode = "burn"
    else:
        mode = "burn"

    aid, note = _bind_asset(t, assets, AssetKind.SUBTITLES)
    notes = [note] if note else []
    if aid is None:
        return None, Clarification(
            question="which file should provide the subtitles?",
            hint="provide it (e.g. --asset subs.srt) and refer to it in the request",
        ), None
    if mode == "burn" and not _SUB_BURN_RE.search(t):
        notes.append(
            "subtitle mode: burned into the video (default — say 'soft' or "
            "'selectable' for a separate track)"
        )
    op = SubtitleOp(asset_id=aid, mode=mode)
    return op, None, "; ".join(notes) or None


# ---------------------------------------------------------------- unsupported

UNSUPPORTED_PATTERNS: list[tuple[re.Pattern, str]] = [
    # speed, rotation and cropping are now supported — their detectors run in
    # parse() before this scan, so only what the detectors cannot resolve
    # (e.g. an out-of-range factor) reaches the user, with its own message
    (re.compile(r"\bflip(?:ping)?\b|\bmirror(?:ing)?\b", re.I), "flipping and mirroring"),
    (re.compile(r"\bvolume\b|\blouder\b|\bquieter\b|\bsoften\w*\b|\bamplif\w*\b|\bloudness\b|\bnormali[sz]e\s+audio\b", re.I), "audio level changes"),
    # Adding subtitles IS supported (M5) — only extracting them to a
    # separate file is not. (An srt:// URL is a stream scheme, M3b — no
    # pattern here may eat it.)
    (re.compile(r"\bextract\s+(?:the\s+)?(?:subtitles?|captions?)\b", re.I), "extracting subtitles to a separate file"),
    (re.compile(r"\bconcat(?:enate)?\b|\bmerge\w*\b|\bstitch\w*\b|\bappend\w*\b", re.I), "concatenation of multiple files"),
    (re.compile(r"\bsplit(?:ting)?\b", re.I), "splitting into segments"),
    (re.compile(r"\bblur(?:ring)?\b|\bsepia\b|\bvintage\b|\bretro\b|\bfade(?:s|d|ing)?\b", re.I), "additional color effects"),
    (re.compile(r"\bsharpen\w*\b|\bdenois\w*\b|\bstabil(?:is|iz)\w*\b", re.I), "stabilization, sharpening, denoising"),
    (re.compile(r"add\s+(?:some\s+|the\s+|a\s+)?text\b|\btext\s+overlay\b|\btitle\s+card\b|\btimestamp\b", re.I), "text overlays"),
    (re.compile(r"\bhdr\b|\btone[\s-]?map\w*\b", re.I), "HDR workflows"),
    # gpu/nvenc/hardware acceleration are supported (M6, NVIDIA nvenc);
    # the other hardware-encoding stacks stay out
    (re.compile(r"\bcuda\b|\bvideotoolbox\b|\bqsv\b", re.I), "CUDA / VideoToolbox / QSV hardware acceleration"),
    (re.compile(r"\bextract\s+(?:the\s+)?audio\b", re.I), "extracting audio to a separate file"),
]


def _scan_unsupported(t: str) -> list[UnsupportedPart]:
    out = []
    for rx, label in UNSUPPORTED_PATTERNS:
        m = rx.search(t)
        if m:
            out.append(
                UnsupportedPart(
                    fragment=m.group(0),
                    reason=f"'{label}' is not in the MVP support set (deferred per PRD section 5)",
                )
            )
    return out


# ---------------------------------------------------------------- ambiguous

# Exact file-size targets (byte units only — bit units are bitrates, which
# the MVP *can* honor; see _detect_bitrate below).
_SIZE_TARGET = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|kilobytes?|megabytes?|gigabytes?)\b",
    re.I,
)
_BITRATE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(mbps|kbps|mbit(?:s?)?|kbit(?:s?)?|megabits?|kilobits?)\b",
    re.I,
)


def _detect_bitrate(t: str) -> int | None:
    """Target video bitrate in kbps, when the instruction names one
    ('cbr 10 Mbps', '5 mbps', '2000 kbps')."""
    m = _BITRATE.search(t)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    kbps = int(round(value * 1000)) if unit.startswith("m") else int(round(value))
    if not (100 <= kbps <= 200000):
        return None
    return kbps


# ---------------------------------------------------------------- stream destination (M3b)

_STREAM_DEST = re.compile(
    r"\b((" + "|".join(output_schemes()) + r")://[^\s]+)"
)
_TRAILING_PUNCT = re.compile(r"[.,;:!?'\"()\]]+$")


def _detect_destination(t: str, assets: dict) -> list[str]:
    """Stream output destinations named in the instruction.

    A URL that matches a bound asset's path is an *input* reference, not a
    destination, so it is excluded here. Trailing sentence punctuation is
    stripped; duplicates collapse to the first occurrence.
    """
    found: list[str] = []
    for m in _STREAM_DEST.finditer(t):
        url = _TRAILING_PUNCT.sub("", m.group(1))
        if not url:
            continue
        if any(a.path.lower() == url.lower() for a in assets.values()):
            continue
        if url not in found:
            found.append(url)
    return found
_VAGUE_SIZE = re.compile(
    r"\b(smaller|small|shrink|shrunk|bigger|enlarge|larger|compress(?:ion|ed)?|lighter)\b", re.I
)
_LOSSLESS = re.compile(r"\blossless\b|without\s+(?:any\s+)?(?:quality\s+)?loss\b", re.I)


def _scan_ambiguous(t: str, resize: ResizeOp | None) -> Clarification | None:
    """Focused questions for requests whose intent cannot be safely
    resolved (PRD clarification policy)."""
    if _LOSSLESS.search(t):
        return Clarification(
            question=(
                "true lossless encoding (e.g. ProRes) is not in the MVP presets; 'visually "
                "lossless' is usually just high-quality H.264. Which did you mean?"
            )
        )
    if _SIZE_TARGET.search(t):
        return Clarification(
            question=(
                "targeting an exact file size needs bitrate estimation (possibly two-pass "
                "encoding), which the MVP does not do. Would you prefer a quality preset "
                "(e.g. default H.264) or a smaller height (e.g. 720p) instead?"
            )
        )
    if resize is None and _VAGUE_SIZE.search(t):
        return Clarification(
            question="do you mean smaller video dimensions (e.g. 720p) or a smaller file (target size)?",
            hint=SUPPORTED_SUMMARY,
        )
    return None


# ---------------------------------------------------------------- adapter

class RulesAdapter:
    name = "rules"

    def parse(self, instruction, assets, previous_plan=None, media=None) -> ParseResult:
        t = re.sub(r"\s+", " ", (instruction or "").strip().lower())
        if not t:
            return ParseResult(
                status=ParseStatus.NEEDS_CLARIFICATION,
                clarifications=[Clarification(question="what edit would you like?")],
            )

        # 1. unsupported fragments — detected, never silently dropped
        unsupported = _scan_unsupported(t)
        if unsupported:
            return ParseResult(status=ParseStatus.UNSUPPORTED, unsupported=unsupported)

        # 2. detect
        trim, trim_note, trim_clar = _resolve_trim(t, media.duration if media else None)
        crop, crop_clar, crop_frag, crop_spans = _detect_crop(t)
        resize, resize_clar = _detect_resize(t, exclude=crop_spans)
        speed, speed_clar, speed_frag = _detect_speed(t)
        rotate, rotate_clar, rotate_frag, rotate_note = _detect_rotate(t)
        audio_resample, rs_clar, rs_frag = _detect_resample(t)
        pixel_format = _detect_pixel_format(t)
        video_profile = _detect_profile(t)
        video_preset = _detect_preset(t)
        gpu = _detect_gpu(t)
        audio = _detect_audio(t)
        watermark, wm_clar, wm_note = _detect_watermark(t, assets)
        audio_replace, ar_clar, ar_note = _detect_audio_replace(t, assets)
        subtitle, sub_clar, sub_note = _detect_subtitles(t, assets)
        if audio_replace is not None and audio in ("remove", "conflict"):
            audio = None  # a replacement wins over remove / keep+remove
        grayscale = GRAYSCALE_RE.search(t) is not None
        codec = _detect_codec(t)
        container, unsupported_container = _detect_container(t)
        bitrate_kbps = _detect_bitrate(t)
        if unsupported_container:
            unsupported.append(UnsupportedPart(fragment=unsupported_container,
                                                reason="not in the MVP support set"))
            return ParseResult(status=ParseStatus.UNSUPPORTED, unsupported=unsupported)
        for frag in (crop_frag, speed_frag, rotate_frag, rs_frag):
            if frag is not None:
                unsupported.append(UnsupportedPart(fragment=frag,
                                                   reason="outside the MVP support set"))
                return ParseResult(status=ParseStatus.UNSUPPORTED, unsupported=unsupported)

        # 2b. stream output destination (M3b) — its scheme fixes the container
        dests = _detect_destination(t, assets)
        if len(dests) > 1:
            return ParseResult(
                status=ParseStatus.NEEDS_CLARIFICATION,
                clarifications=[Clarification(
                    question=(f"you named {len(dests)} stream destinations "
                              f"({'; '.join(dests)}) — which one should the output go to?")),
                ],
            )
        dest = dests[0] if dests else None
        dest_container = stream_output_info(dest)[0] if dest else None
        if dest is not None and container is not None and container != dest_container:
            return ParseResult(
                status=ParseStatus.NEEDS_CLARIFICATION,
                clarifications=[Clarification(
                    question=(f"you named a {container.upper()} output and the destination "
                              f"{dest}, which carries {dest_container.upper()} — "
                              "which is the intended output?")),
                ],
                defaults=[trim_note] if trim_note else [],
            )

        ambiguous = _scan_ambiguous(t, resize)

        # 3. revision: keep the previous watermark's image when the follow-up
        #    only repositions it (the new text may not name the file again)
        if previous_plan is not None and wm_clar is not None and _WMARK.search(t):
            prev_wm = next((o for o in previous_plan.operations if o.type == "watermark"), None)
            if prev_wm is not None:
                position = _detect_position(t)
                watermark = WatermarkOp(
                    asset_id=prev_wm.asset_id,
                    position=position or prev_wm.position,
                    margin_px=prev_wm.margin_px,
                    scale_percent=prev_wm.scale_percent,
                )
                wm_clar = None
                wm_note = "watermark: kept the previous image"

        # 3b. revision: keep the previous subtitle file when the follow-up
        #    re-mentions subtitles without naming a new file
        if previous_plan is not None and sub_clar is not None and _SUBTITLES_RE.search(t):
            prev_sub = next(
                (o for o in previous_plan.operations if o.type == "subtitles"), None)
            if prev_sub is not None:
                if _SUB_SOFT_RE.search(t):
                    sub_mode = "soft"
                elif _SUB_BURN_RE.search(t):
                    sub_mode = "burn"
                else:
                    sub_mode = prev_sub.mode
                subtitle = SubtitleOp(asset_id=prev_sub.asset_id, mode=sub_mode)
                sub_clar = None
                sub_note = "subtitles: kept the previous file"

        # 4. explicit clarifications win (focused, one at a time)
        for c in (trim_clar, resize_clar, crop_clar, speed_clar, rotate_clar,
                  rs_clar, wm_clar, ar_clar, sub_clar, ambiguous):
            if c is not None:
                return ParseResult(
                    status=ParseStatus.NEEDS_CLARIFICATION,
                    clarifications=[c],
                    defaults=[trim_note] if trim_note else [],
                )
        if audio == "conflict":
            return ParseResult(
                status=ParseStatus.NEEDS_CLARIFICATION,
                clarifications=[Clarification(
                    question="you asked to both keep and remove the audio — which takes priority?")],
            )

        # 5. build / merge the plan
        if previous_plan is not None:
            plan, changed = _merge_revision(
                previous_plan, t, trim, resize, audio, watermark, grayscale,
                audio_replace, dest=dest, crop=crop, rotate=rotate, speed=speed,
                audio_resample=audio_resample, subtitle=subtitle, gpu=gpu,
            )
            if not changed:
                return ParseResult(
                    status=ParseStatus.NEEDS_CLARIFICATION,
                    clarifications=[Clarification(
                        question="which part of the previous plan should change?")],
                )
        else:
            plan = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
            plan.trim = trim
            ops = []
            if resize is not None:
                ops.append(resize)
            if crop is not None:
                ops.append(crop)
            if rotate is not None:
                ops.append(rotate)
            if grayscale:
                ops.append(GrayscaleOp())
            if watermark is not None:
                ops.append(watermark)
            if speed is not None:
                ops.append(speed)
            if subtitle is not None:
                # last among the video ops, so a burned filter runs on the
                # final dimensions (after resize/crop/rotate/speed)
                ops.append(subtitle)
            if audio_replace is not None:
                ops.append(audio_replace)
            if audio_resample is not None:
                ops.append(audio_resample)
            plan.operations = ops
            if audio == "remove":
                plan.output.audio_mode = AudioMode.REMOVE
            if dest is not None:
                plan.output_destination = dest

        notes: list[str] = []
        if trim_note:
            notes.append(trim_note)
        if rotate_note:
            notes.append(rotate_note)
        if wm_note:
            notes.append(wm_note)
        if ar_note:
            notes.append(ar_note)
        if sub_note:
            notes.append(sub_note)

        # 6. resolve output defaults — visible, never hidden
        # (gpu alone is not an edit — it only matters when an encode
        # happens, so it is deliberately NOT part of the no-op check below)
        if previous_plan is None:
            if trim is None and resize is None and crop is None and speed is None \
                    and rotate is None and audio_resample is None and watermark is None \
                    and not grayscale and pixel_format is None and video_profile is None \
                    and video_preset is None and codec is None and container is None \
                    and audio != "remove" and audio_replace is None and subtitle is None \
                    and bitrate_kbps is None and dest is None \
                    and not re.search(r"\bconvert\w*|\btranscod\w*|\bexport\w*|\bsave\w*|\brender\w*|\bturn\b", t):
                return ParseResult(
                    status=ParseStatus.NEEDS_CLARIFICATION,
                    clarifications=[Clarification(question="which edit would you like?",
                                                  hint=SUPPORTED_SUMMARY)],
                    defaults=notes,
                )
            main_path = assets.get(plan.input_id, Asset(path="input.mp4")).path
            ext = os.path.splitext(main_path)[1].lstrip(".").lower()
            if container is None:
                if dest is not None:
                    # the stream destination's scheme fixes the container
                    container = dest_container
                    notes.append(f"container set to {container} by the stream destination {dest}")
                elif ext in _EXT_TO_CONTAINER:
                    container = _EXT_TO_CONTAINER[ext]
                    notes.append(f"container defaulted to {container} (from the input filename)")
                else:
                    container = "mp4"
                    notes.append("container defaulted to mp4")
            if codec is None:
                codec = default_codec(container)
                notes.append(f"video codec defaulted to {codec} (the {container.upper()} preset default)")
            plan.output.container = container
            plan.output.video_codec = codec
            if pixel_format is not None:
                plan.output.pixel_format = pixel_format
                notes.append(f"output pixel format set to {pixel_format}")
            if video_profile is not None:
                plan.output.video_profile = video_profile
                notes.append(f"encoder profile set to {video_profile}")
            if video_preset is not None:
                plan.output.video_preset = video_preset
                notes.append(f"encoding speed preset set to {video_preset}")
            if bitrate_kbps is not None:
                plan.output.video_bitrate_kbps = bitrate_kbps
                notes.append(
                    f"targeting a {bitrate_kbps / 1000:g} Mbps video bitrate (CBR-style target)"
                )
            if gpu:
                plan.output.gpu = True
                notes.append("encoding on GPU (NVIDIA nvenc)")
        else:
            # revision: keep previously resolved container/codec unless the
            # instruction changed them (already applied in _merge_revision)
            container = plan.output.container
            codec = plan.output.video_codec

        matrix_err = matrix_error(container, codec)
        if matrix_err:
            return ParseResult(
                status=ParseStatus.UNSUPPORTED,
                unsupported=[UnsupportedPart(fragment=f"{codec} in {container.upper()}",
                                              reason=matrix_err)],
            )

        return ParseResult(status=ParseStatus.READY, plan=plan, defaults=notes)


# ---------------------------------------------------------------- revision

def _merge_revision(previous: EditPlan, t: str, trim, resize, audio, watermark, grayscale,
                    audio_replace=None, dest=None, crop=None, rotate=None, speed=None,
                    audio_resample=None, subtitle=None, gpu=None):
    """Apply a follow-up instruction to a previous plan, preserving
    everything the instruction does not mention (FR-6)."""
    plan = previous.model_copy(deep=True)
    changed: list[str] = []

    if gpu is not None and plan.output.gpu != gpu:
        plan.output.gpu = gpu
        changed.append("gpu -> on (nvenc)" if gpu else "gpu -> off (cpu)")
    codec = _detect_codec(t)
    if codec and plan.output.video_codec != codec:
        plan.output.video_codec = codec
        changed.append(f"video codec -> {codec}")
    container, _ = _detect_container(t)
    if container and plan.output.container != container:
        plan.output.container = container
        changed.append(f"container -> {container}")
    bitrate = _detect_bitrate(t)
    if bitrate is not None and plan.output.video_bitrate_kbps != bitrate:
        plan.output.video_bitrate_kbps = bitrate
        changed.append(f"video bitrate -> {bitrate} kbps")
    pixel_format = _detect_pixel_format(t)
    if pixel_format and plan.output.pixel_format != pixel_format:
        plan.output.pixel_format = pixel_format
        changed.append(f"pixel format -> {pixel_format}")
    profile = _detect_profile(t)
    if profile and plan.output.video_profile != profile:
        plan.output.video_profile = profile
        changed.append(f"video profile -> {profile}")
    preset = _detect_preset(t)
    if preset and plan.output.video_preset != preset:
        plan.output.video_preset = preset
        changed.append(f"video preset -> {preset}")

    # A newly named destination always wins over the previous one; the scheme
    # fixes the container, so switch it when they disagree.
    if dest is not None and plan.output_destination != dest:
        plan.output_destination = dest
        changed.append(f"stream destination -> {dest}")
        d_container = stream_output_info(dest)[0]
        if plan.output.container != d_container:
            plan.output.container = d_container
            changed.append(f"container -> {d_container} (required by the {dest} destination)")

    if audio == "keep" and plan.output.audio_mode != AudioMode.ENCODE:
        plan.output.audio_mode = AudioMode.ENCODE
        changed.append("audio -> keep")
    elif audio == "remove" and plan.output.audio_mode != AudioMode.REMOVE:
        plan.output.audio_mode = AudioMode.REMOVE
        changed.append("audio -> remove")

    if audio_replace is not None:
        # a replacement replaces any prior replacement and implies encoded audio
        plan.operations = [o for o in plan.operations if o.type != "audio_replace"]
        plan.operations.append(audio_replace)
        if plan.output.audio_mode != AudioMode.ENCODE:
            plan.output.audio_mode = AudioMode.ENCODE
        changed.append(f"audio -> replaced ({audio_replace.asset_id})")
    elif audio == "remove" and any(o.type == "audio_replace" for o in plan.operations):
        plan.operations = [o for o in plan.operations if o.type != "audio_replace"]
        changed.append("audio replacement removed")

    if re.search(r"\b(?:no|without|drop|remove|get\s+rid\s+of)\s+(?:the\s+)?watermark\b", t, re.I):
        plan.operations = [o for o in plan.operations if o.type != "watermark"]
        changed.append("watermark removed")
        watermark = None  # negation wins: do not re-add the detected op
    if re.search(r"\b(?:no|without|drop|remove|get\s+rid\s+of)\s+(?:the\s+)?(?:trim|cut)\b|keep\s+the\s+whole\s+video|don'?t\s+cut", t, re.I):
        plan.trim = None
        changed.append("trim removed")
    if re.search(r"remove\s+(?:the\s+)?grayscale|back\s+to\s+color|in\s+color\s+again|keep\s+the\s+color", t, re.I):
        plan.operations = [o for o in plan.operations if o.type != "grayscale"]
        changed.append("grayscale removed")
    if _SUB_NEG_RE.search(t):
        plan.operations = [o for o in plan.operations if o.type != "subtitles"]
        changed.append("subtitles removed")

    if trim is not None and plan.trim != trim:
        plan.trim = trim
        changed.append("trim updated")
    if resize is not None:
        plan.operations = [o for o in plan.operations if o.type != "resize"]
        plan.operations.insert(0, resize)
        changed.append("resize updated")
    # crop and rotate go right after resize (before watermark/grayscale), so
    # the filter order matches a fresh plan
    base = 1 if any(o.type == "resize" for o in plan.operations) else 0
    for op in (crop, rotate):
        if op is not None:
            plan.operations = [o for o in plan.operations if o.type != op.type]
            plan.operations.insert(base, op)
            base += 1
            changed.append(f"{op.type} updated")
    for op in (speed, audio_resample):
        if op is not None:
            plan.operations = [o for o in plan.operations if o.type != op.type]
            plan.operations.append(op)
            changed.append(f"{op.type} updated")
    if watermark is not None:
        plan.operations = [o for o in plan.operations if o.type != "watermark"]
        plan.operations.append(watermark)
        changed.append("watermark updated")
    if grayscale and not any(o.type == "grayscale" for o in plan.operations):
        plan.operations.append(GrayscaleOp())
        changed.append("grayscale added")
    if subtitle is not None:
        # a new subtitle request (possibly the previous file with a new
        # mode) replaces any prior subtitle op; the negation above already
        # removed it when "remove the subtitles" was said
        plan.operations = [o for o in plan.operations if o.type != "subtitles"]
        plan.operations.append(subtitle)
        changed.append(f"subtitles updated ({subtitle.mode})")

    return plan, changed
