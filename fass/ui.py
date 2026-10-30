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
button { margin-top: 14px; padding: 7px 14px; font: inherit; font-weight: 600;
  color: #fff; background: color-mix(in srgb, var(--accent) 80%, #000); border: 0; border-radius: 6px; cursor: pointer; }
button.small { margin-top: 0; padding: 3px 10px; font-size: 12px; font-weight: 500; }
button.danger { background: var(--bad); }
button#apply-btn { background: #1c5c52; }
button.ghost { background: none; border: 1px solid var(--border); color: var(--muted); }
/* the media-info block dims its whole button row while an operation runs */
#info-mi button:disabled { opacity: .5; cursor: not-allowed; }
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
#ffver { white-space: pre-line; }  /* ffmpeg -version lines, breaks kept */
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
.remote-block { margin: 6px 0; }
.remote-block summary { cursor: pointer; }
.remote-block .row { margin-top: 8px; }
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
    <button id="tab-cmd" class="on" onclick="showTab('cmd')">FFmpeg</button>
    <button id="tab-files" onclick="showTab('files')">Files</button>
    <button id="tab-runs" onclick="showTab('runs')">Runs</button>
    <button id="tab-player" onclick="showTab('player')">Player</button>
    <button id="tab-info" onclick="showTab('info')">Info / Log</button>
    <button id="tab-rag" onclick="showTab('rag')">RAG</button>
    <button id="tab-cfg" onclick="showTab('cfg')">Config</button>
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
    <label class="check"><input type="checkbox" id="servehttp"> serve over http <span class="note">(the output lands in the fass-http sidecar's tree — open it in the Player tab; for .m3u8 / .mpd outputs)</span></label>
    <div class="row" style="align-items:center">
      <button type="submit" id="genbtn" disabled>Generate</button>
      <button type="button" id="savebtn" class="ghost" disabled onclick="savePreset()" title="Freeze this generation as a preset (find it back under Info / Log → presets)">Save</button>
      <button type="button" id="enterbtn" disabled onclick="enterCmd()" title="Skip the NLU/LLM — prefill the command line as an editable skeleton from the Input and Output fields (ffmpeg -i … -o …), for people who write the ffmpeg command themselves">Enter</button>
      <button type="button" id="mi-btn" disabled onclick="runMediaInfo()">Mediainfo</button>
      <button type="button" id="clearbtn" class="ghost" disabled onclick="clearCmdPane()">Clear</button>
      <span id="save-status" class="note"></span>
    </div>
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

  <!-- ============================================================== PLAYER -->
  <div id="pane-player" hidden>
    <div class="card">
      <h2 style="margin-top:0">Player</h2>
      <button class="small ghost" style="margin-top:0" onclick="loadStreams()">↻ refresh</button>
      <button class="small danger" style="margin-top:0" onclick="purgeStreams()">Purge stream tree</button>
      <span id="streams-base" class="note"></span>
      <div id="player-stage" hidden style="margin-top:12px">
        <video id="player-video" controls style="width:100%;max-height:420px;background:#000"></video>
        <span id="player-status" class="note"></span>
      </div>
      <div id="streams-wrap" style="overflow-x:auto">
        <table id="streams-table" style="width:100%">
          <thead><tr><th>playlist</th><th>kind</th><th>state</th><th>size</th><th>modified</th><th></th></tr></thead>
          <tbody id="streams-body"></tbody>
        </table>
        <div id="streams-empty" class="note">No streams under the output volume's http/ directory yet — run a command with "serve over http" checked, and its playlist shows up here (live while the run is going).</div>
      </div>
    </div>
  </div>

  <!-- ========================================================== INFO / LOG -->
  <div id="pane-info" hidden>
    <div class="card">
      <h2 style="margin-top:0">What to look at</h2>
      <label class="check" style="margin-top:0"><input type="radio" name="infosel" id="sel-mi" checked onchange="showInfo('mi')"> media info <span class="note">(ffprobe report on one file)</span></label>
      <label class="check"><input type="radio" name="infosel" id="sel-log" onchange="showInfo('log')"> run log <span class="note">(what ffmpeg printed while running)</span></label>
      <label class="check"><input type="radio" name="infosel" id="sel-prompt" onchange="showInfo('prompts')"> prompts <span class="note">(the last 1000 instructions the model accepted — click one to reuse it in the FFmpeg tab)</span></label>
      <label class="check"><input type="radio" name="infosel" id="sel-presets" onchange="showInfo('presets')"> presets <span class="note">(saved generations — pick one, read its full JSON, and Load it back into the FFmpeg tab)</span></label>

      <div id="info-mi">
        <div class="row">
          <div><label for="mi-target">Look in</label>
            <select id="mi-target" onchange="fillMi()"><option value="input">input</option><option value="output">output</option></select></div>
          <div><label for="mi-file">File (bare name in that folder)</label>
            <input id="mi-file" oninput="miButtons()" placeholder="clip.mp4"></div>
        </div>
        <button type="button" id="mi-showbtn" disabled onclick="showMediaInfo()">Show media info</button>
        <button type="button" id="tsbtn" disabled onclick="showTSAnalyze()">TSAnalyze</button>
        <button type="button" id="vvizbtn" disabled onclick="showVviz()">Bitrate</button>
        <button type="button" id="mi-clearbtn" class="ghost" disabled onclick="clearMiPane(true)">Clear</button>
        <span id="mi-status" class="note"></span>
        <pre id="mi-out" hidden style="margin-top:10px"></pre>
        <iframe id="vviz-frame" hidden sandbox="allow-scripts" style="margin-top:10px;width:100%;height:640px;border:1px solid #ccc" title="Bitrate chart"></iframe>
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

      <div id="info-prompts" hidden>
        <div class="row" style="align-items:flex-end">
          <div style="flex:1 1 auto"><span class="note">Latest first. Click a prompt to put it into the FFmpeg tab's Instruction field.</span></div>
          <div style="flex:0 0 auto"><button type="button" class="copy" onclick="loadPrompts()">↻ refresh</button></div>
        </div>
        <span id="prompt-status" class="note"></span>
        <div id="prompt-out" style="margin-top:10px"></div>
      </div>

      <div id="info-presets" hidden>
        <div class="row" style="align-items:flex-end">
          <div style="flex:1 1 auto"><label for="preset-sel">Preset (newest first)<span class="tip" data-tip="One JSON file per saved generation, named by the md5 of instruction + inputs + ffmpeg command — re-saving the same generation overwrites its file.">?</span></label>
            <select id="preset-sel" onchange="previewPreset()"></select></div>
          <div style="flex:0 0 auto"><button type="button" class="copy" onclick="loadPresets()">↻ refresh</button></div>
        </div>
        <span id="preset-status" class="note"></span>
        <pre id="preset-preview" hidden style="margin-top:10px;max-height:220px;overflow:auto;font-size:12px;white-space:pre-wrap"></pre>
        <button type="button" id="preset-loadbtn" class="ghost" disabled onclick="loadPreset()" title="Fill the FFmpeg tab from this preset and re-render its stored command">Load into the FFmpeg tab</button>
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
      <details class="remote-block">
        <summary>remote mapping: /input — FTP / SFTP / S3
          <span class="note">(missing files are downloaded from the remote before use)</span></summary>
        <div class="row">
          <div><label>type</label>
            <select id="ri-type">
              <option value="none">none — local folder only</option>
              <option value="ftp">ftp</option>
              <option value="sftp">sftp</option>
              <option value="s3">s3 bucket</option>
            </select></div>
          <div><label>host</label><input id="ri-host" placeholder="minio / 172.17.0.1 — s3: blank = AWS"></div>
          <div><label>port</label><input id="ri-port" type="number" placeholder="blank = default (21 / 22 / 443)"></div>
        </div>
        <div class="row">
          <div><label>user</label><input id="ri-user" placeholder="ftp/sftp user · s3 access key id"></div>
          <div><label>password / secret</label><input id="ri-pass" type="password" placeholder="ftp/sftp password · s3 secret key"></div>
          <div><label>path / prefix</label><input id="ri-path" placeholder="remote sub-dir · s3 object prefix"></div>
        </div>
        <div class="row">
          <div><label>bucket (s3)</label><input id="ri-bucket" placeholder="fass"></div>
          <div><label>region (s3)</label><input id="ri-region" placeholder="us-east-1"></div>
          <div><label class="check" style="margin:0"><input type="checkbox" id="ri-secure"> s3 https (http for on-host MinIO/LocalStack)</label></div>
          <div><label class="check" style="margin:0"><input type="checkbox" id="ri-uuid"> s3 uuid naming (objects keyed by UUID, real basename in S3 metadata)</label></div>
        </div>
        <div class="row">
          <div><button type="button" class="small ghost" onclick="remoteTest('input','ri')">test (unsaved values)</button>
            <span id="ri-status"></span></div>
        </div>
        <p class="note">Under docker, services on the host are reached via
          <span class="mono">host.docker.internal</span> (e.g.
          <span class="mono">host.docker.internal:9100</span> for an on-host MinIO).
          sftp host keys are not pinned (dev-tool convention).</p>
      </details>
      <details class="remote-block">
        <summary>remote mapping: /output — FTP / SFTP / S3
          <span class="note">(the result + its log are uploaded after a successful run)</span></summary>
        <div class="row">
          <div><label>type</label>
            <select id="ro-type">
              <option value="none">none — local folder only</option>
              <option value="ftp">ftp</option>
              <option value="sftp">sftp</option>
              <option value="s3">s3 bucket</option>
            </select></div>
          <div><label>host</label><input id="ro-host" placeholder="minio / 172.17.0.1 — s3: blank = AWS"></div>
          <div><label>port</label><input id="ro-port" type="number" placeholder="blank = default (21 / 22 / 443)"></div>
        </div>
        <div class="row">
          <div><label>user</label><input id="ro-user" placeholder="ftp/sftp user · s3 access key id"></div>
          <div><label>password / secret</label><input id="ro-pass" type="password" placeholder="ftp/sftp password · s3 secret key"></div>
          <div><label>path / prefix</label><input id="ro-path" placeholder="remote sub-dir · s3 object prefix"></div>
        </div>
        <div class="row">
          <div><label>bucket (s3)</label><input id="ro-bucket" placeholder="fass"></div>
          <div><label>region (s3)</label><input id="ro-region" placeholder="us-east-1"></div>
          <div><label class="check" style="margin:0"><input type="checkbox" id="ro-secure"> s3 https (http for on-host MinIO/LocalStack)</label></div>
          <div><label class="check" style="margin:0"><input type="checkbox" id="ro-uuid"> s3 uuid naming (objects keyed by UUID, real basename in S3 metadata)</label></div>
        </div>
        <div class="row">
          <div><button type="button" class="small ghost" onclick="remoteTest('output','ro')">test (unsaved values)</button>
            <span id="ro-status"></span></div>
        </div>
      </details>
      <details class="remote-block">
        <summary>remote mapping: /log — FTP / SFTP / S3
          <span class="note">(run log + record are uploaded after every run)</span></summary>
        <div class="row">
          <div><label>type</label>
            <select id="rl-type">
              <option value="none">none — local folder only</option>
              <option value="ftp">ftp</option>
              <option value="sftp">sftp</option>
              <option value="s3">s3 bucket</option>
            </select></div>
          <div><label>host</label><input id="rl-host" placeholder="minio / 172.17.0.1 — s3: blank = AWS"></div>
          <div><label>port</label><input id="rl-port" type="number" placeholder="blank = default (21 / 22 / 443)"></div>
        </div>
        <div class="row">
          <div><label>user</label><input id="rl-user" placeholder="ftp/sftp user · s3 access key id"></div>
          <div><label>password / secret</label><input id="rl-pass" type="password" placeholder="ftp/sftp password · s3 secret key"></div>
          <div><label>path / prefix</label><input id="rl-path" placeholder="remote sub-dir · s3 object prefix"></div>
        </div>
        <div class="row">
          <div><label>bucket (s3)</label><input id="rl-bucket" placeholder="fass"></div>
          <div><label>region (s3)</label><input id="rl-region" placeholder="us-east-1"></div>
          <div><label class="check" style="margin:0"><input type="checkbox" id="rl-secure"> s3 https (http for on-host MinIO/LocalStack)</label></div>
          <div><label class="check" style="margin:0"><input type="checkbox" id="rl-uuid"> s3 uuid naming (objects keyed by UUID, real basename in S3 metadata)</label></div>
        </div>
        <div class="row">
          <div><button type="button" class="small ghost" onclick="remoteTest('log','rl')">test (unsaved values)</button>
            <span id="rl-status"></span></div>
        </div>
      </details>
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
        <div><label>timeout (seconds)</label><input id="l-timeout" type="number" min="1" max="300" step="1" placeholder="90"></div>
        <div><label class="check" style="margin:0"><input type="checkbox" id="l-thinking"> thinking mode
          <span class="note">(openai provider — Qwen3 / strata / vLLM; off = no reasoning trace, faster JSON)</span></label></div>
      </div>
      <p class="note">timeout: seconds allowed for one LLM request (connect + read) — a hung
      endpoint returns an error instead of blocking the request. 1–300, default 90.
      Local models on a full fass prompt can need 10–30 s per request.
      thinking mode is off by default — for the structured JSON output the reasoning
      trace is pure overhead (and can exhaust max_tokens); endpoints that reject the
      chat-template kwarg fall back to a plain request automatically.</p>
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
      <span class="mono">mediainfo</span> report or its first frame, to use
      it as the input, to tsanalyze it, or (from the output pane) to copy
      it into the input folder. Free space is shown under each pane.</p>
      <button class="small ghost" style="margin-top:0" onclick="loadFiles()">↻ refresh</button>
      <div class="filecols">
        <div class="filecol">
          <div class="filehead">input <span class="note" id="files-count-input"></span></div>
          <div id="files-input" class="filelist"></div>
          <p class="note" id="files-disk-input" style="margin:8px 2px 0"></p>
        </div>
        <div class="filecol">
          <div class="filehead">output <span class="note" id="files-count-output"></span></div>
          <div id="files-output" class="filelist"></div>
          <p class="note" id="files-disk-output" style="margin:8px 2px 0"></p>
        </div>
      </div>
    </div>
  </div>

  <div id="filemenu" class="fmenu">
    <button type="button" id="fm-asinput" style="display:none" onclick="fileAction('as-input')">as input</button>
    <button type="button" id="fm-toinput" style="display:none" onclick="fileAction('to-input')">to input</button>
    <button type="button" onclick="fileAction('ffprobe')">ffprobe</button>
    <button type="button" onclick="fileAction('mediainfo')">mediainfo</button>
    <button type="button" id="fm-tsanalyze" style="display:none" onclick="fileAction('tsanalyze')">tsanalyze</button>
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
  if (d.version) $('ver').textContent = d.version; // the string carries its own v
  // ffmpeg build info: `ffmpeg -version` minus the lib* lines (pre-line CSS
  // keeps the line breaks — version, built-with and configuration lines)
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
  // the base four + 'rag' + 'files' + 'player' (concat keeps the pinned literal in test_webinfo)
  for (const p of ['cmd','runs','info','cfg'].concat('rag','files','player')) {
    $('pane-'+p).hidden = (p !== t);
    $('tab-'+p).classList.toggle('on', p === t);
  }
  if (t === 'runs') loadRuns();
  if (t === 'info') {
    loadLogs();
    showInfo($('sel-prompt').checked ? 'prompts'
           : $('sel-log').checked ? 'log' : 'mi');
  }
  if (t === 'cfg') loadRag();
  if (t === 'files') loadFiles();
  if (t === 'player') { loadStreams(); startPlayerPoll(); }
  else stopPlayerPoll();
}

/* ------------------------------------------------------------ command tab */
/* quality-compare convention (the NLU's compare triggers, M14 flip): the
   Input field is the reference (the main asset — the source in the input
   folder), the Output field the distorted clip — its name from the output
   folder; Output empty means the server auto-detects it. Nothing is
   written; the score lands in the run log. */
const CMP_RX = /\\bcompar(?:e[sd]?|ison|ing)\\b|\\bquality\\s+(?:loss|difference|degradation|drop|impact)\\b|\\blib-?vmaf\\b|\\bpsnr\\b|\\bssim\\b/i;
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
function cmdButtons() {
  // the FFmpeg pane's button row follows its fields, like the Info/Log row:
  // nothing entered → nothing can work, so on a fresh pane (and after Clear)
  // the row is disabled. Generate needs both required fields; Enter only a
  // file (it skips the instruction entirely — that is its point); Mediainfo
  // a file AND an empty Instruction (it is a peek that ignores the
  // instruction, so a real command is never silently replaced by a
  // report); Clear needs something to clear. Save stays gated on lastReady
  // (a ready render), which render() and clearCmdPane() manage.
  const instr = $('instr').value.trim();
  const inp = $('inp').value.trim();
  $('genbtn').disabled = !(instr && inp);
  $('enterbtn').disabled = !inp;
  $('mi-btn').disabled = !inp || instr !== '';
  const any = instr || inp || $('out').value.trim() || $('wm').value.trim() ||
    Array.from(document.querySelectorAll('.extra-inp')).some(i => i.value.trim());
  $('clearbtn').disabled = !any;
}
$('instr').addEventListener('input', () => { setCompareMode(); cmdButtons(); });
$('inp').addEventListener('input', cmdButtons);
$('out').addEventListener('input', cmdButtons);
$('wm').addEventListener('input', cmdButtons);
setCompareMode();
cmdButtons();
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
  row.querySelector('.extra-inp').addEventListener('input', cmdButtons);
}
function mainInputs(raw) {
  // the Input field may carry several `-i <file>` pairs (its placeholder
  // invites `-i clip.mp4`, so "concatenate two files" often lands here as
  // `-i a.ts -i b.ts`). Split on standalone `-i` tokens: the first is 'main',
  // the rest become extra inputs. A value with no standalone `-i` — a stream
  // URL or a bare/previous-run filename, which may hold spaces or an embedded
  // "-i" — is returned whole.
  const s = raw.trim();
  if (!s) return [];
  return s.split(/(?:^|\\s)-i\\s+/).map(p => p.trim()).filter(Boolean);
}
/* the asset map the /v1/commands body carries — compare convention, the
   multi `-i` split of the Input field, the watermark, and the extra-input
   rows. Shared by submit() and savePreset() so a preset always restores
   exactly what the form would have sent. */
