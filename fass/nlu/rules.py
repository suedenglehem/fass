"""Deterministic rule-based NLU adapter.

This is the offline development and testing stand-in for the LLM adapter
(plan0.md: "you can test command generation without calling a model") and
the fallback when no LLM provider is configured. It returns the same
envelope and produces the same strict schema as the LLM adapter.

Ambiguity is an explicit result (plan0.md section 5): the adapter returns
``ready``, ``needs_clarification``, or ``unsupported`` — never a silent
partial plan.
"""

from __future__ import annotations

import os
import re

from fass.probe.ffprobe import MediaInfo
from fass.registry import default_codec, matrix_error
from fass.schema import (
    Asset,
    AssetKind,
    AudioMode,
    Clarification,
    EditPlan,
    GrayscaleOp,
    OutputSpec,
    ParseResult,
    ParseStatus,
    ResizeOp,
    TrimSpec,
    UnsupportedPart,
    WatermarkOp,
)

SUPPORTED_SUMMARY = (
    "Supported: resize (e.g. '720p', '1280x720'), trim ('cut from 00:10 to 00:30', "
    "'the first 20 seconds'), container conversion (mp4, mov, mkv, webm), video "
    "encoding (H.264, H.265, VP9), grayscale ('black and white'), one image "
    "watermark (position, margin, scale), and audio keep/remove."
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


def _detect_resize(t: str):
    """Return (ResizeOp|None, clarification|None)."""
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

    wh = re.search(r"(?<!\d)(\d{2,5})\s*[x×]\s*(\d{2,5})(?!\d)", t)
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
}


def _detect_container(t: str) -> tuple[str | None, str | None]:
    """Return (container, unsupported_fragment)."""
    if re.search(r"\bquicktime\b", t, re.I):
        return "mov", None
    m = re.search(r"\b(mp4|mkv|webm|mov|m4v)\b", t, re.I)
    if m:
        return _EXT_TO_CONTAINER[m.group(1)], None
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
        if base.lower() in t or (asset.label and asset.label.lower() in t) or aid.lower() in t:
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


# ---------------------------------------------------------------- unsupported

UNSUPPORTED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:speed\s*up|slow\s*down|slow[\s-]?motion|time[\s-]?lapse|faster|slower|twice\s+as\s+fast|three\s+times\s+as\s+fast|\d+(?:\.\d+)?\s*x\s*speed|at\s+\d+(?:\.\d+)?\s*x\b)", re.I), "speed changes"),
    (re.compile(r"\brotat(?:e|ed|ing)\b|\bflip(?:ping)?\b|\bmirror(?:ing)?\b", re.I), "rotation and flipping"),
    (re.compile(r"\bcrop(?:s|ping|ing)?\b", re.I), "cropping"),
    (re.compile(r"\bvolume\b|\blouder\b|\bquieter\b|\bsoften\w*\b|\bamplif\w*\b|\bloudness\b|\bnormali[sz]e\s+audio\b", re.I), "audio level changes"),
    (re.compile(r"\bsubtitles?\b|\bcaptions?\b|\bsrt\b|\bburn\s+(?:in|the)\s+sub\w*", re.I), "subtitles"),
    (re.compile(r"\bconcat(?:enate)?\b|\bmerge\w*\b|\bstitch\w*\b|\bappend\w*\b", re.I), "concatenation of multiple files"),
    (re.compile(r"\bsplit(?:ting)?\b", re.I), "splitting into segments"),
    (re.compile(r"\bblur(?:ring)?\b|\bsepia\b|\bvintage\b|\bretro\b|\bfade(?:s|d|ing)?\b", re.I), "additional color effects"),
    (re.compile(r"\bsharpen\w*\b|\bdenois\w*\b|\bstabil(?:is|iz)\w*\b", re.I), "stabilization, sharpening, denoising"),
    (re.compile(r"add\s+(?:some\s+|the\s+|a\s+)?text\b|\btext\s+overlay\b|\btitle\s+card\b|\btimestamp\b", re.I), "text overlays"),
    (re.compile(r"\bhdr\b|\btone[\s-]?map\w*\b", re.I), "HDR workflows"),
    (re.compile(r"\bgpu\b|\bnvenc\b|\bcuda\b|\bvideotoolbox\b|\bqsv\b|\bhardware\s+accelerat\w*", re.I), "hardware acceleration"),
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

