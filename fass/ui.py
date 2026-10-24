"""The web UI (PRD §9): a single self-contained page, no external assets
(local-first model). Three tabs:

- Command — the request form, the clarification flow and the generated
  command (with a Run button when execution is enabled);
- Runs — the executed commands, live status, stop, log and output links;
- Config — the deployment config (M6): volume mappings, stream port
  mappings, GPU, the LLM provider, LLM test, save / apply / compose preview.
"""

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
h2 { font-size: 14px; margin: 14px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; }
h3 { font-size: 14px; margin: 18px 0 6px; }
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
.portrow { display: flex; gap: 8px; margin: 6px 0; align-items: center; }
.portrow input { width: auto; flex: 1; }
.portrow select { width: auto; }
.portrow .name { flex: 2; }
</style>
</head>
<body>
<main>
  <h1>fass</h1>
  <p class="sub">Natural language → <strong>validated</strong> FFmpeg commands.
  The model only plans; the command is built deterministically — and runs here only when
  you press Run.</p>

  <div class="tabs">
    <button id="tab-cmd" class="on" onclick="showTab('cmd')">Command</button>
    <button id="tab-runs" onclick="showTab('runs')">Runs</button>
    <button id="tab-cfg" onclick="showTab('cfg')">Config</button>
  </div>

  <!-- ============================================================ COMMAND -->
  <div id="pane-cmd">
  <form id="f" autocomplete="off">
    <label for="instr">Instruction</label>
    <textarea id="instr" required placeholder="cut from 00:10 to 00:30, resize to 720p, and add my logo to the bottom-right corner"></textarea>
    <div class="row">
      <div><label for="inp">Input video (local path or stream URL)</label>
        <input id="inp" required placeholder="clip.mp4 or rtmp://host:1935/live"></div>
      <div><label for="out">Output (optional — filename, or stream URL to send to: rtmp://, rtsp://, srt://, udp://…)</label>
        <input id="out" placeholder="out.mp4, or rtmp://host:1935/live"></div>
    </div>
    <div class="row">
      <div><label for="wm">Watermark image (optional)</label>
        <input id="wm" placeholder="logo.png"></div>
      <div><label for="ad">Adapter</label>
        <select id="ad"><option>auto</option><option>rules</option><option>llm</option></select></div>
    </div>
    <label for="extra-h">Additional inputs (optional — audio/video/image files or streams, e.g. "replace the audio with song.mp3")</label>
    <div id="extra"></div>
    <button type="button" class="copy" onclick="addExtra()">+ add input</button>
    <label class="check"><input type="checkbox" id="pf"> preflight against the local FFmpeg build</label>
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

  <!-- ============================================================= CONFIG -->
  <div id="pane-cfg" hidden>
    <div class="card">
      <h2 style="margin-top:0">Volumes — host folders ↔ container</h2>
      <div class="row">
        <div><label>/input — inputs live here</label><input id="v-in" placeholder="/data/fass/input"></div>
        <div><label>/output — results land here</label><input id="v-out" placeholder="/data/fass/output"></div>
        <div><label>/log — run logs</label><input id="v-log" placeholder="/data/fass/log"></div>
      </div>
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

      <h3>GPU (NVIDIA)</h3>
      <div class="row">
        <div><label class="check" style="margin:0"><input type="checkbox" id="g-en"> GPU enabled (nvenc h264/h265)</label></div>
        <div><label>device</label><input id="g-dev" value="all" placeholder="all, or e.g. 0"></div>
      </div>

      <h3>LLM provider</h3>
      <div class="row">
        <div><label>provider</label>
          <select id="l-prov"><option value="anthropic">anthropic</option><option value="openai">openai-compatible (strata, vLLM, …)</option></select></div>
        <div><label>host (openai provider)</label><input id="l-host" placeholder="172.17.0.1"></div>
        <div><label>port</label><input id="l-port" type="number" placeholder="8080"></div>
        <div><label>api key (anthropic / optional)</label><input id="l-key" type="password" placeholder="sk-… / blank"></div>
        <div><label>model</label><input id="l-model" placeholder="qwen3.8-27b"></div>
      </div>
      <button type="button" class="small ghost" style="margin-top:10px" onclick="llmTest(true)">test LLM (unsaved values)</button>
      <span id="llm-status"></span>

      <h3>Deploy</h3>
      <div id="cfg-status" class="note"></div>
      <div class="row" style="align-items:center">
        <div style="flex:0 0 auto"><button onclick="saveCfg()">Save</button></div>
        <div style="flex:0 0 auto"><button class="danger" id="apply-btn" onclick="applyCfg()" disabled>Apply to container</button></div>
      </div>
      <h2>Generated compose (what Apply creates)</h2>
      <pre id="compose"></pre>
    </div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let lastRunId = null;
let EXEC_ENABLED = false;
fetch('/v1/env').then(r => r.json()).then(d => { EXEC_ENABLED = !!d.execution; }).catch(() => {});

function showTab(t) {
  for (const p of ['cmd','runs','cfg']) {
    $('pane-'+p).hidden = (p !== t);
    $('tab-'+p).classList.toggle('on', p === t);
  }
  if (t === 'runs') loadRuns();
}

/* ------------------------------------------------------------ command tab */
let extraN = 0;
function addExtra() {
  extraN += 1;
  const id = 'in' + extraN;
  const row = document.createElement('div');
  row.className = 'row';
  row.innerHTML = `<div><input class="extra-inp" data-id="${id}" placeholder="e.g. song.mp3 (id: ${id})"></div>
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
  const assets = { main: $('inp').value.trim() };
  if ($('wm').value.trim()) assets.watermark = $('wm').value.trim();
  document.querySelectorAll('.extra-inp').forEach(i => {
    const v = i.value.trim();
    if (!v) return;
    const kind = i.parentElement.querySelector('.extra-kind').value;
    assets[i.dataset.id] = (kind === 'auto') ? v : { path: v, kind };
  });
  const body = {
    instruction: $('instr').value.trim(),
    assets, target_shell: 'posix', adapter: $('ad').value, preflight: $('pf').checked,
  };
  if ($('out').value.trim()) body.output_filename = $('out').value.trim();
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
function render(d) {
  const badge = d.status === 'ready' ? 'b-ready' :
                d.status === 'needs_clarification' ? 'b-clar' : 'b-bad';
  let html = `<span class="badge ${badge}">${esc(d.status)}</span>`;
  if (d.status === 'ready') {
    html += `<h2>Command</h2>
      <pre class="cmd" id="cmd">${esc(d.command)}</pre>
      <button class="copy" onclick="navigator.clipboard.writeText($('cmd').textContent)">copy</button>
      <h2>Argument array (shell-safe)</h2><pre>${esc(JSON.stringify(d.arguments, null, 2))}</pre>
      ${EXEC_ENABLED
        ? `<button id="runbtn" onclick="runCmd()">▶ Run now</button> <span id="rune" class="note"></span>
           <pre id="cmdargs" hidden style="display:none">${esc(JSON.stringify(d.arguments))}</pre>`
        : '<p class="note">execution is disabled on this deployment (FASS_ALLOW_EXECUTE unset) — copy the command and run it yourself</p>'}
      <h2>What it does</h2>${lists(d.summary)}
      <h2>Defaults applied</h2>${lists(d.defaults) || '<p>none</p>'}
      <h2>Warnings</h2>${lists(d.warnings) || '<p>none</p>'}
      <h2>Validation</h2><p>${esc(d.validation_level)} — ${esc((d.checks || []).map(c => (c.ok ? 'ok' : 'FAIL') + ': ' + c.name + ' — ' + c.detail).join('; ') || 'n/a')}</p>`;
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
async function runCmd() {
  const argv = JSON.parse($('cmdargs').textContent);
  const btn = $('runbtn');
  btn.disabled = true; btn.textContent = 'starting…';
  try {
    const r = await fetch('/v1/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ arguments: argv }),
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
      ? `<a href="/media/output/${encodeURIComponent(run.output)}" target="_blank">${esc(run.output.split('/').pop())}</a>`
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
  if (!confirm('Recreate the fass container with the saved config?\nThe UI will lose connection for a few seconds while the container is swapped (the worker sidecar does the work).')) return;
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

loadCfg().catch(e => $('cfg-status').textContent = 'config load failed: ' + e);
</script>
</body>
</html>
"""