function collectAssets() {
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
    // several `-i <file>` pairs in the Input field → the first is 'main', the
    // rest are extra inputs, so a "concatenate two files" request has a second
    // input to bind (the "+ add input" rows are an equivalent way to add one)
    const mainParts = mainInputs($('inp').value);
    assets.main = mainParts[0] || $('inp').value.trim();
    let mi = 0;
    for (const p of mainParts.slice(1)) {
      mi += 1;
      assets['m' + mi] = p;
    }
    if ($('wm').value.trim()) assets.watermark = $('wm').value.trim();
  }
  document.querySelectorAll('.extra-inp').forEach(i => {
    const v = i.value.trim();
    if (!v) return;
    // the kind <select> is a SIBLING of the input's wrapper <div> (not a
    // descendant of it), so it must be read from the enclosing row — reading
    // it off i.parentElement returned null and threw, killing "Generate"
    const kind = i.closest('.row').querySelector('.extra-kind').value;
    assets[i.dataset.id] = (kind === 'auto') ? v : { path: v, kind };
  });
  return assets;
}
async function submit(e) {
  e.preventDefault();
  const cmp = isCompare();
  const assets = collectAssets();
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
/* Save: freeze the current generation as a preset — the instruction, the
   fields (as the asset map /v1/commands carries), the command as it stands
   in the (editable) textarea, and the full ready response — into the
   presets dir. The button is enabled only while the result area holds a
   ready response (lastReady); re-saving the same state overwrites the same
   md5-named file. */
async function savePreset() {
  if (!lastReady) return;
  const btn = $('savebtn');
  const cmp = isCompare();
  btn.disabled = true;
  $('save-status').textContent = 'saving…';
  const body = {
    instruction: $('instr').value.trim(),
    assets: collectAssets(),
    command: $('cmd').value,
    output_filename: (!cmp && $('out').value.trim()) ? $('out').value.trim() : null,
    adapter: $('ad').value,
    preflight: $('pf').checked,
    verbose: $('verbose').checked,
    servehttp: $('servehttp').checked,
    arguments: lastReady.arguments,
    summary: lastReady.summary,
    defaults: lastReady.defaults,
    warnings: lastReady.warnings,
    validation_level: lastReady.validation_level,
    checks: lastReady.checks,
    evidence: lastReady.evidence,
  };
  try {
    const r = await fetch('/v1/presets', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok)
      throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data));
    $('save-status').textContent = 'saved as ' + data.name + ' (Info / Log tab → presets)';
  } catch (err) {
    $('save-status').innerHTML = `<span class="err">save failed: ${esc(err)}</span>`;
  }
  btn.disabled = !lastReady;
}
/* the Mediainfo button: a peek, not a command — probe the input file and
   print its ffprobe report in the result area. It never goes through the
   form submit, so it works with an empty Instruction (the button is
   disabled while any instruction is present, so it can't replace a real
   command). */
