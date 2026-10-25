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
    REGISTRY,
    all_filter_names,
    audio_codec_for,
    bsf_specs,
    audio_codec_option_keys,
    audio_codecs_for,
    audio_container_for_codec,
    audio_for,
    capability_errors,
    codec_option_specs,
    default_codec,
    filter_expr_problems,
    filter_names,
    filter_spec,
    filtergraph_problems,
    format_option_errors,
    format_option_specs,
    output_schemes,
    sample_rates,
    speed_limits,
    stream_output_info,
    video_codec_option_keys,
    video_codecs_for,
    video_encoder_name,
)
from fass.schema import (
    Asset,
    AssetKind,
    AudioMode,
    AudioReplaceOp,
    AudioResampleOp,
    BitstreamFilterSpec,
    Clarification,
    CropOp,
    EditPlan,
    FilterOp,
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
    "Supported: resize (e.g. '720p', '1280x720'), cropping to exact "
    "dimensions ('crop to 1280x720'), trim ('cut from 00:10 to 00:30', "
    "'the first 20 seconds'), speed changes ('make it 2x faster', 'half "
    "speed'), rotation ('rotate 90 degrees'), grayscale, flipping/mirroring "
    "('flip horizontally'), blur, sharpening, fading in/out, a text overlay "
    "('add the text \"hello\"') and named FFmpeg filters ('use the loudnorm "
    "filter', 'apply eq=contrast=1.2', or a full filtergraph: 'use the "
    "graph [0:v]split=2[a][b]'); audio keep/remove, resampling ('resample "
    "audio to 44.1k mono'), replacing with another file ('replace the audio "
    "with music.mp3'), volume ('set the volume to 200%', 'louder', "
    "'quieter'), loudness normalization, echo, audio fades — and "
    "audio-only output ('extract the audio to mp3', 'make it a wav'); "
    "dumping the input's subtitles to a file ('dump the subtitles to vtt'); "
    "container conversion to any supported container (mp4, mov, mkv, webm, "
    "mpeg-ts, flv, hls, avi, mxf, vob, asf, 3gp, dv, gif, webp, mp3, mp2, "
    "m4a, aac, ogg, flac, wav, aiff, mka, opus); video encoding (H.264, "
    "H.265, VP9, ProRes, MPEG-2, MPEG-4, MJPEG) at quality presets or a "
    "target bitrate (video: 'cbr 10 Mbps'; audio: 'mp2 128k'), pixel "
    "format, encoder profile (H.264 baseline/high, ProRes 422/4444) and "
    "speed preset for H.264, Quick Sync encoding ('re-encode with quick "
    "sync', 'qsv preset=fast'), and per-encoder private options ('with crf "
    "18', 'x264-params keyint=24', 'dialnorm -27', 'vbr 4'); container "
    "format options ('faststart', "
    "'hls_time=6', 'mpegts_flags=resend_headers'); bitstream filters on the "
    "compressed stream without re-encoding ('apply the h264_metadata filter "
    "level=4.1', 'remux with trace_headers'), and stream-copy trim ('copy "
    "trim from 00:10 to 00:30' — no re-encode, video keyframe-accurate); one "
    "image watermark "
    "(position, margin, scale), subtitles from an srt/ass/ssa/vtt file "
    "('add subtitles from subs.srt' — burned in by default, or soft/"
    "selectable on request). Inputs may also be remote streams (http(s)://, "
    "rtmp(s)://, rtsp(s)://, srt://, udp://, rtp://, tcp:// URLs), local "
    "capture sources (lavfi:, v4l2:, alsa:, x11grab: …), and the output may "
    "be a stream ('stream this to rtmp://host:1935/live', 'publish to "
    "https://host/out.m3u8', 'send it to udp://127.0.0.1:5004') or a local "
    "audio device ('play the audio to alsa:hw:0') — the destination's "
    "scheme fixes the output format."
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
        # M10.1: an arbitrary angle is the real rotate filter — the phrase
        # detector in _detect_filters claims it; the quarter-turn RotateOp
        # (transpose) does not apply
        return None, None, None, None
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
    # ProRes family: 'prores 422' -> standard, 'pro res 4444 xq' -> 4444xq …
    m = re.search(
        r"\b(?:prores|pro[-\s]?res)\s+(?:profile\s+)?(proxy|lt|standard|hq|"
        r"422|4444(?:[\s-]?xq)?)\b", t, re.I)
    if m:
        raw = re.sub(r"[\s-]+", " ", m.group(1).lower())
        if raw == "4444 xq":
            return "4444xq"
        if raw == "422":
            return "standard"
        return raw
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


_QSV_ON = re.compile(r"\bqsv\b|\bquick\s?sync\b|\bintel\s+gpu\b|\bonevpl\b", re.I)
_QSV_OFF = re.compile(r"\bno\s+qsv\b|\bwithout\s+qsv\b|\bno\s+quick\s?sync\b", re.I)


def _detect_qsv(t: str) -> bool | None:
    """'use qsv' / 'quick sync' / 'intel gpu' -> True; 'no qsv' -> False;
    nothing said -> None (the plan keeps its current value)."""
    if _QSV_OFF.search(t):
        return False
    if _QSV_ON.search(t):
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
    if re.search(r"\bprores\b|\bpro[-\s]res\b", t, re.I):
        return "prores"
    if re.search(r"\bm[-\s]?jpeg\b|\bmjpeg\b", t, re.I):
        return "mjpeg"
    if re.search(r"\bmpeg[-\s]?2\b", t, re.I):
        return "mpeg2"
    if re.search(r"\bmpeg[-\s]?4\b", t, re.I):
        return "mpeg4"
    return None


# Every extension in the registry maps to its container — the matrix, not
# this adapter, decides what is supported.
_EXT_TO_CONTAINER = {
    ext.lower(): name
    for name, meta in REGISTRY["containers"].items()
    for ext in meta["extensions"]
}
# both the srt:// stream container and the srt_file dump claim '.srt' —
# for a file path that extension is a subtitle dump, never the protocol
_EXT_TO_CONTAINER["srt"] = "srt_file"

# Words that name a video-capable container. A leading dot/slash/word char
# excludes matches inside a filename ('clip.mp4' is the input, not the
# target); 'save as .ts' is caught by the dedicated '.ts' alternative.
_VIDEO_CONTAINER_RX = re.compile(
    r"(?<![\w./-])(mpeg[-\s]?ts|transport\s+stream|mpeg2?\s+ts|m2ts|mts|quicktime|"
    r"hls|avi|mxf|vob|flv|asf|wmv|3gp|gif|webp|webm|mkv|mov|m4v|mp4|dv)\b"
    r"|\.ts\b|\bts\s+file\b",
    re.I,
)
_WORD_TO_VIDEO_CONTAINER = {
    "mpegts": "mpegts", "mpeg ts": "mpegts", "mpeg2 ts": "mpegts",
    "transport stream": "mpegts", "m2ts": "mpegts", "mts": "mpegts",
    "quicktime": "mov", "hls": "hls", "avi": "avi", "mxf": "mxf",
    "vob": "vob", "flv": "flv", "asf": "asf", "wmv": "asf", "3gp": "3gp",
    "gif": "gif", "webp": "webp", "webm": "webm", "mkv": "mkv",
    "mov": "mov", "m4v": "mp4", "mp4": "mp4", "dv": "dv",
}


def _detect_container(t: str) -> tuple[str | None, str | None]:
    """Return (container, unsupported_fragment) — video-capable containers."""
    m = _VIDEO_CONTAINER_RX.search(t)
    if not m:
        return None, None
    word = m.group(1)
    if word is None:  # '.ts' / 'ts file'
        return "mpegts", None
    return _WORD_TO_VIDEO_CONTAINER[word.lower().replace("-", " ")], None


# Audio-only containers: the codec IS the format. Same filename guard as
# above — 'replace the audio with song.mp3' names an input file, not a
# target format.
_AUDIO_CONTAINER_RX = re.compile(
    r"(?<![\w./-])(mp3|mp2|m4a|aac|ogg|oga|flac|wave|wav|aiff|aif|opus)\b",
    re.I,
)
_WORD_TO_AUDIO_CONTAINER = {
    "mp3": "mp3", "mp2": "mp2", "m4a": "m4a", "aac": "aac",
    "ogg": "ogg", "oga": "ogg", "flac": "flac", "wave": "wav",
    "wav": "wav", "aiff": "aiff", "aif": "aiff", "opus": "opus",
}


def _detect_audio_container(t: str) -> str | None:
    m = _AUDIO_CONTAINER_RX.search(t)
    if not m:
        return None
    return _WORD_TO_AUDIO_CONTAINER[m.group(1).lower()]


# A video container word in an audio-only request maps to its natural
# audio form ('extract the audio to mp4' means m4a, not an mpeg file).
_VIDEO_TO_AUDIO_CONTAINER = {
    "mp4": "m4a", "m4v": "m4a", "mov": "m4a", "mkv": "mka", "webm": "ogg",
    "mpegts": "mp2", "flv": "mp3",
}

# Containers that carry audio only, and containers that carry video —
# derived from the matrix, not lists kept in sync by hand. The *_file
# subtitle-dump containers carry neither.
_AUDIO_CONTAINERS = frozenset(
    name for name, row in REGISTRY["matrix"].items()
    if not row.get("video") and not name.endswith("_file")
)
_VIDEO_CONTAINERS = frozenset(
    name for name, row in REGISTRY["matrix"].items() if row.get("video")
)

# An explicit video codec with no named container lands in its natural
# home — ProRes is a .mov file, MPEG-2 a broadcast .ts, MPEG-4 an .avi.
# An explicitly named container always wins over this.
_NATURAL_VIDEO_CONTAINER = {"prores": "mov", "mpeg2": "mpegts", "mpeg4": "avi"}


def _resolve_audio_container(video_container: str | None, audio_container: str | None,
                             audio_codec: str | None) -> str | None:
    """Container for an audio-only output, when the instruction names one
    of its ingredients: an explicit audio word > the codec's format > the
    audio twin of a video word. None when nothing is named — the caller
    applies the input-filename / mp3 fallbacks."""
    if audio_container is not None:
        return audio_container
    if audio_codec is not None:
        return audio_container_for_codec(audio_codec)
    if video_container is not None:
        return _VIDEO_TO_AUDIO_CONTAINER.get(video_container)
    return None


def _detect_audio_codec(t: str) -> str | None:
    """An audio codec named in the instruction (same filename guard)."""
    rx = (r"(?<![\w./-])")
    if re.search(rx + r"mp2\b", t, re.I):
        return "mp2"
    if re.search(rx + r"mp3\b", t, re.I):
        return "mp3"
    if re.search(rx + r"(?:eac3|dd\s*\+?)\b", t, re.I):
        return "eac3"
    if re.search(rx + r"ac3\b", t, re.I):
        return "ac3"
    if re.search(rx + r"(?:libfdk[-_ ]?aac|fdk[-_ ]?aac)\b", t, re.I):
        return "fdk_aac"
    if re.search(rx + r"aac\b", t, re.I):
        return "aac"
    if re.search(rx + r"(?:alac|apple\s+lossless)\b", t, re.I):
        return "alac"
    if re.search(rx + r"opus\b", t, re.I):
        return "opus"
    if re.search(rx + r"vorbis\b", t, re.I):
        return "vorbis"
    if re.search(rx + r"flac\b", t, re.I):
        return "flac"
    if re.search(rx + r"(?:pcm|wave|wav)\b", t, re.I):
        return "pcm_s16le"
    return None


# "extract the audio", "audio only", "audio track", "without the video" …
_AUDIO_ONLY = re.compile(
    r"\b(?:extract|rip|pull|export|dump|write|save|get|take)\s+(?:the\s+)?"
    r"(?:audio|sound|music|soundtrack)\b"
    r"|\baudio[\s-]?(?:only|track|file)\b"
    r"|\b(?:just|only)\s+(?:the\s+)?audio\b"
    r"|\bwithout\s+(?:the\s+)?video\b",
    re.I,
)

# A subtitle DUMP ('extract the subtitles', 'dump the subtitles to srt') —
# must be tested before the burn/soft subtitle detector, which would ask
# for a subtitle file the user never intends to provide.
_SUB_DUMP_RE = re.compile(
    r"\b(?:extract|dump|export|pull|write|get|convert|turn)\s+(?:the\s+)?"
    r"(?:subtitles?|captions?|subs)\b"
    r"|\b(?:subtitles?|captions?|subs)\s+(?:to|into|as)\s+(?:a\s+)?"
    r"(?:srt|vtt|webvtt|ass|ssa)\b",
    re.I,
)


def _detect_subtitle_dump(t: str) -> str | None:
    """The dump container (srt_file/vtt_file/ass_file) for a subtitle
    dump request, else None. SRT is the default format."""
    if not _SUB_DUMP_RE.search(t):
        return None
    m = re.search(r"\b(vtt|webvtt|ass|ssa|srt)\b", t)
    if m:
        name = m.group(1).lower()
        if name in ("vtt", "webvtt"):
            return "vtt_file"
        if name in ("ass", "ssa"):
            return "ass_file"
    return "srt_file"


# ---------------------------------------------------------------- filters (M7)

# A pasted filtergraph: a run of filtergraph characters (filter names,
# arguments, '[label]' pads, ',' / ';' separators) that contains at least
# one pad label AND names a filter outside the labels — 'split=2[a][b]',
# "[0:v]split[a][b];[a]eq=contrast=1.2[v]". Prose like 'the [0:0] stream'
# (no filter name outside the labels) is left alone.
_FILTER_GRAPH_RX = re.compile(r"[A-Za-z0-9_=:\[\],.;'\"%+/.-]+")


def _extract_pasted_graph(t: str) -> tuple[str | None, int, int]:
    """Find a pasted filtergraph in t → (graph, start, end) or (None, 0, 0).
    Surrounding paste quotes are dropped ('split[a][b]') so the validator
    sees the bare graph; the cut span still spans the quotes."""
    for m in _FILTER_GRAPH_RX.finditer(t):
        run = m.group(0)
        if "[" not in run or "]" not in run:
            continue
        outside = re.sub(r"\[[^\]]*\]", "", run).replace("[", "").replace("]", "")
        if not re.search(r"[A-Za-z0-9_]", outside):
            continue
        g = run
        if len(g) >= 2 and g[0] == g[-1] and g[0] in "'\"":
            g = g[1:-1]
        elif g[-1] in "'\"" and g.count(g[-1]) % 2:
            g = g[:-1]
        return g, m.start(), m.end()
    return None, 0, 0

# A raw 'name=args' token whose name is an allowlisted filter
# ('eq=contrast=1.2', 'scale=1280:720', "drawtext=text='hi'").
_RAW_FILTER_RX = re.compile(r"\b([a-z][a-z0-9_]*(?:=[^\s,;]+)+)\b")

# 'apply the loudnorm filter', 'use boxblur'…
_NAMED_FILTER_RX = re.compile(
    r"\b(?:apply|use|add|run|with)\s+(?:the\s+)?([a-z][a-z0-9_]*)\s+filter\b", re.I
)

_TEXT_OVERLAY_RX = re.compile(
    r"\b(?:add|overlay|put|write|superimpose)\s+(?:the\s+|some\s+|a\s+)?"
    r"text\s+[\"']([^\"']{1,80})[\"']",
    re.I,
)


def _detect_filters(
    t: str, duration: float | None, pre_seen: set[str] | None = None
) -> tuple[list[FilterOp], str | None, str | None, Clarification | None, str | None]:
    """Video/audio filters: named phrases, raw 'name=args' expressions,
    named filters, and a full custom filtergraph.
    Returns (ops, graph, note, clarification, unsupported_fragment).
    ``pre_seen`` seeds the dedup set with expressions the transform pre-pass
    already claimed, so a phrase never adds the same filter twice."""
    video_filters = filter_names("video")
    ops: list[FilterOp] = []
    seen: set[str] = set(pre_seen or ())
    notes: list[str] = []
    clar: Clarification | None = None
    frag: str | None = None

    def add(domain: str, expr: str) -> None:
        nonlocal frag
        if expr in seen or frag:
            return
        seen.add(expr)
        problems = filter_expr_problems(expr)
        if problems:
            frag = f"the filter '{expr}' is not usable: " + "; ".join(problems)
            return
        ops.append(FilterOp(domain=domain, expr=expr))

    # A pasted graph claims the whole request — validate it first and cut
    # its span out of the text so 'split=2[a][b]' is not re-read as a raw
    # expression (its brackets are only legal inside a graph).
    graph: str | None = None
    g, g0, g1 = _extract_pasted_graph(t)
    if g is not None:
        graph = g
        problems = filtergraph_problems(graph)
        if problems:
            frag = f"the filtergraph '{graph[:80]}' is not usable: " + "; ".join(problems)
            graph = None
        text = t[:g0] + " " + t[g1:]
    else:
        text = t

    # --- video phrases -------------------------------------------------
    m = re.search(r"\b(?:flip|mirror)\w*\b", text, re.I)
    if m:
        window = text[m.start():m.start() + 35]
        if re.search(r"verti\w*", window, re.I):
            add("video", "vflip")
        else:
            add("video", "hflip")
            if not re.search(r"horizont\w*|verti\w*", window, re.I):
                notes.append("flip assumed horizontal")

    if re.search(r"\bblur\w*\b", text, re.I):
        add("video", "boxblur=2:2")
    if re.search(r"\bsharpen\w*\b", text, re.I):
        add("video", "unsharp=5:5:1.0")

    m = _TEXT_OVERLAY_RX.search(text)
    if m:
        label = m.group(1).replace("\\", "\\\\").replace("'", "\\'")
        add("video",
            f"drawtext=text='{label}':fontsize=48:box=1:boxcolor=black@0.5:"
            f"x=(w-text_w)/2:y=h-text_h-50")

    # fade in/out — audio when an audio/sound/music word sits within ~30
    # chars of 'fade', video otherwise
    m = re.search(r"\bfade(?:s|d|ing)?\s+(in|out)\b", text, re.I)
    if m:
        direction = m.group(1).lower()
        ctx = text[max(0, m.start() - 30):m.end() + 10]
        audio = re.search(r"\b(?:audio|sound|music)\b", ctx, re.I) is not None
        if audio:
            if direction == "in":
                add("audio", "afade=t=in:st=0:d=2")
            elif duration is None:
                clar = Clarification(
                    question="How long is the clip? I need its length to "
                             "time the audio fade-out.")
            else:
                add("audio", f"afade=t=out:st={max(0.0, duration - 2):g}:d=2")
        else:
            if direction == "in":
                add("video", "fade=t=in:st=0:d=1.5")
            elif duration is None:
                clar = Clarification(
                    question="How long is the clip? I need its length to "
                             "time the fade-out.")
            else:
                add("video", f"fade=t=out:st={max(0.0, duration - 1.5):g}:d=1.5")

    # --- audio phrases ---------------------------------------------------
    m = re.search(r"\bvolume\s+(?:to\s+)?(\d+(?:\.\d+)?)\b", text, re.I)
    if m:
        n = float(m.group(1))
        value = n / 100.0 if n > 10 else n  # >10 reads as a percentage
        if value > 0:
            add("audio", f"volume={value:g}")
    elif re.search(r"\blouder\b|\bvolume\s+up\b", text, re.I):
        add("audio", "volume=1.5")
    elif re.search(r"\bquieter\b|\bsofter\b|\bsoften\b|\bvolume\s+down\b", text, re.I):
        add("audio", "volume=0.5")

    # --- M10.1 transform phrases -------------------------------------------
    # Normalization family (dynaudnorm vs loudnorm vs compand): the dynamic
    # phrasing is the specific one and wins over plain normalization;
    # 'compand' is its own filter name. Precedence, never a silent double.
    dyn = re.search(
        r"\bdynamic(?:ally)?\s+(?:audio\s+)?(?:normal\w*|compress\w*)\b"
        r"|\bnormaliz\w*[^\n.!?]{0,24}\bdynamic\w*\b"
        r"|\bdynaudnorm\b", text, re.I)
    if dyn:
        add("audio", "dynaudnorm")
    elif re.search(r"\bnormaliz\w*\s+(?:the\s+)?audio\b|\bloudness\b|\bloudnorm\b",
                   text, re.I):
        add("audio", "loudnorm")
    if re.search(r"\bcompress(?:ion|ed)?\s+(?:the\s+)?audio\b"
                 r"|\baudio\s+compress\w*\b|\bcompressor\b"
                 r"|\bcompress\s+(?:the\s+)?dynamic\s+range\b", text, re.I):
        add("audio", "acompressor")
    if re.search(r"\bcompand\b", text, re.I):
        add("audio", "compand")
    if re.search(r"\bdynamic\s+range\s+control\b|\badrc\b", text, re.I):
        add("audio", "adrc")
    if re.search(r"\bdynamic\s+equali[sz]er\b|\badynamicequalizer\b", text, re.I):
        add("audio", "adynamicequalizer")
    if re.search(r"\bde[\s-]?click\w*|\bclicks?\s+and\s+pops\b"
                 r"|\b(?:remove|fix|kill)\s+(?:the\s+)?clicks?\b", text, re.I):
        add("audio", "adeclick")
    # 'remove the silences' (jargon → silenceremove) is not 'silence the
    # audio' (mute → anull): the verb decides
    if re.search(r"\bsilence[\s-]?remove\b|\bremove\s+(?:out\s+)?(?:the\s+)?silenc\w*\b"
                 r"|\bstrip\s+(?:out\s+)?(?:the\s+)?silenc\w*\b"
                 r"|\bcut\s+out\s+(?:the\s+)?silenc\w*\b", text, re.I):
        add("audio", "silenceremove=start_periods=1:start_silence=0.1")
    if re.search(r"\bhdcd\b|\bdc\s+offset\b", text, re.I):
        add("audio", "hdcd")

    if re.search(r"\becho\b", text, re.I):
        add("audio", "aecho=0.8:0.88:60:0.4")
    if re.search(r"\bsilence\s+(?:the\s+)?audio\b", text, re.I):
        add("audio", "anull")

    # --- M10.1 video transform phrases --------------------------------------
    if re.search(r"\bde[\s-]?interlac\w*\b", text, re.I):
        add("video", "yadif")
    if re.search(r"\bdecimate\b|\b(?:drop|remove)\s+(?:the\s+)?duplicate\s+frames?\b",
                 text, re.I):
        add("video", "decimate")
    if re.search(r"\bgreen[\s-]?screen\b|\bchroma[\s-]?key\w*\b"
                 r"|\bremove\s+(?:the\s+)?green\b|\bkey\s+out\s+(?:the\s+)?green\b",
                 text, re.I):
        add("video", "chromakey=color=green")
    m = re.search(r"\b(bt709|bt601|smpte170m|bt2020)\b", text, re.I)
    if m and re.search(r"\bcolor\b|\bspace\b|\bmatrix\b", text, re.I):
        # iall declares the INPUT properties — colorspace rejects frames
        # whose primaries are unknown (the common case for web-sourced
        # video), so the phrase sets both sides (verified on 9.0.1)
        add("video", f"colorspace=iall={m.group(1)}:all={m.group(1)}")
    m = re.search(r"\bfps\s+(?:to\s+|of\s+)?(\d+(?:\.\d+)?)\b"
                  r"|\b(\d+(?:\.\d+)?)\s*(?:fps|frames?\s+per\s+second)\b", text, re.I)
    if m:
        add("video", f"fps=fps={m.group(1) or m.group(2)}")
    # arbitrary-angle rotation: the quarter-turn RotateOp owns 90/180/270,
    # any other explicit angle is the real rotate filter (padded frame)
    m = re.search(r"\brotat\w*[^\n.!?]{0,20}?(\d{1,3})\s*(?:degrees?|deg\b|°)",
                  text, re.I)
    if m and int(m.group(1)) not in (90, 180, 270):
        a = int(m.group(1))
        add("video",
            f"rotate=angle={a}*PI/180:fillcolor=black:"
            f"out_w=rotw({a}*PI/180):out_h=roth({a}*PI/180)")
    m_dar = re.search(r"\bset\s+(?:the\s+)?display\s+aspect\s+ratio\b|\bsetdar\b",
                      text, re.I)
    m_sar = re.search(r"\bset\s+(?:the\s+)?sample\s+aspect\s+ratio\b|\bsetsar\b",
                      text, re.I)
    which = "setdar" if m_dar else ("setsar" if m_sar else None)
    if which:
        r2 = re.search(r"(\d{1,2}(?:\.\d+)?)\s*[:/]\s*(\d{1,2}(?:\.\d+)?)", text)
        if r2:
            add("video", f"{which}=ratio={r2.group(1)}/{r2.group(2)}")

    # --- raw 'name=args' and 'apply the X filter' -------------------------
    for m in _RAW_FILTER_RX.finditer(text):
        expr = m.group(1)
        name = expr.split("=", 1)[0]
        if name in all_filter_names():
            add("audio" if name in filter_names("audio") else "video", expr)
    for m in _NAMED_FILTER_RX.finditer(text):
        name = m.group(1).lower()
        if name in all_filter_names():
            add("audio" if name in filter_names("audio") else "video", name)

    return ops, graph, ("; ".join(notes) if notes else None), clar, frag


def _detect_format_options(t: str, container: str | None) -> dict[str, str]:
    """key=value tokens whose key is a valid option for the container
    (registry-driven), plus natural-language mappings ('faststart',
    '6 second segments', 'resend headers')."""
    out: dict[str, str] = {}
    if not container:
        return out
    specs = format_option_specs(container)
    filters = all_filter_names()
    for m in re.finditer(r"\b([a-z][a-z0-9_]+)=([^\s,;]+)\b", t):
        key, value = m.group(1), m.group(2)
        if key in specs and key not in filters and key not in out:
            out[key] = value
    if "movflags" in specs and re.search(r"\bfast\s?start\b", t, re.I):
        out["movflags"] = "faststart"
    if container == "hls" and "hls_time" in specs:
        m = re.search(
            r"\b(\d+)\s*second(?:s)?\s+(?:of\s+)?segments?\b"
            r"|\bsegments?\s+(?:of\s+)?(\d+)\b", t, re.I)
        if m:
            out["hls_time"] = m.group(1) or m.group(2)
    if container == "mpegts" and "mpegts_flags" in specs:
        if re.search(r"\bresend\s+headers?\b", t, re.I):
            out["mpegts_flags"] = "resend_headers"
    return out


# ---------------------------------------------------------------- bitstream filters

# FFmpeg has NO 'trim' bitstream filter ('trim' is a decode-time video
# filter): a copy-cut phrasing switches the trim to stream-copy mode
# (-ss/-t with -c copy, keyframe-accurate video, sample-accurate audio).
_COPY_TRIM_RX = re.compile(
    r"\bstream[\s-]?copy\s+(?:trim|cut)\b"
    r"|\bcopy\s+(?:trim|cut)\b"
    r"|\bfast\s+(?:trim|cut)\b"
    r"|\b(?:trim|cut)\w*[^\n.!?]{0,48}\bwithout\s+re-?encod\w+\b"
    r"|\bno\s+re-?encod\w+[^\n.!?]{0,48}\b(?:trim|cut)\b",
    re.I,
)


def _copy_trim_requested(t: str) -> bool:
    """True when the instruction asks for a stream-copy cut (see
    _COPY_TRIM_RX)."""
    return bool(_COPY_TRIM_RX.search(t))


def _detect_bitstream_filters(t: str) -> list[dict]:
    """Bitstream filters named in the instruction (registry-driven): a
    known filter name anywhere in the text, plus key=value option tokens
    whose key is valid for that filter. Returns
    [{'name': ..., 'options': {...}}] ordered by first mention. 'trim' is
    not a filter — copy-cut phrasings go through _copy_trim_requested.
    Each option token is claimed by the first (in text order) filter whose
    spec has that key, so 'h264_metadata level=4.1 aud=insert' is unambiguous."""
    specs = bsf_specs()
    positions: dict[str, int] = {}
    for name in specs:
        if name == "trim":
            continue
        m = re.search(rf"\b{re.escape(name)}\b", t, re.I)
        if m:
            positions[name] = m.start()
    found = [
        {"name": name, "options": {}}
        for name in sorted(positions, key=positions.get)
    ]
    if not found:
        return found
    claimed: set[str] = set()
    for m in re.finditer(r"\b([a-z][a-z0-9_]+)=([^\s,;]+)\b", t):
        key, value = m.group(1), m.group(2)
        if key in claimed:
            continue
        for f in found:
            opts = specs[f["name"]].get("options", {})
            if key in opts and key not in f["options"]:
                f["options"][key] = value
                claimed.add(key)
                break
    return found


# ------------------------------------------------- transform filters (M10.1)

# Bare-name claim set: transform-filter names that are unambiguous jargon.
# Names that are ordinary words ('loop', 'trim', 'crop', 'scale', 'fade',
# 'volume', 'rotate') stay out — their keyword detectors (TrimOp, CropOp,
# ResizeOp, the fade/volume phrases, RotateOp) own those words; the explicit
# forms ('loop=loop=2:size=100', 'apply the loop filter') reach them through
# _detect_filters. Diagnostic / multi-input / reference filters are separate
# plan shapes (M10.2–M10.4) and are not claimed here.
_TRANSFORM_JARGON = {
    "audio": {"acompressor", "adeclick", "adrc", "adynamicequalizer",
              "asetrate", "compand", "dynaudnorm", "hdcd", "silenceremove"},
    "video": {"chromakey", "colormatrix", "colorspace", "decimate",
              "drawbox", "fps", "framerate", "setdar", "setsar",
              "setparams", "yadif", "scale_cuda", "yadif_cuda"},
}

# Bare names that take a bare number next to them ('fps to 30', 'asetrate
# 44100', '30 fps'): the number is the filter's primary option, not a
# bitrate or a sample-rate target.
_TRANSFORM_NUMBERED = {"fps": "fps", "framerate": "fps", "asetrate": "sample_rate"}


def _transform_domain(name: str) -> str | None:
    for domain in ("video", "audio"):
        if name in _TRANSFORM_JARGON[domain]:
            return domain
    return None


def _detect_transform_filters(t: str) -> tuple[list[dict], list[str]]:
    """Registry transform filters named bare in the instruction, with their
    option tokens — the bitstream-filter pattern (see
    _detect_bitstream_filters). Returns (found, spans): found is
    [{'name': …, 'options': {…}, 'domain': …}] in text order, spans are
    (start, end) of every claimed token, so parse() can blank them out of
    the detector text length-preservingly — 'asetrate=44100' must not also
    read as a resample target, 'fps=30' not as a bitrate.

    Two spellings are claimed: the expression token ('asetrate=44100',
    'yadif=mode=send_frame') and the space form ('asetrate sample_rate=44100').
    Each loose option token goes to the first (in text order) filter whose
    schema has that key."""
    positions: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    jargon = _TRANSFORM_JARGON["video"] | _TRANSFORM_JARGON["audio"]
    for m in re.finditer(r"\b([a-z][a-z0-9_]*)\b", t):
        name = m.group(1)
        if name in jargon and name not in positions:
            positions[name] = m.start()
            spans.append(m.span())
    found = [
        {"name": n, "options": {}, "positional": [], "domain": _transform_domain(n)}
        for n in sorted(positions, key=positions.get)
    ]
    if not found:
        return [], spans
    # expression form: 'name=k=v:k2=v2' claims the whole token; bare values
    # are positional and map onto the schema keys in _transform_expr
    expr_spans: list[tuple[int, int]] = []
    for f in found:
        m = re.search(rf"\b{re.escape(f['name'])}=(\S+)", t)
        if m:
            spans.append(m.span())
            expr_spans.append(m.span())
            for pair in m.group(1).split(":"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    f["options"].setdefault(k, v)
                elif pair:
                    f["positional"].append(pair)
    # bare number next to a numbered filter: 'fps to 30', 'asetrate 44100',
    # '30 fps' — claimed with the name so no other detector reads it
    for f in found:
        key = _TRANSFORM_NUMBERED.get(f["name"])
        if key is None or f["options"] or f["positional"]:
            continue
        m = re.search(rf"\b{re.escape(f['name'])}\b", t)
        if m:
            s0, s1 = m.span()
            after = re.match(r"\s*(?:to\s+|of\s+|at\s+)?(\d+(?:\.\d+)?)", t[s1:])
            if after:
                f["options"][key] = after.group(1)
                spans.append((s0, s1 + after.end()))
                continue
        before = re.search(r"(\d+(?:\.\d+)?)\s+$", t[:s0])
        if before:
            f["options"][key] = before.group(1)
            spans.append((s0 - before.end(), s1))
    # space form: loose 'key=value' tokens, claimed by schema ownership —
    # tokens inside a claimed expression token are already parsed there
    claimed: set[str] = set()
    for m in re.finditer(r"\b([a-z][a-z0-9_]+)=([^\s,;]+)\b", t):
        key, value = m.group(1), m.group(2)
        if key in claimed:
            continue
        if any(a <= m.start() < b for a, b in expr_spans):
            continue
        for f in found:
            opts = (filter_spec(f["name"]) or {}).get("options", {})
            if key in opts and key not in f["options"]:
                f["options"][key] = value
                claimed.add(key)
                spans.append(m.span())
                break
    return found, spans


def _transform_expr(f: dict) -> str:
    """The filter expression for a claimed transform filter: the name plus
    its options in FFmpeg's 'name=k=v:k2=v2' form. Positional values (the
    'asetrate=44100', 'drawbox=0:0:10:10:red' spelling) are mapped onto the
    schema's option keys in order, so the expression is explicit and the
    option validator sees named keys."""
    opts = f["options"]
    positional = f.get("positional", [])
    if not opts and not positional:
        return f["name"]
    # positional order = the schema's canonical keys in order; alias keys
    # (drawbox 'w'/'h' repeat 'width'/'height' with the same description)
    # are skipped so values land on the right option
    schema_keys: list[str] = []
    seen_desc: set[str] = set()
    for k, opt in (filter_spec(f["name"]) or {}).get("options", {}).items():
        d = opt.get("description", k)
        if d in seen_desc:
            continue
        seen_desc.add(d)
        schema_keys.append(k)
    pairs: list[str] = []
    ki = 0
    for v in positional:
        while ki < len(schema_keys) and schema_keys[ki] in opts:
            ki += 1
        if ki >= len(schema_keys):
            pairs.append(v)  # schema exhausted — keep the positional form
        else:
            pairs.append(f"{schema_keys[ki]}={v}")
            ki += 1
    pairs += [f"{k}={v}" for k, v in opts.items()]
    return f["name"] + "=" + ":".join(pairs)


# ---------------------------------------------------------------- codec options

# explicit 'key=value' encoder options (M8) — the key class needs the
# hyphen for x264-params / x265-params / vvenc-params
_CODEC_OPT_TOKEN = re.compile(r"\b([a-z][a-z0-9_\-]*)=([^\s,;]+)", re.I)
# the *-params options also come in the space form ('x264-params
# keyint=24') — restricted to that family so ordinary words ('tune the
# color', 'set a profile') are never mistaken for option values
_CODEC_PARAMS_TOKEN = re.compile(
    r"\b((?:x264|x265|vvenc)[-_ ]?params)\s+([^\s,;]+)", re.I)


def _detect_codec_options(t: str) -> tuple[dict[str, str], dict[str, str]]:
    """Explicit key=value tokens whose key is a private encoder option in
    the registry (per-encoder tables), plus the space form of the
    *-params options. Split into (video, audio) by the key unions;
    dvbsub's min_bpp is excluded — there is no subtitle encoder path
    yet. Strict per-encoder validation happens at the final gate (an
    unknown key for the resolved encoder is rejected there)."""
    video_keys = video_codec_option_keys() - {"min_bpp"}
    audio_keys = audio_codec_option_keys()
    v: dict[str, str] = {}
    a: dict[str, str] = {}
    for rx in (_CODEC_OPT_TOKEN, _CODEC_PARAMS_TOKEN):
        for m in rx.finditer(t):
            key, value = m.group(1).lower().replace(" ", "-"), m.group(2).rstrip(".")
            if key in video_keys and key not in v:
                v[key] = value
            elif key in audio_keys and key not in a:
                a[key] = value
    return v, a


def _video_codec_option_defaults(t: str, opts: dict[str, str],
                                 encoder: str | None) -> list[str]:
    """Natural-language forms that map onto the resolved video encoder's
    private options: 'crf 18' (crf on x264/x265, cq on nvenc, noted and
    skipped where the encoder has no constant-quality knob) and an
    extended 'profile main10' the profile field does not cover."""
    notes: list[str] = []
    if encoder and "crf" not in opts:
        m = re.search(r"\bcrf\s+(\d{1,2})\b", t, re.I)
        if m:
            crf = m.group(1)
            specs = codec_option_specs(encoder)
            if "crf" in specs:
                opts["crf"] = crf
                notes.append(f"CRF set to {crf}")
            elif "cq" in specs:
                opts["cq"] = crf
                notes.append(f"CRF {crf} given as the {encoder} "
                             f"constant-quality knob (cq {crf})")
            else:
                notes.append(f"{encoder} has no CRF-style knob — "
                             f"CRF {crf} ignored (set a video bitrate instead)")
    if encoder and "profile" not in opts and _detect_profile(t) is None:
        m = re.search(r"\bprofile\s+([a-z][a-z0-9_]*)\b", t, re.I)
        if m:
            name = m.group(1).lower()
            if name not in ("baseline", "main", "high"):
                spec = codec_option_specs(encoder).get("profile")
                if spec and (spec.get("kind") != "choice"
                             or name in spec.get("values", [])):
                    opts["profile"] = name
                    notes.append(f"encoder profile set to {name}")
    return notes


def _audio_codec_option_defaults(t: str, opts: dict[str, str]) -> list[str]:
    """'dialnorm -27' -> the ac3/eac3/fdk dialogue-level option;
    'vbr 4' -> the libfdk-aac VBR mode."""
    notes: list[str] = []
    if "dialnorm" not in opts:
        m = re.search(r"\bdialnorm\s*(-?\d+)\b", t, re.I)
        if m:
            opts["dialnorm"] = m.group(1)
            notes.append(f"dialnorm set to {m.group(1)} dB")
    if "vbr" not in opts:
        m = re.search(r"\bvbr\s+(\d)\b", t, re.I)
        if m:
            opts["vbr"] = m.group(1)
            notes.append(f"VBR mode set to {m.group(1)}")
    return notes


# ---------------------------------------------------------------- grayscale / audio / watermark

GRAYSCALE_RE = re.compile(r"black\s+and\s+white|\bb&w\b|gr(?:a|e)y\s*scale|desaturat\w*|monochrome", re.I)

_AUDIO_REMOVE = re.compile(
    r"\b(?:remove|strip|drop|delete)\s+(?:the\s+)?audio\b|\bno\s+audio\b|"
    r"without\s+(?:the\s+)?audio\b|\baudio\s+off\b|mute\s+everything|\bvideo\s+only\b",
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
    # speed, rotation, cropping, flips, volume changes, filters, subtitle
    # dumps and audio extraction are supported (M5–M7) — their detectors run
    # in parse() before this scan, so only what a detector cannot resolve
    # (e.g. an out-of-range factor) reaches the user, with its own message
    (re.compile(r"\bconcat(?:enate)?\b|\bmerge\w*\b|\bstitch\w*\b|\bappend\w*\b", re.I), "concatenation of multiple files"),
    # 'split the video into 3 parts' — but 'split' as a FILTER
    # ('[0:v]split=2[a][b]') is a filtergraph, not a segment request
    (re.compile(r"\bsplit(?:ting)?\s+(?:the\s+)?(?:video|file|clip|it)\b|\bsplit\s+into\b", re.I), "splitting into segments"),
    (re.compile(r"\bsepia\b|\bvintage\b|\bretro\b", re.I), "additional color effects"),
    (re.compile(r"\bdenois\w*\b|\bstabil(?:is|iz)\w*\b", re.I), "stabilization and denoising"),
    (re.compile(r"\bhdr\b|\btone[\s-]?map\w*\b", re.I), "HDR workflows"),
    # gpu/nvenc (M6) and qsv/quick sync (M8) are supported; the other
    # hardware-encoding stacks stay out
    (re.compile(r"\bcuda\b|\bvideotoolbox\b", re.I), "CUDA / VideoToolbox hardware acceleration"),
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


_BITRATE_AUDIO_CTX = re.compile(
    r"\b(?:audio|sound|music|mp2|mp3|aac|alac|flac|opus|vorbis)\b", re.I
)


def _bitrate_is_audio(t: str, mode: str) -> bool:
    """Whether a named bitrate targets the audio stream: audio mode
    always, otherwise when an audio word sits within ~25 chars before the
    bitrate token ('aac audio at 128 kbps')."""
    if mode == "audio":
        return True
    m = _BITRATE.search(t)
    return m is not None and _BITRATE_AUDIO_CTX.search(
        t[max(0, m.start() - 25):m.start()]) is not None


def _detect_bitrate(t: str, audio: bool = False) -> int | None:
    """Target bitrate in kbps, when the instruction names one
    ('cbr 10 Mbps', '128 kbps'). `audio` selects the audio range
    (32–3840); the video range is 100–200000."""
    m = _BITRATE.search(t)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    kbps = int(round(value * 1000)) if unit.startswith("m") else int(round(value))
    lo, hi = (32, 3840) if audio else (100, 200000)
    if not (lo <= kbps <= hi):
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


def _scan_ambiguous(t: str, resize: ResizeOp | None,
                    filters: list[FilterOp] | None = None) -> Clarification | None:
    """Focused questions for requests whose intent cannot be safely
    resolved (PRD clarification policy)."""
    # 'lossless' no longer needs a question — parse() resolves it to
    # ProRes (video) or FLAC (audio) with a note.
    if _SIZE_TARGET.search(t):
        return Clarification(
            question=(
                "targeting an exact file size needs bitrate estimation (possibly two-pass "
                "encoding), which the MVP does not do. Would you prefer a quality preset "
                "(e.g. default H.264) or a smaller height (e.g. 720p) instead?"
            )
        )
    if resize is None and _VAGUE_SIZE.search(t):
        # 'compress the audio' is a filter request (M10.1), not a size target
        if filters and any(
                o.domain == "audio"
                and o.expr.split("=")[0] in {"acompressor", "compand",
                                             "dynaudnorm", "adrc"}
                for o in filters):
            return None
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
        # Bitstream filters claim their name and option tokens up front
        # (e.g. 'flip=horizontal' for h264_metadata), so the keyword
        # detectors (flip, rotate, width/height, encoder level) cannot
        # double-read them. The mask is length-preserving, so every span
        # and index computed on it stays valid for the original text.
        bsfs = _detect_bitstream_filters(t)
        if bsfs:
            t_det = t
            for f in bsfs:
                t_det = re.sub(rf"\b{re.escape(f['name'])}\b",
                               lambda m: " " * len(m.group(0)), t_det)
                for k, v in f["options"].items():
                    t_det = t_det.replace(f"{k}={v}", " " * (len(k) + len(v) + 1))
        else:
            t_det = t
        # M10.1: transform filters claim their name and option tokens the
        # same way — 'asetrate=44100' must not also read as a resample
        # target, 'fps=30' not as a bitrate, 'yadif=mode=send_frame' not as
        # encoder options. The mask is length-preserving (see the BSF note).
        tf_found, tf_spans = _detect_transform_filters(t_det)
        for s0, s1 in tf_spans:
            t_det = t_det[:s0] + " " * (s1 - s0) + t_det[s1:]
        tf_ops: list[FilterOp] = []
        tf_frag: str | None = None
        for f in tf_found:
            expr = _transform_expr(f)
            problems = filter_expr_problems(expr)
            if problems:
                tf_frag = (f"the filter '{expr}' is not usable: "
                           + "; ".join(problems))
                break
            tf_ops.append(FilterOp(domain=f["domain"], expr=expr))
        trim, trim_note, trim_clar = _resolve_trim(t_det, media.duration if media else None)
        crop, crop_clar, crop_frag, crop_spans = _detect_crop(t_det)
        resize, resize_clar = _detect_resize(t_det, exclude=crop_spans)
        speed, speed_clar, speed_frag = _detect_speed(t_det)
        rotate, rotate_clar, rotate_frag, rotate_note = _detect_rotate(t_det)
        audio_resample, rs_clar, rs_frag = _detect_resample(t_det)
        pixel_format = _detect_pixel_format(t_det)
        gpu = _detect_gpu(t_det)
        qsv = _detect_qsv(t_det)
        video_codec_opts, audio_codec_opts = _detect_codec_options(t_det)
        # bitstream filters claimed their own option tokens (e.g. a 'level'
        # for h264_metadata must not double-land as an encoder level)
        bsf_keys = {k for f in bsfs for k in f["options"]}
        if bsf_keys:
            video_codec_opts = {k: v for k, v in video_codec_opts.items()
                                if k not in bsf_keys}
            audio_codec_opts = {k: v for k, v in audio_codec_opts.items()
                                if k not in bsf_keys}
        # an explicit 'preset=X' / 'profile=X' token is a codec option —
        # the NL field detectors must not also claim the same word
        video_profile = None if "profile" in video_codec_opts \
            else _detect_profile(t_det)
        video_preset = None if "preset" in video_codec_opts \
            else _detect_preset(t_det)
        audio = _detect_audio(t_det)
        watermark, wm_clar, wm_note = _detect_watermark(t_det, assets)
        audio_replace, ar_clar, ar_note = _detect_audio_replace(t_det, assets)
        # a subtitle DUMP must win over the burn/soft subtitle detector,
        # which would ask for a subtitle file the user never intends to give
        sub_dump = _detect_subtitle_dump(t_det)
        subtitle, sub_clar, sub_note = (
            (None, None, None) if sub_dump else _detect_subtitles(t_det, assets))
        if audio_replace is not None and audio in ("remove", "conflict"):
            audio = None  # a replacement wins over remove / keep+remove
        grayscale = GRAYSCALE_RE.search(t_det) is not None
        codec = _detect_codec(t_det)
        container, unsupported_container = _detect_container(t_det)
        audio_codec = _detect_audio_codec(t_det)
        audio_container = _detect_audio_container(t_det)
        # 'lossless' resolves to a real codec instead of a question: FLAC
        # for an audio output, ProRes for a video one (visible as a note)
        lossless_note: str | None = None
        if _LOSSLESS.search(t_det) and codec is None and audio_codec is None:
            if audio_container is not None or re.search(r"\b(?:audio|sound|music)\b", t_det, re.I):
                audio_codec = "flac"
                lossless_note = "lossless interpreted as FLAC (lossless audio codec)"
            else:
                codec = "prores"
                lossless_note = "lossless interpreted as ProRes (visually lossless mastering codec)"
        # the output MODE: subtitle dump > audio-only > video. An audio
        # container/codec word alone implies audio mode, but only when it
        # is not the audio of a named video ('h264 with aac audio').
        audio_wanted = _AUDIO_ONLY.search(t_det) is not None
        mode = ("subtitles" if sub_dump else
                "audio" if (audio_wanted or (
                    audio_container is not None and container is None and codec is None))
                else "video")
        bitrate_is_audio = _bitrate_is_audio(t_det, mode)
        audio_bitrate = _detect_bitrate(t_det, True) if bitrate_is_audio else None
        video_bitrate = _detect_bitrate(t_det, False) if not bitrate_is_audio else None
        filters, filtergraph, filter_note, filter_clar, filter_frag = \
            _detect_filters(t_det, media.duration if media else None,
                            pre_seen={o.expr for o in tf_ops})
        filters = tf_ops + filters
        if unsupported_container:
            unsupported.append(UnsupportedPart(fragment=unsupported_container,
                                                reason="not in the MVP support set"))
            return ParseResult(status=ParseStatus.UNSUPPORTED, unsupported=unsupported)
        for frag in (tf_frag, crop_frag, speed_frag, rotate_frag, rs_frag, filter_frag):
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
        if dest is not None and mode != "video":
            return ParseResult(
                status=ParseStatus.NEEDS_CLARIFICATION,
                clarifications=[Clarification(
                    question=(f"a stream destination carries a video output, but this "
                              f"request reads as a {mode} dump — which did you intend?")),
                ],
                defaults=[trim_note] if trim_note else [],
            )
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

        ambiguous = _scan_ambiguous(t_det, resize, filters)

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
        #    re-mentions subtitles without naming a new file (a subtitle
        #    DUMP re-mention is handled by the mode switch instead)
        if previous_plan is not None and sub_dump is None and sub_clar is not None \
                and _SUBTITLES_RE.search(t):
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

        # 3c. mode conflicts: edits the output mode cannot carry
        video_filters = [f for f in filters if f.domain == "video"]
        mode_conflict = None
        if mode == "audio" and (audio == "remove" or resize is not None
                                or crop is not None or rotate is not None
                                or watermark is not None or grayscale
                                or filtergraph or video_filters):
            mode_conflict = Clarification(
                question=("an audio-only output cannot carry video edits "
                          "(resize, crop, rotation, effects) or a removed audio "
                          "track — did you mean a video export with those audio "
                          "settings, or a plain audio extraction?"))
        elif mode == "subtitles" and (audio in ("remove", "keep")
                                      or resize is not None or crop is not None
                                      or rotate is not None or watermark is not None
                                      or grayscale or speed is not None
                                      or audio_resample is not None
                                      or audio_replace is not None
                                      or filters or filtergraph):
            mode_conflict = Clarification(
                question=("a subtitle dump extracts the subtitle stream only — "
                          "video and audio edits don't apply to it (trimming is "
                          "fine). Should I just dump the subtitles, or keep "
                          "these as a video edit?"))
        graph_conflict = None
        if filtergraph is not None and (watermark is not None
                                        or (subtitle is not None and subtitle.mode == "burn")
                                        or resize is not None or crop is not None
                                        or rotate is not None or speed is not None
                                        or grayscale or video_filters):
            graph_conflict = Clarification(
                question=("a custom filtergraph replaces the built-in video filter "
                          "chain — I can't combine it with the other video edits "
                          "in the same request. Should I use only the filtergraph, "
                          "or only the built-in edits?"))

        # 4. explicit clarifications win (focused, one at a time)
        for c in (trim_clar, resize_clar, crop_clar, speed_clar, rotate_clar,
                  rs_clar, wm_clar, ar_clar, sub_clar, filter_clar, mode_conflict,
                  graph_conflict, ambiguous):
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
            prev_path = assets.get(previous_plan.input_id, Asset(path="input.mp4")).path
            input_ext = os.path.splitext(prev_path)[1].lstrip(".").lower()
            plan, changed = _merge_revision(
                previous_plan, t, trim, resize, audio, watermark, grayscale,
                audio_replace, dest=dest, crop=crop, rotate=rotate, speed=speed,
                audio_resample=audio_resample, subtitle=subtitle, gpu=gpu,
                qsv=qsv, codec=codec, container=container, audio_codec=audio_codec,
                audio_container=audio_container, audio_bitrate=audio_bitrate,
                video_bitrate=video_bitrate, mode=mode, sub_dump=sub_dump,
                filters=filters, filtergraph=filtergraph, input_ext=input_ext,
                video_codec_options=video_codec_opts,
                audio_codec_options=audio_codec_opts,
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
            if trim is not None and _copy_trim_requested(t_det):
                trim.stream_copy = True
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
            for f in filters:
                if f.domain == "video":
                    ops.append(f)
            if subtitle is not None:
                # last among the video ops, so a burned filter runs on the
                # final dimensions (after resize/crop/rotate/speed)
                ops.append(subtitle)
            if audio_replace is not None:
                ops.append(audio_replace)
            if audio_resample is not None:
                ops.append(audio_resample)
            for f in filters:
                if f.domain == "audio":
                    ops.append(f)
            # an audio output carries no video edits; a subtitle dump none
            if mode == "audio":
                ops = [o for o in ops if o.type in ("speed", "audio_resample",
                                                    "audio_replace")
                       or (o.type == "filter" and o.domain == "audio")]
            elif mode == "subtitles":
                ops = []
            plan.operations = ops
            if mode == "video" and audio == "remove":
                plan.output.audio_mode = AudioMode.REMOVE
            if dest is not None:
                plan.output_destination = dest

        notes: list[str] = []
        if trim_note:
            notes.append(trim_note)
        if plan.trim is not None and plan.trim.stream_copy:
            notes.append(
                "stream-copy cut (no re-encode): -ss/-t with -c copy — video "
                "starts at the first keyframe at/after the requested start "
                "(keyframe-accurate), audio is sample-accurate"
            )
        if rotate_note:
            notes.append(rotate_note)
        if wm_note:
            notes.append(wm_note)
        if ar_note:
            notes.append(ar_note)
        if sub_note:
            notes.append(sub_note)
        if lossless_note:
            notes.append(lossless_note)
        if filter_note:
            notes.append(filter_note)

        # 6. resolve output defaults — visible, never hidden
        # (gpu alone is not an edit — it only matters when an encode
        # happens, so it is deliberately NOT part of the no-op check below)
        if previous_plan is None:
            if trim is None and resize is None and crop is None and speed is None \
                    and rotate is None and audio_resample is None and watermark is None \
                    and not grayscale and pixel_format is None and video_profile is None \
                    and video_preset is None and codec is None and container is None \
                    and audio != "remove" and audio_replace is None and subtitle is None \
                    and audio_codec is None and sub_dump is None and not audio_wanted \
                    and video_bitrate is None and audio_bitrate is None \
                    and dest is None \
                    and not video_codec_opts and not audio_codec_opts \
                    and not filters and filtergraph is None \
                    and not _detect_format_options(t, container) \
                    and not bsfs \
                    and not re.search(r"\bconvert\w*|\btranscod\w*|\bexport\w*|\bsave\w*|\brender\w*|\bturn\b|\bextract\w*", t):
                return ParseResult(
                    status=ParseStatus.NEEDS_CLARIFICATION,
                    clarifications=[Clarification(question="which edit would you like?",
                                                  hint=SUPPORTED_SUMMARY)],
                    defaults=notes,
                )
            main_path = assets.get(plan.input_id, Asset(path="input.mp4")).path
            ext = os.path.splitext(main_path)[1].lstrip(".").lower()
            if mode == "audio":
                # audio container: explicit audio word > the codec's format
                # > the audio twin of a video word ('to mp4' means m4a)
                # > the input filename > mp3
                if audio_container is not None:
                    container = audio_container
                    notes.append(f"audio container set to {container}")
                elif audio_codec is not None:
                    container = audio_container_for_codec(audio_codec) or "mp3"
                    notes.append(f"container set to {container} (the format {audio_codec} is carried in)")
                elif container is not None:
                    container = _VIDEO_TO_AUDIO_CONTAINER.get(container, container)
                    notes.append(f"container set to {container} (audio form of the requested video format)")
                elif ext in _EXT_TO_CONTAINER and _EXT_TO_CONTAINER[ext] in _AUDIO_CONTAINERS:
                    container = _EXT_TO_CONTAINER[ext]
                    notes.append(f"container defaulted to {container} (from the input filename)")
                else:
                    container = "mp3"
                    notes.append("audio container defaulted to mp3")
                # an explicit codec the container doesn't carry wins: switch
                # to the format that does
                if audio_codec is not None and audio_codec not in \
                        REGISTRY["matrix"].get(container, {}).get("audio", []):
                    swap = audio_container_for_codec(audio_codec)
                    if swap and swap != container:
                        notes.append(f"container switched to {swap} ({audio_codec} is not carried by {container})")
                        container = swap
                if audio_codec is None:
                    audio_codec = audio_codec_for(container, None)
                    notes.append(f"audio codec set to {audio_codec} (the {container.upper()} default)")
                plan.output.mode = "audio"
                plan.output.video_codec = None
                plan.output.gpu = False
                plan.output.pixel_format = None
                plan.output.video_profile = None
                plan.output.video_preset = None
                plan.output.video_bitrate_kbps = None
                plan.output.container = container
                plan.output.audio_codec = audio_codec
                if audio_bitrate is not None:
                    plan.output.audio_bitrate_kbps = audio_bitrate
                    notes.append(f"targeting a {audio_bitrate} kbps audio bitrate")
                # named video options cannot be applied here — the final
                # gate reports them (an audio output has no video encoder)
                if video_codec_opts:
                    plan.output.video_codec_options = video_codec_opts
                aopts = dict(audio_codec_opts)
                _audio_codec_option_defaults(t, aopts)
                if aopts:
                    plan.output.audio_codec_options = aopts
                    notes.append("audio codec options: "
                                 + ", ".join(f"{k}={v}" for k, v in sorted(aopts.items())))
            elif mode == "subtitles":
                plan.output.mode = "subtitles"
                plan.output.container = sub_dump
                plan.output.video_codec = None
                plan.output.audio_codec = None
                plan.output.gpu = False
                plan.output.pixel_format = None
                plan.output.video_profile = None
                plan.output.video_preset = None
                plan.output.video_bitrate_kbps = None
                plan.output.audio_mode = AudioMode.ENCODE
                notes.append(f"subtitle dump format: {sub_dump}")
                # a dump encodes nothing — named codec options are reported
                # by the final gate instead of being applied
                if video_codec_opts:
                    plan.output.video_codec_options = video_codec_opts
                if audio_codec_opts:
                    plan.output.audio_codec_options = audio_codec_opts
            else:  # video
                # an explicit codec with no named container lands in the
                # codec's natural home ('prores' is a .mov file)
                if codec is not None and container is None and dest is None:
                    natural = _NATURAL_VIDEO_CONTAINER.get(codec)
                    if natural is not None:
                        container = natural
                        notes.append(f"container set to {natural} (the natural home for {codec.upper()})")
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
                if audio_codec is not None:
                    plan.output.audio_codec = audio_codec
                    notes.append(f"audio codec set to {audio_codec}")
                if pixel_format is not None:
                    plan.output.pixel_format = pixel_format
                    notes.append(f"output pixel format set to {pixel_format}")
                if video_profile is not None:
                    plan.output.video_profile = video_profile
                    notes.append(f"encoder profile set to {video_profile}")
                if video_preset is not None:
                    plan.output.video_preset = video_preset
                    notes.append(f"encoding speed preset set to {video_preset}")
                if video_bitrate is not None:
                    plan.output.video_bitrate_kbps = video_bitrate
                    notes.append(
                        f"targeting a {video_bitrate / 1000:g} Mbps video bitrate (CBR-style target)"
                    )
                if gpu:
                    plan.output.gpu = True
                    notes.append("encoding on GPU (NVIDIA nvenc)")
                if qsv:
                    plan.output.qsv = True
                    notes.append("encoding on Intel QSV (Quick Sync)")
                # private encoder options: explicit key=value tokens plus
                # the NL forms ('crf 18', 'profile main10') mapped onto the
                # resolved encoder
                vencoder = video_encoder_name(
                    codec, plan.output.gpu, plan.output.qsv)
                notes += _video_codec_option_defaults(t, video_codec_opts, vencoder)
                if video_codec_opts:
                    plan.output.video_codec_options = video_codec_opts
                    notes.append("video codec options: "
                                 + ", ".join(f"{k}={v}" for k, v in sorted(video_codec_opts.items())))
                notes += _audio_codec_option_defaults(t, audio_codec_opts)
                if audio_codec_opts:
                    plan.output.audio_codec_options = audio_codec_opts
                    notes.append("audio codec options: "
                                 + ", ".join(f"{k}={v}" for k, v in sorted(audio_codec_opts.items())))
            format_options = _detect_format_options(t, plan.output.container)
            if format_options:
                plan.output.format_options = format_options
                notes.append("format options: "
                             + ", ".join(f"{k}={v}" for k, v in format_options.items()))
            if bsfs:
                plan.bitstream_filters = [
                    BitstreamFilterSpec(name=f["name"], options=f["options"])
                    for f in bsfs
                ]
                notes.append("bitstream filter(s): " + ", ".join(
                    f["name"] + ("" if not f["options"] else
                                 " (" + ", ".join(f"{k}={v}" for k, v in f["options"].items()) + ")")
                    for f in bsfs))
            if filtergraph is not None:
                plan.filtergraph = filtergraph
                notes.append("custom filtergraph set")

        # final capability gate — the registry is the single source of truth.
        # (Field mutations above bypass the model validators, so the check
        # must run explicitly here — both the model's _capability/_mode
        # consistency rules and the container codec-option tables.)
        errors = capability_errors(plan.output) + format_option_errors(
            plan.output.container, plan.output.format_options)
        try:
            OutputSpec.model_validate(plan.output.model_dump())
        except ValueError as exc:
            msgs = [e["msg"] for e in getattr(exc, "errors", lambda: [{}])()]
            for msg in msgs:
                errors.append(msg.removeprefix("Value error, ").strip())
            if not msgs:
                errors.append(str(exc))
        if errors:
            return ParseResult(
                status=ParseStatus.UNSUPPORTED,
                unsupported=[UnsupportedPart(fragment=plan.output.container,
                                              reason=e) for e in errors],
            )

        return ParseResult(status=ParseStatus.READY, plan=plan, defaults=notes)


# ---------------------------------------------------------------- revision

def _merge_revision(previous: EditPlan, t: str, trim, resize, audio, watermark, grayscale,
                    audio_replace=None, dest=None, crop=None, rotate=None, speed=None,
                    audio_resample=None, subtitle=None, gpu=None, qsv=None, codec=None,
                    container=None, audio_codec=None, audio_container=None,
                    audio_bitrate=None, video_bitrate=None, mode=None,
                    sub_dump=None, filters=None, filtergraph=None, input_ext=None,
                    video_codec_options=None, audio_codec_options=None):
    """Apply a follow-up instruction to a previous plan, preserving
    everything the instruction does not mention (FR-6)."""
    plan = previous.model_copy(deep=True)
    changed: list[str] = []
    # a stream-copy cut phrasing ('copy trim ...') applies to this
    # revision's trim, before it is merged below
    if trim is not None and _copy_trim_requested(t):
        trim.stream_copy = True

    # --- a mode switch resets the fields the new mode forbids ------------
    if mode is not None and mode != plan.output.mode:
        if mode == "subtitles":
            plan.output.mode = "subtitles"
            plan.output.container = sub_dump or "srt_file"
            plan.output.video_codec = None
            plan.output.audio_codec = None
            plan.output.video_bitrate_kbps = None
            plan.output.pixel_format = None
            plan.output.video_profile = None
            plan.output.video_preset = None
            plan.output.gpu = False
            plan.output.qsv = False
            plan.output.video_codec_options = {}
            plan.output.audio_codec_options = {}
            plan.output.audio_mode = AudioMode.ENCODE
            plan.filtergraph = None
            if plan.output_destination is not None:
                plan.output_destination = None
                changed.append("stream destination dropped (a subtitle dump is a file)")
            if plan.operations:
                plan.operations = []
                changed.append("operations dropped (a subtitle dump takes none)")
            changed.append(f"mode -> subtitle dump ({plan.output.container})")
        elif mode == "audio":
            plan.output.mode = "audio"
            plan.output.video_codec = None
            plan.output.video_bitrate_kbps = None
            plan.output.pixel_format = None
            plan.output.video_profile = None
            plan.output.video_preset = None
            plan.output.gpu = False
            plan.output.qsv = False
            plan.output.video_codec_options = {}
            plan.filtergraph = None
            if plan.output_destination is not None:
                plan.output_destination = None
                changed.append("stream destination dropped (an audio output is a file)")
            new_container = _resolve_audio_container(container, audio_container, audio_codec)
            if new_container is None and plan.output.container not in _AUDIO_CONTAINERS:
                if input_ext in _EXT_TO_CONTAINER and _EXT_TO_CONTAINER[input_ext] in _AUDIO_CONTAINERS:
                    new_container = _EXT_TO_CONTAINER[input_ext]
                else:
                    new_container = "mp3"
            if new_container is not None and plan.output.container != new_container:
                plan.output.container = new_container
                changed.append(f"container -> {new_container} (audio-only output)")
            codec_choice = audio_codec or plan.output.audio_codec
            if codec_choice is not None and codec_choice not in \
                    REGISTRY["matrix"].get(plan.output.container, {}).get("audio", []):
                swap = audio_container_for_codec(codec_choice)
                if swap is not None and swap != plan.output.container:
                    plan.output.container = swap
                    changed.append(f"container -> {swap} (carries {codec_choice})")
            if codec_choice is None:
                codec_choice = audio_codec_for(plan.output.container, None)
                changed.append(f"audio codec -> {codec_choice} (the {plan.output.container.upper()} default)")
            if codec_choice is not None and plan.output.audio_codec != codec_choice:
                plan.output.audio_codec = codec_choice
                changed.append(f"audio codec -> {codec_choice}")
            if plan.output.audio_mode != AudioMode.ENCODE:
                plan.output.audio_mode = AudioMode.ENCODE
            kept = [o for o in plan.operations
                    if o.type in ("speed", "audio_resample", "audio_replace")
                    or (o.type == "filter" and o.domain == "audio")]
            if len(kept) != len(plan.operations):
                plan.operations = kept
                changed.append("video edits dropped (an audio-only output carries no video)")
            changed.append("mode -> audio-only")
        else:  # back to video
            plan.output.mode = "video"
            if plan.output.container not in _VIDEO_CONTAINERS:
                plan.output.container = "mp4"
                changed.append("container -> mp4 (video output)")
            changed.append("mode -> video")

    # --- field updates, per current mode ----------------------------------
    if plan.output.mode == "video":
        if gpu is not None and plan.output.gpu != gpu:
            plan.output.gpu = gpu
            changed.append("gpu -> on (nvenc)" if gpu else "gpu -> off (cpu)")
        if qsv is not None and plan.output.qsv != qsv:
            plan.output.qsv = qsv
            changed.append("qsv -> on (intel quick sync)" if qsv else "qsv -> off")
        if container is not None and plan.output.container != container:
            plan.output.container = container
            changed.append(f"container -> {container}")
        if codec is not None and plan.output.video_codec != codec:
            plan.output.video_codec = codec
            changed.append(f"video codec -> {codec}")
        if plan.output.video_codec is None:
            # restored after an away-mode that dropped it
            plan.output.video_codec = default_codec(plan.output.container)
            if plan.output.video_codec:
                changed.append(f"video codec -> {plan.output.video_codec} "
                               f"(the {plan.output.container.upper()} preset default)")
        # an explicit codec lands in its natural home when no container
        # was named ('make it prores' on an mp4 plan -> mov)
        if codec is not None and container is None:
            natural = _NATURAL_VIDEO_CONTAINER.get(codec)
            if natural is not None and plan.output.container != natural:
                plan.output.container = natural
                changed.append(f"container -> {natural} (the natural home for {codec.upper()})")
        if video_bitrate is not None and plan.output.video_bitrate_kbps != video_bitrate:
            plan.output.video_bitrate_kbps = video_bitrate
            changed.append(f"video bitrate -> {video_bitrate} kbps")
        pixel_format = _detect_pixel_format(t)
        if pixel_format and plan.output.pixel_format != pixel_format:
            plan.output.pixel_format = pixel_format
            changed.append(f"pixel format -> {pixel_format}")
        # an explicit 'profile=X' / 'preset=X' token is a codec option —
        # the NL field detectors must not also claim the same word
        profile = None if "profile" in (video_codec_options or {}) \
            else _detect_profile(t)
        if profile and plan.output.video_profile != profile:
            plan.output.video_profile = profile
            changed.append(f"video profile -> {profile}")
        preset = None if "preset" in (video_codec_options or {}) \
            else _detect_preset(t)
        if preset and plan.output.video_preset != preset:
            plan.output.video_preset = preset
            changed.append(f"video preset -> {preset}")
        # per-encoder options: this instruction's tokens plus the NL forms
        # ('crf 18') mapped onto the encoder the plan now resolves to — a
        # named option always wins over the plan's previous value
        vencoder = video_encoder_name(plan.output.video_codec,
                                      plan.output.gpu, plan.output.qsv)
        vopts = dict(video_codec_options or {})
        for note in _video_codec_option_defaults(t, vopts, vencoder):
            changed.append(note)
        if vopts:
            merged = {**plan.output.video_codec_options, **vopts}
            if merged != plan.output.video_codec_options:
                plan.output.video_codec_options = merged
                changed.append("video codec options: "
                               + ", ".join(f"{k}={v}" for k, v in
                                           sorted(vopts.items())))
    else:
        # audio / subtitle outputs: drop video-only fields this instruction
        # may have named (parse() already clarified the intent)
        for field in ("video_bitrate_kbps", "pixel_format", "video_profile",
                      "video_preset"):
            if getattr(plan.output, field) is not None:
                setattr(plan.output, field, None)
        if plan.output.video_codec_options:
            plan.output.video_codec_options = {}
        if plan.output.gpu:
            plan.output.gpu = False
        if plan.output.qsv:
            plan.output.qsv = False

    if audio_codec is not None and plan.output.mode != "subtitles" \
            and plan.output.audio_codec != audio_codec:
        if audio_codec not in REGISTRY["matrix"].get(plan.output.container, {}).get("audio", []) \
                and plan.output.mode == "audio":
            swap = audio_container_for_codec(audio_codec)
            if swap is not None and swap != plan.output.container:
                plan.output.container = swap
                changed.append(f"container -> {swap} (carries {audio_codec})")
        plan.output.audio_codec = audio_codec
        changed.append(f"audio codec -> {audio_codec}")
    if audio_bitrate is not None and plan.output.audio_bitrate_kbps != audio_bitrate:
        plan.output.audio_bitrate_kbps = audio_bitrate
        changed.append(f"audio bitrate -> {audio_bitrate} kbps")
    # per-encoder audio options: this instruction's tokens plus the NL form
    # ('dialnorm -27'); a named option wins over the plan's previous value
    aopts = dict(audio_codec_options or {})
    _audio_codec_option_defaults(t, aopts)
    if aopts:
        merged = {**plan.output.audio_codec_options, **aopts}
        if merged != plan.output.audio_codec_options:
            plan.output.audio_codec_options = merged
            changed.append("audio codec options: "
                           + ", ".join(f"{k}={v}" for k, v in
                                       sorted(aopts.items())))

    # A newly named destination always wins over the previous one; the scheme
    # fixes the container, so switch it when they disagree.
    if dest is not None and plan.output.mode == "video" and plan.output_destination != dest:
        plan.output_destination = dest
        changed.append(f"stream destination -> {dest}")
        d_container = stream_output_info(dest)[0]
        if plan.output.container != d_container:
            plan.output.container = d_container
            changed.append(f"container -> {d_container} (required by the {dest} destination)")

    if plan.output.mode == "video":
        if audio == "keep" and plan.output.audio_mode != AudioMode.ENCODE:
            plan.output.audio_mode = AudioMode.ENCODE
            changed.append("audio -> keep")
        elif audio == "remove" and plan.output.audio_mode != AudioMode.REMOVE:
            plan.output.audio_mode = AudioMode.REMOVE
            changed.append("audio -> remove")

    if audio_replace is not None and plan.output.mode != "subtitles":
        # a replacement replaces any prior replacement and implies encoded audio
        plan.operations = [o for o in plan.operations if o.type != "audio_replace"]
        plan.operations.append(audio_replace)
        if plan.output.mode != "subtitles" and plan.output.audio_mode != AudioMode.ENCODE:
            plan.output.audio_mode = AudioMode.ENCODE
        changed.append(f"audio -> replaced ({audio_replace.asset_id})")
    elif audio == "remove" and plan.output.mode == "video" \
            and any(o.type == "audio_replace" for o in plan.operations):
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
    # trim is the one edit a subtitle dump (and an audio extraction) keeps
    if plan.output.mode == "video":
        if resize is not None:
            plan.operations = [o for o in plan.operations if o.type != "resize"]
            plan.operations.insert(0, resize)
            changed.append("resize updated")
        # crop and rotate go right after resize (before watermark/grayscale),
        # so the filter order matches a fresh plan
        base = 1 if any(o.type == "resize" for o in plan.operations) else 0
        for op in (crop, rotate):
            if op is not None:
                plan.operations = [o for o in plan.operations if o.type != op.type]
                plan.operations.insert(base, op)
                base += 1
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
    for op in (speed, audio_resample):
        if op is not None and plan.output.mode != "subtitles":
            plan.operations = [o for o in plan.operations if o.type != op.type]
            plan.operations.append(op)
            changed.append(f"{op.type} updated")

    if filters and plan.output.mode != "subtitles":
        plan.operations = [o for o in plan.operations if o.type != "filter"]
        if plan.output.mode == "video":
            vf = [f for f in filters if f.domain == "video"]
            af = [f for f in filters if f.domain == "audio"]
            if vf:
                # spliced before the subtitles op (burned subs run last)
                try:
                    idx = next(i for i, o in enumerate(plan.operations)
                               if o.type == "subtitles")
                except StopIteration:
                    idx = len(plan.operations)
                plan.operations[idx:idx] = vf
            plan.operations.extend(af)
        else:  # audio mode: audio-domain filters only (parse clarified the rest)
            plan.operations.extend(f for f in filters if f.domain == "audio")
        changed.append("filters updated")
    if filtergraph is not None and plan.output.mode == "video" \
            and plan.filtergraph != filtergraph:
        plan.filtergraph = filtergraph
        changed.append("filtergraph updated")

    # registry-driven format options for the (possibly new) container
    fo = _detect_format_options(t, plan.output.container)
    if fo and {**plan.output.format_options, **fo} != plan.output.format_options:
        plan.output.format_options = {**plan.output.format_options, **fo}
        changed.append("format options: "
                       + ", ".join(f"{k}={v}" for k, v in fo.items()))

    # bitstream filters: a named filter replaces the current list (a new
    # 'h264_metadata level=5.1' means 'set it to 5.1')
    bsfs = _detect_bitstream_filters(t)
    if bsfs:
        plan.bitstream_filters = [
            BitstreamFilterSpec(name=f["name"], options=f["options"])
            for f in bsfs
        ]
        changed.append("bitstream filter(s): "
                       + ", ".join(f["name"] for f in bsfs))
    elif re.search(r"\bremove\s+(?:the\s+)?(?:bitstream\s+filters?|bsfs?)\b", t, re.I) \
            and plan.bitstream_filters:
        plan.bitstream_filters = None
        changed.append("bitstream filters removed")
    # 'frame-accurate' / 're-encode the trim' switches a copy cut back to
    # a re-encoding trim
    if plan.trim is not None and plan.trim.stream_copy and re.search(
            r"\bframe[\s-]?accurate\b|\bre-?encode\s+(?:the\s+)?trim\b", t, re.I):
        plan.trim.stream_copy = False
        changed.append("trim switched back to re-encoding (frame-accurate)")

    return plan, changed
