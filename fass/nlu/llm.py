"""LLM NLU adapter (PRD section 8).

A capable pretrained model with schema-constrained output. Its only job
is to translate intent into the strict editing-plan schema (or a
clarification / unsupported result). It never emits FFmpeg flags or file
paths: assets are referenced by id, and the deterministic builder is the
only thing that ever touches FFmpeg syntax. Model output is validated
against the same Pydantic schema as the rule-based adapter.

Two providers (deployment config, M6):
- 'anthropic' — api.anthropic.com (x-api-key), the development default;
- 'openai' — any OpenAI-compatible /v1/chat/completions endpoint
  (strata, vLLM, llama.cpp, …) addressed by host + port from the config
  tab. Thinking models are handled: when the model exhausts its tokens
  in reasoning (content null, reasoning_content set) the request is
  retried once with more headroom.
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
crop (exact width AND height only — an aspect-ratio crop like 16:9 needs a
clarification asking for exact dimensions), speed (factor 0.25-8; "slower" inverts
the factor), rotate (angle 90/180/270 only, clockwise by default — anything else is
unsupported), grayscale, watermark (asset_id, position top_left|top_right|bottom_left|
bottom_right|center, margin_px, scale_percent), subtitles (asset_id of an
srt/ass/ssa/vtt file; mode "burn" is the default — hard-coded into the video,
forces a re-encode — and "soft" muxes a selectable track, which exists only
for mp4/mov (mov_text) and mkv (srt) outputs; for any other container use
"burn"), audio_replace (asset_id of an audio
file — its first audio stream replaces the output audio; the output ends when the
shorter of video and replacement audio ends), audio_resample (sample_rate and/or
channels 1|2|6). Output extras (set on "output", only when the user asks):
"pixel_format" ("yuv420p", "yuv420p10le", "yuv444p" — yuv444p is VP9-only),
"video_profile" ("baseline"|"main"|"high" — H.264; on the GPU only "main"/"high")
and "video_preset" ("ultrafast" through "veryslow" — H.264 CPU only),
"gpu" (bool, default false). Output containers: {containers}. Video codecs: {codecs}.
Codec/container matrix (anything else is unsupported): {matrix}
Audio: "encode" (default, keep) or "remove". An audio_replace operation implies "encode".
GPU encoding (M6): set "gpu": true on "output" when the user asks to encode on the
GPU / with nvenc / with an NVIDIA card. The GPU covers h264 (h264_nvenc) and h265
(hevc_nvenc) only — VP9 is CPU-only, and x264 speed presets do not apply on the GPU.
Target bitrate: an explicit video bitrate ("cbr 10 Mbps", "5 mbps", "2000 kbps") is set as
"video_bitrate_kbps" on the output (integer kbps, e.g. 10000 for 10 Mbps) — a CBR-style
target that overrides the quality preset. A *file size* target ("5 MB file") is NOT a
bitrate: return "needs_clarification".
Stream input/output (M3b):
- An input asset may be a local file or a URL with an allowlisted scheme (http, https,
  rtmp, rtmps, rtsp, rtsps, rtp, udp, tcp, srt, tls). If the instruction names a URL,
  reference its bound asset id — never emit the URL as a path.
- The output may be a stream destination: set "output_destination" on the plan to the
  URL exactly as the user gave it. Only these schemes are allowed: rtmp, rtmps, rtsp,
  rtsps, srt, udp, rtp, tcp. The destination's scheme fixes the output container:
  rtmp/rtmps -> flv, rtsp/rtsps -> rtsp, srt -> srt, udp/rtp/tcp -> mpegts — and
  "output.container" must match it. FLV carries h264 + aac only.
- Naming a stream destination is itself an edit. If the user names a file output format
  that conflicts with the destination's container, return "needs_clarification".
{ops_doc}

Available assets (reference by id ONLY — never emit file paths):
{assets}

Rules:
- Never invent operations, parameters, or FFmpeg flags. No raw ffmpeg options or filter expressions anywhere in the plan.
- Times are numbers of seconds. "720p" means resize height 720 with aspect_ratio "preserve" (no padding to 1280x720).
- An input asset may have kind "source" (a local capture source such as lavfi:/v4l2:/
  alsa:/x11grab:) — reference it by id exactly like any other input.
- If any part of the request is beyond the supported set (e.g. volume changes, extracting subtitles, concatenation, flipping, stabilization, HDR, lossless), set status "unsupported" and name the fragment in "unsupported" — do NOT return a partial plan.
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

User: Make it 2x faster and crop to 1280x720.
{{"status": "ready", "plan": {{"schema_version": "1.0", "input_id": "main", "output": {{"container": "mp4", "video_codec": "h264", "audio_mode": "encode"}}, "trim": null, "operations": [{{"type": "crop", "width": 1280, "height": 720}}, {{"type": "speed", "factor": 2}}]}}, "clarifications": [], "unsupported": [], "defaults": ["container defaulted to mp4", "video codec defaulted to h264"]}}

User: Convert to WebM with H.264.
{{"status": "unsupported", "plan": null, "clarifications": [], "unsupported": [{{"fragment": "H.264 in WebM", "reason": "the supported WebM preset uses VP9"}}], "defaults": []}}

User: Cut the first 5 seconds and stream it to rtmp://live.example.com:1935/show.
{{"status": "ready", "plan": {{"schema_version": "1.0", "input_id": "main", "output": {{"container": "flv", "video_codec": "h264", "audio_mode": "encode"}}, "trim": {{"start_seconds": 0, "duration_seconds": 5}}, "operations": [], "output_destination": "rtmp://live.example.com:1935/show"}}, "clarifications": [], "unsupported": [], "defaults": ["container set to flv by the stream destination"]}}

User: Encode this on the GPU as H.265.
{{"status": "ready", "plan": {{"schema_version": "1.0", "input_id": "main", "output": {{"container": "mp4", "video_codec": "h265", "audio_mode": "encode", "gpu": true}}, "trim": null, "operations": []}}, "clarifications": [], "unsupported": [], "defaults": ["container defaulted to mp4", "GPU encoding: H.265 via hevc_nvenc"]}}

User (revision, previous plan uses h265 in mp4): Use H.264 instead.
{{"status": "ready", "plan": <the complete previous plan with output.video_codec = "h264", everything else unchanged>, "clarifications": [], "unsupported": [], "defaults": []}}

Respond with JSON only — no prose, no markdown fences."""


