"""LLM NLU adapter (PRD section 8).

A capable pretrained model with schema-constrained output. Its only job
is to translate intent into the strict editing-plan schema (or a
clarification / unsupported result). It never emits FFmpeg flags or file
paths: assets are referenced by id, and the deterministic builder is the
only thing that ever touches FFmpeg syntax. Model output is validated
against the same Pydantic schema as the rule-based adapter.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from fass.probe.ffprobe import MediaInfo
from fass.registry import REGISTRY
from fass.schema import AssetMap, EditPlan, ParseResult, ParseResult as Envelope, ParseStatus

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"


def _plan_schema() -> str:
    return json.dumps(
        EditPlan.model_json_schema(),
        indent=1,
    )


def _asset_lines(assets: AssetMap) -> str:
    lines = []
    for aid, a in assets.items():
        label = f" (label: {a.label})" if a.label else ""
        lines.append(f"- {aid}: {a.kind.value}{label}")
    return "\n".join(lines) or "- (no assets)"


SYSTEM_TEMPLATE = """You are the intent parser for fass, an FFmpeg command assistant.
Translate the user's editing request into ONE JSON object with exactly this shape:

{{
  "status": "ready" | "needs_clarification" | "unsupported",
  "plan": null | <EditPlan>,
  "clarifications": [ {{"question": str, "hint": str|null}} ],
  "unsupported": [ {{"fragment": str, "reason": str}} ],
  "defaults": [str]
}}

EditPlan schema:
{schema}

Supported operations: resize (width/height in pixels, aspect_ratio "preserve"|"exact"),
grayscale, watermark (asset_id, position top_left|top_right|bottom_left|bottom_right|center,
margin_px, scale_percent). Output containers: {containers}. Video codecs: {codecs}.
Codec/container matrix (anything else is unsupported): {matrix}
Audio: "encode" (default, keep) or "remove".
{ops_doc}

Available assets (reference by id ONLY — never emit file paths):
{assets}

Rules:
- Never invent operations, parameters, or FFmpeg flags. No raw ffmpeg options or filter expressions anywhere in the plan.
- Times are numbers of seconds. "720p" means resize height 720 with aspect_ratio "preserve" (no padding to 1280x720).
- If any part of the request is beyond the supported set (e.g. speed change, rotation, cropping, volume, subtitles, concatenation, HDR, lossless), set status "unsupported" and name the fragment in "unsupported" — do NOT return a partial plan.
- If a detail is missing or ambiguous in a way that materially changes the edit (e.g. "make it smaller" without a target, missing watermark file, exact file size, lossless vs high quality), set status "needs_clarification" with exactly one focused question.
- Use documented defaults for low-risk choices and record them in "defaults".
- For revisions: start from the previous plan, change only what the instruction changes, preserve everything else, and return the complete updated plan.
- If the instruction matches nothing supported and is not a revision, ask which edit is wanted.

Examples:

User: Resize to 720p, cut from 00:10 to 00:30, make it black and white, and save as MP4.
{{"status": "ready", "plan": {{"schema_version": "1.0", "input_id": "main", "output": {{"container": "mp4", "video_codec": "h264", "audio_mode": "encode"}}, "trim": {{"start_seconds": 10, "duration_seconds": 20}}, "operations": [{{"type": "resize", "height": 720, "aspect_ratio": "preserve"}}, {{"type": "grayscale"}}]}}, "clarifications": [], "unsupported": [], "defaults": []}}

User: Add my logo to the bottom-right corner.
{{"status": "ready", "plan": {{"schema_version": "1.0", "input_id": "main", "output": {{"container": "mp4", "video_codec": "h264", "audio_mode": "encode"}}, "trim": null, "operations": [{{"type": "watermark", "asset_id": "logo", "position": "bottom_right", "margin_px": 16, "scale_percent": 100}}]}}, "clarifications": [], "unsupported": [], "defaults": ["container defaulted to mp4", "video codec defaulted to h264"]}}

User: Make it smaller.
{{"status": "needs_clarification", "plan": null, "clarifications": [{{"question": "do you mean smaller video dimensions (e.g. 720p) or a smaller file (target size)?", "hint": null}}], "unsupported": [], "defaults": []}}

User: Convert to WebM with H.264.
{{"status": "unsupported", "plan": null, "clarifications": [], "unsupported": [{{"fragment": "H.264 in WebM", "reason": "the supported WebM preset uses VP9"}}], "defaults": []}}

User (revision, previous plan uses h265 in mp4): Use H.264 instead.
{{"status": "ready", "plan": <the complete previous plan with output.video_codec = "h264", everything else unchanged>, "clarifications": [], "unsupported": [], "defaults": []}}

Respond with JSON only — no prose, no markdown fences."""


class LLMAdapter:
    name = "llm"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 90.0):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("FASS_LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def _system(self, assets: AssetMap) -> str:
        matrix = REGISTRY["matrix"]
        matrix_txt = "; ".join(
            f"{c}: video {', '.join(v['video'])}, audio {', '.join(v['audio'])}"
            for c, v in matrix.items()
        )
        ops_doc = "\n".join(
            f"- {name}: {spec['description']}"
            for name, spec in REGISTRY["operations"].items()
        )
        return SYSTEM_TEMPLATE.format(
            schema=_plan_schema(),
            containers=", ".join(REGISTRY["matrix"]),
            codecs=", ".join(REGISTRY["video_codecs"]),
            matrix=matrix_txt,
            ops_doc=ops_doc,
            assets=_asset_lines(assets),
        )

    def parse(self, instruction, assets, previous_plan=None, media=None) -> ParseResult:
        if not self.available():
            from fass.nlu.base import AdapterError

            raise AdapterError(
                "no ANTHROPIC_API_KEY configured — set it or use --adapter rules"
            )
        user = [f"Instruction: {instruction}"]
        if media is not None and media.duration is not None:
            user.append(f"Input duration (seconds): {media.duration:g}")
        if previous_plan is not None:
            user.append(
                "Previous plan (this is a revision — change only what the instruction changes):\n"
                + previous_plan.model_dump_json()
            )
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0,
            "system": self._system(assets),
            "messages": [{"role": "user", "content": "\n".join(user)}],
        }
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"LLM provider error (HTTP {e.code}): {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"LLM provider unreachable: {e}") from e

        text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
        data = _extract_json(text)
        try:
            return Envelope.model_validate(data)
        except Exception as e:  # pydantic ValidationError or bad structure
            raise RuntimeError(f"model output failed schema validation: {e}") from e


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