_SIZE_TARGET = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|megabytes?|gigabytes?|kilobytes?|kilobits?|megabits?|kbps|mbps)\b",
    re.I,
)
_VAGUE_SIZE = re.compile(
    r"\b(smaller|small|shrink|shrunk|bigger|enlarge|larger|compress(?:ion|ed)?|lighter)\b", re.I
)
_LOSSLESS = re.compile(r"\blossless\b|without\s+(?:any\s+)?(?:quality\s+)?loss\b", re.I)


def _scan_ambiguous(t: str, resize: ResizeOp | None) -> Clarification | None:
    """Focused questions for requests whose intent cannot be safely
    resolved (plan0.md section 5, PRD clarification policy)."""
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
        resize, resize_clar = _detect_resize(t)
        audio = _detect_audio(t)
        watermark, wm_clar, wm_note = _detect_watermark(t, assets)
        grayscale = GRAYSCALE_RE.search(t) is not None
        codec = _detect_codec(t)
        container, unsupported_container = _detect_container(t)
        if unsupported_container:
            unsupported.append(UnsupportedPart(fragment=unsupported_container,
                                                reason="not in the MVP support set"))
            return ParseResult(status=ParseStatus.UNSUPPORTED, unsupported=unsupported)

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

        # 4. explicit clarifications win (focused, one at a time)
        for c in (trim_clar, resize_clar, wm_clar, ambiguous):
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
                previous_plan, t, trim, resize, audio, watermark, grayscale
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
            if grayscale:
                ops.append(GrayscaleOp())
            if watermark is not None:
                ops.append(watermark)
            plan.operations = ops
            if audio == "remove":
                plan.output.audio_mode = AudioMode.REMOVE

        notes: list[str] = []
        if trim_note:
            notes.append(trim_note)
        if wm_note:
            notes.append(wm_note)

        # 6. resolve output defaults — visible, never hidden
        if previous_plan is None:
            if trim is None and resize is None and watermark is None and not grayscale \
                    and codec is None and container is None and audio != "remove" \
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
                if ext in _EXT_TO_CONTAINER:
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

def _merge_revision(previous: EditPlan, t: str, trim, resize, audio, watermark, grayscale):
    """Apply a follow-up instruction to a previous plan, preserving
    everything the instruction does not mention (FR-6)."""
    plan = previous.model_copy(deep=True)
    changed: list[str] = []

    codec = _detect_codec(t)
    if codec and plan.output.video_codec != codec:
        plan.output.video_codec = codec
        changed.append(f"video codec -> {codec}")
    container, _ = _detect_container(t)
    if container and plan.output.container != container:
        plan.output.container = container
        changed.append(f"container -> {container}")

    if audio == "keep" and plan.output.audio_mode != AudioMode.ENCODE:
        plan.output.audio_mode = AudioMode.ENCODE
        changed.append("audio -> keep")
    elif audio == "remove" and plan.output.audio_mode != AudioMode.REMOVE:
        plan.output.audio_mode = AudioMode.REMOVE
        changed.append("audio -> remove")

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

    if trim is not None and plan.trim != trim:
        plan.trim = trim
        changed.append("trim updated")
    if resize is not None:
        plan.operations = [o for o in plan.operations if o.type != "resize"]
        plan.operations.insert(0, resize)
        changed.append("resize updated")
    if watermark is not None:
        plan.operations = [o for o in plan.operations if o.type != "watermark"]
        plan.operations.append(watermark)
        changed.append("watermark updated")
    if grayscale and not any(o.type == "grayscale" for o in plan.operations):
        plan.operations.append(GrayscaleOp())
        changed.append("grayscale added")

    return plan, changed
