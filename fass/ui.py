"""The web UI (PRD §9): a single self-contained page, no external assets
(local-first model). Six tabs:

- Command — the request form (incl. the verbose-log toggle), the
  clarification flow and the generated command (with a Run button when
  execution is enabled);
- Runs — the executed commands, live status, stop, log and output links;
- Info / Log — media info (ffprobe JSON for an input or output file) and
  the run/verbose log browser;
- Config — the deployment config (M6): volume mappings, stream port
  mappings, GPU, the LLM provider, LLM test, save / apply / compose preview;
- RAG — direct evidence lookup against the fass-rag service (M11.2,
  PRD §9): query field, chunk cards with manual links, fail-open status;
- Files — the input and output folders side by side (name / size / date),
  with a right-click menu per file: ffprobe JSON, mediainfo JSON and the
  first frame as a JPEG, each shown in a closable popup.
"""

UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ffmpeg assistant — natural language → validated FFmpeg commands</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Quicksand:wght@400;500;600;700&display=swap');
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
/* explicit theme (Config → Appearance): a data-theme attribute on <html>
   that wins over the prefers-color-scheme auto above whenever present.
   The Quicksand webfont (Hello Kitty only) degrades to the system stack
   when offline — the palette carries the scheme either way. */
html[data-theme="light"] {
  --bg: #f6f7f9; --card: #fff; --text: #1c2128; --muted: #57606a;
  --border: #d0d7de; --accent: #0969da;
  --ready: #1a7f37; --warn: #9a6700; --bad: #cf222e;
}
html[data-theme="dark"] {
  --bg: #0d1117; --card: #161b22; --text: #e6edf3; --muted: #8b949e;
  --border: #30363d; --accent: #58a6ff;
  --ready: #3fb950; --warn: #d29922; --bad: #f85149;
}
html[data-theme="kitty"] {
  --bg: #fff5f9; --card: #fff; --text: #7a4059; --muted: #b57f98;
  --border: #ffc5dc; --accent: #ff4f9a;
  --ready: #1a7f37; --warn: #9a6700; --bad: #cf222e;
}
html[data-theme="kitty"] body { font-family: 'Quicksand', system-ui, -apple-system, sans-serif; }
html[data-theme="kitty"] h1 { color: var(--accent); }
html[data-theme="kitty"] .card { border: 2px solid var(--border); border-radius: 24px;
  box-shadow: 0 6px 16px #ffb6d133; }
html[data-theme="kitty"] button { border-radius: 30px; }
html[data-theme="autodesk"] {
  --bg: #383838; --card: #454545; --text: #e0e0e0; --muted: #aaaaaa;
  --border: #555555; --accent: #e8a04a;
  --ready: #3fb950; --warn: #d29922; --bad: #f85149;
}
html[data-theme="autodesk"] body { font: 13px/1.5 Arial, sans-serif; }
html[data-theme="autodesk"] input,
html[data-theme="autodesk"] select,
html[data-theme="autodesk"] textarea { background: #303030; border-color: #222; }
/* Maya's solid-gray toolbar buttons; the orange accent stays on links, the
   active tab underline and status badges */
html[data-theme="autodesk"] button:not(.danger):not(.ghost):not(.copy) {
  background: #505050; border: 1px solid #606060; border-radius: 2px; color: var(--text); }
html[data-theme="autodesk"] button:not(.danger):not(.ghost):not(.copy):hover { background: #626262; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; font-size: 14px; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.tabs button { padding: 8px 16px; font: inherit; font-weight: 600; color: var(--muted);
  background: none; border: 0; border-bottom: 2px solid transparent; cursor: pointer; }
.tabs button.on { color: var(--accent); border-bottom-color: var(--accent); }
form, .result, .card { background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; margin-bottom: 16px; }
label { display: block; font-size: 13px; color: var(--muted); margin: 10px 0 4px; }
input, textarea, select { width: 100%; padding: 8px 10px; font: inherit;
  color: var(--text); background: var(--bg); border: 1px solid var(--border); border-radius: 6px; }
textarea { min-height: 64px; resize: vertical; }
.row { display: flex; gap: 12px; flex-wrap: wrap; }
.row > div { flex: 1 1 200px; }
.check { display: flex; align-items: center; gap: 8px; margin-top: 12px;
  font-size: 14px; color: var(--text); }
.check input { width: auto; }
button { margin-top: 14px; padding: 9px 18px; font: inherit; font-weight: 600;
  color: #fff; background: var(--accent); border: 0; border-radius: 6px; cursor: pointer; }
button.small { margin-top: 0; padding: 3px 10px; font-size: 12px; font-weight: 500; }
button.danger { background: var(--bad); }
button.ghost { background: none; border: 1px solid var(--border); color: var(--muted); }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.b-ready { background: color-mix(in srgb, var(--ready) 15%, transparent); color: var(--ready); }
.b-clar { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }
.b-bad   { background: color-mix(in srgb, var(--bad) 15%, transparent); color: var(--bad); }
.b-run   { background: color-mix(in srgb, var(--accent) 15%, transparent); color: var(--accent); }
pre { background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 12px; overflow-x: auto; font-size: 13px; white-space: pre-wrap; word-break: break-all; }
.cmd { font-weight: 600; }
textarea.cmd { font-family: ui-monospace, monospace; font-size: 13px; font-weight: 600;
  width: 100%; min-height: 0; padding: 10px 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all;
  color: var(--text); background: var(--bg); border: 1px solid var(--border); border-radius: 6px; }
h2 { font-size: 14px; margin: 14px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; }
h3 { font-size: 14px; margin: 18px 0 6px; }
/* section separators in the config card: the card frame's own line, full
   frame width (the negative margins eat the card's padding + border) */
.card hr.sep { border: 0; border-top: 1px solid var(--border); margin: 18px -17px 14px; }
ul { margin: 4px 0; padding-left: 20px; }
li { margin: 2px 0; }
.copy { margin-left: 10px; padding: 2px 10px; font-size: 12px; background: none;
  border: 1px solid var(--border); color: var(--muted); border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; }
.note { font-size: 13px; color: var(--muted); }
.err { color: var(--bad); font-size: 13px; }
.ok { color: var(--ready); font-size: 13px; }
a { color: var(--accent); }
.sources { margin-top: 14px; }
.sources summary { cursor: pointer; font-size: 14px; color: var(--muted);
  text-transform: uppercase; letter-spacing: .03em; margin-bottom: 6px; }
.sources summary:hover { color: var(--text); }
.sources summary::-webkit-details-marker { display: none; }
.sources summary::before { content: "▸ "; }
.sources[open] summary::before { content: "▾ "; }
.sources .badge { margin-bottom: 4px; }
.rag-results { max-height: 70vh; overflow-y: auto; margin-top: 12px; }
.rag-card { margin-top: 10px; }
.rag-head { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 8px; }
.badge.mini { font-size: 11px; padding: 1px 8px; margin: 0 4px 4px 0; }
.portrow { display: flex; gap: 8px; margin: 6px 0; align-items: center; }
.portrow input { width: auto; flex: 1; }
.portrow select { width: auto; }
.portrow .name { flex: 2; }
.filecols { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 10px; }
.filecol { flex: 1 1 300px; min-width: 0; }
.filehead { display: flex; gap: 8px; align-items: baseline; font-size: 13px;
  color: var(--muted); text-transform: uppercase; letter-spacing: .03em;
  border-bottom: 1px solid var(--border); padding-bottom: 4px; }
.filerow { display: flex; gap: 8px; align-items: baseline; padding: 4px 2px;
  border-bottom: 1px solid var(--border); font-size: 13px; cursor: context-menu; }
.filerow:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
.filerow .fname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.filerow .fsize, .filerow .fdate { flex: 0 0 auto; color: var(--muted); font-size: 12px; }
.fmenu { position: fixed; z-index: 40; min-width: 150px; display: none;
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  box-shadow: 0 2px 10px rgba(0,0,0,.25); padding: 4px; }
.fmenu button { display: block; width: 100%; margin: 0; padding: 6px 10px;
  font: inherit; font-size: 13px; font-weight: 500; text-align: left;
  color: var(--text); background: none; border: 0; border-radius: 4px; cursor: pointer; }
.fmenu button:hover { background: color-mix(in srgb, var(--accent) 15%, transparent); }
.overlay { position: fixed; inset: 0; z-index: 50; background: rgba(0,0,0,.45);
  display: flex; align-items: flex-start; justify-content: center; padding: 24px 16px; }
.modal { width: 100%; max-width: 760px; background: var(--card);
  border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px;
  box-shadow: 0 6px 24px rgba(0,0,0,.35); }
.overlay[hidden] { display: none; }  /* .overlay's display:flex would win over [hidden] */
.modal pre { max-height: 70vh; overflow: auto; }
.modal img { display: block; margin: 0 auto; max-width: 100%; height: auto; }
.tip { display: inline-block; width: 14px; height: 14px; line-height: 12px;
  text-align: center; font-size: 11px; font-weight: 700; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 999px; cursor: help;
  position: relative; vertical-align: -2px; }
.tip:hover::after { content: attr(data-tip); position: absolute; left: 50%;
  top: 20px; transform: translateX(-50%); width: 280px; white-space: normal;
  background: var(--text); color: var(--bg); font-size: 12px; font-weight: 400;
  line-height: 1.45; padding: 8px 10px; border-radius: 6px; z-index: 10;
  box-shadow: 0 2px 10px rgba(0,0,0,.25); }
.tip:hover::before { content: ""; position: absolute; left: 50%; top: 13px;
  transform: translateX(-50%); border: 5px solid transparent;
  border-bottom-color: var(--text); z-index: 11; }
</style>
</head>
<body>
<main>
  <h1>ffmpeg assistant</h1>
  <p class="sub">Natural language → <strong>validated</strong> FFmpeg commands.
  The model only plans; the command is built deterministically — and runs here only when
  you press Run.</p>

  <div class="tabs">
    <button id="tab-cmd" class="on" onclick="showTab('cmd')">Command</button>
    <button id="tab-runs" onclick="showTab('runs')">Runs</button>
    <button id="tab-info" onclick="showTab('info')">Info / Log</button>
    <button id="tab-cfg" onclick="showTab('cfg')">Config</button>
    <button id="tab-rag" onclick="showTab('rag')">RAG</button>
    <button id="tab-files" onclick="showTab('files')">Files</button>
  </div>

  <!-- ============================================================ COMMAND -->
  <div id="pane-cmd">
  <form id="f" autocomplete="off">
    <label for="instr">Instruction</label>
    <textarea id="instr" required placeholder="cut from 00:10 to 00:30, resize to 720p, and add my logo to the bottom-right corner"></textarea>
    <div class="row">
      <div><label for="inp" id="lbl-inp">Input video (-i a file from the input folder, or a stream URL)</label>
        <input id="inp" required placeholder="-i clip.mp4 or rtmp://host:1935/live"></div>
      <div><label for="out" id="lbl-out">Output (optional — filename, or stream URL to send to: rtmp://, rtsp://, srt://, udp://…)<span class="tip" data-tip="To chain ffmpeg processings, start the filename with /input/ — e.g. /input/step1.mp4 — and the result is written into the input folder instead of /output. The next run then picks it up by bare name (step1.mp4).">?</span></label>
        <input id="out" placeholder="out.mp4, /input/step1.mp4, or rtmp://host:1935/live"></div>
    </div>
    <div id="cmp-hint" class="note" hidden>Quality comparison: the Input field is the <b>reference</b> (the source clip from the input folder), the Output field the <b>distorted</b> clip — its name from the output folder; leave Output empty and the server picks the transcoded file (same name as the input, else the only video, else the newest). Nothing is written to a file: the VMAF / PSNR / SSIM score is printed in the run log (Log tab).</div>
    <div class="row">
      <div><label for="wm">Watermark image (optional)<span class="tip" data-tip="For multiple watermarks, add the other images as additional inputs (kind: image) and name them in the request — e.g. 'put the logo top-left and the badge bottom-right'.">?</span></label>
        <input id="wm" placeholder="logo.png"></div>
      <div><label for="ad">Adapter</label>
        <select id="ad"><option>auto</option><option>rules</option><option>llm</option></select></div>
    </div>
    <label for="extra-h">Additional inputs (optional — audio/video/image files or streams, e.g. "replace the audio with song.mp3")</label>
    <div id="extra"></div>
    <button type="button" class="copy" onclick="addExtra()">+ add input</button>
    <label class="check"><input type="checkbox" id="pf"> preflight against the local FFmpeg build</label>
    <label class="check"><input type="checkbox" id="verbose"> verbose log <span class="note">(every pass runs with -loglevel verbose; the log lands in the output folder's log/ directory — see the Info / Log tab)</span></label>
    <button type="submit">Generate</button>
  </form>
  <div class="result" id="res" hidden></div>
  </div>

  <!-- ============================================================== RUNS -->
  <div id="pane-runs" hidden>
    <div class="card">
      <h2 style="margin-top:0">Executed commands</h2>
      <button class="small ghost" style="margin-top:0" onclick="loadRuns()">↻ refresh</button>
      <span id="runs-note" class="note"></span>
      <div style="overflow-x:auto"><table id="runs-table">
        <thead><tr><th>id</th><th>started</th><th>status</th><th>exit</th><th>command</th><th>output</th><th></th></tr></thead>
        <tbody id="runs-body"></tbody>
      </table></div>
      <pre id="runs-log" hidden style="margin-top:10px"></pre>
    </div>
  </div>

  <!-- ========================================================== INFO / LOG -->
  <div id="pane-info" hidden>
    <div class="card">
      <h2 style="margin-top:0">What to look at</h2>
      <label class="check" style="margin-top:0"><input type="radio" name="infosel" id="sel-mi" checked onchange="showInfo('mi')"> media info <span class="note">(ffprobe report on one file)</span></label>
      <label class="check"><input type="radio" name="infosel" id="sel-log" onchange="showInfo('log')"> run log <span class="note">(what ffmpeg printed while running)</span></label>

      <div id="info-mi">
        <div class="row">
          <div><label for="mi-target">Look in</label>
            <select id="mi-target" onchange="fillMi()"><option value="input">input file</option><option value="output">output file</option></select></div>
          <div><label for="mi-file">File (bare name in that folder)</label>
            <input id="mi-file" placeholder="clip.mp4"></div>
        </div>
        <button type="button" onclick="showMediaInfo()">Show media info</button>
        <span id="mi-status" class="note"></span>
        <pre id="mi-out" hidden style="margin-top:10px"></pre>
      </div>

      <div id="info-log" hidden>
        <div class="row" style="align-items:flex-end">
          <div><label for="log-run">Log file</label><select id="log-run"></select></div>
          <div style="flex:0 0 auto"><button type="button" class="copy" onclick="loadLogs()">↻ refresh</button></div>
        </div>
        <button type="button" onclick="showLogFile()">Show log</button>
        <span id="log-status" class="note"></span>
        <pre id="log-out" hidden style="margin-top:10px"></pre>
        <p class="note">Logs longer than 10000 lines are shown as their last 1000.</p>
      </div>
    </div>
  </div>

  <!-- ============================================================= CONFIG -->
  <div id="pane-cfg" hidden>
    <div class="card">
      <h3 style="margin-top:0">Appearance</h3>
      <div class="row" style="align-items:center">
        <div style="flex:0 0 auto"><label for="theme">theme</label>
          <select id="theme">
            <option value="auto">auto — follow the system</option>
            <option value="light">light</option>
            <option value="dark">night (dark)</option>
            <option value="kitty">pink</option>
            <option value="autodesk">gray</option>
          </select></div>
        <div style="flex:0 0 auto"><label for="width">content width</label>
          <select id="width">
            <option value="">default (860px)</option>
            <option value="1260px">wider (+400px)</option>
          </select></div>
      </div>
      <hr class="sep">
      <h3>Volumes — host folders ↔ container</h3>
      <div class="row">
        <div><label>/input — inputs live here</label><input id="v-in" placeholder="/data/fass/input"></div>
        <div><label>/output — results land here</label><input id="v-out" placeholder="/data/fass/output"></div>
        <div><label>/log — run logs</label><input id="v-log" placeholder="/data/fass/log"></div>
      </div>
      <hr class="sep">
      <h3>Streaming — port mappings (host port ↔ container port)</h3>
      <label class="check" style="margin-top:4px"><input type="checkbox" id="s-en"> enable stream host rewrite
        <span class="note">('host' in a URL → the docker host; udp/rtp inputs bind 0.0.0.0)</span></label>
      <div class="row" style="margin-top:8px">
        <div><label>host address (docker bridge gateway)</label><input id="s-host" value="172.17.0.1"></div>
      </div>
      <div id="ports"></div>
      <button type="button" class="copy" onclick="addPort()">+ add port mapping</button>
      <p class="note">Receive: forward host port → container port (proto must match); the command
      uses <span class="mono">udp://host:PORT</span>. Send/pull: the command targets
      <span class="mono">…://host:PORT</span> and the container resolves it to the host address.</p>

      <hr class="sep">
      <h3>GPU (NVIDIA)</h3>
      <div class="row">
        <div><label class="check" style="margin:0"><input type="checkbox" id="g-en"> GPU enabled (nvenc h264/h265)</label></div>
        <div><label>device</label><input id="g-dev" value="all" placeholder="all, or e.g. 0"></div>
      </div>

      <hr class="sep">
      <h3>LLM provider</h3>
      <div class="row">
        <div><label>provider</label>
          <select id="l-prov"><option value="anthropic">anthropic</option><option value="openai">openai-compatible (strata, vLLM, …)</option></select></div>
        <div><label>host (openai provider)</label><input id="l-host" placeholder="172.17.0.1"></div>
        <div><label>port</label><input id="l-port" type="number" placeholder="8080"></div>
        <div><label>api key (anthropic / optional)</label><input id="l-key" type="password" placeholder="sk-… / blank"></div>
        <div><label>model</label><input id="l-model" placeholder="qwen3.8-27b"></div>
        <div><label>timeout (seconds)</label><input id="l-timeout" type="number" min="1" max="300" step="1" placeholder="10"></div>
      </div>
      <p class="note">timeout: seconds allowed for one LLM request (connect + read) — a hung
      endpoint returns an error instead of blocking the request. 1–300, default 10.</p>
      <button type="button" class="small ghost" style="margin-top:10px" onclick="llmTest(true)">test LLM (unsaved values)</button>
      <span id="llm-status"></span>

      <hr class="sep">
      <h3>RAG — documentation retrieval</h3>
      <div id="rag-status" class="note"></div>
      <p class="note">The fass-rag sidecar serves pinned FFmpeg manual corpora; the LLM
      adapter asks it once per generation and cites what the plan used. Enable it with
      <span class="mono">FASS_RAG_ENABLED=1</span> + <span class="mono">FASS_RAG_URL=…</span>
      (or the <span class="mono">rag</span> block of config.json). It is fail-open: a dead
      service never blocks a generation or changes the command.</p>

      <hr class="sep">
      <h3>Deploy</h3>
      <div id="cfg-status" class="note"></div>
      <div class="row" style="align-items:center">
        <div style="flex:0 0 auto"><button onclick="saveCfg()">Save</button></div>
        <div style="flex:0 0 auto"><button class="danger" id="apply-btn" onclick="applyCfg()" disabled>Apply to container</button></div>
      </div>
      <h3>Generated compose (what Apply creates)</h3>
      <pre id="compose"></pre>
      <hr class="sep">
      <p class="note"><span id="ffver"></span><br id="ffbr" hidden><span id="ver"></span> · Post bugs here:
      <a href="https://github.com/suedenglehem/fass/issues" target="_blank" rel="noopener">https://github.com/suedenglehem/fass/issues</a>
      <br><a href="https://www.youtube.com/watch?v=YVI6SCtVu4c" target="_blank" rel="noopener">if you're still clueless</a></p>
    </div>
  </div>

  <!-- ================================================================= RAG -->
  <div id="pane-rag" hidden>
    <div class="card">
      <h2 style="margin-top:0">RAG — ask the documentation service directly</h2>
      <p class="note">Advisory evidence lookup (PRD §9): the chunks fass-rag retrieves for
      your query, with links into the pinned FFmpeg manual pages. Fail-open — a disabled
      or unreachable service reports here as a status line, never as a failure.</p>
      <label for="rag-q">Query</label>
      <input id="rag-q" placeholder="e.g. overlay a logo in the bottom-right corner">
      <button type="button" onclick="ragQuery()">query</button>
      <button type="button" class="ghost" onclick="ragClear()">clear</button>
      <div id="rag-results" class="rag-results"></div>
    </div>
  </div>

  <!-- ============================================================= FILES -->
  <div id="pane-files" hidden>
    <div class="card">
      <h2 style="margin-top:0">Files — the input and output folders</h2>
      <p class="note">The two deployment volumes, newest first. Right-click a
      file for its <span class="mono">ffprobe</span> /
      <span class="mono">mediainfo</span> report or its first frame.</p>
      <button class="small ghost" style="margin-top:0" onclick="loadFiles()">↻ refresh</button>
      <div class="filecols">
        <div class="filecol">
          <div class="filehead">input <span class="note" id="files-count-input"></span></div>
          <div id="files-input" class="filelist"></div>
        </div>
        <div class="filecol">
          <div class="filehead">output <span class="note" id="files-count-output"></span></div>
          <div id="files-output" class="filelist"></div>
        </div>
      </div>
    </div>
  </div>

  <div id="filemenu" class="fmenu">
    <button type="button" onclick="fileAction('ffprobe')">ffprobe</button>
    <button type="button" onclick="fileAction('mediainfo')">mediainfo</button>
    <button type="button" onclick="fileAction('frame')">1st frame</button>
  </div>

  <div id="filemodal" class="overlay" hidden onclick="if (event.target === this) closeModal()">
    <div class="modal">
      <div style="display:flex;gap:10px;align-items:center;justify-content:space-between">
        <h3 id="modal-title" style="margin:0"></h3>
        <button type="button" class="copy" style="margin-top:0" onclick="closeModal()">close</button>
      </div>
      <div id="modal-body" style="margin-top:10px"></div>
    </div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let lastRunId = null;
let EXEC_ENABLED = false;
fetch('/v1/env').then(r => r.json()).then(d => {
  EXEC_ENABLED = !!d.execution;
  if (d.version) $('ver').textContent = 'v' + d.version;
  // ffmpeg build line (first line of `ffmpeg -version` — no lib* versions)
  if (d.build) { $('ffver').textContent = d.build; $('ffbr').hidden = false; }
}).catch(() => {});

/* Appearance: an explicit theme on <html data-theme> overrides the OS
   prefers-color-scheme; the pick persists in this browser. 'auto' removes
   the attribute so the media query drives the scheme. The content-width
   pick sets an inline max-width on <main> (the CSS default is 860px);
   '' = default. Both persist per browser only. */
(function () {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem('fassTheme'); } catch (e) {}
  if (saved && saved !== 'auto') root.dataset.theme = saved;
  const sel = $('theme');
  if (sel) {
    sel.value = (saved && saved !== 'auto') ? saved : 'auto';
    sel.addEventListener('change', () => {
      const v = sel.value;
      if (v === 'auto') delete root.dataset.theme;
      else root.dataset.theme = v;
      try { localStorage.setItem('fassTheme', v); } catch (e) {}
    });
  }
  const wsel = $('width');
  if (wsel) {
    const main = document.querySelector('main');
    let w = null;
    try { w = localStorage.getItem('fassWidth'); } catch (e) {}
    if (w && !w.endsWith('px')) w += 'px';  // legacy unitless pick
    if (w === '1060px') w = '1260px';       // legacy 'wide' pick → wider
    if (w && main) { wsel.value = w; main.style.maxWidth = w; }
    wsel.addEventListener('change', () => {
      const v = wsel.value;
      if (main) {
        if (v) main.style.maxWidth = v;
        else delete main.style.maxWidth;
      }
      try {
        if (v) localStorage.setItem('fassWidth', v);
        else localStorage.removeItem('fassWidth');
      } catch (e) {}
    });
  }
})();

function showTab(t) {
  // the base four + 'rag' + 'files' (concat keeps the pinned literal in test_webinfo)
  for (const p of ['cmd','runs','info','cfg'].concat('rag','files')) {
    $('pane-'+p).hidden = (p !== t);
    $('tab-'+p).classList.toggle('on', p === t);
  }
  if (t === 'runs') loadRuns();
  if (t === 'info') { loadLogs(); showInfo($('sel-log').checked ? 'log' : 'mi'); }
  if (t === 'cfg') loadRag();
  if (t === 'files') loadFiles();
}

/* ------------------------------------------------------------ command tab */
/* quality-compare convention (the NLU's compare triggers, M14 flip): the
   Input field is the reference (the main asset — the source in the input
   folder), the Output field the distorted clip — its name from the output
   folder; Output empty means the server auto-detects it. Nothing is
   written; the score lands in the run log. */
const CMP_RX = /\bcompar(?:e[sd]?|ison|ing)\b|\bquality\\s+(?:loss|difference|degradation|drop|impact)\b|\blib-?vmaf\b|\bpsnr\b|\bssim\b/i;
const INP_LBL = $('lbl-inp'), OUT_LBL = $('lbl-out');
const INP_HTML = INP_LBL.innerHTML, OUT_HTML = OUT_LBL.innerHTML;
function isCompare() { return CMP_RX.test($('instr').value); }
function setCompareMode() {
  const cmp = isCompare();
  $('cmp-hint').hidden = !cmp;
  INP_LBL.innerHTML = cmp
    ? 'Reference (the source clip — a file from the input folder)'
    : INP_HTML;
  OUT_LBL.innerHTML = cmp
    ? 'Distorted (the transcoded clip — its name from the output folder; leave empty to auto-detect)'
    : OUT_HTML;
}
$('instr').addEventListener('input', setCompareMode);
setCompareMode();
let extraN = 0;
function addExtra() {
  extraN += 1;
  const id = 'in' + extraN;
  const row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = `<div><input class="extra-inp" data-id="${id}" placeholder="e.g. -i song.mp3 (id: ${id})"></div>
    <div style="flex: 0 0 auto"><select class="extra-kind" style="width:auto">
      <option value="auto">auto</option><option value="video">video</option>
      <option value="audio">audio</option><option value="image">image</option>
      <option value="source">source</option>
    </select></div>
    <button type="button" class="copy" onclick="this.parentNode.remove()">remove</button>`;
  $('extra').appendChild(row);
}
async function submit(e) {
  e.preventDefault();
  const cmp = isCompare();
  const assets = {};
  if (cmp) {
    // compare convention (M14 flip): the Input field is the reference —
    // the main asset, the source in the input folder; the Output field
    // names the distorted clip in the output folder (empty = the server
    // auto-detects it). No output_filename — the metric score is printed
    // in the run log
    assets.main = $('inp').value.trim();
    const outv = $('out').value.trim();
    if (outv) assets.distorted = { path: outv, kind: 'video' };
  } else {
    assets.main = $('inp').value.trim();
    if ($('wm').value.trim()) assets.watermark = $('wm').value.trim();
  }
  document.querySelectorAll('.extra-inp').forEach(i => {
    const v = i.value.trim();
    if (!v) return;
    const kind = i.parentElement.querySelector('.extra-kind').value;
    assets[i.dataset.id] = (kind === 'auto') ? v : { path: v, kind };
  });
  const body = {
    instruction: $('instr').value.trim(),
    assets, target_shell: 'posix', adapter: $('ad').value, preflight: $('pf').checked,
    verbose: $('verbose').checked,
  };
  if (!cmp && $('out').value.trim()) body.output_filename = $('out').value.trim();
  $('res').hidden = false;
  $('res').innerHTML = '<span class="badge b-clar">working…</span>';
  try {
    const r = await fetch('/v1/commands', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
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
/* M11.2: the response's evidence block (PRD §6.4) — derived from the
   validated plan, never model-generated. */
function sourcesHtml(ev) {
  if (!ev || !(ev.citations || []).length) return '';
  const cls = ev.status === 'supported' ? 'b-ready'
            : ev.status === 'unavailable' ? 'b-bad' : 'b-clar';
  let head = `<span class="badge ${cls}">${esc(ev.status)}</span>`;
  if (ev.corpus_id)
    head += ` <span class="badge b-run">corpus ${esc(ev.corpus_id)}${ev.corpus_match ? ' · ' + esc(ev.corpus_match) : ''}</span>`;
  if (ev.retrieval_ms != null)
    head += ` <span class="note">${ev.retrieval_ms} ms</span>`;
  let html = `<details open class="sources"><summary>Sources ${head}</summary>`;
  if (ev.note) html += `<p class="note">${esc(ev.note)}</p>`;
  html += '<ul>';
  for (const c of ev.citations) {
    const href = c.url ? (c.anchor ? c.url + '#' + c.anchor : c.url) : null;
    const label = href
      ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(c.title || c.component)}</a>`
      : esc(c.title || c.component);
    html += `<li>${label} <span class="badge b-clar">${esc(c.component)}</span> <span class="note">${esc(c.source)}</span></li>`;
  }
  html += '</ul>';
  if ((ev.warnings || []).length)
    html += '<p class="note">' + ev.warnings.map(w => '— ' + esc(w)).join('<br>') + '</p>';
  return html + '</details>';
}
function render(d) {
  const badge = d.status === 'ready' ? 'b-ready' :
                d.status === 'needs_clarification' ? 'b-clar' : 'b-bad';
  let html = `<span class="badge ${badge}">${esc(d.status)}</span>`;
  if (d.status === 'ready') {
    html += `<h2>Command</h2>
      <textarea class="cmd" id="cmd" rows="4" spellcheck="false">${esc(d.command)}</textarea>
      <pre id="cmdorig" hidden style="display:none">${esc(d.command)}</pre>
      <button class="copy" onclick="navigator.clipboard.writeText($('cmd').value)">copy</button>
      <h2>Argument array (shell-safe)</h2><pre>${esc(JSON.stringify(d.arguments, null, 2))}</pre>
      ${EXEC_ENABLED
        ? `<button id="runbtn" onclick="runCmd()">▶ Run now</button> <span id="rune" class="note"></span>
           <p class="note">editable — the command is re-validated server-side before it runs</p>
           <pre id="cmdargs" hidden style="display:none">${esc(JSON.stringify(d.arguments))}</pre>`
        : '<p class="note">execution is disabled on this deployment (FASS_ALLOW_EXECUTE unset) — copy the command and run it yourself</p>'}
      <h2>What it does</h2>${lists(d.summary)}
      ${sourcesHtml(d.evidence)}
      <h2>Defaults applied</h2>${lists(d.defaults) || '<p>none</p>'}
      <h2>Warnings</h2>${lists(d.warnings) || '<p>none</p>'}
      <h2>Validation</h2><p>${esc(d.validation_level)} — ${esc((d.checks || []).map(c => (c.ok ? 'ok' : 'FAIL') + ': ' + c.name + ' — ' + c.detail).join('; ') || 'n/a')}</p>`;
  } else if (d.status === 'needs_clarification') {
    html += '<h2>Please clarify</h2>' + lists(
      (d.clarifications || []).map(c => c.question || c.reason || JSON.stringify(c)));
  } else if (d.status === 'unsupported') {
    html += '<h2>Not supported in the MVP</h2>' + lists(
      (d.unsupported || []).map(u => (u.reason || '') + (u.hint ? ' — ' + u.hint : ''))) +
      '<h2>Supported instead</h2><p>' + esc(d.unsupported && d.unsupported[0] && d.unsupported[0].hint || '') + '</p>' +
      sourcesHtml(d.evidence);
  } else {
    html += '<h2>Errors</h2>' + lists(d.errors);
  }
  $('res').innerHTML = html;
}
async function runCmd() {
  const argv = JSON.parse($('cmdargs').textContent);
  const body = { arguments: argv, verbose: $('verbose').checked };
  const text = $('cmd').value;
  // an expert edit is sent as `command` (shlex-split + revalidated server-side);
  // an untouched textarea keeps the exact generated argv, as before
  if (text.trim() !== $('cmdorig').textContent.trim()) body.command = text;
  const btn = $('runbtn');
  btn.disabled = true; btn.textContent = 'starting…';
  try {
    const r = await fetch('/v1/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) {
      $('runbtn').disabled = false; $('runbtn').textContent = 'Run now';
      $('rune').textContent = 'run failed: ' + (typeof data.detail === 'string' ? data.detail : JSON.stringify(data));
      return;
    }
    lastRunId = data.run.id;
    $('rune').innerHTML = `run <span class="mono">${esc(data.run.id)}</span> started`;
    showTab('runs');
    setTimeout(loadRuns, 1200);
  } catch (err) {
    $('runbtn').disabled = false; $('runbtn').textContent = 'Run now';
    $('rune').textContent = 'run failed: ' + err;
  }
}
$('f').addEventListener('submit', submit);

/* ------------------------------------------------------------ runs tab */
let runPoll = null;
async function loadRuns() {
  const r = await fetch('/v1/runs');
  const data = await r.json();
  const rows = data.runs || [];
  const tbody = $('runs-body');
  tbody.innerHTML = '';
  for (const run of rows) {
    const tr = document.createElement('tr');
    const out = run.output
      ? `<a href="/media/run/${encodeURIComponent(run.id)}/output" target="_blank">${esc(run.output.split('/').pop())}</a>`
      : '<span class="note">—</span>';
    const statusCls = run.status === 'success' ? 'b-ready' :
                      run.status === 'running' ? 'b-run' : 'b-bad';
    let actions = `<button class="small ghost" onclick="showLog('${esc(run.id)}')">log</button>`;
    if (run.status === 'running') {
      actions += ` <button class="small danger" onclick="stopRun('${esc(run.id)}')">stop</button>`;
    }
    tr.innerHTML = `<td class="mono">${esc(run.id)}</td><td>${esc(run.started)}</td>
      <td><span class="badge ${statusCls}">${esc(run.status)}</span></td>
      <td>${run.exit_code ?? '—'}</td>
      <td class="mono">${esc(run.command)}</td><td>${out}</td><td>${actions}</td>`;
    tbody.appendChild(tr);
  }
  $('runs-note').textContent = rows.some(x => x.status === 'running') ? ' (auto-refreshing…)' : '';
  const anyRunning = rows.some(x => x.status === 'running');
  if (anyRunning && !runPoll) runPoll = setInterval(loadRuns, 2500);
  if (!anyRunning && runPoll) { clearInterval(runPoll); runPoll = null; }
}
async function stopRun(id) {
  await fetch(`/v1/runs/${id}/stop`, { method: 'POST' });
  loadRuns();
}
async function showLog(id) {
  const r = await fetch(`/v1/runs/${id}/log`);
  $('runs-log').hidden = false;
  $('runs-log').textContent = (await r.text()) || '(empty log — ffmpeg prints progress on stderr)';
}

/* -------------------------------------------------------- info / log tab */
function showInfo(which) {
  $('info-mi').hidden = (which !== 'mi');
  $('info-log').hidden = (which !== 'log');
  if (which === 'mi') fillMi();
}
function fillMi() {
  // auto-fill from the Command tab's fields ('-i ' prefix stripped),
  // still editable by hand
  const src = ($('mi-target').value === 'output')
    ? $('out').value.trim() : $('inp').value.trim();
  $('mi-file').value = src.replace(/^-i\\s+/, '');
}
async function showMediaInfo() {
  const target = $('mi-target').value;
  const file = $('mi-file').value.trim();
  const out = $('mi-out');
  $('mi-status').textContent = 'reading…';
  try {
    const r = await fetch(`/v1/mediainfo?target=${encodeURIComponent(target)}&file=${encodeURIComponent(file)}`);
    const data = await r.json();
    if (!r.ok) {
      out.hidden = true;
      $('mi-status').innerHTML = `<span class="err">${esc(typeof data.detail === 'string' ? data.detail : JSON.stringify(data, null, 2))}</span>`;
      return;
    }
    $('mi-status').textContent = '';
    out.hidden = false;
    out.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    out.hidden = true;
    $('mi-status').innerHTML = `<span class="err">${esc(err)}</span>`;
  }
}
async function loadLogs() {
  const sel = $('log-run');
  try {
    const r = await fetch('/v1/logs');
    const data = await r.json();
    const logs = data.logs || [];
    const keep = sel.value;
    sel.innerHTML = '';
    for (const lg of logs) {
      const opt = document.createElement('option');
      opt.value = lg.name;
      opt.textContent = `${lg.name} (${lg.kind})`;
      sel.appendChild(opt);
    }
    if (keep) sel.value = keep;
    $('log-status').textContent = logs.length ? '' : 'no logs on this deployment yet';
  } catch (e) {
    $('log-status').innerHTML = `<span class="err">log list failed: ${esc(e)}</span>`;
  }
}
async function showLogFile() {
  const name = $('log-run').value;
  if (!name) { $('log-status').textContent = 'pick a log first'; return; }
  const out = $('log-out');
  $('log-status').textContent = 'loading…';
  try {
    const r = await fetch(`/v1/logs/${encodeURIComponent(name)}`);
    const text = await r.text();
    if (!r.ok) {
      out.hidden = true;
      $('log-status').innerHTML = `<span class="err">${esc(text)}</span>`;
      return;
    }
    out.hidden = false;
    out.textContent = text || '(empty log — ffmpeg prints progress on stderr)';
    $('log-status').textContent = '';
  } catch (e) {
    $('log-status').innerHTML = `<span class="err">${esc(e)}</span>`;
  }
}

/* ------------------------------------------------------------ config tab */
let cfg = null;
async function loadCfg() {
  const r = await fetch('/v1/config');
  const data = await r.json();
  cfg = data.config;
  $('v-in').value = cfg.volumes.input || '';
  $('v-out').value = cfg.volumes.output || '';
  $('v-log').value = cfg.volumes.log || '';
  $('s-en').checked = cfg.streaming.enabled;
  $('s-host').value = cfg.streaming.host_address;
  $('ports').innerHTML = '';
  for (const mp of cfg.streaming.port_mappings) addPort(mp.name, mp.container_port, mp.host_port, mp.proto);
  $('g-en').checked = cfg.gpu.enabled;
  $('g-dev').value = cfg.gpu.device;
  $('l-prov').value = cfg.llm.provider;
  $('l-host').value = cfg.llm.host;
  $('l-port').value = cfg.llm.port || '';
  $('l-key').value = cfg.llm.api_key;
  $('l-model').value = cfg.llm.model;
  $('l-timeout').value = cfg.llm.timeout || 10;
  $('apply-btn').disabled = !data.docker.worker;
  const dockerNote = data.docker.manage
    ? `docker control: <span class="ok">available</span> (engine ${esc(data.docker.version || '?')}, container <span class="mono">${esc(data.docker.container || 'n/a')}</span>)`
    : `docker control: <span class="note">not mounted — Save persists the config and the compose below is ready to apply by hand` +
      (data.docker.error ? ` <span class="err">(${esc(data.docker.error)})</span>` : '');
  $('cfg-status').innerHTML = data.docker.worker
    ? dockerNote + ` + <span class="ok">fass-worker sidecar</span> (Apply recreates the container)`
    : dockerNote + ` <span class="note">— no fass-worker sidecar: Apply off, use the compose below on the host</span>`;
  $('compose').textContent = data.compose || '';
}
function addPort(name, cport, hport, proto) {
  const row = document.createElement('div');
  row.className = 'portrow';
  row.innerHTML = `<input class="p-name" placeholder="label (e.g. camera-in)" value="${esc(name || '')}">
    <input class="p-cport" type="number" placeholder="container" value="${cport || ''}">
    <input class="p-hport" type="number" placeholder="host" value="${hport || ''}">
    <select class="p-proto"><option value="tcp"${proto === 'tcp' ? ' selected' : ''}>tcp</option><option value="udp"${proto === 'udp' ? ' selected' : ''}>udp</option></select>
    <button type="button" class="copy" onclick="this.parentNode.remove()">remove</button>`;
  $('ports').appendChild(row);
}
function readCfg() {
  return {
    volumes: { input: $('v-in').value.trim(), output: $('v-out').value.trim(), log: $('v-log').value.trim() },
    streaming: {
      enabled: $('s-en').checked,
      host_address: $('s-host').value.trim() || '172.17.0.1',
      port_mappings: [...document.querySelectorAll('.portrow')].map(r => ({
        name: r.querySelector('.p-name').value.trim(),
        container_port: parseInt(r.querySelector('.p-cport').value, 10) || 0,
        host_port: parseInt(r.querySelector('.p-hport').value, 10) || 0,
        proto: r.querySelector('.p-proto').value,
      })).filter(m => m.container_port && m.host_port),
    },
    gpu: { enabled: $('g-en').checked, device: $('g-dev').value.trim() || 'all' },
    llm: {
      provider: $('l-prov').value,
      host: $('l-host').value.trim(),
      port: parseInt($('l-port').value, 10) || 0,
      api_key: $('l-key').value,
      model: $('l-model').value.trim(),
      timeout: parseInt($('l-timeout').value, 10) || 10,
    },
  };
}
async function saveCfg() {
  const r = await fetch('/v1/config', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(readCfg()),
  });
  const data = await r.json();
  $('cfg-status').innerHTML = r.ok
    ? `<span class="ok">saved</span> — LLM, stream addresses and the compose preview are live; volumes/ports/GPU take effect after Apply.`
    : `<span class="err">${esc(typeof data.detail === 'string' ? data.detail : JSON.stringify(data))}</span>`;
  if (r.ok) $('compose').textContent = data.compose || '';
  loadCfg();
}
async function applyCfg() {
  if (!confirm('Recreate the fass container with the saved config?\\nThe UI will lose connection for a few seconds while the container is swapped (the worker sidecar does the work).')) return;
  $('apply-btn').disabled = true;
  $('cfg-status').innerHTML = '<span class="note">asking fass-worker to recreate the container…</span>';
  try {
    const r = await fetch('/v1/config/apply', { method: 'POST' });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data));
    }
  } catch (e) {
    // A connection drop right after the POST is EXPECTED (the container is
    // stopped mid-request) — keep polling either way. A pre-drop error is not.
    if (/Failed to fetch|network|terminated/i.test(String(e))) {
      $('cfg-status').innerHTML = '<span class="note">connection dropped (expected) — waiting for the new container…</span>';
    } else {
      $('cfg-status').innerHTML = `<span class="err">${esc(String(e.message || e))}</span>`;
      $('apply-btn').disabled = false;
      return;
    }
  }
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await new Promise(res => setTimeout(res, 2000));
    let health = false;
    try { health = (await (await fetch('/healthz')).json()).ok === true; } catch {}
    if (!health) continue;
    try {
      const st = await (await fetch('/v1/config/apply-result')).json();
      if (st.status === 'applied') {
        $('cfg-status').innerHTML =
          `<span class="ok">applied</span> — new container <span class="mono">${esc(st.new_container || '')}</span>: ` +
          esc(JSON.stringify(st.ports || {})) + (st.gpu ? ' + GPU' : '');
        $('apply-btn').disabled = false;
        loadCfg();
        return;
      }
      if (st.status === 'failed') {
        $('cfg-status').innerHTML =
          `<span class="err">apply failed: ${esc(st.error || 'unknown error')}</span> — check <span class="mono">docker ps</span> on the host; the old container was re-created if the swap failed partway.`;
        $('apply-btn').disabled = false;
        loadCfg();
        return;
      }
    } catch { /* state file not written yet */ }
    $('cfg-status').innerHTML = '<span class="note">container is back — waiting for apply result…</span>';
  }
  $('cfg-status').innerHTML = '<span class="note">timed out after 120 s — check <span class="mono">docker ps</span> on the host and <span class="mono">/v1/config/apply-result</span> (last-apply.json in the config volume).</span>';
  $('apply-btn').disabled = false;
  loadCfg();
}
async function llmTest(useUnsaved) {
  const el = $('llm-status');
  el.innerHTML = '<span class="note">testing…</span>';
  try {
    const r = await fetch('/v1/config/llm-test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_unsaved: useUnsaved, llm: readCfg().llm }),
    });
    const data = await r.json();
    el.innerHTML = r.ok
      ? `<span class="ok">ok</span> — model <span class="mono">${esc(data.model)}</span> answered in ${esc(data.seconds)}s: “${esc(data.answer)}”`
      : `<span class="err">${esc(typeof data.detail === 'string' ? data.detail : JSON.stringify(data))}</span>`;
  } catch (err) {
    el.innerHTML = `<span class="err">${esc(err)}</span>`;
  }
}
async function loadRag() {
  const el = $('rag-status');
  el.innerHTML = '<span class="note">checking…</span>';
  try {
    const d = await (await fetch('/v1/rag')).json();
    if (!d.enabled) {
      el.innerHTML = `<span class="err">RAG disabled</span> — the LLM adapter plans ` +
        `without retrieved documentation (citations are the registry's static ` +
        `doc_refs). Enable with <span class="mono">FASS_RAG_ENABLED=1</span> + ` +
        `<span class="mono">FASS_RAG_URL</span>, or the <span class="mono">rag</span> ` +
        `block of config.json.`;
      return;
    }
    const c = d.config;
    let html = `RAG enabled — <span class="mono">${esc(c.url)}</span> ` +
      `<span class="note">(timeout ${c.timeout_ms} ms · top_k ${c.top_k} · ` +
      `evidence ${c.max_evidence_chars} chars · corpus ${esc(c.corpus)} · ` +
      `instruction in query: ${c.send_instruction ? 'yes' : 'no'})</span><br>`;
    const s = d.service;
    if (!s) {
      html += '<span class="err">service state unknown</span>.';
    } else if (s.ok) {
      const corps = (s.corpora || []).map(k =>
        `${esc(k.corpus_id)}${k.loaded ? ' (loaded)' : ''}`).join(', ') || 'no corpora';
      html += `corpora: ${corps}`;
      if (s.loaded_corpus_id)
        html += ` — serving <span class="mono">${esc(s.loaded_corpus_id)}</span>`;
    } else {
      html += `<span class="err">service unreachable</span> (${esc(s.error)}) — ` +
        'generations degrade to RAG-less (fail-open); commands are unaffected.';
    }
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<span class="err">status check failed</span>: ${esc(e)}`;
  }
}

/* ---------------------------------------------------------------- rag tab */
async function ragQuery() {
  const q = $('rag-q').value.trim();
  const box = $('rag-results');
  if (!q) { box.innerHTML = '<p class="note">type a query first.</p>'; return; }
  box.innerHTML = '<span class="note">querying…</span>';
  try {
    const r = await fetch('/v1/rag/query', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q }),
    });
    const d = await r.json();
    if (!r.ok) {
      box.innerHTML = `<span class="err">${esc(typeof d.detail === 'string' ? d.detail : JSON.stringify(d, null, 2))}</span>`;
      return;
    }
    box.innerHTML = ragResultsHtml(d);
  } catch (e) {
    box.innerHTML = `<span class="err">query failed: ${esc(e)}</span>`;
  }
}
function ragClear() {
  $('rag-q').value = '';
  $('rag-results').innerHTML = '';
}
function ragResultsHtml(d) {
  if (!d.ok) {
    return `<span class="badge b-bad">RAG unavailable</span> ` +
      `<span class="note">${esc(d.error || 'no detail')}</span>`;
  }
  const stCls = d.evidence_status === 'supported' ? 'b-ready'
              : d.evidence_status === 'unavailable' ? 'b-bad' : 'b-clar';
  let html = `<span class="badge b-run">corpus ${esc(d.corpus_id || '—')}</span> ` +
    `<span class="badge b-clar">${esc(d.corpus_match || '—')}</span> ` +
    `<span class="badge ${stCls}">${esc(d.evidence_status || '—')}</span> ` +
    `<span class="note">${esc(d.retrieval_ms)} ms</span>`;
  for (const w of (d.warnings || []))
    html += `<p class="note">⚠ ${esc(w)}</p>`;
  const chunks = d.chunks || [];
  if (!chunks.length) return html + '<p class="note">no chunks returned.</p>';
  html += `<p class="note">${chunks.length} chunk(s)</p>`;
  for (const c of chunks) {
    const href = c.url ? (c.anchor ? c.url + '#' + c.anchor : c.url) : null;
    const title = esc(c.title || c.component_name || c.chunk_id || 'untitled');
    const head = href
      ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${title}</a>`
      : title;
    let comp = '';
    if (c.component_type || c.component_name)
      comp = `<span class="badge b-clar">${esc([c.component_type, c.component_name].filter(Boolean).join(' · '))}</span>`;
    const opts = (c.option_names || [])
      .map(o => `<span class="badge mini b-run">${esc(o)}</span>`).join('');
    html += `<div class="card rag-card"><div class="rag-head">${head}${comp}` +
      `<span class="note">score ${esc(Number(c.score ?? 0).toFixed(3))}</span></div>` +
      (opts ? `<div>${opts}</div>` : '') +
      `<pre>${esc(c.text)}</pre></div>`;
  }
  return html;
}

/* ------------------------------------------------------------ files tab */
function fmtSize(n) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Number(n) || 0, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return (i === 0 ? v : v.toFixed(1)) + ' ' + units[i];
}
function fmtDate(sec) {
  const d = new Date(Number(sec) * 1000);
  const p = x => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
    + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}
async function loadFiles() {
  for (const key of ['input', 'output']) {
    const box = $('files-' + key);
    try {
      const d = await (await fetch('/v1/files?dir=' + key)).json();
      const files = d.files || [];
      $('files-count-' + key).textContent = d.exists
        ? files.length + ' file(s)' : 'folder not available on this deployment';
      box.innerHTML = '';
      for (const f of files) {
        const row = document.createElement('div');
        row.className = 'filerow';
        row.dataset.dir = key;
        row.dataset.name = f.name;
        row.title = f.name + ' — right-click for ffprobe / mediainfo / 1st frame';
        row.innerHTML = `<span class="fname mono">${esc(f.name)}</span>`
          + `<span class="fsize">${esc(fmtSize(f.size))}</span>`
          + `<span class="fdate">${esc(fmtDate(f.mtime))}</span>`;
        box.appendChild(row);
      }
      if (!files.length)
        box.innerHTML = '<p class="note" style="padding:6px 2px">empty</p>';
    } catch (e) {
      box.innerHTML = `<p class="err">list failed: ${esc(String(e))}</p>`;
    }
  }
}
function fileMenuAt(ev) {
  const row = ev.target.closest ? ev.target.closest('.filerow') : null;
  if (!row) return;
  ev.preventDefault();
  const menu = $('filemenu');
  menu.dataset.dir = row.dataset.dir;
  menu.dataset.name = row.dataset.name;
  menu.style.display = 'block';
  menu.style.left = '0px'; menu.style.top = '0px';
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.max(4, Math.min(ev.clientX, window.innerWidth - w - 8)) + 'px';
  menu.style.top = Math.max(4, Math.min(ev.clientY, window.innerHeight - h - 8)) + 'px';
}
function closeFileMenu() { $('filemenu').style.display = 'none'; }
document.addEventListener('contextmenu', fileMenuAt);
document.addEventListener('click', e => {
  if (e.target.closest && e.target.closest('#filemenu')) return;
  closeFileMenu();
});
function openModal(title, bodyHtml) {
  $('modal-title').textContent = title;
  $('modal-body').innerHTML = bodyHtml;
  $('filemodal').hidden = false;
}
function closeModal() {
  $('filemodal').hidden = true;
  $('modal-body').innerHTML = '';
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeModal(); closeFileMenu(); }
});
function detailMsg(d) {
  return typeof d.detail === 'string' ? d.detail : JSON.stringify(d, null, 2);
}
async function fileAction(tool) {
  closeFileMenu();
  const menu = $('filemenu');
  const dir = menu.dataset.dir, name = menu.dataset.name;
  if (!dir || !name) return;
  openModal(name + ' — ' + (tool === 'frame' ? '1st frame' : tool),
    '<span class="note">reading…</span>');
  const body = $('modal-body');
  try {
    if (tool === 'frame') {
      const r = await fetch('/v1/files/frame', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ dir, file: name }),
      });
      const d = await r.json();
      if (!r.ok) { body.innerHTML = `<p class="err">${esc(detailMsg(d))}</p>`; return; }
      if (!d.ok) { body.innerHTML = `<p class="err">${esc(d.error || 'no frame')}</p>`; return; }
      body.innerHTML = `<img alt="${esc(name)}" src="data:image/jpeg;base64,${esc(d.jpeg_b64)}">`
        + `<p class="note">${esc(String(d.width))} × ${esc(String(d.height))}</p>`;
      return;
    }
    const r = await fetch('/v1/files/inspect?dir=' + encodeURIComponent(dir)
      + '&file=' + encodeURIComponent(name) + '&tool=' + encodeURIComponent(tool));
    const d = await r.json();
    if (!r.ok) { body.innerHTML = `<p class="err">${esc(detailMsg(d))}</p>`; return; }
    body.innerHTML = `<pre>${esc(JSON.stringify(d.json, null, 2))}</pre>`;
  } catch (e) {
    body.innerHTML = `<p class="err">${esc(String(e))}</p>`;
  }
}

loadCfg().catch(e => $('cfg-status').textContent = 'config load failed: ' + e);
</script>
</body>
</html>
"""
