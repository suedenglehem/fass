"""FastAPI layer over the same generate() the CLI uses (plan0: the web
interface comes after the CLI path works end to end).

PRD §9 API contract: POST /v1/commands takes an instruction, an asset map of
local file references, an output filename, a target shell, an optional
previous plan, and optional pre-collected media metadata; it returns the
structured response (status, plan, argv, rendered command, explanation,
defaults, warnings, validation results, clarifications).

Security invariants (PRD §10), all enforced by design here:
- the service NEVER executes generated commands — it returns argv, period;
- assets are local paths bound by the client; the model never sees paths;
- no media upload endpoints; nothing leaves the machine;
- target shell is POSIX only — other shells are deferred and a non-POSIX
  value is a validation error, not a best-effort render.

Run:  fass serve [--host 127.0.0.1] [--port 8000]
     (or: uvicorn fass.api:app)
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from fass.pipeline import generate
from fass.probe.ffprobe import ffmpeg_available, parse_ffprobe_json, probe_ffmpeg
from fass.registry import REGISTRY
from fass.schema import Asset, EditPlan, guess_kind
from fass.nlu.rules import SUPPORTED_SUMMARY


class CommandRequest(BaseModel):
    """POST /v1/commands body. Malformed bodies → 422 (FastAPI validation)."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=4000)
    assets: dict[str, str] = Field(
        description="asset id -> local file path; must include 'main' (input video)"
    )
    output_filename: str | None = Field(default=None, max_length=1024)
    target_shell: Literal["posix"] = "posix"
    previous_plan: EditPlan | None = None
    media: dict | None = Field(
        default=None, description="pre-collected ffprobe JSON for the input"
    )
    adapter: Literal["auto", "rules", "llm"] = "auto"
    preflight: bool = False