async function runMediaInfo() {
  const file = $('inp').value.trim();
  const btn = $('mi-btn');
  btn.disabled = true;
  btn.textContent = 'probing…';
  $('res').hidden = false;
  $('res').innerHTML = '<span class="badge b-clar">probing…</span>';
  try {
    const r = await fetch('/v1/mediainfo?target=input&file=' + encodeURIComponent(file));
    const data = await r.json();
    if (!r.ok) {
      $('res').innerHTML = `<span class="badge b-bad">mediainfo error</span>
        <pre>${esc(typeof data.detail === 'string' ? data.detail : JSON.stringify(data, null, 2))}</pre>`;
    } else {
      $('res').innerHTML = `<span class="badge b-ready">mediainfo — ${esc(file)}</span>
        <pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
    }
  } catch (err) {
    $('res').innerHTML = `<span class="badge b-bad">request failed</span><pre>${esc(err)}</pre>`;
  }
  btn.textContent = 'Mediainfo';
  cmdButtons();
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
/* The LLM's verbatim reply — what the model actually returned, before any
   processing (JSON extraction, schema validation, plan building). LLM
   adapter only; the rules adapter has no raw text. */
function rawLlmHtml(d) {
  if (!d.raw_llm) return '';
  return `<details class="sources"><summary>raw LLM output <span class="note">(the model's verbatim reply — before any processing)</span></summary>
    <pre>${esc(d.raw_llm)}</pre></details>`;
}
/* the response last rendered while status:'ready' — the Save button
   captures exactly this (fields + response) into a preset; a non-ready
   render clears it, so a stale command is never re-saved */
let lastReady = null;
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
      (d.errors && d.errors.length ? '<h2>Second try (direct LLM)</h2>' + lists(d.errors) : '') +
      '<h2>Supported instead</h2><p>' + esc(d.unsupported && d.unsupported[0] && d.unsupported[0].hint || '') + '</p>' +
      sourcesHtml(d.evidence);
  } else {
    html += '<h2>Errors</h2>' + lists(d.errors);
  }
  html += rawLlmHtml(d);
  $('res').innerHTML = html;
  lastReady = (d.status === 'ready') ? d : null;
  $('savebtn').disabled = !lastReady;
}
async function runCmd() {
  const argv = JSON.parse($('cmdargs').textContent);
  // serve_http is an execution-time choice: it redirects this run's output into
  // the fass-http sidecar's tree so the Player tab can stream it.
  const body = {
    arguments: argv, verbose: $('verbose').checked, serve_http: $('servehttp').checked,
    // the instruction the command was generated from — recorded in the
    // run log footer above the command, so the log is self-describing
    instruction: $('instr').value.trim() || null,
  };
  const text = $('cmd').value;
  // an expert edit is sent as `command` (shlex-split + revalidated server-side);
  // an untouched textarea keeps the exact generated argv, as before
  if (text.trim() !== $('cmdorig').textContent.trim()) body.command = text;
  const btn = $('runbtn');
  // the button returns to '▶ Run now' on EVERY outcome — success included:
  // the run is server-side (tracked in the Runs tab), so a successful start
  // does not consume the button; leaving it stuck at 'starting…' made a
  // finished run look stuck too
  const restore = () => { btn.disabled = false; btn.textContent = '▶ Run now'; };
  btn.disabled = true; btn.textContent = 'starting…';
  try {
    const r = await fetch('/v1/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) {
      restore();
      $('rune').textContent = 'run failed: ' + (typeof data.detail === 'string' ? data.detail : JSON.stringify(data));
      return;
    }
    lastRunId = data.run.id;
    restore();
    $('rune').innerHTML = `run <span class="mono">${esc(data.run.id)}</span> started`;
    showTab('runs');
    setTimeout(loadRuns, 1200);
  } catch (err) {
    restore();
    $('rune').textContent = 'run failed: ' + err;
  }
}
$('f').addEventListener('submit', submit);
/* the three option checkboxes (preflight / verbose / serve over http) are
   independent — combine them freely. The old 'mediainfo' tick and its
   mutual-exclusivity are gone: Mediainfo is now a button next to Generate
   (runMediaInfo) that never needs an instruction. */
/* the Clear button: resets the whole FFmpeg pane — every field, the option
   checkboxes, the adapter, the extra inputs, the result area, and the two
   derived states (the compare labels and the Mediainfo button) */
function clearCmdPane() {
  $('instr').value = '';
  $('inp').value = '';
  $('out').value = '';
  $('wm').value = '';
  $('ad').value = 'auto';
  $('extra').innerHTML = '';
  $('pf').checked = false;
  $('verbose').checked = false;
  $('servehttp').checked = false;
  $('res').hidden = true;
  $('res').innerHTML = '';
  lastReady = null;
  $('savebtn').disabled = true;
  $('save-status').textContent = '';
  // a programmatic .value never fires the input listener, so re-derive both
  // the compare labels and the button row (every field empty → all disabled)
  setCompareMode();
  cmdButtons();
}
/* the Enter button (power users): skip the NLU/LLM entirely and prefill the
   command line as an editable skeleton — `ffmpeg -i <in1> [-i <in2> …]
   -o <out>` — from the form: the Input video field (its multi `-i` split,
   so a "concatenate two files" field yields two `-i`), the extra-input
   rows, and the watermark image when set; the output from the Output field.
   Bare filenames are prefixed with the deployment's folders
   (/input/<name> for inputs, /output/<name> for the output); a value that
   already starts with / (chained /input/step1.mp4) or a :// URL (rtmp://,
   rtsp://, srt://, udp://, …) is used as-is. The skeleton is rendered as a
   synthetic ready response (NO server call — that is what skips the LLM
   part), so the line lands in the editable textarea and Runs exactly like
   any generated command: unedited, the skeleton argv executes as a bare
   remux; edited, it goes through the same server-side revalidation. */
function enterPath(v, isOut) {
  const s = v.trim();
  if (!s) return '';
  if (s.startsWith('/') || s.includes('://')) return s;
  return (isOut ? '/output/' : '/input/') + s;
}
function enterCmd() {
  const mainParts = mainInputs($('inp').value);
  const ins = (mainParts.length ? mainParts : [$('inp').value.trim()]).filter(Boolean);
  document.querySelectorAll('.extra-inp').forEach(i => {
    const v = i.value.trim();
    if (v) ins.push(v);
  });
  const wm = $('wm').value.trim();
  if (wm) ins.push(wm);
  const argv = ['ffmpeg'];
  for (const p of ins) argv.push('-i', enterPath(p, false));
  const out = enterPath($('out').value, true);
  if (out) argv.push('-o', out);
  $('res').hidden = false;
  render({
    status: 'ready',
    command: argv.join(' '),
    arguments: argv,
    summary: ['Manual command (Enter): the NLU/LLM part was skipped — this is a skeleton prefilled from the Input and Output fields. Edit the line, then ▶ Run now.'],
    defaults: ['Paths: bare filenames became /input/<name> (inputs) and /output/<name> (output); values starting with / or a :// URL were kept as-is.' +
      (wm ? ' The watermark image is included as the last -i.' : '')],
    warnings: [],
    validation_level: 'manual',
    checks: [],
    evidence: null,
    raw_llm: null,
  });
  $('cmd').focus();
}

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
    // M13.7: an http-served run links to the Player tab (the sidecar streams
    // it); an ordinary run links to the raw output file.
    const out = run.output
      ? (run.http
        ? `<a href="#" onclick="showTab('player');return false">${esc(run.output.split('/').pop())} <span class="note">· player</span></a>`
        : `<a href="/media/run/${encodeURIComponent(run.id)}/output" target="_blank">${esc(run.output.split('/').pop())}</a>`)
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

/* ------------------------------------------------------------ player tab */
/* M13.7: play the fass-http sidecar's stream tree. shaka-player plays both
   .m3u8 (HLS) and .mpd (DASH) through one API — vendored under /static/
   (fixed allowlist, no CDN). The list auto-refreshes while the tab is open
   because a live run is still appending segments. */
let playerPoll = null;
let playerShaka = null; // active shaka.Player instance
let streamSpecs = [];   // parallel to the rendered rows: {path, kind, url}
function startPlayerPoll() {
  if (!playerPoll) playerPoll = setInterval(loadStreams, 5000);
}
function stopPlayerPoll() {
  if (playerPoll) { clearInterval(playerPoll); playerPoll = null; }
}
function fmtSize(n) {
  if (n == null) return '—';
  const u = ['B','KB','MB','GB','TB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
}
function playerStatus(msg) { $('player-status').textContent = msg || ''; }
async function loadStreams() {
  const r = await fetch('/v1/streams');
  const data = await r.json();
  const streams = data.streams || [];
  const base = data.http_base_url;
  $('streams-base').textContent = base
    ? 'served at ' + base + ' (the fass-http sidecar mounts the output volume)'
    : 'no streaming.http_base_url configured — set it in Config to get playable URLs';
  streamSpecs = streams;
  const tbody = $('streams-body');
  tbody.innerHTML = '';
  streams.forEach((s, i) => {
    const tr = document.createElement('tr');
    const state = s.live
      ? '<span class="badge b-run">live</span>'
      : '<span class="badge b-ready">vod</span>';
    const modified = s.mtime != null ? new Date(s.mtime * 1000).toLocaleString() : '—';
    tr.innerHTML = `<td class="mono">${esc(s.name)}</td><td>${esc(s.kind)}</td>
      <td>${state}</td><td>${fmtSize(s.size)}</td><td>${modified}</td>
      <td><button class="small ghost" onclick="playStream(${i})">play</button></td>`;
    tbody.appendChild(tr);
  });
  $('streams-empty').style.display = streams.length ? 'none' : '';
  $('streams-table').hidden = !streams.length;
}
/* lazy-load a vendored lib once; resolves to the global it sets */
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error('failed to load ' + src));
    document.head.appendChild(s);
  });
}
function stopPlayer() {
  if (playerShaka) { try { playerShaka.destroy(); } catch (e) {} playerShaka = null; }
  const v = $('player-video');
  v.pause();
  v.removeAttribute('src');
  v.load();
  playerStatus('');
}
async function playStream(i) {
  const s = streamSpecs[i];
  if (!s) return;
  const { path, kind, url } = s;
  const v = $('player-video');
  $('player-stage').hidden = false;
  stopPlayer();
  playerStatus('loading…');
  if (!url) { playerStatus('no http_base_url configured — cannot resolve a playable URL'); return; }
  try {
    if (window.shaka === undefined) await loadScript('/static/shaka-player.compiled.js');
    if (!(await shaka.Player.isBrowserSupported())) {
      playerStatus('this browser lacks the media APIs shaka needs (MSE) — cannot play');
      return;
    }
    playerShaka = new shaka.Player(v);
    playerShaka.addEventListener('error', (e) => {
      const d = e.detail;
      if (d && d.severity === 'fatal')
        playerStatus(`fatal ${kind.toUpperCase()} error (${d.code}): ${d.message || d.detail || 'unknown'}`);
    });
    await playerShaka.load(url);
    playerStatus(`playing ${kind.toUpperCase()} (shaka-player) — ${path}`);
    v.play().catch(() => {});  // autoplay may be blocked; the user presses play
  } catch (err) {
    playerStatus('failed to start player: ' + err);
  }
}
async function purgeStreams() {
  if (!confirm('Purge the whole stream tree? This deletes every file under the output volume http/ directory (all served playlists and segments).')) return;
  const r = await fetch('/v1/streams/purge', { method: 'POST' });
  const data = await r.json();
  if (!r.ok) { alert('purge failed: ' + (typeof data.detail === 'string' ? data.detail : JSON.stringify(data))); return; }
  stopPlayer();
  $('player-stage').hidden = true;
  loadStreams();
}

/* -------------------------------------------------------- info / log tab */
function showInfo(which) {
  $('info-mi').hidden = (which !== 'mi');
  $('info-log').hidden = (which !== 'log');
  $('info-prompts').hidden = (which !== 'prompts');
  $('info-presets').hidden = (which !== 'presets');
  if (which === 'mi') fillMi();
  if (which === 'prompts') loadPrompts();
  if (which === 'presets') loadPresets();
}
let vvizBlob = null;
let miBusy = false;
/* the button row follows the File field: with no file, none of the
   operations can run, so all four buttons stay disabled; a non-empty
   file (typed or auto-filled by fillMi) enables all of them. After a
   probe, showMediaInfo re-gates TSAnalyze/Bitrate by the probed
   format/codec (the server enforces the same checks). */
function miButtons() {
  if (miBusy) return; // the vviz row lock wins while a chart generates
  const has = $('mi-file').value.trim() !== '';
  ['mi-showbtn', 'tsbtn', 'vvizbtn', 'mi-clearbtn'].forEach(id => { $(id).disabled = !has; });
}
/* hide the Bitrate chart and release its blob URL (one output at a
   time: fillMi / clearMiPane / showMediaInfo / showTSAnalyze all call it) */
function hideVvizFrame() {
  const frame = $('vviz-frame');
  frame.hidden = true;
  frame.src = 'about:blank';
  if (vvizBlob) { URL.revokeObjectURL(vvizBlob); vvizBlob = null; }
}
function fillMi() {
  // auto-fill from the Command tab's fields ('-i ' prefix stripped),
  // still editable by hand
  const src = ($('mi-target').value === 'output')
    ? $('out').value.trim() : $('inp').value.trim();
  $('mi-file').value = src.replace(/^-i\\s+/, '');
  miButtons();
  hideVvizFrame();
}
/* Clear: wipes every output in the media-info block — the ffprobe report /
   tsanalyze text and the Bitrate chart */
function clearMiPane(wipeFile = false) {
  const out = $('mi-out');
  out.hidden = true;
  out.textContent = '';
  hideVvizFrame();
  if (wipeFile) { $('mi-file').value = ''; miButtons(); }
  $('mi-status').innerHTML = '';
}
async function showMediaInfo() {
  const target = $('mi-target').value;
  const file = $('mi-file').value.trim();
  const out = $('mi-out');
  // one output at a time: a fresh report replaces the Bitrate chart
  hideVvizFrame();
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
    // TSAnalyze applies to MPEG-TS containers only — enable it when the
    // probed file is one (the server enforces the same check)
    const fmt = (data && data.format && data.format.format_name) || '';
    $('tsbtn').disabled = !/^mpegts/i.test(fmt);
    // The bitrate visualisation (vviz_py) charts h264/hevc/av1/vp9 video
    // only — enable it when the probed video codec is one of them (the
    // server enforces the same set)
    const vcodec = ((data && data.streams) || []).find(s => s.codec_type === 'video');
    $('vvizbtn').disabled = !vcodec || !['h264', 'hevc', 'av1', 'vp9'].includes((vcodec.codec_name || '').toLowerCase());
  } catch (err) {
    out.hidden = true;
    $('mi-status').innerHTML = `<span class="err">${esc(err)}</span>`;
  }
}
/* TSAnalyze: the tsanalyze tool's full TS report (tsduck) — plain text,
   shown where the media info is shown */
async function showTSAnalyze() {
  const target = $('mi-target').value;
  const file = $('mi-file').value.trim();
  if (!file) {
    $('mi-status').textContent = 'give the file name first';
    return;
  }
  const btn = $('tsbtn');
  btn.disabled = true;
  // one output at a time: the TS report replaces the Bitrate chart
  hideVvizFrame();
  $('mi-status').textContent = 'running tsanalyze…';
  try {
    const r = await fetch(`/v1/tsanalyze?target=${encodeURIComponent(target)}&file=${encodeURIComponent(file)}`);
    const text = await r.text();
    if (!r.ok) {
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch (_) {}
      $('mi-status').innerHTML = `<span class="err">${esc(detail)}</span>`;
      $('mi-out').hidden = true;
      return;
    }
    $('mi-status').textContent = '';
    $('mi-out').textContent = text;
    $('mi-out').hidden = false;
  } catch (err) {
    $('mi-status').innerHTML = `<span class="err">${esc(err)}</span>`;
  } finally {
    btn.disabled = false;
  }
}
/* Bitrate visualisation: vviz_py's interactive frame/GOP/bitrate chart.
   The server brings the source into a temp mp4 in /output (an mp4 is
   copied as-is, any other container is remuxed losslessly) and returns
   self-contained Plotly HTML for the sandboxed iframe to embed. A 409
   means the remux would be long — confirm, then re-POST with confirm:true.
   While the chart generates the whole button row is dimmed + disabled:
   the processing can take a while, and other clicks would race it. */
async function showVviz() {
  const target = $('mi-target').value;
  const file = $('mi-file').value.trim();
  if (!file) {
    $('mi-status').textContent = 'give the file name first';
    return;
  }
  // one output at a time: the chart is the only info shown, so the
  // mediainfo / tsanalyze / previous chart outputs go first
  clearMiPane();
  const btn = $('vvizbtn');
  const frame = $('vviz-frame');
  const row = [$('mi-showbtn'), $('tsbtn'), btn, $('mi-clearbtn')];
  const was = row.map(b => b.disabled);
  miBusy = true; // keep miButtons() from re-enabling the row mid-flight
  row.forEach(b => { b.disabled = true; });
  const unlock = () => { miBusy = false; row.forEach((b, i) => { b.disabled = was[i]; }); };
  const body = { target, file, confirm: false };
  const post = () => fetch('/v1/vviz', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  try {
    $('mi-status').textContent = 'generating the chart (non-mp4 files are remuxed to mp4 first, that can take a while)…';
    let r = await post();
    if (r.status === 409) {
      let detail = '';
      try { detail = (await r.json()).detail || ''; } catch (_) {}
      if (!confirm(detail || 'This file is long; wait for the remux to mp4?')) {
        $('mi-status').textContent = 'cancelled';
        return;
      }
      body.confirm = true;
      $('mi-status').textContent = 'remuxing to mp4…';
      r = await post();
    }
    const text = await r.text();
    if (!r.ok) {
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch (_) {}
      $('mi-status').innerHTML = `<span class="err">${esc(detail)}</span>`;
      return;
    }
    $('mi-status').textContent = '';
    // unhide first, then load: a document committed while the iframe is
    // hidden (display:none) gets a 0x0 viewport. Load the document via a
    // blob: URL, not srcdoc — a sandboxed srcdoc document's root box stays
    // 0x0 in Chrome (srcdoc takes precedence over src, so the frame is
    // about:srcdoc), plotly measures 0x0 and renders its 700x450 fallback,
    // i.e. the "blank page". The same document via blob: fills the iframe,
    // and the sandbox attribute still blocks download / popup / navigation.
    frame.hidden = false;
    if (vvizBlob) URL.revokeObjectURL(vvizBlob);
    vvizBlob = URL.createObjectURL(new Blob([text], { type: 'text/html' }));
    frame.src = vvizBlob;
  } catch (err) {
    $('mi-status').innerHTML = `<span class="err">${esc(err)}</span>`;
  } finally {
    unlock();
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
async function loadPrompts() {
  // the last 1000 accepted instructions, latest first; clicking one
  // reuses it as the FFmpeg tab's instruction
  const out = $('prompt-out');
  $('prompt-status').textContent = 'loading…';
  try {
    const r = await fetch('/v1/prompts');
    const data = await r.json();
    const prompts = data.prompts || [];
    $('prompt-status').textContent =
      prompts.length ? '' : 'no accepted prompts yet';
    out.innerHTML = '';
    for (const p of prompts) {
      const row = document.createElement('div');
      row.className = 'filerow';
      row.style.cursor = 'pointer';
      row.title = p.instruction; // full text on hover (the row ellipsizes)
      const when = new Date(p.ts * 1000).toLocaleString();
      row.innerHTML =
        `<span class="fname mono">${esc(p.instruction)}</span>` +
        `<span class="note">${when}</span>`;
      row.onclick = () => usePrompt(p.instruction);
      out.appendChild(row);
    }
  } catch (e) {
    $('prompt-status').innerHTML =
      `<span class="err">prompt list failed: ${esc(e)}</span>`;
  }
}
function usePrompt(text) {
  $('instr').value = text;
  // a programmatic value never fires the input listener — re-derive both the
  // compare labels and the button row (Mediainfo disables on an instruction)
  setCompareMode();
  cmdButtons();
  showTab('cmd');
}
/* the PRESETS block: the generations saved by the FFmpeg tab's Save button.
   The selector lists the preset files newest first; picking one shows its
   full JSON in the preview pane; Load fills the FFmpeg fields from the
   preset and re-renders the stored ready response, so the pane lands
   exactly where a fresh generation left it. */
let presetCurrent = null; // the selected preset's JSON object
function assetText(v) {
  // an asset value is a plain path/URL or {path, kind}
  return typeof v === 'string' ? v : (v && v.path) || '';
}
async function loadPresets() {
  const sel = $('preset-sel');
  $('preset-status').textContent = 'loading…';
  presetCurrent = null;
  $('preset-loadbtn').disabled = true;
  $('preset-preview').hidden = true;
  $('preset-preview').textContent = '';
  try {
    const r = await fetch('/v1/presets');
    const data = await r.json();
    if (!r.ok)
      throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data));
    const presets = data.presets || [];
    sel.innerHTML = '';
    if (!presets.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '(no presets yet — Save a generation in the FFmpeg tab)';
      sel.appendChild(opt);
      $('preset-status').textContent = 'no presets saved on this deployment yet';
      return;
    }
    for (const p of presets) {
      const opt = document.createElement('option');
      opt.value = p.name;
      const when = p.saved_at ? new Date(p.saved_at).toLocaleString() : '';
      opt.textContent = (p.instruction || p.name) + (when ? '  —  ' + when : '');
      sel.appendChild(opt);
    }
    $('preset-status').textContent = '';
  } catch (e) {
    sel.innerHTML = '';
    $('preset-status').innerHTML = `<span class="err">preset list failed: ${esc(e)}</span>`;
  }
}
async function previewPreset() {
  const name = $('preset-sel').value;
  presetCurrent = null;
  $('preset-loadbtn').disabled = true;
  if (!name) {
    $('preset-preview').hidden = true;
    $('preset-preview').textContent = '';
    return;
  }
  $('preset-status').textContent = 'loading…';
  try {
    const r = await fetch('/v1/presets/' + encodeURIComponent(name));
    const text = await r.text();
    if (!r.ok) throw new Error(text);
    presetCurrent = JSON.parse(text);
    $('preset-status').textContent = '';
    // the full file, line by line
    $('preset-preview').textContent = text;
    $('preset-preview').hidden = false;
    $('preset-loadbtn').disabled = false;
  } catch (e) {
    $('preset-preview').hidden = true;
    $('preset-preview').textContent = '';
    $('preset-status').innerHTML = `<span class="err">preset load failed: ${esc(e)}</span>`;
  }
}
function loadPreset() {
  const p = presetCurrent;
  if (!p) return;
  const assets = p.assets || {};
  $('instr').value = p.instruction || '';
  // derive the compare mode from the stored instruction — the same test the
  // next Generate runs, so the fields round-trip through collectAssets()
  const cmp = isCompare();
  if (cmp) {
    $('inp').value = assetText(assets.main);
    $('out').value = assetText(assets.distorted); // '' = server auto-detects
  } else {
    const mains = [assetText(assets.main)];
    let mi = 1;
    while (assets['m' + mi]) { mains.push(assetText(assets['m' + mi])); mi += 1; }
    $('inp').value = mains.map(m => '-i ' + m).join(' ');
    $('out').value = p.output_filename || '';
    $('wm').value = assetText(assets.watermark);
  }
  $('ad').value = p.adapter || 'auto';
  // keep the extra rows' original asset ids (in1, in7, …) so a re-save lands
  // on the same md5 — and bump the id counter past them so "+ add input"
  // can't collide
  for (const k of Object.keys(assets)) {
    const m = /^in(\\d+)$/.exec(k);
    if (m) extraN = Math.max(extraN, parseInt(m[1], 10));
  }
  $('extra').innerHTML = '';
  for (const [k, v] of Object.entries(assets)) {
    if (k === 'main' || k === 'watermark' || k === 'distorted' || /^m\\d+$/.test(k)) continue;
    addExtra();
    const row = $('extra').lastElementChild;
    const inp = row.querySelector('.extra-inp');
    inp.dataset.id = k;
    inp.placeholder = 'e.g. -i song.mp3 (id: ' + k + ')';
    inp.value = assetText(v);
    const kind = (typeof v === 'object' && v.kind) ? v.kind : 'auto';
    row.querySelector('.extra-kind').value = kind;
  }
  $('pf').checked = !!p.preflight;
  $('verbose').checked = !!p.verbose;
  $('servehttp').checked = !!p.servehttp;
  setCompareMode();
  cmdButtons();
  // the 'generation accomplished' state: re-render the stored ready response
  // (command + everything the result pane shows) instead of re-generating
  $('res').hidden = false;
  render({
    status: 'ready',
    command: p.command,
    arguments: p.arguments,
    summary: p.summary,
    defaults: p.defaults,
    warnings: p.warnings,
    validation_level: p.validation_level,
    checks: p.checks,
    evidence: p.evidence,
    raw_llm: null,
  });
  showTab('cmd');
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
  loadRemote('ri', cfg.volumes.input_remote);
  loadRemote('ro', cfg.volumes.output_remote);
  loadRemote('rl', cfg.volumes.log_remote);
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
  $('l-timeout').value = cfg.llm.timeout || 90;
  $('l-thinking').checked = !!cfg.llm.thinking;
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
function readRemote(p) {
  return {
    type: $(p + '-type').value,
    host: $(p + '-host').value.trim(),
    port: parseInt($(p + '-port').value, 10) || null,
    user: $(p + '-user').value.trim(),
    password: $(p + '-pass').value,
    bucket: $(p + '-bucket').value.trim(),
    path: $(p + '-path').value.trim(),
    region: $(p + '-region').value.trim(),
    secure: $(p + '-secure').checked,
    uuid_naming: $(p + '-uuid').checked,
  };
}
function loadRemote(p, rc) {
  rc = rc || { type: 'none' };
  $(p + '-type').value = rc.type || 'none';
  $(p + '-host').value = rc.host || '';
  $(p + '-port').value = rc.port || '';
  $(p + '-user').value = rc.user || '';
  $(p + '-pass').value = rc.password || '';
  $(p + '-bucket').value = rc.bucket || '';
  $(p + '-path').value = rc.path || '';
  $(p + '-region').value = rc.region || '';
  $(p + '-secure').checked = !!rc.secure;
  $(p + '-uuid').checked = !!rc.uuid_naming;
}
async function remoteTest(which, p) {
  const el = $(p + '-status');
  el.innerHTML = '<span class="note">testing…</span>';
  try {
    const r = await fetch('/v1/config/remote-test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ which: which, remote: readRemote(p) }),
    });
    const data = await r.json();
    el.innerHTML = r.ok
      ? `<span class="ok">${esc(data.message)}</span>`
      : `<span class="err">${esc(typeof data.detail === 'string' ? data.detail : JSON.stringify(data))}</span>`;
  } catch (e) {
    el.innerHTML = `<span class="err">${esc(String(e))}</span>`;
  }
}
function readCfg() {
  return {
    volumes: {
      input: $('v-in').value.trim(), output: $('v-out').value.trim(), log: $('v-log').value.trim(),
      input_remote: readRemote('ri'), output_remote: readRemote('ro'), log_remote: readRemote('rl'),
    },
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
      thinking: $('l-thinking').checked,
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
// right-click 'tsanalyze' is offered for these container extensions
// (the server still checks the real format and 400s on non-TS files)
const TS_EXT_RX = /\\.(ts|mpg|mpeg|ps)$/i;
function fmtSize(n) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Number(n) || 0, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return (i === 0 ? v : v.toFixed(1)) + ' ' + units[i];
}
async function loadDisk() {
  try {
    const d = await (await fetch('/v1/files/disk')).json();
    for (const key of ['input', 'output']) {
      const v = d[key] || {};
      $('files-disk-' + key).textContent = v.exists
        ? 'free ' + fmtSize(v.free) + ' / total ' + fmtSize(v.total)
        : 'volume not available';
    }
  } catch (e) {
    $('files-disk-input').textContent = '';
    $('files-disk-output').textContent = '';
  }
}
function fmtDate(sec) {
  const d = new Date(Number(sec) * 1000);
  const p = x => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
    + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}
async function loadFiles() {
  loadDisk(); // fire-and-forget: one fetch feeds both pane footers
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
  // pane-dependent items: 'as input' on the input pane, 'to input' on
  // the output pane; tsanalyze in BOTH panes, only for TS-ish extensions
  const isInput = row.dataset.dir === 'input';
  $('fm-asinput').style.display = isInput ? '' : 'none';
  $('fm-toinput').style.display = isInput ? 'none' : '';
  $('fm-tsanalyze').style.display =
    TS_EXT_RX.test(row.dataset.name) ? '' : 'none';
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
  if (tool === 'as-input') {
    // put this input-folder file into the Command tab's input field
    $('inp').value = name;
    cmdButtons(); // programmatic fill — no input event, re-gate the row
    openModal(name + ' — as input',
      `<p class="note">Copied into the FFmpeg tab's
       <span class="mono">Input video</span> field — switch to FFmpeg
       and press <span class="mono">Generate</span> to run it.</p>`);
    return;
  }
  if (tool === 'to-input') {
    openModal(name + ' — to input', '<span class="note">copying…</span>');
    const body = $('modal-body');
    try {
      const r = await fetch('/v1/files/copy-to-input', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const d = await r.json();
      if (!r.ok) { body.innerHTML = `<p class="err">${esc(detailMsg(d))}</p>`; return; }
      body.innerHTML = `<p class="note">${esc(name)} is now in the input
        folder (the output original was kept). Right-click it in the input
        pane to use it — e.g. <b>as input</b>.</p>`;
      loadFiles(); // panes + free space
    } catch (e) {
      body.innerHTML = `<p class="err">${esc(String(e))}</p>`;
    }
    return;
  }
  if (tool === 'tsanalyze') {
    // reuse the Info/Log tab's TSAnalyze display: pick this file, then run.
    // mi-file is assigned LAST, after showInfo('mi') — fillMi() would
    // otherwise overwrite it from the Command tab's fields.
    showTab('info');
    $('mi-target').value = dir; // the pane the file was right-clicked in
    if ($('info-mi').hidden) showInfo('mi');
    $('mi-file').value = name;
    miButtons(); // programmatic fill — no input event, re-gate the row
    showTSAnalyze();
    return;
  }
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