class LLMAdapter:
    name = "llm"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: float = 90.0, provider: str | None = None,
                 host: str | None = None, port: int | None = None):
        self.timeout = timeout
        self.provider = (provider or os.environ.get("FASS_LLM_PROVIDER", "anthropic")).lower()
        self.host = host or os.environ.get("FASS_LLM_HOST", "")
        self.port = int(port or os.environ.get("FASS_LLM_PORT", 0))
        if self.provider == "openai":
            # No default model for a third-party endpoint: the model id is
            # whatever the server actually loads (config tab).
            self.model = model or os.environ.get("FASS_LLM_MODEL", "")
            self.api_key = api_key or os.environ.get("FASS_LLM_API_KEY", "")
        else:
            self.model = model or os.environ.get("FASS_LLM_MODEL", DEFAULT_MODEL)
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def available(self) -> bool:
        if self.provider == "openai":
            return bool(self.host and self.port and self.model)
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
        from fass.nlu.base import AdapterError

        if not self.available():
            if self.provider == "openai":
                raise AdapterError(
                    "no OpenAI-compatible LLM configured — set the LLM host/port "
                    "(config tab) or use --adapter rules"
                )
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
        system = self._system(assets)
        text = (
            self._chat_openai(system, "\n".join(user))
            if self.provider == "openai"
            else self._chat_anthropic(system, "\n".join(user))
        )
        data = _extract_json(text)
        try:
            return Envelope.model_validate(data)
        except Exception as e:  # pydantic ValidationError or bad structure
            raise RuntimeError(f"model output failed schema validation: {e}") from e

    def test(self) -> tuple[str, float]:
        """Minimal end-to-end probe (config tab 'test LLM'): a one-word
        reply through the real request path, with latency in seconds."""
        import time

        t0 = time.monotonic()
        text = (
            self._chat_openai("Reply with the single word: ok", "ping")
            if self.provider == "openai"
            else self._chat_anthropic("Reply with the single word: ok", "ping")
        )
        return text.strip()[:80], round(time.monotonic() - t0, 1)

    # ------------------------------------------------------------ providers

    def _chat_anthropic(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
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

        return "".join(
            b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
        )

    def _chat_openai(self, system: str, user: str) -> str:
        """OpenAI-compatible /v1/chat/completions (strata, vLLM, …).

        Thinking models can exhaust max_tokens in their reasoning trace:
        the response then carries content=null with reasoning_content set
        (finish_reason 'length'). One retry with 8x headroom covers that;
        a second empty answer is a real error."""
        url = f"http://{self.host}:{self.port}/v1/chat/completions"
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        last_finish = "unknown"
        for attempt, max_tokens in enumerate((2048, 16384)):
            body = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers=headers, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                raise RuntimeError(f"LLM provider error (HTTP {e.code}): {detail}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise RuntimeError(f"LLM provider unreachable: {e}") from e

            choices = payload.get("choices") or [{}]
            choice = choices[0]
            msg = choice.get("message") or {}
            content = (msg.get("content") or "").strip()
            last_finish = str(choice.get("finish_reason") or "unknown")
            if content:
                return content
            if attempt == 0:
                continue  # empty answer with reasoning trace: retry with headroom
        raise RuntimeError(
            f"model returned no answer (finish_reason={last_finish}) — a "
            "thinking model can exhaust max_tokens in its reasoning trace; "
            "raise max_tokens or lower the thinking budget in the model config"
        )


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