def create_app() -> FastAPI:
    app = FastAPI(
        title="fass",
        version="1.0",
        description="Natural language -> validated FFmpeg commands "
        "(argv first; the service never executes anything).",
    )

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/v1/commands")
    def create_command(body: CommandRequest):
        if "main" not in body.assets:
            raise HTTPException(422, "assets must include 'main' (the input video)")
        try:
            assets = {aid: Asset(path=p, kind=guess_kind(p))
                      for aid, p in body.assets.items()}
        except ValueError as e:  # pydantic ValidationError is a ValueError
            raise HTTPException(422, f"invalid asset reference: {e}")

        media = parse_ffprobe_json(body.media) if body.media else None
        resp = generate(
            body.instruction,
            assets,
            output_filename=body.output_filename,
            previous_plan=body.previous_plan,
            media=media,
            adapter_name=body.adapter,
            preflight_check=body.preflight,
        )
        # Every domain outcome (ready / needs_clarification / unsupported /
        # error) is a structured 200; 4xx is reserved for malformed requests.
        return resp.to_dict()

    @app.get("/v1/support")
    def support():
        return {
            "summary": SUPPORTED_SUMMARY,
            "registry_version": REGISTRY["registry_version"],
            "video_codecs": list(REGISTRY["video_codecs"]),
            "audio_codecs": list(REGISTRY["audio_codecs"]),
        }

    @app.get("/v1/env")
    def env():
        if not ffmpeg_available():
            return {"available": False, "note": "ffmpeg not found on PATH"}
        ff = probe_ffmpeg()
        encoders = {}
        for name, spec in REGISTRY["video_codecs"].items():
            encoders[f"video:{name}"] = {
                "encoder": spec["encoder"], "present": spec["encoder"] in ff.encoders,
            }
        for name, spec in REGISTRY["audio_codecs"].items():
            encoders[f"audio:{name}"] = {
                "encoder": spec["encoder"], "present": spec["encoder"] in ff.encoders,
            }
        return {
            "available": True,
            "build": ff.version,
            "registry_version": REGISTRY["registry_version"],
            "encoders": encoders,
            "filters": {n: n in ff.filters for n in ("scale", "pad", "hue", "overlay", "format")},
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        return UI_HTML

    return app


app = create_app()


# ------------------------------------------------------------- minimal UI
# Self-contained page (no external assets, per the local-first model): a
# request form, the clarification flow, and the command output — PRD §9 UI.

UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fass — natural language → validated FFmpeg commands</title>
<style>
:root {
  --bg: #f6f7f9; --card: #fff; --text: #1c2128; --muted: #57606a;
  --border: #d0d7de; --accent: #0969da;
  --ready: #1a7f37; --warn: #9a6700; --bad: #cf222e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --card: #161b22; --text: #e6edf3; --muted: #8b949e;
    --border: #30363d; --accent: #58a6ff;
    --ready: #3fb950; --warn: #d29922; --bad: #f85149;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; font-size: 14px; }
form, .result { background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; margin-bottom: 16px; }
label { display: block; font-size: 13px; color: var(--muted); margin: 10px 0 4px; }
input, textarea, select { width: 100%; padding: 8px 10px; font: inherit;
  color: var(--text); background: var(--bg); border: 1px solid var(--border);
  border-radius: 6px; }
textarea { min-height: 64px; resize: vertical; }
.row { display: flex; gap: 12px; flex-wrap: wrap; }
.row > div { flex: 1 1 200px; }
.check { display: flex; align-items: center; gap: 8px; margin-top: 12px;
  font-size: 14px; color: var(--text); }
.check input { width: auto; }
button { margin-top: 14px; padding: 9px 18px; font: inherit; font-weight: 600;
  color: #fff; background: var(--accent); border: 0; border-radius: 6px;
  cursor: pointer; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.b-ready { background: color-mix(in srgb, var(--ready) 15%, transparent); color: var(--ready); }
.b-clar { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }
.b-bad   { background: color-mix(in srgb, var(--bad) 15%, transparent); color: var(--bad); }
pre { background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 12px; overflow-x: auto; font-size: 13px; white-space: pre-wrap;
  word-break: break-all; }
.cmd { font-weight: 600; }
h2 { font-size: 14px; margin: 14px 0 6px; color: var(--muted); }
ul { margin: 4px 0; padding-left: 20px; }
li { margin: 2px 0; }
.copy { margin-left: 10px; padding: 2px 10px; font-size: 12px; background: none;
  border: 1px solid var(--border); color: var(--muted); border-radius: 6px; }
</style>
</head>
<body>
<main>
  <h1>fass</h1>
  <p class="sub">Natural language → <strong>validated</strong> FFmpeg commands.
  The model only plans; the command is built deterministically and never executed here.</p>
  <form id="f" autocomplete="off">
    <label for="instr">Instruction</label>
    <textarea id="instr" required placeholder="cut from 00:10 to 00:30, resize to 720p, and add my logo to the bottom-right corner"></textarea>
    <div class="row">
      <div><label for="inp">Input video (local path)</label>
        <input id="inp" required placeholder="clip.mp4"></div>
      <div><label for="out">Output filename (optional)</label>
        <input id="out" placeholder="default: clip_out.mp4"></div>
    </div>
    <div class="row">
      <div><label for="wm">Watermark image (optional)</label>
        <input id="wm" placeholder="logo.png"></div>
      <div><label for="ad">Adapter</label>
        <select id="ad"><option>auto</option><option>rules</option><option>llm</option></select></div>
    </div>
    <label class="check"><input type="checkbox" id="pf"> preflight against the local FFmpeg build</label>
    <button type="submit">Generate</button>
  </form>
  <div class="result" id="res" hidden></div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function submit(e) {
  e.preventDefault();
  const assets = { main: $('inp').value.trim() };
  if ($('wm').value.trim()) assets.watermark = $('wm').value.trim();
  const body = {
    instruction: $('instr').value.trim(),
    assets,
    target_shell: 'posix',
    adapter: $('ad').value,
    preflight: $('pf').checked,
  };
  if ($('out').value.trim()) body.output_filename = $('out').value.trim();
  $('res').hidden = false;
  $('res').innerHTML = '<span class="badge b-clar">working…</span>';
  try {
    const r = await fetch('/v1/commands', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) {
      $('res').innerHTML = `<span class="badge b-bad">request error</span>
        <pre>${esc(typeof data.detail === 'string' ? data.detail : JSON.stringify(data, null, 2))}</pre>`;
      return;
    }
    render(data);
  } catch (err) {
    $('res').innerHTML = `<span class="badge b-bad">request failed</span><pre>${esc(err)}</pre>`;
  }
}
function lists(items) {
  if (!items || !items.length) return '';
  return '<ul>' + items.map(i => `<li>${esc(i)}</li>`).join('') + '</ul>';
}
function render(d) {
  const badge = d.status === 'ready' ? 'b-ready' :
                d.status === 'needs_clarification' ? 'b-clar' : 'b-bad';
  let html = `<span class="badge ${badge}">${esc(d.status)}</span>`;
  if (d.status === 'ready') {
    html += `<h2>Command</h2>
      <pre class="cmd" id="cmd">${esc(d.command)}</pre>
      <button class="copy" onclick="navigator.clipboard.writeText($('cmd').textContent)">copy</button>
      <h2>Argument array (shell-safe)</h2><pre>${esc(JSON.stringify(d.arguments, null, 2))}</pre>
      <h2>What it does</h2>${lists(d.summary)}
      <h2>Defaults applied</h2>${lists(d.defaults) || '<p>none</p>'}
      <h2>Warnings</h2>${lists(d.warnings) || '<p>none</p>'}
      <h2>Validation</h2><p>${esc(d.validation_level)} — ${esc((d.checks || []).map(c => c.check + ': ' + c.status).join('; ') || 'n/a')}</p>`;
  } else if (d.status === 'needs_clarification') {
    html += '<h2>Please clarify</h2>' + lists(
      (d.clarifications || []).map(c => c.question || c.reason || JSON.stringify(c)));
  } else if (d.status === 'unsupported') {
    html += '<h2>Not supported in the MVP</h2>' + lists(
      (d.unsupported || []).map(u => (u.reason || '') + (u.hint ? ' — ' + u.hint : ''))) +
      '<h2>Supported instead</h2><p>' + esc(d.unsupported && d.unsupported[0] && d.unsupported[0].hint || '') + '</p>';
  } else {
    html += '<h2>Errors</h2>' + lists(d.errors);
  }
  $('res').innerHTML = html;
}
$('f').addEventListener('submit', submit);
</script>
</body>
</html>
"""
