# HANDOFF — fass (natural language → validated FFmpeg commands)

## 2026-10-30 — audio stream copy SHIPPED to i7 (91f1bf1 + bump 3a15f7c, PUSHED + deployed + live-verified; "Enter" button still REQUESTED + NOT STARTED; full suite 1566 passed / 2 skipped; details in PROGRESS.md "Last state")
1. **Commit-based versioning — now PUSHED + on i7 (v1.137036.91f1bf1).** User rule (life of project): every code commit → recover its short sha → `__version__ = "v1.137036.<sha>"` in `fass/version.py` → commit the bump (bump references the code commit it follows; no recursive bump; docs-only commits exempt; VERSION_ID changes only on user word). `0828bd6` (ui: no v prefix) + `dd7159f` (version.py + test_cli pin), first application: audio copy `91f1bf1` → bump `3a15f7c`. `fass --version` → `fass v1.137036.91f1bf1`. PENDING: docs commit (this section) + rule memorization (mycelium + file memory) + report. **Any NEW code commit from now on must bump version.py per this rule.**
2. **Audio stream copy — SHIPPED (2026-10-30; user bug "…h264 baseline 600k … and copy audio as is" → error "audio stream copy is not supported").** `AudioMode.COPY`; re-encode builder emits `-c:a copy`; upfront planner validation rejects copy vs speed/resample/audio filters/filtergraph/combine/audio bitrate/codec options/ABR ladder/device/container-can't-carry-input-codec/codec mismatch; copy+replace → replace wins (warning); same-codec shape remuxes; rules NLU `_AUDIO_COPY` ("copy the audio as-is", "keep the audio untouched", "without re-encoding the audio"…) incl. "copy" in no-op/ladder/subtitles/merge paths; llm.py prompt documents "copy"; explain.py line. User's exact instruction now yields `-c:v libx264 -b:v 600k … -profile:v baseline … -c:a copy`. 18 new tests + bench-092/093. **i7 deploy (user-authorized "propagate audio-copy to i7"):** ff-merged 138a563→3a15f7c (compose opt-in + ffprobe patch verified intact), rebuild `fass` + force-recreate; live-verified — healthz ok, in-container + `/v1/env` = `v1.137036.91f1bf1`, the user's EXACT instruction on titan_double.mp4 → ready with `-c:a copy` in the argv + the audio-copy notes; fass-worker / fass-rag unharmed.
3. **"Enter" button — REQUESTED, NOT STARTED (user hit "stop").** FFmpeg pane, between Save and Mediainfo, for power users: skips NLU/LLM, prefills the command line `ffmpeg -i /input/<in1> [-i /input/<in2>…] -o /output/<out>` from the Input video field (multi `-i`) + Output field. Map of the code reached: ui.py:235-241 (button row), `cmdButtons()` 579-594, `render()`/`#cmd`/`#cmdargs`/`runCmd()` 809-886, `loadPreset()` = the local synthetic-render pattern. Open: prefix rules (filename → `/input/`, URL → as-is; output → `/output/`, leading `/`/`://` → as-is) and whether extra-input rows join the `-i` list.

## Programmatic-fill row re-gate + Run now restore — SHIPPED (2026-10-29; 138a563, PUSHED + i7 DEPLOYED + live-verified 22:53Z; user browser-test next; full suite 1548 passed / 2 skipped)
Two user-reported bugs from live-testing the deployed row-state build (dae2e31): (1) "buttons are disabled on opening. next i go to 'Files' tab, select a file and do right click->'as input', this puts file into 'Input video' on FFmpeg tab. at that moment button 'Mediainfo' must become active ! it's not right now."; (2) "i've executed a generation (generated ffmpeg cli and next executed it), it finished ok, but on FFmpeg pane last button got stuck in state 'starting...'".
- **Root cause (1) — the programmatic-fill class:** a programmatic `.value =` assignment fires no `input` event, so the row-state wiring (cmdButtons/miButtons, 880130e/c0402a5) never re-gated. Audit of every programmatic fill of `$('inp')`/`$('instr')`/`$('out')`/`$('wm')`/`$('mi-file')`: `clearCmdPane`/`usePrompt`/`loadPreset` already re-gated; two did not — the Files-tab right-clicks: "as input" sets `$('inp').value = name` (then opens its modal) and "tsanalyze" sets `$('mi-file').value = name` (then `showTSAnalyze()`), leaving the FFmpeg and Info/Log rows stuck all-disabled. Fixed AT THE FILL SITE: `cmdButtons();` after the inp fill, `miButtons();` after the mi-file fill (commented "programmatic fill — no input event, re-gate the row").
- **Root cause (2):** `runCmd()` flipped `#runbtn` to `starting…` while `POST /v1/runs` is in flight but restored it only in the two failure branches — the success branch left it stuck (the run is server-side, tracked in the Runs tab, so a successful start does not consume the button). A single `restore()` now runs on success / HTTP failure / network error; the old failure restore also read 'Run now' without the glyph — the restored text matches the markup's '▶ Run now' again.
- **Tests (tests/test_webinfo.py):** the row-state test extended — the as-input branch body must carry `$('inp').value = name;` + `cmdButtons();`, the tsanalyze branch body `$('mi-file').value = name;` + `miButtons();`; new `test_ui_run_now_restores_after_every_outcome` (markup `▶ Run now`, the `starting…` flip, the restore definition, `body.count("restore();") == 3`). **Full suite: 1548 passed / 2 skipped (143.11 s).**
- **Verified headless (tr4, port 8099, real clicks):** fresh pane all-disabled; Files tab → right-click `clip.ts` → "as input" → `#inp` filled + row {Generate off, Save off, Mediainfo ON, Clear ON}; right-click → "tsanalyze" → `#mi-file` filled + Info/Log row all four on (tsbtn settles after its in-flight toggle); Run now across three stubbed POST /v1/runs outcomes — ok → "run r-1 started" + button restored, HTTP error → "run failed: boom" + restored, network reject → "run failed" + restored; zero page errors. (Note: the first headless run mis-read the failure branch — a TEST-harness typo, the stub swap `window.fetch((u,o) => …)` was a call, not an assignment, so the "failure" case still hit the success stub; the app's failure path was green in isolation before the harness was fixed.)
- **i7 deploy: DONE (user-authorized "commit and deploy it"):** pushed `2fc427f..138a563`; i7 ff-merged `dae2e31 → 138a563` (compose opt-in `FASS_ALLOW_EXECUTE=1` + the `fass/probe/ffprobe.py` "Exiting with exit code" patch verified intact); `docker compose -f docker/compose.yaml build fass` + `up -d --force-recreate fass`. **Live-verified 22:53Z:** healthz `{"ok":true}`; served page — as-input `cmdButtons();` re-gate ×1, tsanalyze `miButtons();` re-gate ×1, runCmd restore definition ×1, `restore();` ×3, old stuck-restore string ×0, `▶ Run now` markup ×1; fass-worker (Up 5 h) / fass-rag (healthy) unharmed; fass-http still Exited(127) pre-existing; LLM on 8080 → 200.
- **Next:** user re-test on i7 — fresh FFmpeg pane → Files tab → right-click a file → "as input" → Mediainfo + Clear come on at that moment (Generate/Save stay off); generate + Run now → after the run starts the button reads '▶ Run now' again (on a failed start too).

## FFmpeg button row follows its fields — SHIPPED (2026-10-29; 880130e, PUSHED + i7 DEPLOYED + live-verified 22:14Z; user browser-test in progress; full suite 1547 passed / 2 skipped)
User (while browser-testing the deployed presets build): "on opening FFmpeg tab very first time, after clearing it, deactivate buttons 'Generate' etc, as nothing is entered they cant work" — the same row-state pattern as the Info/Log fix (c0402a5), applied to the FFmpeg pane's row (Generate | Save | Mediainfo | Clear).
- **UI (fass/ui.py):** all four buttons now start `disabled` in the markup (Save already did — it is gated on `lastReady`). `cmdButtons()` REPLACES `syncMiBtn()` (whose single rule moves inside): Generate enabled iff instruction AND input are non-empty (both `required`); Mediainfo iff input non-empty AND instruction empty (its original rule — a peek never silently replaces a real command); Clear iff any of instruction / input / output / watermark / an extra-input row has content. Save is untouched — `render()`/`clearCmdPane()` keep gating it on `lastReady`. Wired on the input events of `#instr` (wrapping `setCompareMode()`), `#inp`, `#out`, `#wm`, and each extra-input row's field (listener attached in `addExtra()`, so rows created by `loadPreset()` get it too); once at load; and after every programmatic fill that bypasses the listeners — `clearCmdPane()`, `usePrompt()`, `loadPreset()`, `runMediaInfo()`'s tail.
- **Tests:** test_webinfo.py — 3 syncMiBtn pins reworked (function name; the mi-btn gating expression → `!inp || instr !== ''`; the usePrompt comment) + new `test_ui_ffmpeg_button_row_follows_the_fields` (row starts disabled in markup; the three gating expressions; exactly four plain `addEventListener('input', cmdButtons)` listeners + the instr wrapper; clearCmdPane re-derives the row). **Full suite: 1547 passed / 2 skipped (142.50 s).**
- **Verified:** headless-Chrome click test ALL GREEN (fresh pane: all four disabled; instruction only: just Clear; + input: Generate on, Mediainfo off (instruction present); input only: Mediainfo on; nothing: all disabled again; extra-input content: Clear enabled; after Clear: instr/inp/out/wm emptied, no extra rows, all four disabled; zero page errors) — throwaway venv server on tr4 port 8099 (temp dirs /tmp/fass_ui_test, exact-PID killed, no repo artifacts).
- **i7 deploy: DONE (user-authorized "push and deploy to i7, i will test it"):** pushed `812e236..dae2e31`; i7 ff-merged `812e236 → dae2e31` (compose opt-in `FASS_ALLOW_EXECUTE=1` + the `fass/probe/ffprobe.py` "Exiting with exit code" patch verified intact); `docker compose -f docker/compose.yaml build fass` + `up -d --force-recreate fass`. **Live-verified 22:14Z:** healthz ok; served page carries `function cmdButtons()` + `id="genbtn" disabled` + `id="mi-btn" disabled` + `id="clearbtn" class="ghost" disabled` (1 each), exactly four `addEventListener('input', cmdButtons)` listeners, ZERO `syncMiBtn`; presets regression round-trip OK (`POST` → `a33345b9…json` in `/config/presets`, listed, raw JSON served, 404 guard, test preset removed); fass-worker/fass-rag unharmed; LLM on 8080 still 200.
- **Next:** user browser test of the row states (fresh FFmpeg tab: all four buttons disabled; type instruction → only Clear enables; add input → Generate enables, Mediainfo off; clear instruction → Mediainfo enables; Clear → everything wiped + row all-disabled) alongside the presets flow.

## Presets (save + re-load a successful generation) — SHIPPED (2026-10-29; code 8b54574 + docs 812e236, PUSHED + i7 DEPLOYED + live-verified 21:35Z; user browser-test in progress; full suite 1546 passed / 2 skipped)
User spec: "a preset = a successfully generated ffmpeg command set. 'Save' button next to 'Generate' on the FFmpeg pane storing Instruction text, inputs, outputs, generated ffmpeg string into a JSON file named by md5 over 'Instruction+inputs+ffmpeg command'. In Info/Log pane, a 'PRESETS' block under 'WHAT TO LOOK AT': selector of preset json files; selecting shows full content line-by-line in a small div; on confirm, FFmpeg pane fields filled from preset and set to the 'generation accomplished' state."
- **Server (fass/api.py):** `PresetSaveRequest` (extra=forbid; instruction max 4000, command max 65536, both min_length=1; output_filename/adapter optional; preflight/verbose/servehttp bools; arguments/summary/defaults/warnings/validation_level/checks/evidence optional — the result-pane metadata). `_presets_dir()`: `$FASS_PRESETS_DIR` when set (tests), else `config_path().parent / "presets"` → **`/config/presets` in the container, persisted by the EXISTING `/config` volume — no compose change needed on i7**. `_preset_name()`: md5 over `instruction + "\n" + json.dumps(assets, sort_keys=True, separators=(",",":")) + "\n" + command` + `.json` — assets key order does NOT change the name; a changed instruction, input, or command does. `GET /v1/presets`: newest first (saved_at, name), rows `{name, instruction, command[:120], saved_at, size}`; non-`.json` and corrupt/non-dict JSON files are skipped, never a 500. `POST /v1/presets`: idempotent overwrite on the same md5; the file is written WITHOUT sort_keys (indent=2 + trailing newline) so the assets' insertion order — the order the `-i` flags take — is preserved; `saved_at` = `time.strftime('%Y-%m-%dT%H:%M:%SZ', gmtime())`. `GET /v1/presets/{name}`: the raw file text, content-type application/json; strict `re.fullmatch(r"[0-9a-f]{32}\.json", name)` — the filename itself is the path-traversal guard; unsaved name → 404, corrupt file → 500.
- **UI (fass/ui.py):** Save button `#savebtn` (ghost) in the Generate row + `#save-status` note. **Save is gated on a real generation:** `let lastReady = null` — `render()` sets it to the response when `d.status === 'ready'` (else null) and re-gates the button; `clearCmdPane()` resets it + the status. `collectAssets()` extracted VERBATIM from `submit()` and shared by both — compare: `main`/`distorted`; non-cmp: `mainInputs()` split → `main` + `m1..mn`, `watermark`, then the `.extra-inp` rows (kind from `.closest('.row')`, `auto` → bare string, else `{path, kind}`) — so a preset always stores exactly what the next Generate would send. `savePreset()`: posts instruction + collectAssets() + `$('cmd').value` + output + adapter + the three checkboxes + lastReady's arguments/summary/defaults/warnings/validation_level/checks/evidence; status "saved as <name> (Info / Log tab → presets)" or an err span. **Info/Log:** fourth radio `#sel-presets` under WHAT TO LOOK AT → `#info-presets` (toggle in `showInfo` + lazy `loadPresets()`, like prompts): `#preset-sel` (option text = instruction + locale saved_at, value = filename) + "↻ refresh" + `#preset-status` + `#preset-preview` (pre, max-height 220px, pre-wrap — the FULL raw file, line by line) + `#preset-loadbtn` (ghost, disabled until a preview has loaded). `loadPresets()` (empty state: "(no presets yet — Save a generation in the FFmpeg tab)"), `previewPreset()` (raw text → `presetCurrent` = parsed object → preview pane → enable Load). **`loadPreset()` = restore-by-synthesis:** fill `instr`; the compare branch is decided by `isCompare()` on the stored instruction (the SAME test the next Generate runs) — cmp: `inp`/`out`(distorted); non-cmp: mains `main`+`m1..mn` joined as `-i …` into `inp` (display only — collectAssets re-splits to the same map), `out` = output_filename, `wm` = watermark; adapter + preflight/verbose/servehttp; then recreate the extra-input rows with their ORIGINAL `inN` ids (a re-save from the restored pane lands on the SAME md5 — idempotent) with `extraN` bumped past them so "+ add input" cannot collide; finally `render({status:'ready', command, arguments, summary, defaults, warnings, validation_level, checks, evidence, raw_llm:null})` + `showTab('cmd')` — pixel-identical generation-accomplished state, NO re-generation (no LLM nondeterminism).
- **Tests:** NEW tests/test_presets.py — 10 tests, ALL PASS: empty-dir list; save+list roundtrip (name = independent md5 recompute, saved_at shape, full file schema, assets key order ["main","in1"] preserved, list row command[:120]+size); idempotent re-save (1 file); name tracks instruction/inputs/command + reordered-assets key-order invariant; raw-JSON get (text == file text); non-preset names → 404 (no .json, non-hex, unsaved); corrupt + foreign files skipped by the list, corrupt direct read → 500; malformed bodies → 422 (empty command/instruction, extra field) with a valid body still saving; Save-button UI markup; PRESETS-block UI markup. tests/test_webinfo.py: the `verbose: $('verbose').checked` count pin 2 → 3 (savePreset() is the third, legitimate occurrence). **Full suite: 1546 passed / 2 skipped (142.76 s).**
- **i7 deploy: DONE (user-authorized "push and deploy to i7, i will test it"):** pushed `aa1301e..812e236`; i7 ff-merged `c0402a5 → 812e236` (local compose opt-ins `FASS_ALLOW_EXECUTE=1` + the `fass/probe/ffprobe.py` "Exiting with exit code" patch verified intact); `docker compose -f docker/compose.yaml build fass` + `up -d --force-recreate fass`. **Live-verified 21:35Z:** healthz ok; all UI markers in the served page (savebtn/savePreset/lastReady/collectAssets/info-presets/preset-loadbtn/loadPreset); full round-trip — `POST /v1/presets` → `dd03bc65…json` in `/config/presets` (host-viewed on the volume), `GET /v1/presets` lists it, `GET /v1/presets/<name>` returns the raw JSON, `notes.json` → 404 (traversal guard); test preset removed after; fass-worker/fass-rag unharmed; LLM on 8080 still 200.
- **Next:** user browser test (in progress): generate → Save → Info/Log → presets → select → read the line-by-line preview → Load → FFmpeg pane filled + generation-accomplished state; then Save again from the restored pane → SAME md5 (idempotent overwrite, list still shows one entry). (The FFmpeg row-state fix above, 880130e, landed in the NEXT deploy round, 22:14Z.)

## vviz (bitrate visualisation) — SHIPPED (2026-10-29; code 3e3a25b + UI fix batch 255fc2e, both PUSHED + i7 DEPLOYED + live-verified; user browser re-test pending)
User request: "add vviz app into docker … add bitrate visualisation option to Info/Log tab, under 'mediainfo'". Clarifications: vviz works for files in BOTH the input and output folders; the temp file is ALWAYS in the output folder; remux warning for non-mp4 > 20 min (user confirms to wait); free-space check before any copy/remux; codec gate = h264/hevc/av1/vp9 (the "av2" in the request meant VP9, confirmed); ".pg" in the request = program stream `.ps` (covered by "non-mp4 → remux").
- **Docker (docker/Dockerfile):** new `bento` builder stage — Bento4 mp4dump (Bento4-SDK-1-6-0-641 static binary, vviz_py's hard dependency) → /usr/local/bin; `git` in the final apt line; the venv RUN now also does `pip install plotly==5.24.1 pandas scipy pytz` + `git clone --depth 1 wabiloo/vviz_py /opt/vviz_py` + the pkt_pts→pts sed patch (ffprobe≥5 dropped `pkt_pts`) + `COPY --from=bento mp4dump`.
- **Server (fass/api.py):** `VvizRequest {target: input|output, file, confirm=false}` (extra="forbid"); module helpers `VVIZ_VIDEO_CODECS={h264,hevc,av1,vp9}`, `VVIZ_TMP_NAME="vviz_src.mp4"` (space-free — vviz shells out with shell=True), `_vviz_py_path`/`_vviz_mp4dump` (FASS_VVIZ_PY / FASS_VVIZ_MP4DUMP overrides), `_vviz_remux_warn_seconds` (FASS_VVIZ_REMUX_WARN_SECONDS, default 1200), `_vviz_timeout` (FASS_VVIZ_TIMEOUT, default 900), `vviz_available()` (mp4dump on PATH + vviz.py present, else 503), `_vviz_duration`, `_vviz_run(target,title)` (runs `sys.executable /opt/vviz_py/vviz.py <target> -f interactive --ffprobe-exec ffprobe --mp4dump-exec mp4dump -t <title>`; vviz writes its Plotly HTML NEXT TO the target file — hence the temp workdir; 502 on failure). `POST /v1/vviz` inside create_app(): bare-name jail → `execute.volume_file` (404) → output dir must be mounted (503) → ffprobe codec gate (400) → mp4 sources (probe format mp4 + .mp4 suffix) are BYTE-COPIED (no repackage, no confirm); non-mp4 are `ffmpeg -c copy -f mp4` remuxed with the >20-min confirm gate (409 → UI re-POSTs confirm:true) → 507 when output free ≤ source size → `_vviz_run` → `HTMLResponse(html)`; `finally: shutil.rmtree(workdir)` (workdir = `.vviz-*` in /output).
- **UI (fass/ui.py):** "bitrate visualisation" button (`#vvizbtn`) after TSAnalyze in the Info/Log media-info block + hidden `#vviz-frame` iframe (640px). Gated on the probed video codec (`h264/hevc/av1/vp9`, mirroring tsbtn's mpegts gate in `showMediaInfo()`); reset in `fillMi()`. `showVviz()`: POST /v1/vviz; 409 → `confirm(detail)` → re-POST confirm:true; OK → self-contained Plotly loaded into the sandboxed iframe via a blob: URL (see the UI fix-batch bullet below — srcdoc + sandbox broke the chart's layout in Chrome).
- **Tests:** tests/test_api.py +13 (503 unavailable; bare-name/schema 400+422; 404; unsupported-codec 400 + audio-only; output-volume source; mp4 copied-not-remuxed; long-ts 409→confirm→remux argv `-c copy -f mp4`; short non-mp4 direct; remux-failure 400; no-room 507 (disk_usage faked); unmounted output 503; E2E h264 mp4, skipped without the vviz stack) + `test_vviz_button_is_in_the_ui` in test_api.py; `import types` added. **Status: full suite 1535 passed / 2 skipped (tr4 host; the vviz E2E test skips — no vviz stack on the host).**
- **tr4 in-container E2E (`fass:latest`, port 8099, fixtures in /tmp/vviz_e2e/in):** h264.mp4, h264.ts, hevc.mp4, hevc.ts, av1.mkv, vp9.webm ALL → 200 + Plotly chart (~4.6 MB self-contained HTML each); bad_mpeg4.mpg → 400 "has video codec mpeg4, which vviz cannot visualise (supported: av1, h264, hevc, vp9)"; 21-min long.ts → 409 without confirm, 200 + 6.95 MB chart in 5.3 s wall with confirm:true; no `.vviz-*` leftovers in /output; served UI markers (vvizbtn/vviz-frame/showVviz) present; in-container stack check passed (mp4dump at /usr/local/bin, /opt/vviz_py/vviz.py, plotly/pandas/scipy/pytz importable, pkt_pts patch at models.py:167).
- **i7 deploy (user-authorized "propagate vviz build to i7 and i will test it"):** i7 ff-merged b7ad2eb→3e3a25b (`git fetch origin && git merge --ff-only origin/main`; local compose.yaml opt-ins + ffprobe.py "Exiting with exit code" patch verified intact), `docker compose -f docker/compose.yaml build fass` (bento stage + venv deps + vviz_py built clean) + `up -d --force-recreate fass`. **Live-verified:** healthz `{"ok":true}`; UI markers present; `POST /v1/vviz` on titan.mp4 (mp4 direct) → 200 + plotly, on titan.ts (remux) → 200 + plotly; no output-volume leftovers; fass-worker/fass-rag unharmed (running 52 min); fass-http already `Exited (127)` 14 h BEFORE the deploy (pre-existing, not caused by it); LLM on 8080 untouched. **Docs commit: (this commit) + PUSHED.**
- **UI fix batch (user's first live test — COMMITTED 255fc2e + PUSHED + i7 RE-DEPLOYED + live-verified):** user live-tested the i7 build and reported: (1) "when i click on the button i get a blank browser page, and then it is downloaded"; (2) "the button does not disable/dim"; plus asks: clear the previous outputs (mediainfo/tsanalyze/other vviz) before the chart so the chart is the only info shown; the chart must NOT be downloadable (everything stays inside the web UI); button title "bitrate visualization" → "Bitrate"; while the vviz button is processing, all other row buttons dim + disable; a "Clear" button next to "Bitrate" that clears outputs. **Root cause of the blank page (empirical A/B matrix, /tmp/vviz_e2e/iframe_diag7-13.py):** a SANDBOXED SRCDOC document's root box stays 0×0 in Chrome (srcdoc takes precedence over src, so the frame is about:srcdoc) — html/body/wrapper all 0×0, plotly.js measures a 0×0 container and falls back to a 700×450 svg (invisible chart). The same document renders fine without the sandbox, and fine via a real URL WITH the sandbox — only the srcdoc+sandbox combination breaks. **Fixes:** (a) `showVviz()` loads the document via a blob: URL (`frame.src = URL.createObjectURL(new Blob([text], {type:'text/html'}))`, previous blob revoked, unhide before load) — the `sandbox="allow-scripts"` attribute stays, so download/popup/navigation remain structurally blocked; `hideVvizFrame()` (hidden + src=about:blank + revoke) centralises the one-output-at-a-time clearing in fillMi/clearMiPane/showMediaInfo/showTSAnalyze. (b) `_vviz_finalize_html()` (renamed from `_vviz_strip_modebar`) injects into the head: `html,body{height:100%;margin:0}` + `body>div{height:100%}` (vviz's plot div is `height:100%` inside an auto-height wrapper — the percentage chain cannot resolve without this) + `.plotly-graph-div{width:100vw!important;height:100vh!important}` (viewport-pinned, structure-independent backstop) + `.modebar{display:none!important}` (hides plotly's "Download Plotly.js HTML / image" bar). (c) row lock: all four buttons (Show media info / TSAnalyze / Bitrate / Clear) disabled while generating, restored in `finally { unlock(); }`, dimmed via `#info-mi button:disabled { opacity:.5; cursor:not-allowed; }`, status text during the remux; "Bitrate" label; `#mi-clearbtn` (Clear) wipes the report + chart + status. **Verified:** headless-Chrome click test on tr4 ALL GREEN (chart renders INSIDE the sandboxed iframe at 792×638 via the blob URL, svg matches the frame, modebar display:none, no new window, lock/clear/restore, no JS page errors); full suite **1536 passed / 2 skipped**; i7 re-deploy (ff-merged → 255fc2e, compose opt-ins + ffprobe.py patch intact, `docker compose build fass` + `up -d --force-recreate fass`) live-verified (healthz ok; UI tokens incl. createObjectURL/hideVvizFrame; `POST /v1/vviz` titan.mp4 → 200 + plotly + all new CSS rules, titan.ts remux → 200; no `.vviz-*` leftovers; fass-worker/fass-rag healthy; LLM 8080 → 200).
- **Row-state fix (user round 2 — COMMITTED c0402a5 + PUSHED + i7 RE-DEPLOYED + live-verified):** user: "on opening Info/Log page, all buttons should be disabled, because till file is not selected these operations can't be done. after file name is entered into File field, you should activate ALL buttons. also, on Clear button press, content of File input field should be cleared." All four buttons (Show media info / TSAnalyze / Bitrate / Clear) now start `disabled` in the markup and in `fillMi()`; `miButtons()` (on the File input's `oninput`) enables all four iff the field is non-empty (typed or auto-filled) and re-disables when emptied; `clearMiPane(wipeFile)` — the Clear button calls `clearMiPane(true)` — additionally empties the File field (→ row disabled again), while `showVviz()`'s internal `clearMiPane()` keeps the file. `miBusy` guards the row lock: `miButtons()` ignores input while a chart generates. `showMediaInfo()` still re-gates TSAnalyze/Bitrate by the probed format/codec after a probe (server enforces the same checks, so a wrong click just gets a 400). Also fixed: `showTSAnalyze()` re-enables its button in a `finally` (a 400 previously left it stuck disabled — a wide path now that any entered file enables the row). test_api.py: `test_vviz_button_is_in_the_ui` +5 assertions (oninput/miButtons, the three disabled-on-open buttons, clearMiPane(true)). **Verified:** headless-Chrome click test all green (tab open no file → all four disabled; file entered → all four enabled; Clear → field emptied + row disabled; rest of the Bitrate flow unchanged); full suite 1536 passed / 2 skipped; i7 re-deploy (ff-merged → c0402a5, opt-ins + ffprobe patch intact, compose build + force-recreate) live-verified (healthz ok; UI tokens incl. `oninput="miButtons()"` / `clearMiPane(true)` / disabled mi-showbtn; titan.mp4 → 200 + plotly; fass-worker/fass-rag healthy).
- **Next:** user browser re-test of the fixed build on i7 (Info/Log tab → all buttons disabled until a file is entered; Bitrate: chart visible, not downloadable; Clear wipes outputs + the File field; dimmed row while processing). After that: the presets feature — SHIPPED (8b54574, pushed + i7 deployed + live-verified; see the Presets section above).

## Goal
CLI-first pipeline (instruction → NLU → strict EditPlan → planner → builder
argv → POSIX render), 5 MVP ops (trim, resize, grayscale, encode/convert,
one watermark), 70+ deterministic tests, then a 50-request benchmark, real
media-fixture outcome tests, README. No web UI yet. (Shipped as milestone 1;
the original plan doc was removed from the repo and its history 2026-10-24.)
Spec: ffmpeg-assistant-prd.md.

**MILESTONE 1 COMPLETE (2026-10-23).** 162 tests pass (33 s): 70 deterministic
(schema, rules, planner/builder, render-adversarial, cli) + 78 language
benchmark + 10 media outcomes (synthetic lavfi) + 4 real-media (titan.mp4).

**MILESTONE 2 COMPLETE (2026-10-23).** FastAPI web layer over `generate()`
(web after the CLI path works end to end). 178 tests
pass: +16 API tests (PRD §9 contract, security invariants). `fass serve`
smoke-tested live (healthz, /v1/commands, UI).

## Current state (2026-10-29, **AV1 via libaom-av1 + the encoder-expansion batch (libsvtav1/av1_svt, av1_nvenc, libtheora/ogv, libvpx/VP8, libvvenc/VVC) BOTH ADDED (COMMITTED 0ffc75a + PUSHED + DEPLOYED to i7 + live-verified — full suite 1518 passed / 1 skipped, E2E-verified on tr4 6.1.1: svt→av1, theora→ogv, vp8→webm, vvc plan-ready with preflight flagging libvvenc absent); jpeg2000 DEFERRED (still-image codec, needs a dedicated single-frame→.jp2 export, not a video re-encode — needs a user decision); prior: smaller + darker solid-accent buttons + 'Apply to container' → #1c5c52 (COMMITTED 586b800 + PUSHED + DEPLOYED to i7 + live-verified)**)
**Shipped this session:** 4-change UI/log bundle (3babae7, DEPLOYED to i7 + live-verified), raw LLM output in the UI (88cb80e, pushed + DEPLOYED to i7 + live-verified), Files-tab bundle (0f7fcd7, pushed + DEPLOYED to i7 + live-verified), tsanalyze-output + Clear (447e73f), Mediainfo-as-button + non-exclusive options (946fa66), prompt log (979acea), **smaller + darker accent buttons + Apply→#1c5c52 (586b800, pushed + DEPLOYED to i7 + live-verified)**.
**HLS playback broken in the Player tab (user 2026-10-29: "hls playing in player is broken … shaka don't play file", input titan_double.mp4, "serve over http" on):** TWO independent causes.
- **(a) fass-http sidecar was dead (i7 deployment state, NO code):** `docker compose ps -a` → fass-http `Exited (127)` (Apache log: `caught SIGWINCH, shutting down gracefully` at 2026-10-28 23:31) AND `docker inspect` `Ports={}` — that container instance had NO published port, so nothing listened on `:8081` (the `http_base_url`). The browser's manifest fetch died at the TCP layer → Shaka played nothing (the "live" badge was just the run still `running` at that moment; it later finished `success`). **Fix (i7 only, no rebuild):** `docker compose up -d --force-recreate fass-http` from the current config (renders `published: "8081"` from `FASS_HTTP_PORT=8081` in `docker/.env`); verified `0.0.0.0:8081->80/tcp` + playlist AND segment both return 200 + `Access-Control-Allow-Origin: *` (the CORS conf `docker/fass-http/httpd-fass.conf` is intact). **Latent trap:** fass-http's compose DEFAULT port is 8080 (`${FASS_HTTP_PORT:-8080}`); the user's LLM occupies 8080, so `FASS_HTTP_PORT=8081` in `.env` and `streaming.http_base_url` on :8081 MUST stay in agreement or the sidecar tries to bind 8080 and collides with the LLM.
- **(b) VOD HLS truncated to the last 5 segments (code, COMMITTED b7ad2eb):** even with the sidecar up, the `.m3u8` listed only the last 5 of 13 segments (`MEDIA-SEQUENCE:8`, `#EXT-X-ENDLIST`) — Shaka would play only the tail ~16s of the 40s clip. Root cause: **FFmpeg 9.0 changed the default `hls_list_size` from 0 (keep all) to 5** (`ffmpeg -h muxer=hls` → "(default 5)"); fass set no value for a VOD output, so it inherited 5 and rolled. **Fix (planner.py):** for a non-event (VOD) hls output, inject `hls_list_size=0` (keep the whole clip) when not explicitly set — in the plain-hls block (~planner.py:1597, mutates `out.format_options`; the key is registry-validated `int 0..1000`, "0 = unbounded") AND the tee hls-branch (before `_tee_branch_options` routes it into the bracket). An explicit user `hls_list_size` and an `event` (live) playlist are both left alone. **Tests:** test_planner_builder.py +3 (`test_vod_hls_defaults_list_size_zero`, `test_vod_hls_keeps_explicit_list_size`, `test_event_hls_is_not_forced_to_keep_all`); 3 existing pins updated to the new VOD shape (`test_single_variant_hls_argv_regression_guard` now carries `-hls_list_size 0`; test_rules `test_tee_argv_bracket_template` → `[f=hls:hls_list_size=0]clip.m3u8`; `test_hls_segment_duration` → format_options also carries `hls_list_size 0`). **Full suite 1523 passed / 1 skipped.** **E2E on i7 ffmpeg 9.0:** a `-hls_list_size 0` transcode of titan_double.mp4 → the playlist holds ALL 13 segments, `MEDIA-SEQUENCE:0`, `#EXT-X-ENDLIST` (vs the default-5's 5 / SEQ:8). **Changed files:** fass/planner/planner.py (2 injections), tests/test_planner_builder.py (+3, 1 pin), tests/test_rules.py (2 pins). **i7 deploy: DONE** — COMMITTED b7ad2eb + PUSHED (origin/main); i7 ff-merged f166b06→b7ad2eb (local `docker/compose.yaml` + `fass/probe/ffprobe.py` opt-ins preserved through the merge); `docker compose -f docker/compose.yaml build fass` + `up -d --force-recreate fass`. **Live-verified on i7:** healthz `{"ok":true}`; `POST /v1/commands` (the user's exact instruction on titan_double.mp4) → status ready, command ends `-c:a aac -b:a 32k -hls_list_size 0 titan_double.m3u8` + the "VOD HLS keeps the whole clip — -hls_list_size 0" default note; fass-http (38 min) / fass-worker (5 h) / fass-rag (healthy) unharmed.
**Multi-input "Generate does nothing" + RAG `blackdetect` + compare-detection (user 2026-10-29, COMMITTED 5e38838 + PUSHED + DEPLOYED to i7):** three separate bugs, all fixed + verified.
- **Multi-input (ui.py).** (1) The "input video" field accepted `-i titan.ts -i input.ts`; the server's `_strip_i_prefix` (api.py:85) splits on the FIRST whitespace, so the whole field collapsed to the broken path `titan.ts -i input.ts` → no valid input → dead Generate. Fix: client-side `mainInputs()` (ui.py ~584) splits the main field on standalone `-i` tokens BEFORE submit — first → `assets.main`, rest → `assets['m1']…` (the assets dict the builder already consumes; no server/contract change). (2) `addExtra()` rows: the `kind` `<select>` is a SIBLING of the input's wrapper div, not its parent — `i.parentElement.querySelector('.extra-kind')` returned null and lost the kind; now `i.closest('.row').querySelector('.extra-kind')`. **JS-regex trap (CRITICAL, bit us twice):** `UI_HTML` is one big NON-f-string Python string, so every JS regex escape must be DOUBLED in the .py source (`\\s`/`\\b`/`\\.`); a single `\b` is a Python backspace (0x08) baked into the served page → dead regex. `mainInputs`' `\s` AND the pre-existing `CMP_RX` (ui.py:546) compare-detection were BOTH single-backslash → `isCompare()` always false → the compare / quality-detection UI mode was dead. Fixed by doubling. **Test:** test_webinfo.py `test_ui_multi_input_generate_sends_all_inputs` — global invariant `"\x08" not in html` (catches ANY leaked control byte — this is what surfaced the CMP_RX bug) + the mainInputs split + `assets.main = mainParts[0]` + the `.closest('.row')` kind read. node-verified (splitting + isCompare) + tr4 E2E (2-input concat → two `-i` in argv).
- **RAG `blackdetect` → nothing (rag/retrieval.py).** The manual groups related filters under ONE comma-joined heading ("blackdetect, blackdetect_vulkan") and the chunker (`chunker.py:_component_name_for`) leaves `component_name` NULL for it — 11 such family chunks (blackdetect/aderivative/bass/treble/lut/setdar/…). The exact `component_names` filter was SQL `lower(component_name) IN (…)` on a NULL column → always 0 rows (confirmed on i7: filter ON → 0 chunks, OFF → 2). Fix (code-only, NO re-ingest): `_component_names_for(c)` derives a chunk's names from `component_name` PLUS the heading-leaf comma-tokens, gated by `_IDENT_RX = ^[a-z0-9_-]+$` (keeps dotted option-forms and build-help `.txt` stems out); the index maps EVERY name → chunk (`_by_component` / `_known_components` / new `_chunk_names`); the `component_names` request is now a PYTHON post-filter on the candidate set (not SQL) with the exact-component boost re-applied to family-name hits, so `evidence_status` → `supported`. `scale` (control) unchanged. **Test:** test_rag_retrieval.py `test_component_name_filter_multi_name_heading` (self-contained tmp corpus — MUST make BOTH manual AND build-help gen dirs + generations.json, the Retriever loads both kinds): both family names resolve with `supported`, a non-family name is excluded, `get_component` resolves both. Verified locally on the real corpus: all 11 family members resolve, RAG suite 70 passed.
- **Changed files:** fass/ui.py (mainInputs + kind `.closest('.row')` + CMP_RX doubled), fass/rag/retrieval.py (family-name index + Python filter), tests/test_webinfo.py (+1), tests/test_rag_retrieval.py (+1).
- **i7 deploy: DONE (COMMITTED 5e38838 + PUSHED; i7 ff-merged 0ffc75a→5e38838; `docker compose build fass fass-rag` + `up -d --force-recreate fass fass-rag`).** The retrieval.py fix lives in the **fass-rag IMAGE** (docker/Dockerfile.rag bakes `rag/*.py` + re-ingests the corpus at build time, NO volume) → rebuilt + recreated so blackdetect is live; the ui.py fixes rebuilt into `fass:latest` + recreated. fass-worker / fass-http untouched (both still as before). **Live-verified on i7:** served page has NO control byte + all markers (mainInputs / `-i`-split / `assets.main` / `assets['m'+mi]` / `.closest('.row')` kind, old `parentElement` gone / CMP_RX `\b`); `POST /v1/rag/query` "blackdetect" → `evidence_status: supported` + the "blackdetect, blackdetect_vulkan" family chunk (was NOTHING pre-fix), "blackdetect_vulkan" → supported, "scale_cuda" control intact. All 3 services up (fass-rag `healthy`). NOTE: `/v1/rag/query` deliberately does NOT project `exact_matches` (api.py:483) — the RAG tab shows evidence_status + chunks; exact_matches is internal-NLU-only.
- **Open observation (not a reported bug, needs user call):** the CLI concat of "transcode to 300kbps h264 and 48kbps aac audio" emitted `-b:a 160k` (the 48k audio bitrate was not honored) — separate NLU/bitrate-parsing matter, flagged for a later decision.
**Small + darker solid-accent buttons on every pane + 'Apply to container' → #1c5c52 (user 2026-10-29: "make blue buttons on FFmpeg pane a tad smaller and their blue darker" → clarified to ALL panes / all solid-accent buttons + "change color of red button 'Apply to container' on Config pane to #1c5c52", COMMITTED 586b800 + PUSHED + DEPLOYED to i7 + live-verified):** pure CSS in ui.py (the whole page is one Python triple-quoted string; no JS touched). The base `button` rule — which styles EVERY pane's solid accent buttons (Generate, Apply, etc.) — shrank `padding: 9px 18px` → `7px 14px` (ui.py:98) and darkened the fill `background: var(--accent)` → `background: color-mix(in srgb, var(--accent) 80%, #000)` (ui.py:99 — a darker blue that survives both light + dark themes). New `button#apply-btn { background: #1c5c52; }` (ui.py:102, after `button.danger`) overrides ONLY the Config pane's "Apply to container" button to the user's requested teal-green (user first typed #590746, then corrected to #1c5c52). The red `danger` buttons and the ghost buttons are untouched. **i7 deploy:** ff-merge 946fa66→586b800 (i7 main has no upstream — plain pull fails), `docker compose build fass` + `up -d --force-recreate fass` (code is baked into the image, so a CSS change needs a rebuild); live-verified (healthz ok; served HTML carries each new marker exactly once — `padding: 7px 14px`, `80%, #000`, `1c5c52` — and the old `padding: 9px 18px` + `background: var(--accent);` are both gone; fass-worker / fass-http / docker-fass-rag-1 unharmed; local compose opt-ins + the ffprobe "Exiting with exit code" patch intact).
**AV1 encoding via libaom-av1 (user 2026-10-28 "add support of av1 encoding via libaom-av1", COMMITTED 0ffc75a + PUSHED + DEPLOYED to i7 + live-verified — folded into the encoder-batch commit as the plain `av1` default):** the CPU reference encoder `libaom-av1` is now a first-class `av1` video codec. A new codec needs touching THREE independent allowlists: (1) `registry/registry.json` — new `video_codecs.av1` (encoder `libaom-av1`, pix_fmt `yuv420p`, preset crf 32) + `"av1"` added to the `video` list of 8 matrix container rows (mp4, mov, mkv, webm, mpegts, hls, dash, tee); (2) `schema/plan.py:188` `video_codec: Literal[...]` — the pydantic allowlist (THE EASY-TO-MISS ONE: omitting it made `OutputSpec(video_codec="av1")` raise a ValidationError → surfaced as 'unsupported' in the NLU path — that was the 3-test regression, now fixed); (3) `nlu/rules.py:_detect_codec` — `re.search(r"\bav[-\s]?1\b", t, re.I)` after the vp9 branch. The ABR-ladder check (plan.py `video_codec not in ("h264","h265")`) is deliberately UNCHANGED (ladders are h264/h265 only). libaom is CPU-only, so NO gpu/qsv coder is added — the planner's generic gpu/qsv path returns None → "not available", correct for a CPU codec. **Tests:** test_rules.py +2 (`test_av1_detection`, `test_av1_webm`), test_planner_builder.py +1 (`test_av1_default_crf` — pins `-c:v libaom-av1` + default `-crf 32`). Targeted subset (rules + planner_builder + schema + codec_options) **383 passed** after the plan.py Literal fix. **E2E:** `libaom-av1 -crf 32` on tr4 6.1.1 host → ffprobe `codec_name=av1, profile=Main`. **Full suite now 1518 passed / 1 skipped** (the AV1 + encoder-batch work is one tree — see the encoder-batch bullet below). **i7 deploy: DONE** — committed in 0ffc75a, ff-merged to i7, `docker compose build fass` + `up -d --force-recreate fass`; live-verified (healthz ok; `/v1/support` lists `av1`/`av1_svt`/`vp8`/`theora`/`vvc` + `av1_nvenc` gpu coder; `POST /v1/commands` "re-encode with theora" → status ready, theora/ogv, `-c:v libtheora`; fass-worker / fass-rag unharmed; local compose opt-ins + ffprobe patch intact).
**Encoder-expansion batch (user 2026-10-29: "add encoders: gif, jpeg2000, libsvtav1, libtheora, libvpx, libvvenc, libwebp, mpeg2, h264_nvenc, hevc_nvenc, av1_nvenc, common part of NVENC encoding" — CODE + TESTS + E2E, COMMITTED 0ffc75a + PUSHED + DEPLOYED to i7 + live-verified):** new video codecs added the SAME three-way as av1 (registry + plan.py Literal + rules.py): **`av1_svt`** (libsvtav1 — the user chose it as a SEPARATE codec from the plain `av1`/libaom default; the "SVT" qualifier wins the NLU match over bare "AV1", so `_detect_codec` checks `svt`+`av1` BEFORE the bare-av1 branch; preset crf 32, same containers as av1); **`vp8`** (libvpx, crf 10 + bitrate 0, webm-only — left OUT of the natural-home map to match the vp9 precedent); **`theora`** (libtheora, bitrate 1000k, new **`ogv`** container = the muxer-ogg video sibling of audio-only ogg/oga; ogv added to the matrix with vorbis/opus audio; theora IS in the `_NATURAL_VIDEO_CONTAINER` map → ogv because ogv is its ONLY container home, exactly like prores→mov); **`vvc`** (libvvenc, crf 30, declared with an honest requires-note since libvvenc is in NEITHER verified build — mp4/mov/mkv only, NOT the streaming rows). **av1_nvenc:** `av1` added to `gpu.coders` (`{encoder: av1_nvenc, profiles: [main, main10]}`) — the planner's GPU branch (planner.py ~2800) is codec-agnostic (reads `gpu.coders[codec].encoder`), so no planner change was needed; the stale "(libvpx-vp9) CPU-only" GPU error message (planner.py:826-829) now reads "use H.264, H.265 or AV1 on the GPU". Already supported (no change): gif, webp, mpeg2, h264_nvenc, hevc_nvenc. **Tests:** test_rules.py +5 (`test_av1_svt_detection`, `test_vp8_webm`, `test_theora_lands_in_ogv`, `test_vvc_detection`, `test_h266_detection`), test_planner_builder.py +4 (`test_av1_svt_encoder` pins `-c:v libsvtav1`, `test_av1_nvenc_on_gpu` pins `-c:v av1_nvenc`, `test_vp8_encoder` pins `-c:v libvpx`, `test_theora_encoder_ogv` pins `-c:v libtheora`). Full suite **1518 passed / 1 skipped** (141.66 s; +11 over the 1507 av1 baseline). **E2E (real `fass --adapter rules --execute` on tr4 6.1.1 host):** SVT-AV1→mp4 `codec_name=av1 profile=Main`, Theora→.ogv `theora`+Vorbis, VP8→.webm `vp8`+Opus; H.266/VVC→ plan ready + command `-c:v libvvenc -crf 30`, `--preflight` honestly reports `[FAIL] encoder libvvenc: NOT available in this FFmpeg build`; AV1-on-GPU→ plan ready, command carries `av1_nvenc` (no GPU on the host, so argv-level only). **jpeg2000 DEFERRED:** it is a still-image codec with no multi-frame video container (unlike mjpeg, which has the MJPEG-AVI video container), so it cannot be a video re-encode target; it would need a dedicated single-frame→.jp2 export path — offered as a follow-up, needs a user decision. **i7 deploy: DONE** — ff-merged 586b800→0ffc75a (i7 main has no upstream — plain pull fails), `docker compose build fass` + `up -d --force-recreate fass`; live-verified (healthz ok; `/v1/support` lists all four new video codecs + `av1_nvenc`; `POST /v1/commands` "re-encode with theora" (rules, titan.mp4) → status ready, theora/ogv, command carries `-c:v libtheora`; fass-worker / docker-fass-rag-1 unharmed; local compose opt-ins + ffprobe "Exiting with exit code" patch intact).
**Mediainfo tick → button + non-exclusive options (user 2026-10-28: "you made all ticks exclusive — one should be able to activate preflight/verbose/serve simultaneously; remove mediainfo from options, make it a button next to Generate, it should work without prompt; if some instruction is given, deactivate the Mediainfo button", COMMITTED 946fa66 + PUSHED + DEPLOYED to i7 + live-verified):** the whole `#mi`-tick + `miSet` + mutual-exclusivity machinery (introduced for the "mediainfo blocked by empty Instruction" fix, 1b37b61) is REPLACED by a simpler design. **ui.py:** (1) the `#mi` checkbox is removed from the options row; a `<button type="button" id="mi-btn" onclick="runMediaInfo()">Mediainfo</button>` sits in the button row BETWEEN `#genbtn` and `#clearbtn`. (2) `runMediaInfo()` is a standalone async fn — probes `/v1/mediainfo?target=input&file=<inp>` and renders the ffprobe JSON into `#res` (probing… → ready/error). It never goes through the form submit, so it works with an EMPTY Instruction; `#instr` keeps its native `required` always (no more lifting). (3) The exclusive-row change-listener (`for (const id of ['mi','pf','verbose','servehttp'])`) and the `miSet(on)` helper are DELETED — `pf`/`verbose`/`servehttp` are now independent and can all be ticked at once. (4) New `syncMiBtn()`: `$('mi-btn').disabled = $('instr').value.trim() !== ''` — the Mediainfo button is DISABLED whenever any instruction is present (a real command is never silently replaced by a peek). It runs on every `#instr` input (the listener is now `() => { setCompareMode(); syncMiBtn(); }`) + once at load, and is called from `clearCmdPane()` (after clearing `#instr`) and `usePrompt()` (after the programmatic fill) since a programmatic `.value` never fires the input listener. `clearCmdPane()` no longer resets `#genbtn` (its label no longer flips) and no longer calls `miSet`; it now calls `syncMiBtn()`. **submit()** lost its "Clear-state" branch and its "mediainfo ticked" branch — it's back to pure command generation. **Tests (test_webinfo.py, net 0 new — 3 bodies reworked + 1 pin updated):** `test_ui_mediainfo_ticker_on_the_command_tab` (no `#mi` checkbox; the exclusive array `['mi','pf','verbose','servehttp']` gone; the three options still ordered pf<verbose<servehttp; `#mi-btn` present + `onclick="runMediaInfo()"` + ordered genbtn<mi-btn); `test_ui_mediainfo_ticker_does_not_require_instruction` → `test_ui_mediainfo_button_independent_of_instruction` (no `function miSet` / no `removeAttribute('required')` / no exclusive listener; `#mi-btn` + `runMediaInfo` wired; `#instr` still `required`; `syncMiBtn` present + the disable expression + `setCompareMode(); syncMiBtn()` in the listener); `test_ui_command_tab_clear_button` (button row genbtn<mi-btn<clearbtn; reset lines now `pf`/`verbose`/`servehttp` unchecked + `setCompareMode()` + `syncMiBtn()`, no `miSet`; `"miSet" not in html`); `test_ui_ffmpeg_tab_label_and_prompts_view` (the usePrompt pin now targets the re-derive comment + `syncMiBtn();`). Full suite **1506 passed / 1 skipped** (141.70 s; count unchanged — the change reworked existing UI tests rather than adding new ones; the `/v1/mediainfo` server endpoint and its tests are untouched). **i7 deploy:** ff-merge 979acea→946fa66 (i7 main has no upstream — plain pull fails), `docker compose build fass` + `up -d --force-recreate fass`; live-verified (healthz ok; fass / fass-http / fass-worker / docker-fass-rag-1 all running; local compose opt-ins + the ffprobe "Exiting with exit code" patch intact; served HTML carries `id="mi-btn"` / `function runMediaInfo` / `function syncMiBtn` in the genbtn→mi-btn→clearbtn row and ZERO `#mi` checkbox / `miSet` / `['mi','pf','verbose','servehttp']` exclusive array; `#instr` still `required`; the `value.trim() !== ''` disable expression present; `/v1/mediainfo?target=input&file=titan_10s.mp4` → HTTP 200 ffprobe JSON).
**Mediainfo ticker blocked by empty Instruction (user report 2026-10-28, COMMITTED 1b37b61 + PUSHED + DEPLOYED to i7 + live-verified):** the `mi` branch in `submit()` already ran before any instruction logic — the real blocker was the native `required` attribute on `#instr`: the browser swallows the submit event itself (validation bubble) before `submit()` ever fires, so a ticked mediainfo + empty Instruction could never generate the report. Fix (ui.py): new `miSet(on)` helper — sets `$('mi').checked` AND lifts/restores `#instr`'s `required` (removed while ticked; restored whenever the ticker ends up off). EVERY off-path goes through it: the user tick/uncheck (`if (id === 'mi') { miSet($('mi').checked); return; }` in the exclusive-row listener), the other boxes unchecking it (loop now calls `miSet(false)` instead of a bare `$(o).checked = false`), and the Clear branch (`$('mi').checked = false` → `miSet(false)`). Bare `el.checked = false` assignment fires NO change event, which is why the lift/restore had to live in a helper called from each site, not in the change listener alone. `#inp` stays `required` — the report is about that file, and an empty input correctly still blocks. **Test:** test_webinfo.py `test_ui_mediainfo_ticker_does_not_require_instruction` (helper defined before the listeners; both `required` manipulations; mi-tick and ≥2 `miSet(false)` off-paths; no bare `$('mi').checked = false` left). Full suite **1500 passed / 1 skipped** (142.76 s; baseline 1499/1 + 1 new). **i7 deploy:** ff-merge 0f7fcd7→1b37b61, `docker compose build fass` + `up -d --force-recreate fass`; live-verified (healthz ok, worker/http/rag unharmed; served HTML carries all 3 miSet markers and zero bare `$('mi').checked = false`).
**tsanalyze for output-pane files (user 2026-10-28: "tsanalyze should be added also in right click option of Output file list", UNCOMMITTED — active work):** the server's `GET /v1/tsanalyze` ALREADY accepted `target=output` (the `target not in ("input", "output")` gate + `execute.volume_file(target, name)`), so this was a pure UI change. (1) `fileMenuAt`: the `#fm-tsanalyze` gate is now extension-ONLY — `TS_EXT_RX.test(row.dataset.name) ? '' : 'none'` in BOTH panes (was `isInput && …`); `#fm-asinput` / `#fm-toinput` stay pane-dependent. (2) `fileAction` tsanalyze branch: `$('mi-target').value = 'input'` → `= dir` (the pane the row was right-clicked in) so the report fetches from the right volume; the mi-file-after-fillMi ordering is untouched. **Tests:** test_webinfo — the UI test now pins the extension-only gate (`isInput && TS_EXT_RX` absent) + `$('mi-target').value = dir` (no hardcoded `'input'` anywhere — the other `mi-target` hits are reads); new `test_tsanalyze_reaches_the_output_volume` (fake .ts in the output dir → the mpegts-gate 400, not a 404 — proves the output volume is reached). Full suite on the tsanalyze change: **1501 passed / 1 skipped** (142.72 s; baseline 1500/1 + 1 new).
**Command-tab Clear button (user 2026-10-28: "add 'Clear' button on the right side opposite of 'Generate', and it should reset Command pane" → clarified "put it next to 'Generate', right side", UNCOMMITTED — active work):** a ghost-styled `<button type="button" id="clearbtn" onclick="clearCmdPane()">Clear</button>` in a `.row` flex pair immediately right of `#genbtn`. `clearCmdPane()` resets the whole pane: `instr`/`inp`/`out`/`wm` emptied, adapter back to `auto`, extra inputs removed (`#extra`), all four option checkboxes off (**`miSet(false)`** for mi — keeps the Instruction `required` in sync, the invariant from the mediainfo fix), `#genbtn` un-disabled + relabelled "Generate" (also unwinds the mediainfo "Clear"/"probing…" states), `#res` hidden + emptied, and `setCompareMode()` restores the default Input/Output labels + hides the compare hint. Deliberately NOT touched: other tabs, the Config theme, the Runs list. Note the coexistence: after a mediainfo probe `#genbtn` itself reads "Clear" (submit-to-reset) while `#clearbtn` is always "Clear" (pane-reset) — adjacent same labels, both reset, consistent. **Test:** test_webinfo `test_ui_command_tab_clear_button` (button in the cmd pane after `#genbtn`, the `onclick`, the function, and every reset line incl. `miSet(false)` + `setCompareMode()`). Full suite on the bundle: **1502 passed / 1 skipped** (141.69 s; baseline 1501/1 + 1 new). i7 deploy: ff-merge 1b37b61→447e73f, rebuild + recreate, live-verified (healthz ok; served HTML has `clearCmdPane`, the extension-only tsanalyze gate, `value = dir`; worker/http/rag unharmed).
**Prompt log (last 1000 accepted) + 'prompts' Info/Log selector + Command→FFmpeg rename (user 2026-10-28: "add log of last 1000 prompts in Info/Log pane, add selector 'prompts' under 'run log'… clicking on a prompt inserts it into the Instruction field… Rename 'Command' pane into 'FFmpeg' pane" + "log only prompts which were accepted by llm as correct", COMMITTED 979acea + PUSHED + DEPLOYED to i7 + live-verified):** **Server (api.py):** per-app in-memory prompt log — `app.state.prompt_log = {lock, deque(maxlen=1000)}` in `create_app()` (fresh per deployment/test); `POST /v1/commands` records `{instruction, ts}` ONLY when `resp.status == "ready"` (the "accepted" filter — needs_clarification / unsupported / error are not remembered; holds for the rules adapter too, whose ready = a valid plan); `GET /v1/prompts` returns `{"prompts": [...]}` newest-first (reversed deque under the lock). In-memory by design: a restart wipes it (fits the "last 1000" recency framing; run logs already persist on disk). **UI (ui.py):** tab label Command → **FFmpeg** — the display label only: internal id `cmd` / `pane-cmd` / `showTab('cmd')` untouched (the tab-order test pins those, still green); the visible "Command tab" strings → "FFmpeg tab" (the as-input modal; the result-area `<h2>Command</h2>` heading stays — it labels the generated command, not the pane). Third radio `#sel-prompt` "prompts" under "run log" → `showInfo('prompts')`; `#info-prompts` section (note + `↻ refresh` + `#prompt-status` + `#prompt-out`); `showInfo` gains the toggle + auto-load; the tab-show hook is now three-way (`sel-prompt` → `sel-log` → mi). `loadPrompts()` renders `.filerow` rows (instruction ellipsized, `row.title = p.instruction` for the full text on hover, timestamp `toLocaleString()`); `usePrompt(text)` → `$('instr').value = text` + `setCompareMode()` (a programmatic value never fires the input listener — compare labels would go stale) + `showTab('cmd')` (the user lands where the text went). **Tests:** test_api.py +3 (empty log; accepted-only — the pacing-only phrase "read the input at real time" is needs_clarification at the generate() level and stays out, the ready pair comes back newest-first with float ts; cap — 1002 entries inserted straight into the deque → 1000 served, newest first, two oldest dropped, reset in finally); test_webinfo.py +1 (FFmpeg tab label + no `>Command<` in the bar, the prompts selector under run log, the showInfo toggle/hook lines, fetch/usePrompt wiring incl. the setCompareMode pin). Full suite **1506 passed / 1 skipped** (142.49 s; baseline 1502/1 + 4 new). **i7 deploy:** ff-merge 447e73f→979acea, rebuild + recreate; live-verified (healthz ok, worker/http/rag unharmed; all FFmpeg/prompts markup markers in the served HTML; E2E: fresh container → `/v1/prompts` empty → `POST /v1/commands` "resize to 720p" (rules, titan.mp4) → status ready → the prompt appears in `/v1/prompts` with its ts — that "resize to 720p" entry is the verification record in the live log).
**User's 4 changes (this session, commit 3babae7):**
1. **Info/Log "Look in" labels** — `input file`/`output file` → bare `input`/`output` (ui.py mi-target options). DONE.
2. **Instruction in the log footer** — the NL instruction that generated the command is recorded ABOVE the `# command:` line. Threaded end to end: `RunRequest.instruction` (max 20000) → `/v1/runs` → `execute.start_run`/`start_run_sequence` (+`instruction` kwarg) → `_watch`/`_watch_sequence` thread args → `_log_footer(logf, commands, t0, instruction)` writes `# instruction: <whitespace-collapsed>` first. UI `runCmd()` posts `instruction: $('instr').value.trim() || null`. DONE.
3. **Command tab "mediainfo" ticker** — checkbox `#mi` BEFORE `#pf`, mutually exclusive with `pf`/`verbose`/`servehttp` (change-listener unchecks the others; both directions). Ticked + Generate → fetches `/v1/mediainfo?target=input&file=<inp>` and prints the JSON into `#res` in the Command tab; the button (`#genbtn`) flips Generate→"Clear"; while "Clear", a submit just resets the tab (hide `#res`, uncheck `#mi`, button back to "Generate"). Non-`#mi` submits always reset the button label to "Generate". DONE.
4. **TSAnalyze** — button next to "Show media info" (`#tsbtn`, `disabled` until a probe shows an mpegts container). Enabled client-side from `data.format.format_name` in `showMediaInfo()` (`/^mpegts/i`); re-disabled on `fillMi()` (selection change). New endpoint `GET /v1/tsanalyze?target=&file=` (api.py) mirrors `/v1/mediainfo`'s guards (bare name, no `..`, volume jail), then ffprobe-format mpegts gate (else 400 "not an MPEG-TS"), `shutil.which("tsanalyze")` (else 502 "not installed on this build"), runs `tsanalyze <path>` (timeout 600 s) and returns the plain-text report into `#mi-out` (same place as mediainfo). DONE.
5. **Raw LLM output in the UI** (user follow-up, 2026-10-28: "show what the LLM gave in raw form, not after any processing") — the model's VERBATIM reply (fences and all, pre-`_extract_json`) is surfaced: `LlmAdapter.parse()` sets `ParseResult.raw` (new optional field; the LLM prompt schema is built from `EditPlan`, so the prompt is untouched; rules adapter leaves it None). On schema-validation failure the raw text is attached to the RuntimeError (`err.raw_output`) — a broken plan is exactly when the user needs it. `generate()` copies it to `GenerationResponse.raw_llm` (dataclass field, flows into `/v1/commands` JSON and the CLI `--json`). UI `render()` appends a collapsible `<details>` "raw LLM output" block (`rawLlmHtml()`) to EVERY status (ready / needs_clarification / unsupported / error). DONE.

**Files-tab bundle (user 2026-10-28: "add one last feature …", COMMITTED 0f7fcd7 + PUSHED + DEPLOYED to i7 + live-verified):**
6. **Tab reorder** (user mid-turn: "move 'Files' tab on second position next to 'Command'" + "move 'Config' tab into last position, after RAG tab") → tab bar is now Command, Files, Runs, Player, Info / Log, RAG, Config. The `showTab()` pane list (`['cmd','runs','info','cfg'].concat('rag','files','player')`) is order-independent and UNTOUCHED (still asserted verbatim by two tests). DONE.
7. **Right-click 'as input' (input pane)** — `#fm-asinput` menu item (visible only in the input pane): writes the bare name into the Command tab's `#inp` field + a confirming modal ("switch to Command and press Generate"). DONE.
8. **Right-click 'to input' (output pane)** — `#fm-toinput` (output pane only) → new `POST /v1/files/copy-to-input` (`CopyToInputRequest {name}`, bare-name jail like `/v1/files/inspect`; 404 missing in output, **409 on an existing input — never silently overwrites**, 503 unmounted input volume, 500 copy failure). The copy (not move) keeps the output original; modal + `loadFiles()` refresh on success. DONE.
9. **Free space under each pane** — new `GET /v1/files/disk`: `shutil.disk_usage` per volume root → `{input:{exists,total,used,free}, output:{…}}` (unmounted volume = `{exists:false}`, like `/v1/files`). `loadDisk()` (one fetch) feeds the `#files-disk-input` / `#files-disk-output` note lines ("free X / total Y" or "volume not available"); called from `loadFiles()` so tab-show and ↻ refresh both update it. DONE.
10. **Right-click 'tsanalyze' (input pane, TS-ish extensions only)** — `#fm-tsanalyze` visible when `TS_EXT_RX = /\.(ts|mpg|mpeg|ps)$/i` (case-insensitive, so .TS/.MPG/.MPEG/.ps match; doubled backslash in the Python source). The action reuses the Info/Log tab's existing display: `showTab('info')` → set `mi-target` to `input` → `showInfo('mi')` if hidden → assign `mi-file` LAST (AFTER `fillMi()` would overwrite it from the Command fields — the ordering trap) → `showTSAnalyze()`. The report renders in `#mi-out` where mediainfo/tsanalyze already show; the server's mpegts-format gate still 400s on .ps/.mpg program streams. DONE.
**Dockerfile (docker/Dockerfile):** new multi-stage `tsduck` builder (ubuntu:24.04, TSDUCK_TAG=v3.45-4798, TS_OPTS inlined = NOTEST NODEKTEC NOHIDES NOVATEK NOPCSC NOEDITLINE NOJAVA, NODOC forced, SYSROOT=/install SYSPREFIX=/usr/local — the i7-verified recipe from /dd2/andrei/docker/tsduck) + `libcurl4t64` in the final apt line + `COPY --from=tsduck /install/usr/local /usr/local` + `ldconfig` (merged into the existing venv RUN). ENTRYPOINT/CMD/ENV/VOLUME untouched.
**Changed files:** fass/execute.py (instruction threading), fass/api.py (RunRequest.instruction, pass-through, /v1/tsanalyze), fass/ui.py (all 4 UI changes), docker/Dockerfile, tests/test_m6_execute.py (+1 footer-instruction test), tests/test_webinfo.py (+6 tsanalyze tests; `test_ui_keeps_all_mediainfo_controls_off_the_command_tab` → `test_ui_mediainfo_ticker_on_the_command_tab` + `test_ui_run_cmd_sends_the_instruction`), tests/test_m6_api.py (+2: instruction length 422 + accepted & logged E2E). **Raw LLM output (committed 88cb80e):** fass/nlu/llm.py (parse() keeps verbatim reply in ParseResult.raw; err.raw_output on schema failure), fass/schema/plan.py (ParseResult.raw), fass/pipeline.py (GenerationResponse.raw_llm + both set paths), fass/ui.py (rawLlmHtml + render append), tests/test_m6_llm.py (+2), tests/test_api.py (+2 E2E with mocked LLM + rules None assertion), tests/test_webinfo.py (+1 UI markup). **Files-tab bundle (committed 0f7fcd7):** fass/api.py (CopyToInputRequest, `GET /v1/files/disk`, `POST /v1/files/copy-to-input`), fass/ui.py (tab-bar reorder; Files-pane note + `#files-disk-*` lines; `#filemenu` fm-asinput/fm-toinput/fm-tsanalyze; JS: `TS_EXT_RX`, `fileMenuAt` pane/extension toggles, `loadDisk()` + `loadFiles()` hook, `fileAction` branches for as-input / to-input / tsanalyze with the mi-file-after-fillMi ordering), tests/test_api.py (+6: disk 200/exists-false, copy 200/409/404/400+422), tests/test_webinfo.py (+2: tab-bar order via regex on the `.tabs` div; menu options + disk lines + mi-file ordering; `import re`).
**Tests:** full suite **1486 passed / 1 skipped** (216 s; baseline 1473 + 13 new); raw-LLM follow-up: **1491 passed / 1 skipped** (147 s; +5 new). **Docker:** `fass:tsduck-check` built clean on tr4 (multi-stage tsduck compile works; image kept locally). **Container E2E (port 8099, container fass-tsverify, removed after):** UI markup verified (mediainfo ticker, `#tsbtn disabled`, bare input/output labels, `#genbtn`); `GET /v1/tsanalyze?target=input&file=clip.ts` (real 6.3 MB .ts made from titan.mp4) → 200 + full 142-line TRANSPORT STREAM ANALYSIS REPORT (also `docker run --entrypoint tsanalyze` direct: exit 0); `titan.mp4` → 400 "not an MPEG-TS file (ffprobe format: mov,mp4,…)"; `POST /v1/runs` with instruction → log footer exactly:
```
# instruction: make a one second demo clip
# command: ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=… -f mp4 /output/e2e.mp4
# duration: 0.1s
```
**Deployed to i7 (2026-10-28):** repo ff-merged to 3babae7 (local compose.yaml opt-ins + ffprobe.py patch survived — merge touched neither); `docker compose build fass` green (tsduck multi-stage first build, ~10 min); `up -d --force-recreate fass` — all 4 services up, opt-ins verified live (FASS_DOCKER_MANAGE=1, docker.sock mount, CUDA_VISIBLE_DEVICES=0, FASS_DOCKER_WORKER). Live checks: UI markup (mi ticker, tsbtn disabled, bare input/output labels, genbtn); `GET /v1/tsanalyze?target=input&file=<2s .ts made -c copy from titan.mp4>` → full TRANSPORT STREAM ANALYSIS REPORT; `titan.mp4` → 400 "not an MPEG-TS file (ffprobe format: mov,mp4,…)"; test .ts removed afterwards.
**Files-tab bundle — Tests:** touched files green first (tests/test_api.py + tests/test_webinfo.py: **117 passed** in 4.9 s, incl. the 8 new); then full suite **1499 passed / 1 skipped** (150.65 s; baseline 1491/1 + 8 new). **i7 deploy (user "go ahead"):** `git fetch origin && git merge --ff-only origin/main` → 88cb80e→0f7fcd7 fast-forward; local opt-ins + ffprobe patch untouched by the merge (the 5 committed files don't overlap them). `docker compose build fass` (tsduck layer cached → fast) + `up -d --force-recreate fass`. **Live-verified:** all 4 services up (fass-worker/fass-http/rag unharmed); `/healthz` ok; `GET /v1/files/disk` → both volumes `exists:true`, input ~64 GB free / ~471 GB total; served HTML tab bar order cmd, files, runs, player, info, rag, cfg (Files 2nd, Config last); all 6 new markup markers present (`files-disk-input`/`files-disk-output`, `fm-asinput`/`fm-toinput`/`fm-tsanalyze`, `copy-to-input`); compose opt-ins confirmed intact (docker.sock ×2, FASS_DOCKER_MANAGE=1 ×2, FASS_DOCKER_WORKER=fass-worker:8001, CUDA_VISIBLE_DEVICES=0) and the ffprobe `Exiting with exit code` filter still in the working tree. **Next: user browser E2E on i7 (Files tab — right-click actions, free-space lines, tab order).**
**Previous state (2026-10-28, i7 LLM timeout fix SHIPPED + DEPLOYED (fbcd8ab). M13b COMMITTED (0240b51). Suite 1473 passed / 1 skipped.)**
**i7 LLM timeout bugfix — implemented, COMMITTED (fbcd8ab) + PUSHED, i7 DEPLOYED + VERIFIED, this session.** User report: "test llm" on the Config tab is OK but generating a command fails with `parser error: LLM provider unreachable: timed out`. Root cause: `LLMConfig.timeout` in config.py **defaulted to 10 s** and feeds every generated adapter (`_llm_from_config`, llm-test) — silently shadowing `LLMAdapter.__init__`'s own 90 s default. i7's local LLM (strata, small 3-bit-quantized thinking model behind the qwen3.8-27b name; host 172.17.0.1:8080) needs 10–21 s for the full ~54 KB fass system prompt, so every generation died at exactly 10 s; the tiny "LLM test" prompt fit under the limit, masking the bug. Proven by a urlopen tracer in the container (`timeout=10 FAIL after 10.0s`) + raw probes (10–21 s, no RAG-dependence). **Fix:** config default 10 → **90** (still user-settable, 1–300, Config tab); UI hint + placeholder updated; new test `test_llm_timeout_tracks_adapter_default` pins the config default to the `LLMAdapter.__init__` `timeout` parameter via `inspect.signature` so the two can't drift apart again; 5 existing pins updated (test_m6_config ×4 incl. older-config-fallback, test_m6_api ×1, test_m6_llm ×1) → **1473 passed / 1 skipped** (1472/1 + 1 new). **i7 deploy:** repo ff-merged 0240b51→fbcd8ab (`git fetch origin && git merge --ff-only origin/main` — i7's main has NO upstream tracking, plain `git pull` fails with the set-upstream hint), `docker compose build fass` + `up -d --force-recreate fass` (local compose opt-ins untouched, fass-worker/fass-http/fass-rag unharmed), in-container `load_config().llm.timeout == 90`. **Verified via i7's API** (the user's exact flow): `POST /v1/commands` "pseudo-live hls with a 10 segment window" → `status: ready`, `adapter: llm`, `realtime_input: true`, argv `ffmpeg -hide_banner -loglevel error -re -i /input/titan.mp4 -map 0:v:0 -map 0:a? -f hls -c copy -hls_list_size 10 titan.m3u8`, wall **14.8 s** (previously hard-failed at 10 s). Debug artifacts cleaned from i7 /tmp + tr4 (kept: /tmp/compose.yaml.i7-optins, /tmp/ffprobe-i7.patch, /tmp/PROGRESS.md.i7). **Next: user browser E2E on i7 (Command tab → "pseudo-live hls with a 10 segment window" → serve over http → Player tab → play while generating).**
**M13b (the last M13-series item) — COMMITTED + PUSHED as 0240b51, i7 DEPLOYED (rebuild + recreate, opt-ins preserved), API-verified (reference case 6 exact argv).** Implemented earlier this session.
**M13b (the last M13-series item) — implemented this session.** "pseudo-live" / "read at real time" → `ffmpeg … -re -i in …`: the main input is read at native frame rate (real time) instead of as fast as ffmpeg can — e.g. a live (event) HLS playlist is followed by a player while it is still being generated. **Schema:** `EditPlan.realtime_input: bool = False` (plan.py, between `trim` and `bitstream_filters`; LLM adapter sees it automatically via the model_json_schema prompt — no llm.py edit; byte-identical argv when unset, so the whole suite is the regression gate). **NLU (rules.py):** house tri-state detector `_detect_realtime_input` — `_REALTIME_ON` (`pseudo-live`/`pseudo live`/`pseudolive`, `read [the input] at real time`) → True; `_REALTIME_OFF` (`no pacing`/`without pacing`/`don't pace`/`not at [in] real time`) → False; silence → None (merge keeps the plan's value). NOTE: "pseudo-live hls" matches the M13.2 live-HLS regex AND `_REALTIME_ON` ON PURPOSE (the hyphen is a word boundary) — reference case 6 = the event playlist with `-re` in front of `-i`. Pacing-only phrases without an action ("read the input at real time") stay needs_clarification per house behavior — the tests carry an action word. **Planner (planner.py):** `-re` is appended to `ep.inputs[0].pre_opts` (main input ONLY — watermark/replacement-audio inputs are never paced) at the main return, placed AFTER `_attach_stabilize_passes` (so `ep.passes` exists) and BEFORE `ep.warnings.extend(warnings)` (a first version appended after the merge and the stream-input warning was silently LOST — caught by test, moved). Multi-pass (stabilize): earlier passes get a plain `InputSpec` copy of the main input, only the FINAL (transform) pass is paced — the analysis pass must not wait out the file at real time. Diagnostic (null-muxer) plans: no `-re`, a defaults line explains why. Stream input (rtsp/…): `-re` still emitted + warning "no effect … already read at real time". **Executor (execute.py):** `-re` added to `_BOOL_FLAGS` — the E2E run was REFUSED ("unknown flag '-re' (not in the builder's vocabulary)") until then; the test suite only checks argv shape, not the executor vocabulary. **Tests:** 11 new in test_rules.py (reference case 6 exact argv; plain output; phrasings; word variants; "live hls" untouched; negation; finite ENDLIST warning coexists; stream-input no-effect warning; main-only pacing; revision on/off) + 2 in test_stabilize.py (NLU combined phrase; `build_argv_all` — `-re` in transform argv, NOT in detect argv) → **1472 passed / 1 skipped** (baseline 1459/1 + 13). **E2E (both halves):** tr4 host 6.1.1 via `execute.start_run` on titan.mp4 — "trim the first 6 seconds, pseudo-live" → `ffmpeg … -ss 0 -re -i titan.mp4 … -t 6 …` success exit 0, **wall 6.5 s** (paced); unpaced control of the same 6 s **1.20 s** (5.4× difference = real-time pacing). Docker `fass:m133-verify` (9.0.1): same argv in-container (media ro + /tmp out mounted) — **wall 6.9 s**, 1.68 MB out.mp4. **Next: commit M13b; user browser E2E of live HLS/DASH playback on tr4/i7 still owed** (serve_http run → Player tab → play while generating; M13b gives that flow its paced generator — "pseudo-live hls" is exactly reference case 6).
**Previous state (2026-10-28, M13.7 Player tab + shaka-player swap + fass-http CORS — COMMITTED and PUSHED (origin/main, fc25cd5). Suite 1459 passed / 1 skipped.)**
**M13.7 (Player tab, the last numbered M13 milestone) — implemented this session.** Player tab between Runs and Info: manifest list (name/kind/state/size/modified) with a `live` badge while the owning run is still running (else `vod`), ~5 s auto-refresh while the tab is open, a `served at <base_url>` line, per-row **play**, and a **Purge stream tree** button (confirm) → `POST /v1/streams/purge`. api.py: `GET /v1/streams` entries gained `size`/`mtime`/`live` (run_id parsed from the `http/<run-id>/…` path prefix, live = that run's status is "running"); purge refuses 409 while ANY run is running, else counts + rmtree's `{output}/http` → `{"purged": true, "files": n}`; `GET /static/{name}` is a FIXED allowlist (keeps the page CDN-free, no directory browsing) whose only entry is the vendored player lib. execute.py: `Run.http` flag (set for serve_http runs) + in `to_dict`. ui.py: a `serve over http` checkbox on the Command tab (`runCmd` sends `serve_http`); the Runs tab's output cell links http runs to the Player tab instead of /media. **Player library — user directive to replace the hls.js + dash.js pair with shaka-player:** one vendored UMD build, `fass/static/shaka-player.compiled.js` (shaka-player@5.3.2, 886,066 B, Apache-2.0, global `shaka`; + LICENSE + SHA256SUMS + VENDORED.md pin table) — plays BOTH .m3u8 and .mpd through one API. v5 API facts (the earlier v4-era assumptions are WRONG): no static `create` — `new shaka.Player(video)`; `await player.load(url)`; `player.destroy()`; `await shaka.Player.isBrowserSupported()`; `sh.Player.version` is the STRING "v5.3.2" (not a function); fatal errors via `addEventListener('error', e => e.detail)` with `severity === 'fatal'`. Node smoke test needs `globalThis.self = globalThis` first. **fass-http CORS (dispatched job j_a9a4961bd0 on tr4-secondary, cherry-picked from 787ca79):** new `docker/fass-http/httpd-fass.conf` — `<FilesMatch "\.(m3u8|mpd|ts|m4s)$">` → `Access-Control-Allow-Origin *`; `<FilesMatch "\.(m3u8|mpd)$">` → `Cache-Control no-cache` (live playlists grow in place and browsers cache them aggressively — stale-copy protection); compose.yaml: fass-http runs `httpd-foreground -c "Include conf/extra/httpd-fass.conf"` + a read-only mount of the conf (`FASS_HTTP_CORS_CONF` override). The stock httpd:2.4-alpine httpd.conf has NO live conf/extra glob include (all commented except proxy-html), so the include must be explicit; mod_headers is loaded by default. E2E-verified by the worker on port **8099** (host 8080 is taken by the strata LLM server — never 8080 for test containers). **Tests:** 7 new + 2 assertions (static serve/allowlist/traversal, streams size/mtime/live incl. live-while-running via monkeypatched `execute.list_runs`, purge empty/real/409, UI wiring incl. `new shaka.Player(`) + 2 pre-existing pin fixes (JS-syntax test now selects the line containing "saved config?" — the purge confirm is the first `confirm(` in the served script; files-tab pin `.concat('rag','files')` → `.concat('rag','files','player')`). **Gates:** full suite **1459 passed / 1 skipped**; served `<script>` (36,625 ch) `node --check` OK; sha256 verified. No browser exists in this environment — playback E2E is owed on a machine with one (tr4/i7 web UI); the node smoke (UMD load + v5 API surface) is the verification ceiling here. **Next:** user browser E2E of live HLS/DASH playback on tr4/i7 (serve_http run → Player tab → play while generating); then M13b (-re pseudo-live).
**Previous state (2026-10-27, M13.1–M13.4 (d6b2c0d) + the 4-item bundle + same-value warnings + UI theme picker (6 themes) COMMITTED as 4 commits and PUSHED (origin/main = aafc388); i7 FULLY DEPLOYED + VERIFIED (rebuild + recreate fass/fass-worker on the new image).**)
**Source-relative + aspect-ratio resize NLU + same-value warnings (this segment, UNCOMMITTED).** User ask: "resize video to 4:3, keep same height as in source file" must resolve via the probed source, not ask; AND warn (warn-only, ops STAY in the plan) when a requested conversion lands on the source's OWN value. `pipeline.generate()` now probes the input BEFORE `adapter.parse()` (pre-parse probe, fed to NLU; post-parse re-point to `edit.input_id` for the planner contract) — local files only, stream input leaves media None. rules.py `_detect_resize(..., media)` fills a missing dimension from the source (src_w→width, src_h→height, src_res→both, "…kept from the source" notes); an aspect target ('resize to 16:9') fills its missing dim from the source height; without a probe it asks (needs_clarification). `_same_value_notes(media, resize, pixel_format, video_bitrate_kbps, audio_bitrate_kbps, resample, fps_target)` warns on: geometry (resize vs media.width/height, incl. preserve cases), pix_fmt (exact), video bitrate (10% tol), audio bitrate (10%), sample rate (exact), fps (0.5%). Warnings → `ParseResult.defaults` → `GenerationResponse.defaults`; llm.py prompt carries the same instruction. **Two bugs found+fixed while verifying:** (1) video/audio bitrate warning was structurally dead — it read `video_opts["b:v"]`/`audio_opts["b:a"]` but the call site passed `video_codec_opts` (the private-encoder dict, never has b:v); fixed to pass the real `_detect_bitrate` int-kbps vars (deleted the dead `_bps` helper). (2) fps warning structurally dead — `_detect_transform_filters` claims the fps phrasing into a `fps=fps=N` video filter and blanks its span before the old `m_fps` text-scan ran; fixed by deriving fps_target from the plan's own final ops (scan `plan.operations` for a video `fps=fps=` filter). (3) `ops` UnboundLocalError on the revision path (both the previous-plan and else branches fall through to the notes call but only else assigns `ops`) — now reads `plan.operations` (set in both). (4) setdar/setsar regression — "set the display/sample aspect ratio to N:M" is a stream-metadata filter, not a resize; the new aspect-fill misread its N:M as a resize target and asked for a height (broke 2 transform + 6 revision + 4 subtitles + 1 gpu tests = 26); fixed with `_SAR_DAR_RX` guard in `_detect_aspect` (returns None for display/sample aspect ratio / setdar / setsar). ffprobe.py MediaInfo += `video_bitrate`/`audio_bitrate` (bps int|None) + `audio_sample_rate` (Hz int|None). 20 new tests (test_rules.py: 8 source-relative + 11 same-value pos/neg + 1; test_m6_execute.py: 1 log-footer). **E2E-verified on tr4 titan.mp4** (1280x720, 25.0fps, yuv420p, vbr~2386k, abr~127k, 48k): 4:3+source-height → scale=960:720; "cbr 2.4 Mbps"→video-bitrate warn (2400k vs ~2386k), "cbr 1.5"→none; "aac 128 kbps"→audio warn, "96"→none; "resample 48k"→warn, "44.1k"→none; "yuv420p"→pixfmt warn; "25 fps"→warn, "24"→none. `.ts` regression clean (no `-movflags +faststart`). **mediainfo in docker** (Dockerfile apt line += mediainfo — the Files-tab inspect path 503s without it). **Log footer** (execute.py `_log_footer(logf, commands, t0)`): every run appends `# command: …` (or per-step) + `# duration: {t}s` to the END of its own log (seek(0,2) — the child wrote through the inherited fd, the Python pos is still 0); wired into `_watch`/`_watch_sequence` finally blocks via thread args (start_run passes `run.command, time.monotonic()`; start_run_sequence passes rendered argvs). The CLI `--execute` is a plain dev `subprocess.run` and does NOT write a run log — the footer is on the service `start_run` path (unit-tested). **UI theme picker** (ui.py, user-requested "night mode" + "Hello Kitty" + "Autodesk/Maya" schemes): Appearance section at the top of the Config tab — a `<select id="theme">` (auto/light/night(dark)/Hello Kitty/Autodesk); an explicit `html[data-theme=…]` attribute (light/dark/kitty/autodesk variable blocks) wins over the existing `@media (prefers-color-scheme)` auto; persisted in `localStorage['fassTheme']`; 'auto' deletes the attribute (OS-driven). kitty = the user's pink palette mapped onto the UI vars (pale-pink bg, white cards, mauve text, hot-pink accent, 24px cards, 30px buttons) + Quicksand via a top-of-`<style>` `@import` (the ONE external asset — degrades to the system stack offline, noted per the page's local-first design); autodesk = Maya gray (#383838/#454545/#e0e0e0, orange #e8a04a accent, 13px Arial, gray toolbar buttons via `button:not(.danger):not(.ghost):not(.copy)`). 83 UI/API tests pass; page serves with all 6 options + correct `@import` ordering. **The 4 commits are made + pushed (feat(nlu) source-relative+same-value; feat(execute) log footer; chore(docker) mediainfo; feat(ui) theme picker). i7 FULLY deployed: git reset to origin/main (aafc388) + `docker compose build fass fass-worker` (fresh, exit 0) + `up -d --force-recreate fass fass-worker` (live compose.yaml opt-ins preserved via /tmp backup, docker-fass-rag-1 untouched; rules.py sha256 in-container == tr4). VERIFIED on i7: (a) 6 themes baked in the page; (b) `mediainfo` at /usr/bin/mediainfo; (c) `resize/convert titan.mp4 to titan.ts` → `-c copy -bsf:v h264_mp4toannexb titan.ts` with NO `-movflags +faststart` (rules adapter; the `auto` adapter on i7 routes to the LLM which asks a convert-vs-resize clarification — tr4's auto 401s on the invalid LLM key); (d) `4:3 keep same height` → `scale=960:720` + "height kept from source (720)"; (e) `cbr 2.4 Mbps` → same-value warning "2400k matches the source's (~2386k)"; (f) a real run's log ends `# command:` + `# duration:`. **Next: /savework → resume M13.5 tee.** The M13.1–M13.4 entries below are now COMMITTED (d6b2c0d).
**M13.1 (registry 2.9.0 + single-variant DASH) — implemented, COMMITTED in d6b2c0d.** Change set: registry.json 2.9.0 (dash in containers/matrix/format_options.dash — 6 options: seg_duration, window_size, use_timeline, single_file, dash_segment_type [mp4,fmp4], adaptation_sets; hand-formatted, never re-serialize); loader.py `_FORMAT_OPTION_GROUPS` += "dash"; plan.py OutputSpec.container Literal += "dash"; rules.py dash/mpd container words + `.mpd` phrase + SUPPORTED_SUMMARY; planner.py DASH special case (muxer="dash" + force .mpd, mirrors the HLS case) + file-destination container-consistency PlanError placed AFTER the four overwrite-collision guards; pipeline.py `_reconcile_output_destination` file branch (a merely-defaulted container follows the destination extension; an explicit conflicting container → the planner's PlanError) — this also fixes the user-reported bug (mp4 default + .ts destination shipped `-movflags +faststart` into a .ts output; now reconciles to mpegts with no movflags). **Suite 1295 passed / 1 skipped** (M13.1 + Job A + Job B; M13.2's 6 tests land it at **1301**). **E2E 3/3** (start_run on media/titan.mp4, script /tmp/fass_e2e_m13/run.py, artifacts /tmp/fass_e2e_m13/out/): DASH copy (`-f dash -c copy` → dash_e2e.mpd, ffprobe format_name=dash 20.0 s), DASH re-encode (`-vf scale=-2:480 -c:v libx264 -c:a aac` → dash_reenc.mpd), .ts bug repro (defaulted mp4 → mpegts by destination, no -movflags in argv, ffprobe h264 1280x720 + aac). 6.1.1 DASH facts: works with -c copy AND libx264; output = .mpd + init-streamN.m4s + chunk-streamN-NNNNN.m4s.
**M13.2 (live HLS) — implemented, UNCOMMITTED (same working tree; suite now 1301/1).** rules.py `_detect_format_options` live block: "live hls" → hls_playlist_type=event + hls_flags=independent_segments; "…with a N segment window" → hls_list_size=N; "…and delete old segments" → delete_segments (OPT-IN only — deleting rolled-off segments breaks DVR/seek and UI playback); explicit key=value wins over the NL map. planner.py: finite-input warning (event + file input → "…ends with #EXT-X-ENDLIST … for true live use a stream input (or -re for a paced pseudo-live)"; stream input → no warning — the UI's live-run playback needs file-input runs). 6 tests (test_rules.py). E2E (titan.mp4, script /tmp/fass_e2e_m13/run_live.py): `-f hls -c copy -hls_flags independent_segments -hls_list_size 10 -hls_playlist_type event live_e2e.m3u8` → success exit 0, #EXT-X-PLAYLIST-TYPE:EVENT + #EXT-X-ENDLIST (finite input, probed behavior) + 11 segments, ENDLIST warning emitted. **Latent flagset bug fixed en route:** the flagset validator (loader.py) splits values on '+' (FFmpeg flag syntax), but the registry descriptions for hls_flags/flvflags and the llm.py prose said "comma-joined" — a comma-joined multi-flag value fails validation; all three now say "'+'-joined".
**Two delegated jobs INTEGRATED into main this session.** Job A j_bf5977d1b3 (tr4-secondary): editable Command tab, server-side revalidation (api.py RunRequest.command, shlex.split with unbalanced-quote 422, multi-pass 422; ui.py textarea + hidden original; 7 tests) — ff-merged **5e4b2bd**, dispatch_integrate "accepted". Job B j_ae29ca471d (i7): RAG baked into the fass-rag image (Dockerfile.rag COPYs the pinned rag/corpus/ sources and RUNs `python -m rag.ingest` as the non-root user — the index lives in an image layer, no volume; corpus-less context fails the build loudly) + RAG default in compose (fass-rag service no-profile/no-volume + fass gains FASS_RAG_ENABLED=1/FASS_RAG_URL=http://fass-rag:8100; compose.rag.yaml retired; README; test_rag_deployment.py rewritten, 32 tests) — the dispatcher transiently recorded failed/exit-1 but the work was intact in the tr4 worktree (e87c708 on dispatch/j_ae29ca471d); reviewed, rebased onto main, ff-merged **64a0111**, dispatch_integrate "accepted" (record now succeeded/exit-0). i7 live: docker-fass-rag-1 up healthy, baked corpus (1820+836 chunks), live compose hand-merged per PRD 6.7, compose.yaml.bak-rag kept.
**9.0.1 docker halves DONE (dual-build mandate, M13.1 + M13.2):** fass:m132-verify built from the working tree; in-container E2E (script /tmp/fass_e2e_m13/run901.py, container fass-m132-e2e) — 3/3 runs success exit 0 on 9.0.1 (DASH copy, DASH re-encode 480p, live HLS event/10-window); live playlist = EVENT + INDEPENDENT-SEGMENTS + 11 segments + ENDLIST (same shape as 6.1.1); both .mpds well-formed (static, PT20.0S, video+audio) and cross-probed with host 6.1.1 ffprobe → `dash` 20.0 s. (i7's live fass:latest deliberately NOT rebuilt — parked on the M13.1 commit+push, i7 mirror still at 237d554.) **Two 9.0.1/build findings:** (1) the jrottenberg 9.0 build has NO libxml2 → its ffprobe/dash demuxer cannot parse ANY .mpd (fails identically on 6.1.1-made mpds) — verify DASH on 9.0.1 structurally (XML + segments) or cross-probe; (2) DASH muxer segment names (init-streamN.m4s / chunk-streamN-NNNNN.m4s) are independent of the .mpd name — two DASH runs in one shared dir (the future M13.6 http/ folder) overwrite each other's segments; M13.6 must address (document / single_file / per-run dirs). Then /savework.
**M13.3 (ABR HLS ladder + post-run master.m3u8) — implemented + DUAL-BUILD E2E GREEN, UNCOMMITTED (same working tree; suite 1360/1).** VariantSpec (width/height 16..8192, video_bitrate_kbps 100..200000, audio_bitrate_kbps 32..3840, video_profile baseline/main/high, video_level ≤8 chars) + OutputSpec.variants (max 8; hls/dash only; re-encode only — copy can't make levels; levels capped at the probed input height, never upscale; unset per-level fields fall back to the default-table row for that height). Default table: 216p/300k/main/3.0/56k, 360p/700k/main/3.1/64k, 480p/1500k/main/3.2/80k, 720p/2500k/main/3.1/96k, 1080p/4500k/main/4.0/128k (bufsize = 2× video kbps); default count 3 → a 720p input gets 360/480/720. **Mechanism deviation — the plan's interleaved bare-option ladder form was empirically REFUTED on 6.1.1**: a bare per-stream option applies to ALL matching output streams, last occurrence wins (every variant came out at the top level's bitrate). PRIMARY = the stream-specifier form: all `-map 0:v:0 -map 0:a:0` pairs first, shared bare `-c:v libx264 -c:a aac -pix_fmt yuv420p`, per-level 16-token blocks `-b:v:N -minrate:v:N -maxrate:v:N -bufsize:v:N -profile:v:N -level:v:N -s:v:N -b:a:N` (width = input aspect × level height, rounded even), then `-var_stream_map "v:0,a:0 v:1,a:1 …"` as ONE argv element (registry kind=string in BOTH the hls and dash format-option groups; registry hand-bumped 2.9.0 → 2.10.0). execute.py: `_SPEC_RX`/`_spec_base` strip the `:N` index (keeping the `:v` type for b/c/profile) so specifier options get the base flag's value checks; new vocab `-minrate` (rate) / `-level` (`^\d+(\.\d+)?$|^\d{1,2}$`) / `-s` (`^\d+x\d+$`). NLU: "abr hls" / "hls ladder" / "bitrate ladder" / "adaptive hls" / "multi-quality hls" → variants (explicit "levels 216p 300k, 480p 1500k" → filled VariantSpecs; count-only → variants=[] → default table); "dash ladder" / "abr dash" → dash + variants; the generic key=value scan never captures var_stream_map (the planner builds it). **Master synthesis deviation (plan §5.5):** NOT from Run-record metadata — `_ladder_from_argv` (execute.py) derives the ladder from the ARGV at start_run (requires -var_stream_map + per-level specifier options + a .m3u8 destination containing %v; a .mpd or other destination → None, so DASH runs get no master); bandwidth = (video+audio kbps)×1000, CODECS from _AVC_PROFILE_ID (baseline=66/main=77/high=100) + mp4a.40.2, RESOLUTION from the level; `_synthesize_master` writes master.m3u8 into the run's cwd + a Run note; the Run record carries the derived ladder. Ladder + a filter edit in one request → clarification. E2E 6.1.1 ALL PASS (/tmp/m133/e2e_ladder.py, titan.mp4): case A "abr hls with levels 216p 300k, 480p 1500k" (2 levels: 384x216 L3.0 / 854x480 L3.2), case B "abr hls" (360/480/720 at L3.1/3.2/3.1); master.m3u8 BANDWIDTH/RESOLUTION/CODECS exact (avc1.4d401e/4d401f/4d4020 + mp4a.40.2); per-variant bitrate checked via segment-bytes ÷ #EXTINF durations (±15%) — **ffprobe .ts stream.bit_rate is 0/N/A (MPEG-TS carries no per-stream bitrate metadata)**; single-variant HLS argv byte-identical to pre-M13 (regression guard). E2E 9.0.1 in-container (fass:m133-verify, run901_ladder.py) — "## 9.0.1 ladder E2E OK": case A overall 376868/759902/1589533 vs targets 356000/764000/1580000, case B 757123/1574510/2627745 vs 764000/1580000/2596000, master OK, 18 .ts/case; variant geometry/levels cross-probed with host 6.1.1 ffprobe (9.0.1 ffprobe has no libxml2, and .ts bit_rate=0 anyway).
**M13.4 (ABR DASH ladder) — implemented + DUAL-BUILD E2E GREEN, UNCOMMITTED (suite 1365/1).** Reuses M13.3's emitter unchanged (-f dash + var_stream_map + AUTO adaptation sets — never explicit); format_options (seg_duration etc.) ride the ladder path (the planner emits them after the variant block). 5 new tests (DASH ladder argv exact — -f dash, map pairs, per-level specifier blocks, var_stream_map, .mpd destination, no -adaptation_sets; seg_duration emitted before the output path; dash ladder explicit levels; "abr dash"; dash word + ladder in one phrase). **E2E-verified MPD structure (the plan's "auto sets" form, now pinned empirically):** one (video, audio) AdaptationSet PAIR PER VARIANT in variant order — NOT a single pooled video AS; root type=static + mediaPresentationDuration=PT20.0S; one Representation per video AS at the declared resolution with avc1.4d<profile><level> ids (main=0x4d; 3.0=0x1e, 3.1=0x1f, 3.2=0x20); audio reps all mp4a.40.2. **Init segments are per STREAM:** N variants → 2N init + 2N chunk streams. No master.m3u8 for .mpd destinations (Run record ladder=None). E2E 6.1.1 ALL PASS (/tmp/m133/e2e_dash_ladder.py): case A "dash ladder with levels 216p 300k, 480p 1500k" → titan.mpd static PT20.0S, 2 AS pairs, avc1.4d401e/4d4020; case B "abr dash" → 3 AS pairs 640x360/854x480/1280x720, avc1.4d401f/4d4020/4d401f; host ffprobe (libxml2) parses the .mpd. E2E 9.0.1 in-container (run901_dash_ladder.py; structural XML via ElementTree since the 9.0.1 ffprobe cannot parse any .mpd) — "## 9.0.1 dash-ladder E2E OK": identical structure, case A 4 init + 14 chunks, case B 6 init + 21 chunks.
**Next: M13.5 tee fan-out** (plan §4). TeeBranch model (2..4 branches; container Literal hls/dash/mpegts/mp4/webm/matroska — file-form muxers only; bare filename — the validator forbids `/ \ : ..`, a leading `-`, and control chars, because the tee muxer opens the names directly, so an absolute/subdir path would be a volume-jail bypass) + OutputSpec.tee, MUTUALLY EXCLUSIVE with variants (combined ladder+tee → clarification; a 1-branch "tee" is just a normal output → clarification). Bracket template `[f=<muxer>:<k=v:k=v>]<barename>` joined with `|` (per-branch options go INSIDE the template — the `muxer=path:opt` shorthand FAILS on both builds); explicit `-map 0:v -map 0:a` prepended (default stream selection does NOT propagate to tee branches); `-f tee` as the first output_opts token; output_path = the template. **Build-time branch validation is mandatory** (the executor is blind to the template interior — it checks the final token only against _PATH_RE): branch muxer ∈ all_muxers(), per-branch format_option_errors(branch_container, …), the bare-name rule. use_fifo: default OFF, on only when the user sets it (generic bool in the registry; the planner may set it for a live tee). Run.output=None for tee (the template is not a servable file — the Player tab lists the branch outputs). NLU: "tee to hls and dash" / "output to both hls and dash" / "fan out to hls and mp4" → tee branches (container word + optional bare filename + optional key=value per branch; ≥2 branches). Reference fass command: "tee to hls and dash" → `ffmpeg -i in.mp4 -map 0:v -map 0:a -c:v libx264 -b:v 1500k -c:a aac -b:a 80k -f tee "[f=hls:hls_time=6]master.m3u8|[f=dash:seg_duration=6]master.mpd"`. Then M13.6 (Apache + /output/http — incl. resolving the DASH segment-name collision noted above) → M13.7 (Player tab); per milestone: suite green + E2E 6.1.1 + 9.0.1 docker half. -re pseudo-live → M13b.

## Previous state (2026-10-27, **M13 PLANNING — plan FINAL, no coding that session**)
M13 scope (user-picked): HLS ABR ladders + DASH + tee fan-out + live HLS + web-UI playback. Plan: /home/andrei/.claude/plans/majestic-sauteeing-ullman.md (FINAL, 8 sections). Exploration: j_f7d3350725 (i7) + j_c5a42bb4ec (tr4-secondary) + design job j_0130ee5802 (all ffmpeg facts probed on 6.1.1; doc in the job worktree). Key decisions: ladder = interleaved -map groups in output_opts (zero builder change); tee = bracket template + explicit maps (shorthand FAILS); DASH auto adaptation sets (explicit + var_stream_map FAILS); master.m3u8 synthesized post-run (user decision). **HTTP delivery (2026-10-27 revision, user): Apache in the docker image serves <output_dir>/http/ — the executor picks that cwd from argv (Run.http flag, no schema change); GET /v1/http/manifests + POST /v1/http/purge + GET /static/ allowlist (vendored hls.min.js + dash.all.min.js, CORS header load-bearing); new Player tab lists/plays manifests WHILE the run generates + Purge button; the earlier artifacts dir-diff + /media/run/{id}/play/ rewrite route + ▶ modal design is DROPPED.** Mid-generation probes (6.1.1, -re pacing): event-HLS playable while generating (playlist grows, no ENDLIST until completion); vod .m3u8 absent until completion (3 probe rounds); DASH .mpd type=dynamic → static at completion; .tmp atomic segment renames; -hls_time 4 → actual 8.33 s segments (keyint 250). Reference cases are six FASS commands (typed in the UI Command tab) with the probed expected argv — the user hand-tests them on tr4 6.1.1 + i7 9.0.1 first; results lock the E2E cases. Milestones: M13.1 registry+DASH single → M13.2 live → M13.3 HLS ladder+master → M13.4 DASH ladder → M13.5 tee → M13.6 Apache+/output/http → M13.7 Player tab. Suite untouched (plan-only session): 1278/1 baseline.
**Next:** user hand-tests the six reference fass commands on both builds; then M13.1 implementation in a future session. -re pseudo-live → M13b.

## Previous state (2026-10-27 04:14, **M12 COMPLETE — the PRD-deferred scope is all on main: multiple watermarks (ea9919e), batch mode (9967441), HDR→SDR tone-mapping (8c2e11a) + README/DEVELOPMENT docs updated** — main suite **1278 passed / 1 skipped** (tr4 6.1.1, 146 s). All mandated work (M-series filters + the RAG PRD, M11.0–M11.4) is now DONE; what remains are user-decision items below. The 2026-10-27 01:15 state is demoted to the next "Previous state" section.)
**What closed out this segment (all on main, in order):**
- **M12.WM (ea9919e) — multiple watermarks.** N image overlays chain in plan order (inputs 1..N, per-image `overlay` in the labeled `filter_complex`). NLU `_detect_watermarks` (rules.py): every image the request refers to becomes one `watermark` op — an explicit mention wins, with none named every bound image asset is used; each position phrase in text order is assigned to the next watermark, the rest default to bottom-right with a note. plan.py single-use-op validator renamed `_at_most_one_single_use_op` with watermark exempt. E2E on titan.mp4 with two bound images; suite 1233/1.
- **M12.BATCH (9967441) — job j_65e7374aef SUCCEEDED (tr4-secondary), integrated, disposition accepted.** `--batch FILE|DIR …` (a directory contributes its top-level video files, sorted; or an explicit list) + `--batch-out DIR`; the pipeline runs once per file sequentially; a rejected/failing file is reported and skipped, the loop continues, exit 0 only when every file succeeded. Rejects positional input / `-o` / `--previous` / `--metadata` (each names ONE file); shared `--watermark` / `--asset` apply to every file; an instruction placed after the file list parses (trailing-token disambiguation). The single-file CLI path is byte-unchanged. Suite 1254/1.
- **M12.HDR (8c2e11a) — job j_ba4899bb09: the work is good, the job record is NOT.** `ToneMapOp` (no options) in the Operation union; NLU `_TONEMAP_RX` / `_detect_tonemap` (tone-map words or hdr↔sdr / hdr↔rec-709 pairings within 60 chars), detection gated on probe metadata (PQ `smpte2084` / HLG `arib-std-b67`); planner `_TONEMAP_CHAIN = format=gbrpf32le,tonemap=tonemap=hable:desat=0,format=yuv420p`, default output 8-bit Rec.709, a 10-bit `main10` re-encode keeps the HDR; HDR10+ / Dolby Vision named-unsupported; conflicts: audio-mode ops, subtitles mode, stabilize; registry 2.8.0 entry (tonemap, min_version 4.2); 24 tests (tests/test_tonemap.py). Worker E2E-verified on i7 docker 9.0.1. **Job marked failed/invalid_result on the worker's result JSON (outcome 'success_pending_review' outside the enum; acceptance_criteria entries missing 'criterion'; scope_violations [".venv"]) — `dispatch_integrate` REFUSED (server gate: only succeeded jobs can be integrated, no override).** I reviewed the worktree diff, resolved the one 3-way conflict (audio-mode block: kept M12.WM plural `watermarks` + added `or tonemap is not None`), re-verified E2E on tr4 6.1.1 (synthesized HDR10 main10 10-bit PQ source → 8-bit SDR h264, chain + "BT.2020/PQ → Rec.709" disclosure correct; artifacts /tmp/hdr_src.mp4, /tmp/hdr_out.mp4) and merged manually — **8c2e11a IS the integration record**; the job's worktree stays retained. Suite 1278/1.
- **README job (6bca1a3) — j_0214afe7db SUCCEEDED (i7), integrated, accepted.** Worker's scope (per its objective: "DO NOT document unshipped features"): quality-comparison section (reference = FIRST input, `-i ref -i main`, `[distorted][reference]`, libvmaf 7.0+ / `FASS_VMAF_MODEL[_DIR]`, null-muxer diagnostic output, standalone-only, probe-conditional setpts), stream allowlist gained rtp, spec link → ffmpeg-assistant-rag-prd.md.
- **M12 docs close-out (this commit) — the worker was told NOT to write, so it's mine:** README "What it understands" — shape line (watermark is just an image input), Watermarks bullet (multiple; first `--watermark`, extras `--asset name=image:file`), new HDR→SDR and Batch-mode bullets; docs/DEVELOPMENT.md — CLI table (`--batch` / `--batch-out` rows, `--watermark` clarified), feature bullets (Watermarks M12.WM / HDR M12.HDR / Batch M12.BATCH), out-of-scope example `add a second watermark` → `remove the watermark`, roadmap: M11.1 → [x] (all gates PASS) + M11.2/3/4 [x] + M12.WM/BATCH/HDR [x], the "deferred (PRD §5)" line removed (all three shipped).
- **Push (segment end, user's explicit "push it") — origin/main = 237d554.** 844943c..237d554, 6 commits fast-forward (b8ed8ff, ea9919e, 9967441, 8c2e11a, 6bca1a3, 237d554); i7 mirror ff-merged from origin (local state preserved: compose.yaml opt-ins still `M`, .resume + root ffmpeg-rag-prd.md untracked); the push-record docs commit (cdb24af) was pushed with it.
- **i7 fass-worker restart (user-requested, 2026-10-27).** The container was still on the 10-25 image 58b9eba7 (registry 2.2.0) while `fass` ran the rebuilt cc5eda7 (2.7.0) — a plain `docker restart` would have kept the old rootfs, so it was RECREATED: `docker compose -f docker/compose.yaml up -d --force-recreate fass-worker` (i7's own compose config, local opt-ins preserved). Worker log clean ("fass-worker on http://0.0.0.0:8001"), `fass` → `fass-worker:8001/healthz` = `{"ok":true}`. The `fass` container itself was untouched. Note: cc5eda7 was built from 7866a93 (M11 era) — the deployment does not yet run M12 code; a rebuild from current main is the separate step listed in Next.
**Dispatcher protocol drift (recurrence, worth a profile fix):** THREE jobs now left good work behind a false "failed" status because the worker's result JSON used non-schema vocabulary (c27a0d8: outcome 'success'; j_3d4505da56 attempt 1: crash; j_ba4899bb09: 'success_pending_review' + missing criterion keys). Every one needed manual rescue-merge. Recommend adding the exact result-schema contract (outcome enum, tests LIST shape, acceptance_criteria[].criterion strings) to the cc-worker profile prompt.
**Next (user-decision items only — no work is owed):** (1) 2.7.0 merge questions: histogram 1-letter aliases (register or not), tinterlace docs-only `flags`, blockdetect/blurdetect `planes` phrasing. (2) Minor optional: gaps/ note on the auto-adapter 401-no-fallback environmental issue (tr4 has no ANTHROPIC_API_KEY → `--adapter auto` → LLM → 401; use `--adapter rules` for E2E on tr4); dispatcher drift note as above. (3) Rebuild `fass:latest` from current main (image cc5eda7 was built from 7866a93, pre-M12 — the deployment does NOT run batch/watermarks/tonemap yet). Standing: advisory evidence only — registry/builder/argv gate stays sovereign; treat evidence_status 'supported' with skepticism.

## Previous state (2026-10-27 01:15, **M11.4 COMPLETE — §9.2 verdict: NO-GO, ship arm 3; §9.4 ALL GATES GREEN** — registry 2.7.0 (5c34d01), no-audio-container planner fix (4a6a891), gaps drift 31→25 (2ce5e95); main suite **1218 passed / 1 skipped**; ablation 880/880 done + report computed; rag restarted (new pid 1918855); battery **host 6.1.1 21/21 + i7 9.0.1 docker 21/21 (0 skips)**. The 2026-10-26 state is demoted to the next "Previous state" section.)
**§9.2 go/no-go (COMPUTED from /tmp/ablation-run1/report.json via /dd2/andrei/m114_gonogo.py — pre-registered rule: arm 4 beats arm 3 by +5 pp parameter accuracy on HELD-OUT paraphrase, no regression, else ship arm 3 + static doc_ref citations):**
**VERDICT: NO-GO → ship arm 3 (llm_schemas) + static doc_ref citations. No code change needed: `RagConfig.enabled` defaults False (RAG is opt-in), the LLM path already uses the schemas block, and citations already derive from static registry `doc_ref` (`derive_citations`) — RAG evidence augmentation stays an opt-in experiment. Arm 1 (rules) stays the default either way.**
- Held-out paraphrase (n=79): param_acc arm 3 = 1.0, arm 4 = 1.0 → **delta 0.0 < +0.05** (all arms' READY plans are param-exact; the differentiation is in status/ops, where arm 4 does lead).
- No regression (arm 4 ≥ arm 3 on every held-out axis): status 0.814 vs 0.798, ops 0.971 vs 0.971, clarif 0.85 vs 0.75, unsupported 0.9048 both; errors 3 vs 7; p95 66.2 s vs 135.3 s.
- Full held-out (n=129): rules 0.465/0.956/1.0/clarif 0.95/unsup 0.857; llm 0.806/0.971/1.0/0.85/0.905; llm_schemas 0.798/0.971/1.0/0.75/0.905; evidence 0.814/0.971/1.0/0.85/0.905. In-domain requests.json (n=91): rules 1.0 across; llm 0.956/1.0/0.971; schemas 0.923/1.0/1.0; evidence 0.956/1.0/0.99 (citation_precision 0.55 over 200 evidence entries).
- Error rows (19): 7× 180 s LLM timeouts (bench-013, bench-045, abl-018, abl-039, abl-061 llm_schemas; abl-081, bench-063 llm_schemas_evidence) + 7× plan validation (abl-009 ×3 pixel format 'yuva…', abl-048 ×3 filter, abl-072 video-output-needs-encoder) + abl-028/abl-090 (malformed LLM JSON) + abl-038/abl-040/abl-113 (rules-arm errors, ≤0.2 s).
**§9.5 performance budgets (ALL MEASURED):** retrieval warm p95 **27 ms** (≤300 PASS; earlier warm run 29 ms) · cold first-query **33 ms** (≤1000 PASS) · evidence **4000 ch + 263 wrapper** (≤4000 PASS) · RAG generation overhead arm 4 − arm 3 = **−3.10 s mean / −69.11 s p95** (≤400 ms PASS — negative: vLLM latency variance dominates, retrieval itself ~25 ms) · overall generation p95 **rules 3 ms PASS** (default path); LLM arms 73–82 s — NOT met by the local qwen3.8-27b @ tr4 vLLM (dual-model 3090 serving) — recorded per the PRD's "now measured" note; the 8 s target governs the deployed endpoint, and the default (rules) path passes by 3 orders of magnitude · RAG memory **70 MB** (≤4 GB PASS).
**§9.4 e2e gates (ALL GREEN):** full suite **1218/1** (host 6.1.1) PASS · **battery host 6.1.1 = 21/21** (18 executed clean + 3 feature-skips fdk_vbr4/libvmaf/scale_cuda — host build lacks them, same as M10.6 baseline) · **i7 docker 9.0.1 = 21/21, ZERO skips** — all three 9.0.1-only cases (fdk_vbr4, libvmaf, scale_cuda) executed clean on the nvidia build; log `battery-out/m114_battery_i7_901.log` · "turn RAG off" determinism already proven (91/91 byte-identical, M11.2 §9.3). i7 run details: repo synced 4ff3393→7866a93 via bundle (26b4d07 recipe; blocking untracked PROGRESS.md + ffmpeg-assistant-rag-prd.md backed up to ~/i7-pre-m11-backup on i7); fass:latest REBUILT from 7866a93 (old image 58b9eba7 from 10-25, registry 2.2.0, now cc5eda7/2.7.0); serve container recreated via `docker compose up -d fass` (project `docker`, docker/compose.yaml with local opt-ins untouched — only the fass service recreated, fass-worker left as-is on old code until the user decides); /healthz + /v1/support verified from tr4 at 192.168.0.33:8000; 4 battery clips scp'd to i7 docker-data/input (md5-verified).
**Rag service:** old pid 959120 verified rag.server → killed by exact pid → relaunched `FASS_RAG_DATA=rag/corpus nohup .venv/bin/python -u -m rag.server > /tmp/fass-rag-serve.log 2>&1 &` → **new pid 1918855** (:8100, healthz ok, cold budget green).
**Reboot-proofing (user reboots tr4 2026-10-27 ~00:50):** /tmp/ablation-run1 (results.jsonl + report.md + report.json) and /tmp/rag-budgets (warm.json + cold.json) copied to **/dd2/andrei/ablation-run1/** and **/dd2/andrei/rag-budgets/**; analysis script at **/dd2/andrei/m114_gonogo.py**. Post-reboot resume: start docker (vllm-qwen38-27b-dual-max auto-restarts if configured; else `docker start vllm-qwen38-27b-dual-max`) + rag (`FASS_RAG_DATA=rag/corpus nohup .venv/bin/python -u -m rag.server > /tmp/fass-rag-serve.log 2>&1 &`); re-copy the ablation dir back if /tmp was wiped (`cp -a /dd2/andrei/ablation-run1 /tmp/`); a re-run of `scripts/m11_ablation.py --out-dir /tmp/ablation-run1` would resume from the JSONL cache (same cfg fingerprint).
**What closed out this segment:**
- **Registry 2.7.0 (5c34d01) — 100 filters (60 video / 40 audio).** The 10 M11.4 review-merge filters landed (video: edgedetect, freezeframes, thistogram, histogram, blockdetect, blurdetect, tinterlace; audio: ahistogram, aspectralstats, showvolume); acompressor refreshed in place to the 12 modern options (level_in number [0.015625,64] default 1; level_sc = the shared acompressor/sidechaincompress AVOptions block, review note kept OUTSIDE the "(default 1)" group). freezeframes is a genuine two-input combiner (multi_input 2, documented in BOTH corpora) planned by the planner's existing generic combine machinery — zero planner changes; it is now the 10th combiner in the test_combiners/test_filters invariants. Merge decisions: histogram registers long names only (the 6 undocumented 1-letter aliases b/c/d/f/l/m stay in the gaps backlog — open question); tinterlace registers `mode` only (docs-only `flags` noted docs-ahead-of-build); blockdetect/blurdetect `planes` registered as build "1" with the docs "first only" phrasing noted.
- **4a6a891 — no-audio containers (e.g. GIF) drop the input track with -an** (carries_audio gating + -an emission + plan_execution PlanErrors + None-safe warning + clear ValueError for audio_codec_options on no-audio containers) AND the ablation `run_rules` crash→ArmResult(error) wrapper — ablation run 1 (pid 1186350) died on a rules-arm exception; that wrapper is what run 2 needed to resume.
- **2ce5e95 — gaps drift 31→25: three detector artifacts, no data change.** (1) gaps `registry_default` now does a balanced-paren scan — showvolume c's default is an expression containing ')': 'PEAK*255+floor((1-PEAK)*255)*256+0xff000000'; (2) drift compares defaults via author.`defaults_equal` ('all' ≡ the build's expanded flag sum, e.g. aspectralstats measure); (3) the registry level_sc note relocated. Remaining 25 rows are all known/expected: 16 freezedetect/silencedetect string-modeling rows (intentional — builds report <double> but accept -30dB) + 9 9.0.1-only evolution rows (adynamicequalizer, anullsrc, libvmaf, overlay). Two regression tests added (nested-paren extraction incl. the real showvolume entry; all-vs-expanded flag sum).
**Ablation COMPLETE 880/880 (~00:28):** run 1 (pid 1186350) crashed at abl-121's rules arm; run 2 (pid 1774958) resumed appending from the JSONL cache (log /tmp/ablation-run2.log) and finished. 880 arm-rows; report.md + report.json written; error rows tallied above (19: 7 timeouts + 7 validation + 2 JSON + 3 rules). Artifacts at /tmp/ablation-run1/ and (persistent) /dd2/andrei/ablation-run1/.
**Next:** (1) **PUSHED (2026-10-27, user's explicit "push it")** — origin/main = **c949b9e** (50 commits aa0e534..c949b9e, fast-forward, github.com:suedenglehem/fass); i7 ff-merged to the same commit from origin (local state preserved: compose.yaml opt-ins still `M`, .resume + root ffmpeg-rag-prd.md untracked). (2) Open merge questions from 2.7.0: histogram 1-letter aliases (register or not), tinterlace docs-only `flags`, blockdetect/blurdetect `planes` phrasing. (3) PRD-deferred scope still out: multiple watermarks, HDR workflows, batch processing. (4) i7 `fass-worker` container still runs pre-rebuild code (the fass:latest image is new; the worker process is not) — restart it only when the user wants (nothing fass-side depends on it right now). The M11 series is otherwise DONE: all PRD gates measured, both battery halves 21/21. Standing: advisory evidence only — registry/builder/argv gate stays sovereign; treat evidence_status 'supported' with skepticism (M11.1 calibration note). RAG evidence augmentation is now an OPT-IN experiment that measured NO-GO on the pre-registered +5 pp paraphrase-param gate (it leads on every other held-out axis — revisit if the gate ever becomes status/ops-based).

## Previous state (2026-10-26, **M11.3 COMPLETE incl. parser round 3 (f2a1dd8)** — rag/author + rag/gaps integrated (d47f96e, job j_5c845355c5), parser rounds 1+2 on real-corpus findings (10b1fe5, 0116554), 3 real-corpus drafts verified_both / 0 option_diffs merged to **registry 2.6.0** (b122e36: ebur128 audio + signalstats + blackframe video); main suite **1213 passed / 1 skipped**. **10 diagnostic drafts triaged after round 3: 6 verified_both 0-diff ready to merge (edgedetect, freezeframes, thistogram, ahistogram, aspectralstats, showvolume), 4 genuine docs↔build differences documented (histogram 12 undocumented 1-letter alias rows, tinterlace docs-only `flags`, blockdetect/blurdetect `planes` "first only" vs "1"). 31 drift rows triaged (16 intentional string-modeling, 6 → acompressor refresh, 9 genuine 9.x evolution). Merge package /tmp/m114_registry_merge.py ready + DRY-RUN VERIFIED on a scratch copy (registry 2.6.0 → 2.7.0: +6 filters + acompressor refreshed to the 12 modern options) — runs ONLY after ablation (registry live-read by the running arms).** M11.2 UI COMPLETE — Sources panel (bb9bcc0) + RAG tab (15a0baa) + **Files tab (38ce046, from j_2d6b4287ee: /v1/files, /v1/files/inspect ffprobe|mediainfo, /v1/files/frame 360px JPEG, right-click menu + popup)** merged. M11.4 IN FLIGHT — ablation run 1 = **880 arms (220 entries × 4 arms, requests.json 91 + ablation_heldout.json 129 in ONE run — a separate run 2 was absorbed)**, pid 1186350 in background (waiter b7icgf4vl), **641/880 at 23:01, ETA ~00:45 CET**. §9.4 warm budgets + determinism PASS; cold first-query budget owed after run 1 (rag restart then).)
**M11.2 commits on main so far:** d60c167 §6.6 RagConfig block +
FASS_RAG_* env overrides; 1621b8b/63a2a35 §6.1 registry 2.5.0 (doc_ref +
aliases + provenance, all 87 filters); 56324af §6.2 deterministic query
builder (mentioned/jargon/media-facts/corpus-selection) + fail-open RAG
client; **d005578 §6.2 FIX** — `mentioned_components` was appending the
matched TEXT as the component name, so reviewed aliases ('remove
silence', 'deinterlace') leaked into component_names; the name map now
carries (type, canonical registry name), canonicals registered before
aliases can shadow them; 9bd4401 §6.3 evidence block (delimited
`<<<EVIDENCE`, untrusted preamble, budget-bounded, explicit
`[truncated]`) + LLM prompt augmentation (block in the USER TURN, static
system-prompt paragraph only, schema unchanged).
**Committed: 09060a3 §6.4/§6.5 pipeline RAG hook + citation
derivation.** fass/rag/citations.py (used_components / _matches /
chunk_citation / derive_citations — retrieved chunk wins, highest
score; filter-only registry doc_ref fallback `source:
"registry_doc_ref"`; unused chunks dropped) + pipeline.py
(GenerationResponse.evidence; retrieval ONLY on the llm adapter when
resolved_rag().enabled; evidence kwarg into adapter.parse only when the
block exists; build_evidence on ready — status "unavailable"/
evidence_status / "insufficient" with RAG, "static" without;
`_closest_supported` fail-open note on UNSUPPORTED — status never
changed). 19 new tests (tests/test_rag_citations.py 13,
tests/test_pipeline_rag.py 6) → **suite 1100 passed / 1 skipped.**

**Committed: 5704ff0 — §9.3 safety gates.** pipeline.py: both
`rag_retrieve` call sites (generate + `_closest_supported`) wrapped in
fail-open try/except → `_degraded_rag(e)` (a broken client contract
degrades to the same shape instead of failing generation);
build_evidence appends `evidence for filter:X was not used by the plan;
the command is unaffected by it` per unused retrieved filter (deduped,
only when retrieval was ok). tests/test_rag_safety.py (9 tests, all 6
gates): poisoned doc (injection text reaches the LLM turn but
plan/argv byte-identical to a RAG-off run, warning + no citation);
wrong-version corpus (select_corpus unit — never "exact", warnings
surfaced in the response as corpus_match "nearest"); RAG down (REAL
client against a dead port → "unavailable", registry_doc_ref citations
only; a raising client → degraded with the error in warnings); absent
filter (benign chunk unused → no citation / warning / not in command;
LLM induced into a disallowed filter → schema rejection, error, no
command); adversarial filenames roundtrip with RAG on (rules path, 0
retrieval calls); determinism (rules path asdict minus "evidence"
identical RAG on vs off). **Full suite: 1109 passed / 1 skipped.**
(One flaky test_webinfo.py failure in the first full run — passes in
isolation and on re-run; unrelated to this change.)

**INTEGRATED: commit 7 — §6.7 deployment (job j_3d4505da56,
tr4-secondary, cc-worker, base 09060a3) — merge commit 97c0552.**
5 files:
rag/health.py (`python -m rag.health`, FASS_RAG_DATA precedence over
FASS_RAG_DATA_DIR), docker/Dockerfile.rag (python:3.12-slim, non-root,
/data), docker/compose.rag.yaml overlay (profile "rag",
docker-data/rag:/data, 0.0.0.0:8100, healthcheck, restart
unless-stopped; committed compose.yaml untouched), rag/server.py
minimal (FASS_RAG_DATA honored, pure parse_bind(spec), FASS_RAG_BIND
default "127.0.0.1:8100", FASS_RAG_PORT still overrides the port),
tests/test_rag_deployment.py (text assertions — pyyaml NOT in the
venv). Attempt 1 crashed with exit 0 (report-schema rejection, per the
two prior jobs); attempt 2 succeeded. Worktree suite: 1132 passed /
3 skipped + 1 ENVIRONMENTAL failure
(tests/test_rag_benchmark.py::test_generator_deterministic —
rag/corpus/*/chunks/ is gitignored, so clean dispatcher worktrees lack
the generated corpus data; the file exists in the main tree, the job
touched nothing there — job's own 35 tests pass). Diff verified
file-by-file; merged --no-ff as **97c0552**; integration recorded via
ladd (`ladd --json integrate j_3d4505da56 --commit 97c0552…` →
accepted). **Main suite post-merge: 1147 passed / 1 skipped.**

**COMMITTED: commit 6 = bb9bcc0** (UI Sources panel + config-tab rag
block + `GET /v1/rag` status endpoint — fail-open corpora probe,
NEVER 5xx; +3 tests; suite 1147 passed / 1 skipped).

**INTEGRATED: RAG tab = 15a0baa** (job j_1f86a0ad30, tr4-secondary,
base bb9bcc0 — job d7651e5, merged --no-ff). `POST /v1/rag/query`
{query (min 1, not whitespace-only → 422), top_k? ge 1} — disabled →
200 {ok: false, "RAG disabled — enable with FASS_RAG_ENABLED=1 +
FASS_RAG_URL", chunks: []}; top_k via cfg.model_copy; search wrapped
try/except → 200 ok false (the endpoint never 5xx's); success →
corpus_id/corpus_match/evidence_status/retrieval_ms/warnings/chunks
pass-through. UI: fifth tab (button appended via .concat('rag') to
keep the pinned showTab literal), input#rag-q + query/clear,
#rag-results (70vh scroll), chunk cards (url#anchor link, component
badge, option_names mini badges, score 3-dec, pre text), all
server strings esc()'d. +4 tests (disabled / mocked 2-chunk
passthrough / blank 422 / top_k captured). Worktree suite
**1151 passed / 1 skipped** = baseline + 4. Diff reviewed
file-by-file.

**INTEGRATED: §9.2 held-out dataset = 5308bd7** (job j_db1025fa7f,
i7, base bb9bcc0 — job 026472e). EXACTLY two new files:
tests/benchmark/ablation_heldout.json — **129 entries**: 79
paraphrase/jargon (≥60 required; M10.1 transform vocab, fully
specified expected plans), 20 ambiguity (needs_clarification +
note), 20 unsupported (closest-supported note), 10 adversarial
(injection text embedded, benign expectation — the injection must
change nothing); + tests/test_ablation_heldout.py — 13 shape tests
(counts, ids abl-NNN in order, entry/expected key allowlists, ops ⊆
plan.py operation vocabulary DERIVED from the schema, ready↔ops
consistency, trim shape, no instruction occurs verbatim in
fass/nlu/rules.py source, adversarial markers + benign status).
Worktree suite: 1157 passed / 3 skipped + 1 KNOWN ENVIRONMENTAL
failure (test_rag_benchmark::test_generator_deterministic —
rag/corpus chunks gitignored, absent in clean worktrees; pre-existing,
re-confirmed on base; the job touched nothing there).

**MERGED 38ce046: Files tab (job j_2d6b4287ee, tr4-secondary, base
15a0baa, merged --no-ff, integration recorded; suite 1187/1).** tab
"Files" — two columns /input + /output (resolved via the existing
fass/execute.py _input_dir/_output_dir: $FASS_INPUT_DIR/$FASS_OUTPUT_DIR
→ /input|/output fallback → "" when absent), name + size + date rows;
right-click menu "ffprobe" / "mediainfo" / "1st frame" → modal popup
(JSON in scrollable <pre>; 1st frame = system ffmpeg `-frames:v 1
-vf scale=-2:360` JPEG → base64 <img> + WxH caption); new endpoints
GET /v1/files?dir, GET /v1/files/inspect?dir&file&tool (bare-basename
guard, 400/404; mediainfo NOT installed on tr4 → 503 "not installed";
ffprobe reuses the /v1/mediainfo logic), POST /v1/files/frame
(fail-open ok:false + stderr tail).

**IN FLIGHT: ablation run 1 (pid 1186350, waiter b7icgf4vl)** —
scripts/m11_ablation.py (committed 1d543a6), 4 arms (rules / llm /
llm+schemas / llm+schemas+evidence) × **220 entries = requests.json
91 + ablation_heldout.json 129, both datasets in ONE run (880 arms;
the former separate run 2 was absorbed — the §9.2 go/no-go is computed
from the held-out subset of this report)** against tr4 vLLM
qwen3.8-27b fp8 :8091 (shares the endpoint with this session — pace
~2.5 LLM calls/min). Live progress = line count of
/tmp/ablation-run1/results.jsonl (641/880 at 23:01; ETA ~00:45 CET).
Out: /tmp/ablation-run1/{report.md,report.json}, log
/tmp/ablation-run1.log. Two llm_schemas rows hit the 180 s timeout —
check whether error rows are cached in results.jsonl; if so, consider
re-running just those arms after the report.

**§9.4 BUDGETS (scripts/m11_rag_budgets.py — new, uncommitted, to
ship with commit 9):** live fass-rag service :8100 (pid 959120,
started with FASS_RAG_DATA=rag/corpus), n=40 real benchmark queries:
**retrieval warm p95 = 29 ms (budget ≤300) PASS; evidence body max
= 4000 ch (budget ≤4000, +263ch fixed wrapper — the budget bounds
the body per §6.3) PASS; RAG process RSS = 71 MB (≤4 GB) PASS.**
Evidence: /tmp/rag-budgets/warm.json. **Determinism (the "turn RAG
off" proof): full 91-entry benchmark through generate(rules) with
FASS_RAG_ENABLED=1+URL vs unset → 91/91 byte-identical plans
(excl. the harness's own latency_s wall-clock field, which is the
only unstable field — verified by a same-env double sweep) PASS.**
Still open: cold first-query retrieval (needs a service RESTART —
deferred until run 1 finishes so its evidence arm doesn't lose
queries), RAG overhead ≤400 ms + overall ≤8 s (from run 1's arm 3
vs arm 4 latencies).

**M11.3 (COMPLETE) — authoring pipeline + registry 2.6.0 (b122e36).**
Integrated d47f96e (job j_5c845355c5): rag/author.py (docs
section/anchor/options parse in three markup forms, build-facts
AVOptions parse, docs↔build option diff, classification
verified_both/present_in/docs_only/undocumented/conflict, draft JSON +
stdout review report; NEVER writes registry.json — test-enforced) +
rag/gaps.py (coverage, ranked backlog, drift vs build-facts, demand
JSONL clusters). Fixtures adb8c60/f13ae07 (ebur128/signalstats/
blackframe, verbatim real corpus, nested-dl / grouped-dt / prose edge
cases). Parser round 1 (10b1fe5): grouped <dt>s share one <dd>;
alias-group <dt> ("color, c") splits on comma; prose form (c)
"accepts the following parameters" (best-effort + PROSE review note);
marker-without-<dl> guard; registry-kind↔build-type compat map in
gaps. Parser round 2 (0116554): build-facts = union of EVERY
`AVOptions:` block (alias-joined headers acompressor/sidechaincompress,
shared-class (h|v)stack, shared component framesync) + comma-group
section headings ("setpts, asetpts") register every listed name
(heading_names in author find_section + gaps corpus_sections) +
number↔duration type compat. Real-corpus drafts: all three
verified_both, 0 option_diffs, cross-checked against build ground
truth (captures identical across 6.1.1/9.0.1 except the 9.0.1
"Exiting with exit code 0" footer). Registry 2.6.0 (b122e36) —
surgical line insertion (the file is HAND-FORMATTED, never
json.dumps-re-serialize; a round-trip gives an 8229-line diff): audio
ebur128 (6 read-only log exports integrated/range/lra_low/lra_high/
sample_peak/true_peak excluded from options) + video signalstats +
video blackframe, each description/options/diagnostic + doc_ref/
aliases/provenance as the LAST THREE KEYS (test-enforced order);
ebur128 framelog default omitted (docs say info, build says -1);
test count bumps (90 filters; 53 video / 37 audio; version 2.6.0).
Suite **1209 passed / 1 skipped**. Gaps re-run: 90 registry filters,
pilot drift [], all pilot names resolved (9.0.1 unresolved=[];
6.1.1 unresolved=[blackdetect_vulkan] — genuine: the 6.1.1 manual
predates the "blackdetect, blackdetect_vulkan" comma group); 31
remaining drift rows — TRIAGED (verdicts below). Backlog 496
(includes non-filter section headings like "Filtergraph syntax" —
known cosmetic limitation). drafts/ + gaps/ are untracked review
artifacts (reproducible via rag.author / rag.gaps).

**Parser round 3 (f2a1dd8) + 10-draft triage.** Singular form-(b)
marker ("It accepts the following option:" — aspectralstats),
token-based default extraction (decimal-safe "Default is 0.5.",
"Default value is 2048.", "Default mode is merge.", "default is: X",
"Defaults is X", sentence-truncation for "Default is 0, which means
step is disabled."), enabled/disabled ↔ true/false normalization,
"all" vs expanded flag-sum comparison (aspectralstats measure) —
showvolume/thistogram/aspectralstats go from 8–12 diffs to
**verified_both 0-diff**. Test suite **1213 passed / 1 skipped**
(18 tests in test_rag_author.py). Final 10-draft triage:
**CLEAN (merge at 2.7.0): edgedetect, freezeframes (multi_input 2),
thistogram, ahistogram, aspectralstats, showvolume.** GENUINE
docs↔build (no merge without a decision): histogram — 12 rows are
6 undocumented 1-letter aliases (b/c/d/f/l/m) present in BOTH
builds, absent from docs (the 4 decimal conflicts were parser bugs,
now gone); tinterlace — docs document a `flags` option (vlpf/cvlpf/
bypass_il) that NEITHER build has (docs ahead of build);
blockdetect/blurdetect — docs "first only" vs build bitmask "1" for
`planes` (register "1" when merged).

**31 drift rows — final triage (gaps re-run round 3: identical 31,
drift unaffected by the parser fixes — it compares registry vs
build only).** (a) **16 rows INTENTIONAL, keep as-is:**
freezedetect/silencedetect n/noise — builds report `<double>` but
EMPIRICALLY accept dB-suffixed values (`freezedetect=n=-30dB` exits
0 on system ffmpeg 6.1.1); the registry's `string` kind models the
accepted input domain (a `number` kind would false-reject -30dB).
Stable noise in the gaps output. (b) **6 rows → FIXED at 2.7.0:**
acompressor kratio/level/rms "absent from build" (both corpora) —
the M10.0 entry described the PRE-5.0 option set; both corpora's
builds + docs carry the modern 12-option set (level_in, mode,
threshold, ratio, attack, release, makeup, knee, link, detection,
level_sc, mix; shared acompressor/sidechaincompress AVOptions block,
level_sc undocumented under acompressor — documented under
sidechaincompress). Refresh drafted via rag.author (drafts/
acompressor.json, verified_both, only diff = level_sc
undocumented_option) and folded into /tmp/m114_registry_merge.py
(in-place block replacement; doc_ref anchor fixed Commands →
acompressor; test_filters.py's `level=` examples → `level_in=`).
(c) **9 rows GENUINE 9.x build evolution, NO registry change**
(registry targets both builds; 6.1.1 is the common denominator):
adynamicequalizer (auto default disabled→off; `direction` removed in
9.x; mode default cut→cutbelow), anullsrc (r/sample_rate
string→int in 9.x), libvmaf (cpuid/log_format removed in 9.x; pool
bool→string; filter absent from the 6.1.1 corpus build entirely),
overlay (alpha default straight→auto in 9.x). Document as
version-conditional notes; revisit if a 9.x-only registry ever ships.

**M11.1 (COMPLETE, for reference) — part A integrated as 181c1e5**
(from tr4-secondary job j_7dea91b80b): rag/retrieval.py FTS5 lexical
retrieval core + rag/server.py fass-rag FastAPI service (PRD §5.4/5.5)
+ tests.

**M11.1 part B — committed as 0ccdb7f.** PRD §9.1 benchmark:
deterministic query generator (rag/benchmark/generate_queries.py) → 243
queries (87 exact / 79 option / 62 hand-authored NL / 10 corpus-diff
mismatch / 5 insufficient) committed as rag/benchmark/{queries.jsonl,
dataset.json}; two-pass runner (rag/benchmark/run_benchmark.py):
RANKING top-8 + SERVICE (TestClient top-6/4000). **All 9 §9.1 gates
PASS:** exact_top1 1.0 (≥0.95), recall8 0.9095 (≥0.90; the 21 residual
misses are all-NL genuine lexical gaps — documented in dataset.json, the
M11.2 advisory layer must tolerate them), citation 1.0, version 1.0,
no_fabricated_syntax 1.0, mismatch_no_leak 1.0 (TEXT-level novelty
filter), persistence identical, warm_p95 ~43 ms (≤300), cold_first ~24 ms
(≤1000). Changes: `_expand` gap-fill (seeds keep slots; top-3 seeds pull
parent/adjacent chunks, budget-aware, EXPANSION_SCORE_FACTOR 0.9);
build-help provenance (source_url build-help://{corpus}/{file}, anchor
{stem}-{i}) + _validate_chunks non-empty source_url/anchor;
PARSER_VERSION 1.3 (build-facts manifests); candidate-set union of
exact-name component chunks gated to ≤3-token queries
(COMPONENT_UNION_MAX_TOKENS — rescues mm-opt-* recall without flooding
long NL sentences); tests/test_rag_benchmark.py (determinism, shape,
skipif-guarded full-gates run). **Host full suite: 990 passed / 1
skipped.** Calibration note: evidence_status 'supported' is
over-claimed for out-of-domain queries (bm25 cannot separate them, 0–16
token overlap) — the no-fabrication gate checks exact_match
verifiability instead; M11.2 must not treat lexical 'supported' as
ground truth.

**Web-UI job j_dc6d73d9dd (tr4-secondary) — work rescued, merged as
c27a0d8 (branch commit bcb60e8).** The job FAILED as invalid_result —
the worker's result report used its own vocabulary (outcome 'success',
acceptance 'CONFIRMED', tests[].status None) instead of the schema
enums; the work itself was complete and passing (978 passed / 1 skipped
in the worktree). Rescued manually: diff reviewed (scope clean — only
fass/{api,ui,execute,pipeline}.py + tests/test_webinfo.py), verified
-loglevel verbose at argv index 3 on EVERY pass of multi-pass plans,
node --check on the embedded JS OK, merged --no-ff into main. M10.7
features: 'verbose log' checkbox on the Command tab → -loglevel verbose
on every pass (set on the EditPlan before the argv is built, visible in
the displayed command); verbose runs log to {output}/log/{run_id}.log
(non-verbose stay in the runs dir; Run records carry log_path +
verbose); GET /v1/mediainfo?target=input|output&file=<bare name>
(ffprobe JSON, -i prefix accepted, 400/404/502 documented); GET /v1/logs
(runs dir + {output}/log, newest first) + GET /v1/logs/{name} (basename
only, traversal-guarded; >10000 lines served as last 1000 + banner
'[truncated: showing last 1000 of N lines]'); NEW Info / Log tab holds
ALL media-info + log controls (radio Media info | Run log; no
mediainfo controls on the Command tab — the user's tab-consolidation
correction wins). 25 tests (tests/test_webinfo.py). **Merged main
suite: 1015 passed / 1 skipped** (990 + 25). dispatch_integrate is
impossible for failed jobs — the merge commit c27a0d8 records the
integration; the job's worktree is retained for review.

**Next:** (1) ablation run 1 completes (waiter b7icgf4vl fires when
pid 1186350 exits) → read /tmp/ablation-run1/report.md + report.json
(per-dataset tables), sanity-check the 4-arm table; §9.2 go/no-go
from the HELD-OUT subset: arm 4 (llm+schemas+evidence) must beat arm 3
by +5 pp parameter accuracy on paraphrase with no regression
elsewhere, else ship arm 3 + static doc_ref citations (arm 1 rules
stays default either way); §9.4 from the report: RAG overhead =
arm 4 − arm 3 (≤400 ms), overall (≤8 s); check the 2 llm_schemas
180 s timeouts (error rows cached in results.jsonl? re-run just those
arms if worth it). (2) **registry 2.7.0 merge** (the moment the
ablation process is gone): `.venv/bin/python
/tmp/m114_registry_merge.py` (dry-run verified on a scratch copy:
+edgedetect/freezeframes/thistogram video, +ahistogram/
aspectralstats/showvolume audio, acompressor refreshed in place to
the 12 modern options; version 2.6.0 → 2.7.0; 96 filters 56/40) →
test updates: tests/test_filters.py (53/37 → 56/40; acompressor
examples `level=3`/`level=999`/`level=-1` → `level_in=2`/`level_in=
999`/`level_in=0.001` at L99/123-124/150-157/184/221) and
tests/test_registry_docs.py (90 → 96 in two places, "2.6.0" →
"2.7.0") → full suite → commit (Co-Authored-By line). Then a gaps
re-run: expected drift 31 → 25 (the 6 acompressor rows resolved).
(3) restart the fass-rag service (kill EXACT pid 959120 after
verifying it's still the rag server — never pkill -f; relaunch
`FASS_RAG_DATA=rag/corpus nohup .venv/bin/python -m rag.server >
/tmp/fass-rag-serve.log 2>&1 &`) → cold first-query budget
`FASS_RAG_ENABLED=1 FASS_RAG_URL=http://127.0.0.1:8100
.venv/bin/python scripts/m11_rag_budgets.py --cold --n 5 --pid
<newpid> --out /tmp/rag-budgets/cold.json` (≤1000 ms). (4) M11.4
close-out (go/no-go recorded + dual-build battery on media/titan.mp4)
+ §9.4 close-out (all budgets PASS + this HANDOFF) + /savework.
Standing: advisory evidence only — registry/builder/argv gate stays
sovereign; treat evidence_status 'supported' with skepticism per the
M11.1 calibration note. Later backlog (each: author → draft → review
→ surgical-merge cycle): histogram merge (decide: register the 6
undocumented 1-letter aliases b/c/d/f/l/m or keep long names only),
blockdetect + blurdetect merges (register `planes` as build "1" with
a docs-phrasing note), tinterlace merge (register `mode` only, note
the docs-only `flags` as docs-ahead-of-build).

## Previous state (2026-10-26, M10.6 COMPLETE — dual-build 21/21 + suite 953/1 on both halves; M11.0 done; RAG M11.1 in flight at the time)
**M10.6 — per-category E2E verification battery + docs.** The battery
(`scripts/m10_battery.sh local|docker` → `scripts/m10_battery.py`) has
21 cases across every M10 category (resize/crop/rotate/speed/trim/
watermark/audio/codec/GPU/combiners xfade+concat+hstack/comparison
psnr+ssim+libvmaf/two-pass stabilize/fdk VBR). Each case generates
through the REAL pipeline (rules adapter, preflight on), checks its
needles as substrings of the rendered argv, then EXECUTES on the real
ffmpeg of the build. Build-gated cases (fdk_aac VBR, libvmaf,
scale_cuda) expect a preflight warning + skip on 6.1.1 and RUN on 9.0.1.

- **Host 6.1.1: battery 21/21 (validate_argv gate now active), M8
  battery 10/10, suite 953 passed / 1 skipped** (skip = node JS
  check).
- **i7 9.0.1 attempt 1** (job j_e478994233, image d3bae5d1aba6): suite
  green (946/1) but battery 17/21 — xfade/concat/psnr/libvmaf failed
  with "could not be probed": the planner probed combiner extra inputs,
  the combiner main fallback, and the comparison reference with the RAW
  bare filename instead of resolving against the input volume (fatal in
  the container, inputs live in /input). GENUINE BUG, fixed in
  **b989e31**: 4 probe sites wrapped in `resolve_input_name` (the
  mechanism pipeline.py already used for the main asset) + 3 regression
  tests (both media tests proven to FAIL on the unfixed planner).
- **Web-UI `-i <file>` input convention** (same commit): inputs may be
  written `-i <file>` — the API strips the prefix server-side
  (`_strip_i_prefix`, fass/api.py) before planning, so every API client
  gets it; streams/source specs pass through untouched; UI placeholders
  document the form. Bare names resolve against $FASS_INPUT_DIR /
  /input at probe time AND run time.
- **i7 9.0.1 attempt 2** (job j_0a8a17f0c1, image fass:m106b at
  b989e31): suite 949/1 skipped, battery 19/21 — the probe bug is
  fixed; xfade + concat now trip the API's argv gate, exposing two
  more GENUINE build-independent bugs: (1) `settb` (emitted by the
  planner for every xfade input normalization) was missing from the
  filter allowlist; (2) the validator's filtergraph parser stripped
  only TWO leading input labels, so the 4-pad concat graph misparsed
  ("'[1:v][1:a]concat' is not a filter name" — the "(in ...)" context
  prints the instance AFTER one label was already stripped, which
  masked the real shape). Both reproduce on the host — the local
  battery had been bypassing `validate_argv` (docker mode enforces
  it at /v1/runs), so the earlier host 21/21 was hollow for
  xfade/concat.
- **Fixed in this commit (M10.6 bugfix):** `settb` registry entry +
  docs_provenance (51 video + 36 audio = 87);
  `loader._validate_instance` strips ALL leading labels; the local
  battery runs `validate_argv` on every generated argv before
  executing (both modes share acceptance semantics); 2 regression
  tests. Host re-run: battery 21/21 with the gate ACTIVE, suite
  953/1 skipped.
- **Final 9.0.1 re-verification COMPLETE on tr4 — i7 was DOWN** (worker
  died ~06:11, daemon flagged 12:40; job j_0a7359da46 =
  restart_no_result/exit_unknown → CANCELLED, worktree retained at
  c866f64). User directive 2026-10-26: when i7 is down, validate ALL
  ffmpeg against the tr4 SYSTEM ffmpeg 6.1.1 (not docker); the 9.0.1
  half ran docker-ON-tr4 (image fass:m106c, b4a880e9e507 — i7 was only
  the LLM backend). Results, 2026-10-26: container suite **953 passed /
  1 skipped** (skip = node JS check); docker battery **21/21** —
  fdk_vbr4, libvmaf, scale_cuda ALL EXECUTED (tr4 has 3 NVIDIA GPUs —
  "tr4 is AMD, no CUDA" was right about QSV, wrong about GPUs; the
  container ran --gpus all); fresh host 6.1.1 official record: suite
  **953 passed / 1 skipped** (skip = libvmaf E2E — the distro build
  lacks the filter) + local battery **21/21** with the gate.
  **M10.6 = dual-build verified, DONE.** Serve container
  fass-m106c-serve removed after the battery.
- **NEW task (user, 2026-10-26) — web-UI "mediainfo/log" tab — DELEGATED**
  to tr4-secondary as job **j_dc6d73d9dd** (base 88cd63b; scope
  fass/api.py, fass/ui.py, fass/execute.py, fass/pipeline.py, tests/):
  (1) new Info/Log tab showing EITHER media info of the INPUT or the
  OUTPUT file (the mediainfo binary is unavailable — ffprobe JSON
  serves; ALL mediainfo controls live on THIS tab — the user first said
  main-page button, then corrected to tab consolidation, which wins)
  OR the processing log of a CLI run, selector-toggled; logs >10000
  lines are served as the last 1000 + banner (user add); (2) MAIN-page
  "verbose log" checkbox → -loglevel verbose in EVERY pass argv, run log
  → {FASS_OUTPUT_DIR}/log/{run_id}.log, browsable on the tab. New
  endpoints /v1/mediainfo, /v1/logs, /v1/logs/{name}; verbose field on
  CommandRequest/RunRequest (extra='forbid' — must be declared). On
  landing: review diff, full suite, merge.
- **VMAF model (user, 2026-10-26) — FOUND + VERIFIED:** the official
  Netflix/vmaf model (raw.githubusercontent.com/Netflix/vmaf/master/
  model/vmaf_v0.6.1.json) is BYTE-IDENTICAL to
  docker/models/vmaf_v0.6.1.json (sha256 5950d61f…, 19101 B; the
  official v0.6.1 distribution is JSON-only — no .pkl). Provenance
  note: docker/models/README.md. The host 6.1.1 libvmaf skip is
  FILTER-ABSENCE (the Ubuntu build has no --enable-libvmaf, only
  vmafmotion), NOT model absence — no download changes that; the 9.0.1
  container half covers libvmaf (battery libvmaf EXECUTED clean).
  `vmaf_model_path()` now also resolves vmaf_v0.6.1.json under
  FASS_VMAF_MODEL_DIR (+ regression test).
- **M11.1 part A SUCCEEDED** on tr4-secondary (job j_7dea91b80b,
  branch dispatch/j_7dea91b80b, base b989e31): `rag/retrieval.py`
  FTS5 lexical core + `rag/server.py` fass-rag FastAPI service (PRD
  §5.4/5.5) + tests; integration pending (review diff, full suite,
  dispatch_integrate). Part B = §9.1 benchmark + gates +
  lexical-vs-hybrid decision (dataset under rag/benchmark/).

**Next:** (1) integrate web-UI job j_dc6d73d9dd when it lands (review
diff, full suite, merge); (2) M11.1 part A integration (branch
dispatch/j_7dea91b80b, base b989e31 — review, full suite on branch,
merge --no-ff, dispatch_integrate) + part B (benchmark dataset 84
exact + 50 option-level + 60 NL + 10 version-mismatch + 5 insufficient,
§9.1 gates, lexical-vs-hybrid decision) — RAG work continues while the
web-UI job runs; (3) /savework once the ffmpeg-filter side is fully
wrapped (M10.6 is DONE).

## Previous state (2026-10-26, M11.0 — RAG corpus foundation, COMPLETE; M10.5 DUAL-BUILD VERIFIED)
**M11.0 — pinned corpora + parser 1.1 + chunker + ingest CLI** (PRD
§10.3; committed e1ff741 + c9e4f55 + 0081cd3). RAG corpus foundation for
`ffmpeg-assistant-rag-prd.md` — advisory evidence layer only, no fass
code touched.
- **Corpora:** 6.1.1 docs from the Ubuntu deb ffmpeg-doc 6.1.1-3ubuntu5;
  9.0.1 from the official tarball via `make doc` (texinfo 7.1;
  libtext-unidecode-perl REQUIRED). Reproducibility proven: document
  sha256 lists byte-identical across TWO independent builds (tr4 primary
  + tr4-secondary worktree, different machines). Recipe + rebuild
  commands: `rag/corpus/BUILD-RECIPE.md`.
- **Parser 1.1** (`rag/parser.py`): makeinfo HTML → Document/Block tree.
  Stacked deflist pairing for the table idioms: shared `<dd>` (w/h → one
  entry per name), continuation `<dd>` (desc merges into last entry),
  nested `<dl>` value tables become the entry's description
  (eof_action → repeat/endall/pass), bare `<dt>` kept. TOC div excluded.
  Canonical `document_to_dict` → fixture round-trips.
- **Chunker** (`rag/chunker.py`): structure-first; TOKEN_MIN 350 /
  TOKEN_MAX 700; heading paths; option units "term: desc" (nested tables
  inline); examples stay with their introducing paragraph; oversized →
  own flagged chunk, never cut.
- **Ingest CLI** (`rag/ingest.py`): sha256 drift gate; re-ingest = NEW
  immutable generation (explicit collision → ValueError), atomic
  generations.json + history, previous gen kept for rollback; dedup on
  (source_url, content_hash).
- **Final state:** 9.0.1 manual gen 3 = 1820 chunks, build_help gen 2 =
  836 (516 docs); 6.1.1 manual gen 2 = 1687, build_help gen 2 = 849
  (562 docs); parser-1.0 gens preserved on disk.
- **Tests:** 2 parser fixtures round-trip byte-identical; 36 RAG tests
  (14 parser + 12 chunker + 9 ingest) → **full host suite 946 passed, 1
  skipped** (skip = node JS check on the tr4 host). Exit gate met.

**Next:** M10.6 — per-category E2E verification battery + README /
DEVELOPMENT docs, then **M11.1 retrieval API** (embed + retrieve + rank
over the chunk store, benchmark, re-measure the token heuristic against
the real embedding tokenizer).

## Previous state (2026-10-26, M10.5 — two-pass vidstab, GPU consume rule, DUAL-BUILD VERIFIED: 6.1.1 host + 9.0.1 container)
**M10.5 — `stabilize` (two-pass vidstab) + GPU filter-consume rule +
channelsplit-in-graph + anullsrc source spec.** Stabilization needs the
detect pass to see the SAME frames the transform pass emits (trim/speed
would break frame correspondence), so the plan shape changed: an
ExecutionPlan can now carry multiple passes.

**What shipped (committed aa0e534):**
- **Plan schema:** `passes: list[Pass]` (purpose, inputs, vf, output_path,
  output_opts) + `intermediates: list[str]` on ExecutionPlan; top-level
  fields mirror the FINAL pass (backward-compatible single-pass plans
  unchanged). `StabilizeOp` in the Operation union.
- **Planner (`_attach_stabilize_passes`):** detect pass =
  `-vf vidstabdetect=result=<trf>` with `-f null -` (inputs[:1] only);
  transform pass = `vidstabtransform=input=<trf>` prepended to the vf
  chain. The .trf is a BARE name (`<output-stem>.vidstab.trf`) — both
  passes run in one cwd (the final output's dir). Conflict matrix (all
  PlanError): trim ("same frames"), speed ("frame correspondence"),
  video-domain filters ("not compatible with stabilization"),
  audio_replace ("cannot be combined with stabilization"), watermark with
  asset ("cannot be added in the same pass"), bsf ("compressed stream"),
  combine with probeable video ("multi-input filter"), stream destination
  ("intermediate transform file"). ALLOWED: resize, grayscale, rotate,
  audio filters, audio_resample.
- **Builder:** `build_argv()` = final pass (single-plan contract
  unchanged); `build_argv_all()` returns every argv (final last).
- **NLU (rules):** `_STABILIZE_RX` phrases (stabilize/remove camera
  shake/handheld/hand shake); denoise + sepia stay unsupported and the
  denoise claim wins the report in "stabilize and denoise"; mode conflicts
  now surface (bugfix: stabilize→mp3 and →subtitles used to drop the op
  silently; now NEEDS_CLARIFICATION). **GPU consume rule:** a CUDA filter
  (scale_cuda from "resize … on the gpu", yadif_cuda from "deinterlace on
  the gpu") ABSORBS the GPU mention → encoder stays CPU (gpu=False,
  "not the encoder" note in defaults); an explicit encoder word (nvenc,
  or gpu/nvidia/hw-acc within 40 chars of an encode word) re-enables
  nvenc; a bare mention without a CUDA filter keeps M6 nvenc.
- **Pipeline/API/execute/CLI:** `GenerationResponse.steps`;
  `RunRequest.steps` (max 8, validator `steps[-1] == arguments`);
  `start_run_sequence` (per-step timeout, one Run record, `_ACTIVE` =
  current step's proc); CLI `--execute` runs all steps.
- **Registry:** `channelsplit` in user filtergraphs (multi-OUTPUT filter —
  `filtergraph_problems` accepts labeled outputs; unknown filters still
  rejected); `anullsrc`/`lavfi:` source specs pass through bare
  (`sources.input.lavfi`, `-f lavfi` before `-i`).
- **Tests:** `tests/test_stabilize.py` (19: NLU phrasing/neighbours/
  mode-conflicts/GPU-consume at parse level; plan shape, both argvs,
  trf-follows-output-stem, resize-lands-in-transform-only, all 8 conflict
  rejections, 5 allowed combos, pipeline steps, lavfi spec,
  channelsplit-in-graph; REAL-MEDIA two-pass E2E on a synthetic clip —
  detect writes the .trf, transform consumes it, output probed
  h264 320x240). `bench-068` flipped (stabilize is now ready); two M6 GPU
  tests retargeted to the bare-flag rule ("re-encode as h264 on the gpu";
  grayscale base for the toggle).
- **Suite:** **910 passed, 1 skipped on tr4 host ffmpeg 6.1.1 (99 s)**
  (skip = libvmaf E2E — no libvmaf in 6.1.1).

**Dual-build status:** ✅ 6.1.1 host / ✅ 9.0.1 container (i7 job
j_b55343a9be at commit aa0e534, image fass:m105-verify efc5f125149a):
910 passed, 1 skipped in 103.19 s; the sole skip is the node JS syntax
check (tests/test_api.py:311) and the libvmaf E2E RAN and passed (16.53
s, vendored model /opt/vmaf-models). **DUAL-BUILD VERIFIED** (evidence:
/home/andrei/.local/share/local-dispatcher/artifacts/j_b55343a9be/
attempt-1/pytest_full.log).

**Next:** M10.6 — per-category E2E verification battery + README /
DEVELOPMENT docs; then M11 RAG continues at M11.1 (retrieval API) — see
the Current state section above.

## Previous state (2026-10-26, M10.4 — comparison metrics, DUAL-BUILD VERIFIED: 6.1.1 host + 9.0.1 container)
**M10.4 — quality comparison: `psnr`/`ssim`/`libvmaf` against a reference
input.** A comparison is a standalone diagnostic analysis: the reference
clip is the second input (pad 1), the metric compares the two video streams
frame by frame, the summary lands in the run log — nothing is written
(the plan runs through the M10.2 diagnostic path: null muxer, loglevel
info).

**What shipped (uncommitted, with M10.3):**
- **Registry 2.4.0:** `psnr`/`ssim`/`libvmaf` carry `diagnostic: true` +
  `needs_reference: true`; libvmaf `min_version: "7.0"`.
  `comparison_filters()` loader accessor (the `needs_reference` table).
- **Plan schema:** `CompareOp` (type "compare", metric, reference asset
  id, options validated against the registry option schema) in the
  Operation union; at most one per plan; compare + filtergraph rejected
  at schema level.
- **NLU (rules):** triggers — compare/comparison/comparing verb,
  "quality loss/difference/degradation/drop/impact", or a bare metric
  word; metric-less → PSNR-vs-SSIM clarification (VMAF never offered
  unless named); reference binding reuses the combiner binder (named
  file, sole-candidate fallback, multi-candidate → which-file
  clarification, audio-only other asset → unsupported fragment, no
  candidate → bind hint); compare + ANY other edit → standalone-analysis
  clarification (planner enforces the same for API-built plans);
  compare into a non-video output → unsupported fragment; revision
  "instead measure ssim" replaces the previous compare op;
  length-preserving span blanking; SUPPORTED_SUMMARY clause.
- **Planner:** reference resolution (must differ from main, be a bound
  local VIDEO/SOURCE asset, probeable, have video) → probe both inputs;
  conflicts are plan errors (trim, watermark, audio_replace, subtitles,
  ANY other op, non-video output mode); a needs_reference metric on the
  plain `-vf` chain is an error (no reference pad); `_comparison_segments`
  builds the labeled graph — reference scaled to the main's size only on
  dimension mismatch (`[1:v]scale=W:H[c1];[0:v][c1]metric[cmp]`),
  fps/pix_fmt mismatches are WARNINGS only (converting would distort the
  measured signal); the labeled `[cmp]` pad is explicitly `-map`ped
  (filtergraph outputs are not auto-mapped — found by the E2E).
- **libvmaf model:** the filter's `model` option is a SPEC
  (`version=…`/`path=…`) — a bare path is rejected by the filter; the
  planner emits `model=path=<file>` from `$FASS_VMAF_MODEL` (full path)
  or `$FASS_VMAF_MODEL_DIR/vmaf_v0.6.1.pkl`. The 9.0.1 base image links
  libvmaf but ships NO model (verified: "could not load libvmaf model");
  `docker/models/vmaf_v0.6.1.json` vendored (official Netflix/vmaf model,
  19 KB SVM-over-6-features; JSON loads fine in libvmaf 3.0) + Dockerfile
  `COPY docker/models/ /opt/vmaf-models/` + `ENV
  FASS_VMAF_MODEL=/opt/vmaf-models/vmaf_v0.6.1.json`.
- **Tests:** `tests/test_comparison.py` (47: registry flags, CompareOp
  schema, plan shape/scale/fps/pix warnings/model env/conflicts/argv
  gate, NLU phrasings/clarifications/conflicts/revision, E2E psnr +
  ssim executed on lavfi clips asserting the metric line in stderr + no
  file written; libvmaf E2E gated on filter+model presence — skips on
  6.1.1, runs in the 9.0.1 image). `test_m101_transforms`
  presence-warning test now uses `blackdetect_vulkan` (libvmaf can no
  longer be a bare -vf filter).
- **Suite:** **891 passed, 1 skipped on BOTH builds** (845 baseline + 47
  new): host 6.1.1 (skip = libvmaf E2E — no filter in 6.1.1) and 9.0.1
  container (skip = node JS syntax check — no node in the image; the
  libvmaf E2E RAN and passed via the vendored model). The container run
  mounts the working tree at /host, so it double-verifies M10.3 AND
  M10.4 on 9.0.1.

**Dual-build status:** ✅ VERIFIED 2026-10-26 — host 6.1.1 891/1,
`fass:m104-verify` 9.0.1 891/1 (the image venv needs `pip install
pytest httpx` for suite runs; the nvidia base has QSV-absent, no node).
First container attempts failed on venv setup only (no pytest / no
httpx), which exposed two build-ASSUMING tests, now fixed build-agnostically:
`test_env_reports_qsv_coders` (asserted `present is True` — user-confirmed
tr4 = AMD CPU, so QSV is registered-but-unusable there; the 9.0.1 nvidia
image is built without QSV → `present` now cross-checked against a raw
`ffmpeg -encoders` parse) and `test_filters::test_presence_probe_covers_new_filters`
(asserted libvmaf+blackdetect_vulkan absent — real matrix: 6.1.1 lacks
both, 9.0.1 nvidia HAS libvmaf but NO vulkan at all → both cross-checked
against a raw `ffmpeg -filters` parse; probe set == raw set on both
builds). Also fixed `_libvmaf_works()` (the E2E gate's mini-run used bare
`libvmaf`, which fails to init without a compiled-in model — it now
resolves the model exactly as the planner does via `vmaf_model_path()`).
The i7 9.0.1 battery stays blocked (i7 offline since 2026-10-26); tr4
substitutes with `docker run` verification images.

**Next:** M10.5 — two-pass vidstab (design: ExecutionPlan is single-argv;
`start_run` is argv-only → a steps/passes plan shape), GPU filters
(yadif_cuda/scale_cuda phrasings; "with the gpu" currently only sets the
nvenc encoder flag), channelsplit-in-graph, anullsrc → M10.6 verification
+ docs → M11 RAG per `ffmpeg-assistant-rag-prd.md`.

## Previous state (2026-10-26, M10.3 — combiners)
**M10.3 — combiner filters: labeled `-filter_complex` graphs.** Nine
combiners are planned from natural language: video `xfade`, `concat`,
`hstack`, `vstack`, `overlay`; audio `acrossfade`, `amix`, `amerge`,
`join`.

**What shipped:**
- **Registry 2.3.0:** `concat` + `hstack` added (video 48→50, audio 36)
  with option schemas; all nine combiners carry the `multi_input` marker
  (2 = exactly two inputs, 0 = variadic); `overlay` gained its option
  schema (x/y/eof_action/eval/shortest/format).
- **Plan schema:** `combine` operation (op + inputs) in the EditPlan
  operations union; exactly one combiner per plan; conflicts with
  `filtergraph`/`filter` ops rejected at schema level; a new combiner
  revision replaces the previous one.
- **NLU (rules):** two-input phrasings — "crossfade A with B", "stack
  side by side / one above the other", "stitch/merge/concatenate",
  "overlay logo on top", "mix audio"; asset binding (name the second
  asset, or the only other bound asset; clarify when no candidate);
  per-combiner duration parsing (xfade transition length, acrossfade
  `d`); the main input is never a candidate for "the other input".
- **Planner:** extra inputs probed with `probe_media(require_video=False)`
  (ffprobe gained the kwarg — audio-only files are now probeable; default
  behavior unchanged). `_combiner_segments` builds the labeled graph:
  - **xfade:** `offset` = main duration − transition duration (computed
    from the probe, never asked); both pads normalized to the main's
    `fps=…,format=…,settb=AVTB` (6.x `fps` has no `format` option —
    separate `format` filter); size mismatch → extra scaled to the main's
    exact WxH + warning.
  - **concat:** pads in per-input order `[0:v][0:a][1:v][1:a]` (NOT
    all-videos-then-audios — empirically verified on host); extras scaled
    to the main's WxH on mismatch; audio streams dropped when no input
    has one, and the reencode chain adds an explicit `-an` then.
  - **hstack/vstack:** extras auto-scaled to the main's height/width
    (existing pattern, aspect preserved) + warning; **overlay:**
    `[0:v][1:v]overlay[…out]`; **acrossfade:** `acrossfade=d=…`;
    **amix:** `amix=inputs=N:duration=…`; **amerge/join:** multi-input
    merge with matching channel layout. Output maps pick up the graph
    labels (cv/ca); a combiner without audio maps no audio stream.
- **Tests:** `tests/test_combiners.py` (62: schema conflicts, NLU
  phrasing/binding/clarification, xfade offset math + normalization argv,
  concat pad order, hstack/vstack scaling, E2E execution on five
  generated fixtures — a.mp4 1920x1080@15 10s, b.mp4 1280x720 8s,
  c.mp4 1280x720 6s silent, portrait.mp4 720x1280 10s, tone.wav 5s —
  probing output duration/size/streams). `tests/test_filters.py`
  updated (combiner marker + 50/36 counts); `tests/benchmark/requests.json`
  bench-064 "Concatenate two clips": `unsupported` →
  `needs_clarification` (the correct new behavior — it now binds the
  second asset or clarifies).
- **Suite:** **845 passed** on tr4 host 6.1.1 (93 s; 783 baseline + 62).

**Dual-build status:** host 6.1.1 ✅. The i7 docker 9.0.1 E2E battery is
blocked — i7 (192.168.0.33) unreachable (no route to host; dispatcher
backend offline) as of 2026-10-26. Equivalent verification running on
tr4: same `Dockerfile` (base `jrottenberg/ffmpeg:9.0-nvidia2404`) built
as `fass:m103-verify`, full suite to run inside the 9.0.1 container.

**Next (M10.4):** comparison filters — `psnr`/`ssim`/`libvmaf` with a
reference input (plan-level second media), libvmaf model shipped into the
docker image; then M10.5 two-pass vidstab / GPU / anullsrc; M10.6
verification + docs; then M11 RAG per `ffmpeg-assistant-rag-prd.md`.

## Previous state (2026-10-25, M10.0 — filter library foundation, dual-build VERIFIED)
**M10.0 — registry-driven filter option schemas + presence probe + validation
wiring** (the M10.0 phase of the M10 filter-library plan in PROGRESS.md §4).

**What shipped:**
- **Registry 2.2.0:** 50 new filter entries (30 video + 20 audio) with
  per-option schemas — kinds choice/number/int/rational/bool/flagset/string,
  ranges, choice values — transcribed from `ffmpeg -h filter=NAME` on the
  tested 6.1.1 build (acompresor/blackdetect_vulkan/libvmaf authored from
  docs: absent from both tested builds). Category flags: `diagnostic` (10
  log-only filters), `multi_input: N` (amix/amerge/join/vstack 0,
  acrossfade/xfade 2), `needs_reference` (psnr/ssim/libvmaf), `gpu`
  (scale_cuda/yadif_cuda/blackdetect_vulkan), `min_version` (7.1 on
  blackdetect_vulkan). The pre-M10 34 entries keep their description-only
  shape (no schema → arguments pass through, as before).
- **Loader (`fass/registry/loader.py`):** `filter_specs()`/`filter_spec()`,
  `filter_options_problems(name, options)` (filter-side twin of
  `bsf_errors`, reuses `_validate_option_value`), `_parse_filter_args()`
  (named `key=value` + positional slots in ffmpeg -h declaration order,
  `enable` universal, duplicate/too-many/empty-argument detection) — wired
  into `_validate_instance`, so `filter_expr_problems`, the schema layer,
  filtergraph validation and the execute argv gate all validate options.
- **API:** `/v1/env` → `filters: {name: {present}}` for all 84 registry
  filters (probed per build, like bsfs); `/v1/support` → `filters.table`
  full option table + flags (mirrors `bitstream_filters`).
- **Tests:** `tests/test_filters.py` (12: registry shape, schema
  well-formedness, flags, valid/invalid option examples, name-only
  pass-through, expr wiring, positional/named mix, labeled graphs, argv
  gate accept/reject, presence probe + legend-word regression) +
  test_api env/support extensions. **683 passed** (670 baseline + 12 + 1).
- **UDP test fix:** `test_stream_output_udp_mpegts` recv thread kept alive
  until ffmpeg exits (the 1 s socket-timeout race failed on a loaded host —
  pre-existing flake, reproduced on clean 3634bf4).

**Dual-build verification (2026-10-25, i7):** host 6.1.1 suite 683 passed;
docker 9.0.1 rebuilt — `/v1/env` filters present: acompressor False,
libvmaf True, blackdetect_vulkan False, scale_cuda/yadif_cuda True,
showinfo True; `/v1/support` table 84 entries; argv-level E2E on
titan.mp4: `-af dynaudnorm=f=200:g=15` exit 0, `-filter_complex
[0:v][1:v]xfade=transition=fade:duration=1:offset=5` exit 0, `-vf drawbox`
exit 0; gate rejects `xfade=transition=bogus` and `astats=bogus=1` (422).

**Next (M10.1):** transform-filter NLU phrasing + planner wiring (the
registry table is already enforced by every layer); then M10.2 diagnostic
plan mode (`-f null -`), M10.3 combiners, M10.4 comparison (+libvmaf
model ship), M10.5 two-pass vidstab/GPU/anullsrc, M10.6 verification+docs.

## Previous state (2026-10-25, BSF — user-named bitstream filters, dual-build VERIFIED)
**BSF — user-named bitstream filters in stream-copy plans.** Trigger: user:
"next i will need you to work on bitstream filters, i need support of :
trim, trace_headers, showinfo, setts, prores_metadata, mpeg2_metadata,
hevc_mp4toannexb, hevc_metadata, h264_metadata, filter_units, av1_metadata,
aac_adtstoasc" + standing rule: both docker (i7 9.0.1) and python
(local 6.1.1) builds.

**Design:**
- `trim` is NOT an FFmpeg bsf — registry pseudo-entry (`copy_cut: true`):
  switches TrimSpec to stream-copy mode (`-ss`/`-t` + forced `-c copy`,
  keyframe-accurate video / sample-accurate audio); schema validator
  drops it from the list; every layer rejects `-bsf:v trim` with an
  explanatory message.
- 12 real bsfs registry-validated with per-option kinds (level = 22-value
  choice, flip = flagset, rotate = number, discard = choice,
  time_base = rational, …); auto bsfs (h264/hevc_mp4toannexb,
  aac_adtstoasc) unchanged; user bsfs chain AFTER the auto ones, one
  `-bsf:v`/`-bsf:a` flag each, options sorted + colon-joined
  (`h264_metadata=flip=horizontal:level=4.1`); duplicates of the auto
  bsf dedupe to one flag.
- Layers: NLU `_detect_bitstream_filters` (name + key=value tokens) →
  **length-preserving mask** so keyword detectors (flip, rotate,
  width/height, level) can't double-read bsf option tokens → schema
  `BitstreamFilterSpec` (+EditPlan max 8) → planner: re-encode conflict,
  device/subtitles conflict, audio-bsf-on-no-audio, codec match (ffprobe
  name → FFmpeg name), per-build min_version → execute `validate_argv`
  (name + option kinds, `-c copy` now accepted — the gate used to reject
  every copy plan).
- Per-build availability: `FfmpegInfo.bsfs` probed from
  `ffmpeg -bsfs`; `/v1/env` reports `bsfs: {name: {present}}`
  (showinfo = 6.1.1: False / 9.0.1: True); `/v1/support` exposes the full
  spec table. min_version warnings compare the PROBED local version
  (`local_ffmpeg_version()`, cached) — 6.1.1 warns "the local build is
  6.1.1, which predates it", 9.0.1 is silent (fix found by the live E2E).

**Changed (uncommitted, with M9):** fass/registry/{registry.json,
loader.py,__init__.py}, fass/schema/{plan.py,__init__.py},
fass/nlu/rules.py, fass/planner/planner.py, fass/execute.py,
fass/probe/ffprobe.py, fass/api.py, tests/test_bsf.py (13 → 48 tests),
docs/DEVELOPMENT.md (coverage row + checklist).

**Results:**
- Local 6.1.1: **670 passed** (635 baseline + 35). E2E: bsf chain
  `h264_mp4toannexb` + `h264_metadata=level=4.1` → mpegts, SPS level
  probed back as 41; copy-trim through the execution layer
  (`start_run`), 5.079 s out of a 20 s input.
- i7 docker 9.0.1 LIVE-VERIFIED (rsync + `compose up -d --build`):
  /v1/env showinfo present=True; gen with `showinfo` → NO 7.1 warning on
  9.0.1; bsf chain + copy-trim EXECUTED (exit 0), level 41 + 5.079 s
  confirmed by in-container ffprobe; artifacts cleaned.
- i7 host suite job **j_848d7f8076** (patch /tmp/fass-bsf-final.patch):
  **succeeded — 670 passed / ffmpeg 6.1.1** (PATCH applied, PYTEST
  "670 passed, 1 warning in 87.83s", 0 failed). Earlier job
  j_3c90b56228 (pre-min_version-fix patch) had returned 666.
- M9 i7 suite job j_cec61c3098: **635 passed / 6.1.1** — M9 now
  dual-verified as well (live chain E2E + host suite).

**Next unfinished tasks:**
1. ~~Commit M9+BSF~~ — DONE: **7ad1d2e** "M9+BSF: chained processings
   (/input/) + user-named bitstream filters" (17 files, +1493/-91),
   PUSHED on request: origin/main = **bdfb592** (3db9a64..bdfb592).

## Prior state (2026-10-25, M9 — /input/ output chaining, DUAL-VERIFIED)
**M9 — output-into-input-folder for chained processings.** Trigger: user:
"modify output file field in web interface, if filename is '/input/filename',
output will go into input folder, this will allow to do several ffmpeg
processings one after another" + "add help tip about /input convention in
output form, with mouseover css". Standing rule: both docker and python paths.

**Design:** logical, mode-agnostic — a `/input/<name>` output destination
means "write into the deployment's input folder": `resolve_paths` rewrites it
to `$FASS_INPUT_DIR/<name>` at run time. In the container the input folder
IS `/input` → no-op (literal works); local python maps to `$FASS_INPUT_DIR`;
no input dir → literal passthrough (ffmpeg errors clearly). Chain closes
because the next run's bare name already resolves against the input folder
(existing `resolve_input_name`/input rules — no change needed). Planner's
realpath collision guards stay (output may not equal the input file).

**Changed (uncommitted):**
- `fass/execute.py` — `resolve_paths` `/input/<name>` branch (+note); new
  `run_output_file(run_id)` — serves the recorded output of a run from
  whichever volume it landed in, guarded: real file, realpath inside the
  input OR output volume dir.
- `fass/planner/planner.py` — warning "output … lands in the input folder —
  later commands can reference it by bare name (chained processings)".
- `fass/api.py` — `output_filename` Field documents the convention; new
  `GET /media/run/{run_id}/output` (FileResponse).
- `fass/ui.py` — `?` badge on the Output label with pure-CSS hover tooltip
  (`.tip` + `:hover::after`/`::before`, `data-tip` attr, dark-mode aware);
  placeholder now `out.mp4, /input/step1.mp4, or rtmp://…`; Runs-table
  output link → run-scoped endpoint (also fixes the latent 404 where a
  resolved full-path `run.output` was percent-encoded into the
  basename-guarded `/media/output/{filename}`).
- Tests: 8 new (resolve_paths ×3: rewrite / no-input-dir / docker no-op;
  `run_output_file` guard ×5 cases; API media ×3 incl. traversal guard;
  real-ffmpeg chain e2e: run1 → `/input/step1.mp4`, run2 reads bare
  `step1.mp4` → `step2.mp4`).

**Results:**
- Local (6.1.1): **635 passed** (627 baseline + 8).
- i7 docker (container ffmpeg 9.0.1) LIVE-VERIFIED: files scp'd,
  `compose up -d --build`, healthz ok; chain test
  (/tmp/chain_test_i7.py): step1 "scale to 480p" +
  `output_filename=/input/chain1.mp4` → ready, argv tail `/input/chain1.mp4`,
  input-folder warning present; run1 success (output recorded
  `/input/chain1.mp4`); `GET /media/run/<id>/output` → 200, 1 651 686 B;
  step2 "scale to 240p" + `main=chain1.mp4` (bare) → run2 success,
  `/output/chain2.mp4`. Host files confirmed in docker-data/{input,output},
  test artifacts cleaned.
- i7 suite job `j_cec61c3098` (cc-worker, backend i7, base 3db9a64, patch
  /tmp/fass-input-chain.patch pre-staged): **succeeded — 635 passed /
  ffmpeg 6.1.1**.

**Next unfinished tasks:**
1. ~~QUEUED (user): bitstream filters~~ — DONE, see "Current state" above.
2. Commit M9+BSF (Co-Authored-By) + push only when asked (see top
   section's task list — that one governs).

## Prior state (2026-10-25, M8 COMPLETE — codec options)
**M8 — per-encoder private codec options, core DONE, uncommitted.** Trigger:
user: "add all options of encoders / decoders: NVENC, QSV, ProRes, dvbsub,
png, libx265, libvvenc, libfdk_aac, flac, opus, ac3, rawvideo" (link to
ffmpeg-codecs.html#Codec-Options).

- **Registry:** `codec_options` table in registry.json — 16 encoders, 344
  options (h264_nvenc 50, hevc_nvenc 54, h264_qsv 45, hevc_qsv 44, libx264
  48, libx265 11, prores_ks 6, png 3, dvbsub 1, libvvenc 10, ac3 20, eac3
  18, libfdk_aac 13, flac 11, libopus 8, opus 2). Kinds int/float+range,
  choice(values, optional numeric range), string; `builds` tag for
  build-gated options (libfdk_aac* 9.0.1-only, dvbsub.min_bpp 9.0.1).
  rawvideo intentionally has NO table (no private options).
- **Loader:** codec_option_specs / codec_option_errors (unknown key, range,
  type, choice), video_/audio_codec_option_keys families.
- **Plan model:** OutputSpec._capability validates video/audio
  codec_options against the resolved encoder; conflict messages:
  gpu×qsv, rc=cbr* needs bitrate, bitrate×constant-quality (crf/cq/qp),
  video_preset×'preset' option, video_profile×'profile' option;
  audio options validated vs explicit audio_codec or the container
  default codec.
- **Planner:** emits options for their encoder (forces re-encode when a
  stream copy would drop them — the M6 copy-mode discipline); QSV CBR
  default 5000k; QSV copy-mode warning; QSV has no CRF → honest "no
  CRF-style knob" default note.
- **NLU (rules + llm):** phrasings 'with crf 18', 'preset=fast',
  'x264-params keyint=24' (space + key=value forms, restricted param
  family — no false positives), 'dialnorm -27', 'vbr 4', fdk_aac
  phrasings; invalid values → unsupported with the loader's message.
- **execute.py:** codec options accepted by the argv validator only for
  the encoder named in the same argv; wrong-encoder/off-range → error.
- **API:** /v1/support exposes codec_options + qsv.coders; /v1/env rows
  for the QSV coders.
- **Tests:** tests/test_codec_options.py — 39 new tests, all green.
  **Suite: 627 passed (~78 s).**
- **Battery:** scripts/m8_battery.py (+m8_battery.sh wrapper) — 10-case
  dual-build driver on a 10s titan clip: generation (status + exact argv
  tokens) + real execution. Local 6.1.1: **10/10** (QSV executes clean
  here — host has the device; fdk_aac skipped with the preflight warning).
  Docker 9.0.1 (i7): **10/10** (fdk_aac EXECUTED there — 9.0.1 has
  libfdk_aac; QSV cases SKIP, no QSV device in the container — expected).
- **Known accepted gaps:** 'rc cbr 10000k' space form not parsed
  (key=value works); vvenc/png/rv60 are reference-only tables (not
  selectable matrix codecs); libvvenc absent from both builds.

M8 core committed as **133a189**. Dual-build verification (2026-10-25)
**COMPLETE — both jobs succeeded, VERIFIED**:
- **j_69d3f78ec0 (i7, docker 9.0.1) = VERIFIED.** rsync local→i7 (exit 0,
  fass/tests/scripts md5 46fe02a485f4afaa5113fccc5e35d7f0 both sides);
  compose docker-mgmt lines uncommented; `launcher.sh rebuild` exit 0,
  containers Up; healthz ok; battery **10/10 (docker)** — fdk_vbr4
  EXECUTED (9.0.1 has libfdk_aac), QSV cases SKIP (no device in container);
  /v1/support = exactly 16 codec_options encoders; /v1/env 9.0.1 +
  libfdk_aac present. i7 is now running M8 (independently re-checked live).
- **j_c7320e8dfe (tr4-secondary, local 6.1.1) = VERIFIED.** battery
  **10/10 (local)** — 9× PASS (QSV executes, host has device) + fdk SKIP
  (no encoder, preflight warning); full suite **627 passed** in its
  worktree.
No integration needed: both jobs verification-only (commit null, no
changed files, worktrees pristine). Pre-rsync i7 reconciliation (kept for
the record): its M2-era .git commits ccc7066/8d00a22 were verified
functionally already ported locally (cli.py:212, ui.py:272); its tree =
local M7 exactly; backup incl. .git at
i7:/dd2/andrei/fass-backup-pre-m8-20261025.tar.gz.

## Prior state (2026-10-24, M7 IN PROGRESS — capability expansion)
**M7 — broad FFmpeg capability expansion, half done.** Trigger: user report
"'extract audio track and transcode it to mp2 audio 128kbps' returns 2 errors
(audio-only not supported; mp2 not in the matrix) + mxf unsupported → why can't
you add support of ALL audio and video containers". Full scope (user messages):
audio-only output, video-only output, subtitle dump (.srt/.vtt/.ass), ProRes/AVI/
MXF + full common container set, audio+video filters + arbitrary filtergraph,
SRT protocol input, HTTP/HTTPS output, full mpegts/HLS/muxer support, all
ffmpeg `formats` page format-options. Standing instruction: every feature
verified in BOTH the local Python checkout (ffmpeg 6.1.1) AND the i7 docker
deployment (jrottenberg/ffmpeg 9.0.1, live after rebuild).

**Done, committed as 5afac91 (local only — main is 2 commits ahead of
origin/main: 08c949e, 5afac91; no push unless asked):**
- Schema: `OutputSpec.mode` video|audio|subtitles (+ `_mode_consistency`);
  container literal 30 values (video: mp4/mov/mkv/webm/mpegts/flv/avi/mxf/vob/
  asf/3gp/dv/gif/webp/hls + stream rtsp/srt; audio: mp3/mp2/m4a/aac/ogg/flac/
  wav/aiff/mka/opus; dump: srt_file/vtt_file/ass_file); video_codec +
  prores/mpeg2/mpeg4/mjpeg/dv/gif/webp; audio_codec + mp2/ac3/eac3/alac/vorbis/
  pcm_s16le/s24le/f32le; `format_options: dict[str,str]` validated per registry
  kind; `FilterOp` (domain video|audio) + `EditPlan.filtergraph` (≤4096);
  per-codec profile/pixel_format validated at schema level via
  `capability_errors` (mode-aware, duck-typed).
- Registry: matrix rows for avi (mpeg4/pcm_s16le), mxf, vob, asf, 3gp, dv, gif,
  webp, hls + every audio container; flagsets for mp4/mpegts/avi/flv/mp3/wav/
  hls (+faststart, +use_metadata_tags, hls_time, …); 28 containers; srt added
  to streaming.input + input_format srt→srt; output http/https (container from
  URL extension, default mpegts); subtitles dump_codecs (srt/webvtt/ass);
  filter allowlists: 18 video + 16 audio.
- Planner/builder: mode-aware plans — audio: `-map 0:a:0` + `-af`, no video
  maps; subtitles: `-map 0:s:0`, NO codec flags (muxer decodes AVSubtitle
  itself); PCM/lossless audio codecs never emit `-b:a`; filtergraph forces
  re-encode, labeled output pads → explicit `-map [label]`; format options →
  pre-output flags with dedup.
- Verified argv on media/titan.mp4 (smoke, real commands): user's exact case →
  `ffmpeg -hide_banner -loglevel error -i titan.mp4 -map 0:a:0 -c:a mp2 -b:a
  128k out.mp2` (ready); prores mov (`-c:v prores_ks -profile:v standard
  -pix_fmt yuv422p10le`), avi mpeg4 (no `-b:a`), mxf, hls (`.m3u8` + `-f hls`),
  video-only (`-an`), http output (`-f mpegts http://…`), srt input (`-f srt`).
- Suite GREEN: **534 passed** (baseline 533; 7 capability-widening failures
  fixed across bench-077, test_planner_builder ×4, test_schema ×3).

**Done, committed as 4c0a0ed (MY track): NLU rewrite COMPLETE.**
- fass/nlu/rules.py (~1900 lines): SUPPORTED_SUMMARY expanded; `_detect_codec`
  +prores/mpeg2/mpeg4/mjpeg; full-registry container detection (AVI no longer
  rejected; audio containers switch the plan to audio mode; subtitle dump
  containers to subtitles mode); `_NATURAL_VIDEO_CONTAINER` {prores→mov,
  mpeg2→mpegts, mpeg4→avi} — a codec lands in its natural container;
  UNSUPPORTED_PATTERNS trimmed (flip/mirror, volume/louder/quieter,
  extract-subtitles, blur/fade, sharpen, text-overlay, extract-audio all now
  supported); bitrate split video (100–200000) vs audio (32–3840, audio
  context only); lossless → prores (video) / flac (audio), not clarification;
  new detectors: audio-only, subtitle dump (before burn-in detection),
  audio-codec, named filters → FilterOp (hflip/vflip/boxblur/unsharp/
  drawtext/fade/eq/volume/loudnorm/afade/…), raw filter exprs +
  `filtergraph=`, format options (faststart, hls_time, resend_headers);
  mode-aware parse() + _merge_revision; final check via capability_errors.
  No-op gate: a stream `dest` alone IS an edit (dest is None in the gate).
- Test conversions for the widened behavior: 5 Python tests (test_rules ×3,
  test_subtitles ×2) + 4 benchmark expectations (bench-050/060/062/069) now
  assert M7 behavior (prores→mov, mp3 audio-mode extraction, avi/mpeg4,
  unquoted-text overlay → clarification). **Full suite green: 534 passed.**

**Worker job j_41200a56f4 (i7) COMPLETED — integrating:** commit **d4938a6**
on dispatch/j_41200a56f4 (base 07c7c17; merge-base with main = 5afac91). 8
files +521/−48: fass/api.py (/v1/support full M7 surface), fass/execute.py
(_MUXERS from all_muxers(), _check_value filtergraph + format-option
validation), fass/pipeline.py (per-mode preflight), fass/probe/ffprobe.py
(muxer probe — 6.1.1 lacks the aac ADTS muxer, 9.0.1 has it → probe-driven),
fass/explain.py (mode-aware; strictly better than my discarded quick fix —
adds filter-op lines, subtitle_dump_codec, mode-aware trim/speed, PCM
lossless wording), + 21 tests (test_api, test_m6_execute,
test_planner_builder). Their run: 555 passed (on the old rules.py — first
end-to-end combination of my mode plans with their explain/preflight happens
post-merge). Zero file overlap with my commit → clean merge. My local
explain.py quick fix reverted (git checkout HEAD).
**INTEGRATED:** merge commit **43b1182** on main (parents 4c0a0ed + d4938a6),
full suite **555 passed** (534 + 21 worker tests) — first end-to-end
combination of my mode plans with their explain/preflight, green.
`dispatch_integrate(j_41200a56f4, 43b1182)` ACCEPTED.

- **LOCAL REAL-FFMPEG VERIFICATION (6.1.1) — 21/21 PASS.**
  /tmp/verify_m7_local.py drives generate() (rules) on media/titan.mp4 + a
  subtitle-bearing copy (/tmp/fass_m7verify/titan_subs.mp4), executes the
  argv, inspects outputs with ffprobe. Log: /tmp/fass_m7verify/local_run2.log.
  Covers: mp2-128k @exactly 128000bps, m4a/flac/wav, prores@86.6Mbps in mov,
  avi mpeg4+pcm, mxf h264+pcm_s16le, hls (5 segs/2s) + hls_time=6 (4 segs/6s),
  mpeg2-ts, mpeg2+mp2 @192k, video-only, vflip/volume/loudnorm, faststart
  (moov@36<mdat), filtergraph split (2 video streams), dump srt/vtt/ass
  (.ass muxer EXISTS on 6.1.1 — empirical), aac ADTS copy. Bugs found+fixed
  by this run (in the working tree, uncommitted; suite 588 passed):
  (a) video-mode kept-audio branch ignored edit.output.audio_codec
  (planner.py: emits the named codec + bitrate now; copy condition + warning
  split; pipeline.py preflight checks the same codec — regression test added);
  (b) mxf: `-c copy` fails on 6.1.1 (h264 arrives avcc-packaged; aac audio
  rejected by the muxer — pcm_rechunk is PCM-only). Fix is registry-driven:
  containers.mxf gains "reencode": true (planner forces a re-encode +
  default note), matrix mxf audio narrowed to pcm_s16le/s24le/s32le
  (aac/ac3/eac3 removed — empirical on 6.1.1: mpeg2/prores/h264 re-encoded
  video all mux; mpeg4 video rejected; pcm audio works, aac does not).
  "convert to mxf" now: libx264 + pcm_s16le, rc=0, 20.04s output;
  (c) .aac container muxer was "aac" but FFmpeg's muxer is "adts" ('aac' in
  '-formats' is demux-only) — preflight false-failed .aac on 6.1.1. Registry
  muxer renamed to "adts"; 3 tests rewritten/added.
- **DOCKER (9.0.1) REAL-FFMPEG VERIFICATION — 21/21 PASS.**
  /tmp/verify_m7_docker.py (same battery, container paths /input/*) run
  inside the fass container via `docker exec fass python`. Log:
  i7:/tmp/fass_m7verify_docker.log. First run 20/21: mxf rc=176 —
  ffmpeg 9.x's mxfenc index table requires frame offsets within ±127,
  x264's default GOP 250 fails at the trailer ("Error writing trailer:
  Not yet implemented in FFmpeg"); 6.1.1 tolerated it. Fix: containers.mxf
  gains `"gop": 120` + planner emits `-g 120` for non-GPU h264 (mpeg2's
  default GOP is already small; prores takes no GOP option) + default
  note. Re-ran: 21/21, mxf rc=0 (h264+pcm_s16le). Local 6.1.1 regression
  with the cap: rc=0, unchanged.
- **i7 DEPLOYED + LIVE-VERIFIED (2026-10-24).** rsync → re-sed compose
  opt-ins → `./docker/launcher.sh rebuild` → healthy. Containers: fass
  **43fe89fee86d**, fass-worker **63eaa25d46d0** (both recreated at this
  rebuild; e61ea65d558a/69d06f0a091b stale). No stale bare-python
  `fass serve` on i7 — the host ps entries are the containers' processes.
  Live API smoke (POST /v1/commands, 6/6 ready): the user's exact
  mp2-128k instruction (with the typo), mxf (libx264 -g 120 pcm_s16le),
  hls_time=6, subtitle dump, vflip+volume, srt:// stream input
  (`-f srt -i srt://…`). End-to-end /v1/runs (FASS_ALLOW_EXECUTE=1):
  mp2-128k run → status success, out.mp2 downloadable via
  /media/output, 320256 B = exactly 20 s @ 128 kbps.

**Done after integration (uncommitted):**
- llm.py M7 sync (LLM-adapter fallback prompt): SYSTEM_TEMPLATE + _system —
  filter op in the ops list; output modes documented (audio: audio container +
  audio_codec + audio_bitrate_kbps, video_codec null; subtitles: srt_file/
  vtt_file/ass_file, no codec flags); audio_mode remove (video-only);
  format_options with the full registry key list (rendered from
  REGISTRY['format_options'], flagset/hls_flags join semantics); filtergraph
  verbatim rule + conflict clause; 39-filter allowlist (18 video + 22 audio)
  rendered from REGISTRY['filters'] with example exprs + common-effect map
  (hflip/vflip/volume/loudnorm/fade); stream output now lists http/https with
  container-from-URL-extension rule (default mpegts); unsupported examples
  rewritten to the real M7 set (concat, split-into-segments, sepia/vintage/
  retro, denoise, stabilize, HDR, CUDA/VideoToolbox/QSV, out-of-range);
  "lossless vs high quality" dropped from clarification examples; +2 examples
  (mp2-128k audio extract — the user's driving case; subtitle dump).
  **Full suite: 585 passed.** (Prompt renders 32 KB; parse() already passes
  "Input duration" to the model, so fade-at-end guidance works.)
- Filtergraph extraction bugfix in rules.py: old regex captured only from the
  first `[` (dropped a leading unlabeled filter: 'split[a][b]' → '[a][b]') and
  swallowed a pasted closing quote (→ validator saw "'"). New
  `_extract_pasted_graph`: maximal run of filtergraph chars requiring a pad
  label AND a filter name outside the labels; strips surrounding/odd paste
  quotes; prose like 'the [0:0] stream' is left alone.
- 30 new test cases appended to tests/test_rules.py (28 functions,
  parametrize ×3 on dump formats): user's exact mp2-128k case (typo and all),
  m4a, lossless→flac, wav→pcm, prores→mov, avi→mpeg4, mxf, hls, mpeg2 ts,
  mpeg2+mp2, video-only (2 phrasings), dump srt/vtt/ass, vflip, volume,
  loudnorm, raw expr, pasted graph quoted/unquoted/complex, graph-vs-edit
  conflict, fade-out from duration, faststart, hls_time, http destination,
  srt stream input (no missing-file warning), h264+aac, song.mp3 replace,
  audio-mode filter retention. **Full suite: 585 passed.**

**Next unfinished tasks:**
1. ~~i7 docker 9.0.1 battery~~ — 21/21 (see above).
2. ~~i7 deploy + live verification~~ — done (see above).
3. Commit the uncommitted M7 battery + graph fix + llm.py sync + this window's
   planner/pipeline/registry fixes (Co-Authored-By; no push unless asked).
4. Final user reply answering "why not ALL containers": registry-driven
   architecture — container/codec/format-option/filter support is DATA in
   fass/registry/registry.json, not code paths; adding a container is adding
   a row. The added set is the practical full common set supported by BOTH
   installed ffmpeg builds (6.1.1 + 9.0.1), verified live on both.

## Prior state (2026-10-24, M6 COMPLETE — committed 2344be3)
**M6 (docker packaging) — code, tests, i7 deploy, Apply E2E, docs all DONE.**
User request: package fass+ffmpeg in docker (jrottenberg/ffmpeg:9.0-nvidia2404),
config tab (volume + stream-port mapping), streams in/out, GPU transcoding,
LLM config (strata on i7 for testing), execution of generated commands,
everything in a `docker/` folder. Delivered:
- `fass/config.py` — FassConfig pydantic model (llm/gpu/streaming/volumes,
  extra=forbid), $FASS_CONFIG → /config/config.json → ./.fass-config.json,
  reloaded per request.
- `fass/execute.py` — FASS_ALLOW_EXECUTE gate; argv-only (shell=False);
  validate_argv whitelist vs the builder's flag vocabulary; volume path
  resolution (bare name → /input or /output, subtitle filter re-escape);
  runs as sidecar JSON + .log in FASS_RUN_DIR; process-group kill;
  traversal-guarded /media/output.
- `fass/dockerctl.py` — raw Docker Engine API over the unix socket
  (FASS_DOCKER_MANAGE gate); recreate() with rollback; compose preview.
- `fass/ui.py` — 3 tabs: Command / Runs / Config (volumes, stream ports,
  GPU, LLM provider+host+port+key+model with Test button, Apply).
- `docker/` — Dockerfile (FROM jrottenberg/ffmpeg:9.0-nvidia2404, venv,
  FASS_ALLOW_EXECUTE=1 baked in), entrypoint.sh (seeds strata-default
  config.json on first boot), compose.yaml (volumes, 8000, nvidia device
  reservation, commented socket + stream ports), README.md (setup +
  streams + GPU + execution security model).
- GPU: `OutputSpec.gpu`; h264_nvenc/hevc_nvenc, `-rc vbr -cq` quality,
  bitrate → CBR-style; planner rejects VP9+GPU, x264 preset+GPU,
  baseline+GPU; copy+GPU → warning. LLM: openai-compatible provider
  (strata/vLLM/llama.cpp), thinking-model retry (max_tokens 2048→16384).
- Stream host rewrite (pipeline._apply_stream_hosts): placeholder `host`
  → 0.0.0.0 (udp/rtp listen) or bridge gateway (pull/push), only when
  streaming.enabled.
- Tests: 7 new files (test_m6_{config,gpu,stream,execute,llm,api,worker}.py,
  78 tests; real ffmpeg runs incl. stop-kill). **Full suite 530 passed.**
- Bugs the new tests caught (all fixed): execute.py `_KNOWN_FLAGS` typo
  (NameError on every run), llm.py `self.timeout` never stored,
  LLMConfig.port `ge=1` vs default 0 (saved config silently dropped on
  reload → port now `int | None = None`), rules.py UNSUPPORTED_PATTERNS
  still flagged "gpu" (MVP-era), `gpu is None` in the no-op check let a
  bare "use the gpu" return a no-op copy plan (removed — gpu alone
  clarifies), execute `_watch` marked a self-killed run "error" (now
  "stopped" via a _STOPPING set).
- i7 deploy DONE (2026-10-24): rsync → re-sed the compose opt-ins
  (socket mount + FASS_DOCKER_MANAGE=1 on BOTH services — the repo file
  keeps them commented as an opt-in) → `docker compose -f
  docker/compose.yaml up -d --build` on 192.168.0.33. E2E verified:
  healthz, config tab (docker{manage,worker,version,container}),
  strata LLM (qwen3.8-27b via 172.17.0.1:8080) → ready h264_nvenc plan,
  real GPU run on titan.mp4 → success (5.2 MB out), execution validation
  gate live (hand-rolled argv with `-y`/`-c` → 422 refusing to run).
- **Apply (container self-recreate) — sidecar architecture, E2E ×2.**
  Self-swap is impossible: the process answering Apply runs inside the
  container being stopped (uvicorn graceful-shutdown ↔ stop deadlock →
  Docker SIGKILL 137). So `fass-worker` (same image, `command: ["worker",
  "--port", "8001"]`) owns the swap: fass forwards POST /v1/config/apply
  to the worker's /swap → worker acks immediately (202, background
  thread) → stop-old → delete → create+start → 60 s running-poll →
  outcome in /config/last-apply.json → UI polls /v1/config/apply-result.
  Rollback: on failure the old container is re-created from the spec
  captured pre-swap (best effort). Without the sidecar: 409, compose
  preview applied by hand.
- Bugs found+fixed during this window: (1) `import json` missing in the
  api.py apply route; (2) `worker` missing from cli.py main() dispatch
  tuple → worker crashed on i7 with "unrecognized arguments: --port";
  (3) rsync clobbers i7's sed opt-ins — re-sed after EVERY rsync;
  (4) socket bind lost after the first Apply — recreate() rebuilt Binds
  from config only; fixed with `_unmanaged_binds()` (carries over the
  socket + any user-added mounts; regression test added).
- Current i7 containers: fass **e61ea65d558a** (recreated by
  `launcher.sh rebuild` after the UI fix below — fresh image, same
  config), fass-worker up;
  both healthy; docker status manage/worker true, engine 29.7.2.
  Apply result: ports {8000/tcp, 5000/udp}, 4 managed volumes + socket
  bind, gpu true (DeviceIDs ["1"]).
- **GPU pinning (2026-10-24):** i7 runs strata (LLM) on GPU 0 (RTX 4080
  Super) — fass docker is pinned to **GPU 1 (RTX 4060 Ti)** via
  `gpu.device="1"` in the Config tab + Apply (3rd apply: 2f5be4a87f2a →
  597bf50e2716). Container sees exactly one GPU (`DeviceIDs:["1"]`,
  re-indexed as CUDA 0 inside) so `h264_nvenc` can never touch the LLM's
  card. Verified: `nvidia-smi -L` in-container → 4060 Ti only; nvenc run
  exit 0. Stream E2Es also verified same day: **in** — push mpegts →
  `udp://127.0.0.1:5000` (mapped), plan binds `udp://0.0.0.0:5000`, run
  success, 4.86 s output; **out** — "stream it to `udp://host:9999`" →
  plan rewrites host→`172.17.0.1` (gateway) + injects `-f mpegts` +
  remote-endpoint disclosure, container pushes live, host receiver
  captured 7.98 s / 2.4 MB.
- **`docker/launcher.sh` (2026-10-24):** user-facing start/stop for the
  i7 deployment — `start` (no-op if healthy, else `up -d` + healthz wait)
  + fires `google-chrome` at the GUI (http://127.0.0.1:8000;
  `FASS_GUI_URL`/`FASS_BROWSER` overrides, graceful no-DISPLAY message
  for ssh); `rebuild` = `up -d --build`; `stop` = `compose stop`
  (state/volumes kept); `status` = containers + healthz.
- **compose.yaml now pins the GPU too** (`device_ids: ["1"]` on the
  nvidia reservation): before this, the pin existed only in the worker's
  Apply-created container, so a fresh `compose up` (e.g. after `down`)
  would have re-granted all GPUs and nvenc could hit the LLM's card.
  Both creation paths (Apply from config `gpu.device` and compose
  `device_ids`) now agree on GPU 1. Verified: `compose stop`→`up -d`
  recreated fass (69d06f0a091b) with `DeviceIDs:["1"]`, 4060 Ti only,
  manage/worker still true.
- **Launcher E2E tested on i7 (2026-10-24, 26772d9):** full pass over
  ssh — status (up + healthz ok) → stop (state kept) → cold start
  (healthy; "no graphical session (DISPLAY/WAYLAND_DISPLAY)" hint — i7
  sits at the Wayland login screen, no user desktop session) →
  idempotent start ("already up and healthy") → browser-fire path
  proven with a stub browser + fake `DISPLAY`: exactly one argument,
  `http://127.0.0.1:8000` → `rebuild` (fresh image, both containers
  Recreated, healthy, GPU pin intact: `DeviceIDs:["1"]` → 4060 Ti only
  in-container). The test exposed a real bug, now fixed in 26772d9:
  the guard only checked `DISPLAY`, but GNOME Wayland sets
  `WAYLAND_DISPLAY` — Chrome would have never fired on i7's actual
  desktop. Real Chrome fires when the user runs the launcher from a
  desktop terminal after logging into i7.
- **UI bug fixed (2026-10-24): "clicking Config does NOTHING".** The
  whole web UI was dead, not just the Config tab. Root cause: the
  confirm() dialog in `fass/ui.py` contained a `\n` inside a JS
  single-quoted string; Python interpreted that escape while building
  `UI_HTML`, so the browser received a real line break inside the
  string → `SyntaxError: Invalid or unexpected token` → the entire
  `<script>` block was rejected → `showTab`/`submit`/`saveCfg`/
  `applyCfg` all undefined. The UI had been broken since M6 shipped.
  Reproduced with headless Chrome over CDP on i7 (pageJsExceptions
  caught it; `node --check` on the Python source had passed because
  the source still holds the two-character `\`+`n` escape). Fix:
  `\\n` in ui.py (one backslash — the only one in the file).
  Regression test `test_ui_script_is_valid_javascript` syntax-checks
  the SERVED bytes with node (skipif no node) + asserts the confirm
  line is one physical line. Verified live in real Chrome: tabs
  switch, /v1/config populates, Apply enabled, 0 JS exceptions,
  screenshot clean. Deployed via rsync + compose opt-ins + rebuild.
- **Bare-input volume resolution (2026-10-24): "input file was not found"
  false warning.** User report: typed `titan.mp4` (present in the /input
  volume) into the UI's input field → Generate warned "input file was not
  found on this machine", although a run worked fine (file transcoded).
  Root cause: `execute.resolve_paths` resolves bare filenames against
  $FASS_INPUT_DIR at RUN time, but the pipeline's probe + not-found
  warning checked the raw relative path — so inside the container the
  file was never seen. Fix: new shared helper
  `execute.resolve_input_name()` (same rule as the run-time resolution;
  stream URLs / source specs / non-bare names / no-match pass through)
  used by `pipeline.generate` for BOTH the ffprobe candidate and the
  warning check. Command argv still references the bare name (executor
  resolves at run time — unchanged). Tests:
  `test_resolve_input_name_matches_run_time_rule` (execute unit) +
  `test_bare_input_resolved_against_input_volume` (pipeline: no warning
  + `validation_level=="preflight"` proves the probe happened).
  **533 passed.** Deployed to i7 (rsync + re-sed + rebuild) and verified
  live: Generate with "titan.mp4" → ready, `warnings: []`,
  `validation_level: preflight`.

## Prior state (2026-10-23)
- Pipeline, schema, registry, planner, builder, rules+LLM adapters, CLI,
  probe, feedback: COMPLETE and smoke-verified.
- API (new): fass/api.py — create_app() + module-level app; POST /v1/commands
  (CommandRequest: instruction, assets id→local path, output_filename,
  target_shell Literal["posix"], previous_plan, media ffprobe JSON, adapter,
  preflight); domain outcomes (ready/needs_clarification/unsupported/error)
  are structured 200s, malformed requests 422; GET /v1/support, /v1/env,
  /healthz, / (self-contained UI, no external assets). No subprocess in the
  API — PRD §10: the service never executes.
- serve subcommand added to cli.py ("serve" in main() routing tuple +
  handler; --host defaults 0.0.0.0 — all interfaces, per user request —
  --port 8000; LAN reachability smoke-verified).
- BUG FIX found via API tests: probe_ffmpeg() filter parsing used a substring
  test (`line.split()[0] in "TS.-"`) that only matched "TS." flag rows and
  captured description words. Now: first column must match ^[TSC.-]+$ and
  second column != "=" → 555 real filter names. CLI `env` + preflight
  filter checks are fixed by the same change.
- Note: adapter "auto" picks the LLM adapter when ANTHROPIC_API_KEY is set
  (here: an invalid key → structured status "error" with the provider error,
  never a 5xx). API tests pin adapter="rules" like the CLI gen fixture.
- Delegated via dispatcher (project `fass`, team mode, base 3bd8a76):
  - Job A `j_c58bc3ed95` (backend i7): INTEGRATED, merge cdd3f6d,
    dispatch_integrate accepted. 78-request benchmark + 5 rules.py bug fixes
    (range units, 'keep' trim verb, crop/stabilize regexes, revision negation
    precedence). Diff reviewed line-by-line.
  - Job B `j_95829e9a84` (backend tr4-secondary, 140B iq3xs): work INTEGRATED
    as merge 865a891 (tests/test_media.py, 219 lines, 10 tests), but the
    dispatcher records the job as FAILED (failure_class invalid_result: the
    worker's result.json used outcome "success" instead of "completed" and
    descriptive test-status strings). dispatch_integrate refuses failed jobs,
    so the bookkeeping stays failed/pending while the code is on main.
    Lesson: spell the exact result.json enum values in future job prompts
    (the iq3xs 140B writes good code but sloppy result JSON).
- media/titan.mp4 (6.3MB, 1280x720 h264+AAC 20.08 s, user-provided) committed;
  tests/test_titan.py (ab0dbdc) runs generated argv on it and probes outcomes.
- BUG FIX (8d00a22): web UI Validation line read c.check/c.status, which don't
  exist on check objects ({name, ok, detail}) → "preflight — undefined:
  undefined; …". Now renders ok/FAIL per check like the CLI; regression test
  test_ui_validation_line_uses_check_fields. 179 tests.
- FEATURE (bitrate + MPEG-TS, user report: "cbr 10Mbps mpeg ts" was misparsed
  as an exact-file-size request): `OutputSpec.video_bitrate_kbps` (100–200000,
  None = quality preset); registry 1.1.0 adds the mpegts container
  (ts/mts/m2ts; h264/h265+aac, no -movflags) + bitrate_policy. Rules: bit
  units (mbps/kbps/…) now parse to a bitrate, `_SIZE_TARGET` is byte-units
  only (file-size targets still clarify); mpegts detected from "mpeg ts"/
  ".ts"/"transport stream"/mts/m2ts. Planner: a bitrate forces re-encode
  (never stream copy) and emits `-b:v Nk -maxrate Nk` (+ `-bufsize 2Nk` for
  h264/h265; libvpx-vp9 gets no -bufsize). Verified on titan.mp4: the
  user's exact instruction → ready, and a 10 s CBR 10 Mbps .ts output is
  ~12.5 MB as the target demands. LLM prompt + SUPPORTED_SUMMARY + README
  + /v1/support (registry-driven) updated; 197 tests (was 179: +6 rules,
  +2 schema, +5 planner, +3 benchmark, +1 API, +1 titan outcome).
- **M3a COMPLETE (2026-10-23) — multi local typed inputs + audio replacement**
  (user green-light "continue"; design from M3 vision split). `AssetKind`
  gains AUDIO + `guess_kind` audio extensions; `AudioReplaceOp{asset_id}`
  (at most one) replaces the output audio with the first audio stream of
  another bound input. Planner: inputs fixed main=0 → watermark=1 → audio
  last (build_video_chain hardcodes watermark as [1:v]); maps
  `{audio_idx}:a:0` (replacing `0:a?`) + `-shortest`; encoded to the
  container audio codec (aac 160k / opus 128k webm) — never stream-copied,
  so the op forces re-encode; collision check extends to the replacement
  audio file. Rules: `_AUDIO_REPLACE` regex (replace/swap/change/set/put +
  use/take "as/for the audio"), asset binding by id/filename/label with
  sole-audio-asset fallback (noted in defaults); no audio asset bound →
  needs_clarification; "remove the audio" on a plan with a replacement
  removes the op; a bound audio file alone never implies replacement.
  CLI unchanged (`--asset id=path`); API assets dict auto-types by
  extension; UI gained "+ add input" rows (ids in1, in2, …). explain + LLM
  doc line + registry 1.2.0 updated. Real-media outcome verified: 15 s clip
  + 3 s 880 Hz sine → output ≈3.0 s (-shortest), dominant freq 879.75 Hz
  (content is the replacement, not the source tone).
- i7 (192.168.0.33) deployment: whole project rsync'd to the same path
  /dd2/andrei/fass (fresh .venv, pydantic/fastapi/uvicorn/httpx/pytest);
  `fass serve` running detached (setsid nohup) on 0.0.0.0:8000, log at
  /tmp/fass-serve.log. Verified over LAN: healthz + live preflight request
  renders real check names. Stop on i7 by EXACT PID only: `ps -eo pid,args |
  grep -F "fass serve" | grep -v grep`, then `kill <pid>` (never
  `pkill -f "fass serve"` — self-match kills your own shell line).
- README.md written; roadmap checkboxes updated.
- **M3b COMPLETE (2026-10-23) — stream/remote sources + stream outputs**
  (user green-light "build it"). Registry 1.3.0 `streaming` block is the
  source of truth: input schemes http/https/rtmp/rtmps/rtsp/rtsps/rtp/udp/
  tcp/srt/tls; output scheme→container rtmp/rtmps→flv, rtsp/rtsps→rtsp,
  srt→srt, udp/rtp/tcp→mpegts (+ `-f` muxer prepended to output_opts);
  raw udp/rtp/tcp inputs get `-f mpegts` pre-opt in `_main_input`.
  `EditPlan.output_destination` (validated: allowlisted stream URL only,
  no control chars); rtsp/srt have no file form (planner PlanError);
  flv file output allowed. Rules: `_detect_destination` in instructions
  (URLs not equal to bound asset paths, trailing punctuation stripped),
  destination is itself an edit (no-edit gate bypassed), stream-into
  conflicting codec (h265→rtmp) → unsupported, conflicting container →
  clarification, two destinations → clarification; revisions preserve or
  move the destination. Remote inputs: never reported missing, re-encode
  + "can't be probed locally" default note. Stream outputs carry the
  warning "data is sent to a remote endpoint; nothing is saved locally"
  and skip file-collision checks.
  **Pipeline reconciliation** (the `-o` gap): NLU adapters never see the
  output field, so a stream URL passed as `output_filename` is applied to
  the plan in `_reconcile_output_destination()` (pipeline.py): its scheme
  is authoritative for the container only when the plan's container was
  defaulted (detected via NLU default notes); explicit conflicting
  container → planner's PlanError; inline stream dest + file output →
  structured error "name one, not both".
  **Empirical ffmpeg findings**: (a) the udp *muxer* sends variable-length
  datagrams (up to ~1.5 KB, also 32 B) — validate by reassembly: total
  % 188 == 0, 0x47 sync per 188-byte packet, PAT PID 0 present; (b) ffmpeg
  `tcp://` input defaults to CONNECT, not listen — the E2E test uses
  `tcp://127.0.0.1:PORT?listen=1` (URLs pass through verbatim;
  `stream_scheme()` only parses the leading scheme, queries are safe);
  (c) a `udp://` input has NO end-of-stream — a short feed hangs ffmpeg
  forever, so the input E2E is TCP, not UDP; (d) UDP datagram max 65507 B
  (65536 → Errno 90).
  Deferred (standing decisions): HTTP/HLS *output*, "stop streaming"
  negation; two destinations (inline + `-o` file, or two stream URLs) →
  structured error, never silent.
  i7 redeployed same day: rsync → killed old server by exact PID 511780 →
  restarted detached (PID **660597**, 0.0.0.0:8000). LAN-verified on the
  live build: stream-out rtmp (flv + `-f flv` + disclosure + preflight ok),
  stream-in rtmp (accepted, no missing-file warning), `file://` → 422, UI
  kind select present.
  **REPO PUBLISHED (2026-10-23)**: remote `origin` =
  git@github.com:suedenglehem/fass.git, `main` pushed (first push, full
  history incl. media/titan.mp4). README.md became a short landing page;
  the full pre-existing README moved intact to docs/DEVELOPMENT.md
  (commit 5338297).
- **M4 core COMPLETE (2026-10-23, ced889c)** — the goal is to build ffmpeg
  commands for all 8 core FFmpeg components (ffmpeg.org/documentation.html).
  Schema: CropOp (center WxH, even-snapped by the planner), SpeedOp
  (0.25–8), RotateOp (90/180/270, clockwise flag), AudioResampleOp
  (sample_rate and/or channels 1|2|6) — at most one each; OutputSpec gains
  pixel_format / video_profile / video_preset; AssetKind.SOURCE.
  Builder: crop → `crop=W:H`; rotate → transpose (90cw=1, 270cw=2,
  180=hflip+vflip); speed → `setpts=PTS/f` + `atempo` chain (0.5–2.0
  segments); resample → `-ar`/`-ac`; `-pix_fmt`/`-profile`/`-preset`.
  Input sources (registry "devices"): asset path `<format>:<spec>` with
  lavfi/v4l2/alsa/dshow/avfoundation/x11grab → `-f <format>` + `;k=v`
  option tokens. Output devices audio-only (alsa, pulse — a video plan
  cannot target one). Copy mode now remuxes mp4/mov→mpegts with the video
  bitstream filter (h264_mp4toannexb / hevc_mp4toannexb) via
  `bsf_for_copy()`. Components 5 (muxers/demuxers) and 6 (protocols) were
  already covered by M3b — documentation of all 8 is the in-flight docs job.
- **M4 NLU COMPLETE (2026-10-23, 43251a0)** — rules adapter: crop (exact
  dims; aspect-ratio asks clarify; WxH-vs-resize disambiguation by spans),
  speed (numeric + twice/triple/half/quarter word forms; 0.25–8 bounds,
  "2x bigger" is not speed, "encode faster" → preset not playback speed),
  rotate (45° unsupported, ccw defaults to 90 with a note), audio resample
  (bad rates rejected), pixel format / profile / preset detection;
  revisions merge the new ops (crop/rotate insert after resize; speed and
  audio_resample append). CLI `id=source:<spec>` kind prefix, UI source
  option, explain lines for every new op, LLM prompt synced (same op set +
  source-kind rule + example).
- **M4 test suite (delegated, i7 job j_37421e31d3)**: integrated as
  515d443 — 56 tests: schema bounds (op ranges, at-most-one, discriminated
  union), planner/builder behavior (atempo chains, even-snap, transpose
  mapping, copy+bsf, source pre_opts, device plans, PlanErrors), real-media
  E2E on titan.mp4. dispatch_integrate accepted.
- **M4 audio bsf COMPLETE (2026-10-23, 645f903)** — delegated to
  tr4-secondary as `j_893e3a47a6`, integrated + dispatch_integrate
  accepted. `audio_bsf_for_copy(source, target, audio_codec)` (new,
  parallel to the untouched `bsf_for_copy()`) + registry block
  `mpegts_to_mp4_mov: {aac: aac_adtstoasc}` + planner copy-plan wiring
  (`-bsf:a` only when audio is kept and the probe provides a codec) +
  13 tests (tests/test_bsf.py, incl. real-media E2E titan→.ts→.mp4).
  registry_version 1.4.0 → 1.5.0. CLI --execute smoke verified: remuxing
  a real .ts to MP4 yields `-c copy -bsf:a aac_adtstoasc` and a valid
  h264+aac 48 kHz output.
- **M4 docs COMPLETE (2026-10-23, ed067ba + follow-up)** — delegated to
  i7 as `j_bf063ade65`, integrated + dispatch_integrate accepted. PRD
  (deferred list, MVP table rows, multi-input sentence), README (two
  bullets), docs/DEVELOPMENT.md ("What the MVP understands" refresh, new
  "FFmpeg component coverage" section mapping all 8 core FFmpeg
  components to registry keys/modules, roadmap marked done). Every
  factual claim was re-verified against the code before integrating.
  Follow-up edit landed in the same milestone: the three
  "audio-side bitstream filtering is not yet available" notes
  (written while the other job was in flight) now document
  `aac_adtstoasc`.
- **M5 COMPLETE (2026-10-24) — subtitles** (user report "fass doesn't
  understand subtitles"; standing "don't be lazy, don't ask me!" → full
  autonomous pass, no delegation needed). `SubtitleOp{asset_id, mode
  burn|soft}` (at most one; burn = default with a visible default note,
  soft only on explicit "soft"/"selectable"/"embed" wording — "soften the
  audio" must not count). Burn: libass `subtitles=` (srt/vtt) / `ass=`
  (ass/ssa) appended to the video chain (plain `vf` or the filter_complex
  final segment, after any watermark overlay); the filter opens the file
  itself → no extra input; forces a re-encode; container-agnostic. Soft:
  muxes a selectable track — mp4/mov → `mov_text`, mkv → `srt` — via
  `-c:s` AFTER `-c copy`, so a soft-subtitle remux stays a stream copy;
  no soft codec for webm/mpegts → burn or MP4/MOV/MKV; `-shortest` is
  never emitted by subtitles (audio_replace only). Escaping (empirically
  locked on ffmpeg 6.1.1): the filter-path escape `([\\'\":=,;])` →
  `\\\1` is applied TWICE, so each special char ends with THREE
  backslashes; the test asserts a double round-trip unescape recovers the
  path (`it's.srt` verified end-to-end). Colons in the subtitle filename
  are rejected by the planner (ffmpeg parses them as stream protocols).
  Revisions: "remove the subtitles" (no-op on a fresh plan), mode
  switches, file rebinding; with several unbound subtitle assets the
  previous file is kept + noted. NLU: subtitle detection wired after
  audio_replace; "extract the subtitles" → unsupported. Registry 1.6.0
  (`subtitles` block: input_formats, burn_filters, soft_codecs);
  `MediaInfo.subtitle_streams`; preflight checks the soft codec; LLM
  prompt + explain + README/PRD/DEVELOPMENT all updated. New suite
  tests/test_subtitles.py: 86 tests incl. 3 real-media titan E2E (burn
  re-encode, soft stream-copy, soft+trim) — all executed, not skipped.
  Benchmark: bench-063/067 re-expected `needs_clarification` (subtitles
  were "unsupported" only because the feature didn't exist; with no
  subtitle file bound the right answer is now a question); bench-090/091
  added (burn + soft ready paths with a bound subs.srt).
- CLI smoke verified on titan.mp4 (ground truth): "crop to 640x480, make
  it 2x faster and rotate 90 degrees, 10-bit, ultrafast preset" →
  `ffmpeg -hide_banner -loglevel error -i media/titan.mp4 -vf
  crop=640:480,transpose=1,setpts=PTS/2 -af atempo=2 -map 0:v:0 -map '0:a?'
  -c:v libx264 -crf 23 -preset ultrafast -pix_fmt yuv420p10le -c:a aac
  -b:a 160k -movflags +faststart titan.mp4`; "resample audio to 44.1k mono"
  → `-ar 44100 -ac 1` + "Audio resample: to 44100 Hz, mono" summary line.

## Key decisions
- argv arrays are the source of truth; shell rendering is shlex.join only.
  Model-generated shell is never trusted (PRD security).
- `-ss` before `-i` (input option, fast seek) + `-t` output duration —
  trim always re-encodes (frame accuracy, PRD FR-4).
- Stream copy only when no ops/trim AND ffprobe metadata proves codecs match;
  no metadata → re-encode + visible default note.
- Output filename: default `{stem}.{ext}`, de-collided with `_out` suffix;
  explicit `-o` == input is a hard error. Never `-y`.
- Timecode convention: two fields = MM:SS, three fields = H:MM:SS.
- Rules adapter is deterministic/offline default; LLM adapter (Anthropic)
  shares the same pydantic envelope validation.
- Watermark: labeled filter_complex + `overlay`, `-map [vN] -map 0:a?`.
- Target video bitrate is a CBR-style target: `-b:v`/`-maxrate` at the value,
  `-bufsize` 2× for H.264/H.265 (libvpx-vp9 omits it). It overrides the CRF
  preset and forces a re-encode (a stream copy cannot guarantee a bitrate).
  Byte-size targets ("5 MB") remain needs_clarification; bit-rate targets
  ("10 Mbps") are honored.

## Changed files (milestones 1–2, M3, M4, M5, M6)
fass/{__init__,__main__,cli,pipeline,explain,feedback,api,ui,config,
dockerctl,execute}.py,
fass/schema/plan.py, fass/registry/, fass/probe/ffprobe.py,
fass/planner/, fass/builder/, fass/nlu/{rules,llm}.py,
tests/{conftest,test_schema,test_rules,test_planner_builder,
test_render_adversarial,test_cli,test_api}.py,
tests/benchmark/{requests.json}, tests/test_benchmark.py,
tests/test_media.py, tests/test_titan.py, tests/test_bsf.py,
tests/test_subtitles.py (M5),
tests/test_m6_{config,gpu,stream,execute,llm,api,worker}.py (M6),
docker/{Dockerfile,entrypoint.sh,compose.yaml,README.md,launcher.sh},
.dockerignore (M6), serve.sh (launch script),
media/titan.mp4, pyproject.toml,
.gitignore, README.md, docs/DEVELOPMENT.md, ffmpeg-assistant-prd.md,
HANDOFF.md

## Test results
`./.venv/bin/python -m pytest -q` → **627 passed** in ~78 s (2026-10-25,
M8: 588 baseline + 39 tests/test_codec_options.py; M8 changes uncommitted).
M7: **588 passed** in ~71 s (2026-10-24, M7: 555 at merge 43b1182 = 534 +
21 worker tests; +30 new M7 NLU test cases + planner/registry fixes +
mxf -g 120 assertion).
Earlier: 534 at 5afac91 (2026-10-24, M7 in progress, suite green at
commit 5afac91; baseline 533 + 7 capability-widening failures fixed:
bench-077, test_planner_builder ×4,
test_schema ×3).
M6: 530 passed (452 pre-M6 + 78 test_m6_*.py incl. the worker sidecar
suite + the `_unmanaged_binds` regression; bench-063/067 re-expected for
the new subtitle capability since M5).
364 at M4 (2026-10-23; 265 at M3b: +56 M4 core suite 515d443, +30 M4 NLU
tests in 43251a0, +13 bsf tests in 645f903). 91 benchmark entries, 28
API tests.
Real-media sanity on media/titan.mp4: udp stream-out renders
`-f mpegts … udp://127.0.0.1:9999` + trim warning; `-o rtmp://…` → flv +
`-f flv` + reconciliation default note + disclosure in summary.
Live server smoke-tested: `fass serve` + curl (healthz, command, UI).
venv has pydantic 2.14.1, pytest 9.1.1, fastapi+uvicorn, httpx 0.28.1
(TestClient dep, added milestone 2); fass is NOT pip-installed (cwd import) —
run tests from repo root.

## Next steps
**M10.5 COMPLETE (2026-10-26), host-verified; closing out.**
910 passed, 1 skipped on tr4 host 6.1.1. Commit on main (Co-Authored-By;
no push unless asked). Two dispatcher jobs submitted at that commit:
i7 = 9.0.1 container verification (expect 910/1, libvmaf E2E runs),
tr4-secondary = RAG M11.0 corpus foundation per the PRD (root-level
`rag/` package, pinned 6.1.1/9.0.1 corpora). Then /savework.
**After:** M10.6 (per-category E2E battery on titan.mp4 + docs) → M11
RAG phases M11.1–M11.4. Pending user decisions (do not block):
channelsplit multi-output defer-vs-implement; orphaned i7 worktrees;
i7 redeploy to M10.5+ (verification-only for now).

**M8 COMPLETE + DUAL-BUILD VERIFIED (2026-10-25).** Committed 133a189.
j_69d3f78ec0 (i7/9.0.1) and j_c7320e8dfe (tr4-secondary/6.1.1) both
succeeded, all acceptance criteria met, neither committed anything → no
dispatch_integrate. i7 redeployed to M8 and live-verified (16
codec_options tables, fdk executed). Remaining: nothing for M8 — main is
8 commits ahead of origin/main, no push unless asked.

**M7 VERIFICATION COMPLETE (2026-10-24)** — NLU rewrite committed
(4c0a0ed), worker job **j_41200a56f4** INTEGRATED (merge 43b1182,
dispatch_integrate accepted), 30-case M7 test battery + filtergraph fix +
llm.py sync + local/double-build real-ffmpeg verification (21/21 on
6.1.1 AND 21/21 on 9.0.1 in docker, after the mxf `-g 120` fix) + i7
deploy + live API/run E2E — all done. 588 passed. REMAINING: commit the
uncommitted M7 battery + fixes (Co-Authored-By; no push unless asked) +
final user reply ("why not ALL containers" — registry-driven data).
Main is 6 commits ahead of origin/main — no push unless asked.

**M6 committed (pre-rewrite 2344be3)** and **pushed**. UI bug fixed
+ pushed 2026-10-24; bare-input volume-resolution fix committed +
deployed to i7 (see Prior state) — push pending user's word.
- **plan0.md removed (2026-10-24, on request):** every deliverable in the
  doc was already shipped, so the file was removed, ~20 references in
  docstrings/README/DEVELOPMENT/HANDOFF scrubbed, and it was deleted
  from ALL git history (`git filter-repo --invert-paths --path
  plan0.md`; backup refs purged + `git gc --prune=now`; force-pushed).
  Verified: plan0.md in no commit/tree/ref. Consequence: **every commit
  hash recorded in this file (and in mycelium) predates the rewrite and
  no longer resolves** — hashes only compare within pre- or post-rewrite
  history. Also removed the 6 stale fass dispatch worktrees (all jobs
  terminal, work integrated) and synced the deletion to i7.
- **main = origin/main = 028ce91** (post-rewrite tip, pushed
  2026-10-24); the UI fix (cfd3bc9) pushed on top same day; main = origin/main = cfd3bc9.
Milestones 1+2+3, M4 (core + NLU + audio bsf + docs), and M5
(subtitles) all done and committed. M6 (docker deployment: packaging,
Config tab, streams, GPU/nvenc, LLM config, execution, Apply sidecar)
done + deployed to i7 + E2E-verified (including Apply twice). The
8-component build goal is code-complete AND documented. Both delegated
jobs (j_893e3a47a6, j_bf063ade65) integrated, accepted, no jobs in
flight. 530 tests green (2026-10-24). Working tree clean apart from
untracked `.resume`. Both machines launch the dev server via
`serve.sh`; i7 additionally runs the docker deployment (compose project
"docker"). Nothing outstanding unless the user asks.

1. **i7 redeploy DONE (2026-10-23, on request).** The old pre-M4 server
   (PID 660597) had already died — port 8000 was free, nothing to kill.
   rsync'd current `main` (5c3da4f) to /dd2/andrei/fass (excluding
   .venv/.git/media), started detached via setsid nohup. Server
   PID **369596** on 0.0.0.0:8000, log /tmp/fass-serve.log. Verified:
   /healthz ok, /v1/support now lists the M4 ops (crop/speed/rotate),
   and a live `POST /v1/commands` "resize to 720p" → status ready,
   correct argv, validation_level static. Stop by EXACT PID only (same
   rule as before, never `pkill -f "fass serve"`).
2. **i7 redeploy DONE (2026-10-24, M5).** Killed 369596 by exact PID,
   rsync'd main (5fe47f1), restarted detached via setsid nohup. New
   server PID **744429** on 0.0.0.0:8000, log /tmp/fass-serve.log.
   Verified LAN healthz + live `POST /v1/commands` "burn in the
   subtitles from subs.srt" → ready, `subtitles=subs.srt` in `-vf`.
   Stop by EXACT PID only.
3. **serve.sh launch script DONE (2026-10-24, on request).** One
   idempotent launcher at the repo root, deployed to BOTH machines
   (local + i7, same path /dd2/andrei/fass/serve.sh): `./serve.sh`
   launches detached (setsid nohup) and waits for /healthz, or no-ops
   if a healthy server already answers; `./serve.sh --force` kills the
   current server and relaunches. Safety: pidfile /tmp/fass-serve.pid
   + anchored /proc cmdline match (must start with
   `<repo>/.venv/bin/python -m fass serve`) — never a pattern kill;
   refuses if the port is held by an unrelated process; aborts (no
   SIGKILL) if the old server ignores SIGTERM. Verified on both: local
   server PID **29610** (fresh launch — local :8000 was free); i7 old
   PID 744429 had died → script relaunched (28686), then `--force`
   restart exercised → i7 server PID **31580**; i7 re-verified over
   LAN (healthz + live subtitles POST → ready). Stop: `kill $(cat
   /tmp/fass-serve.pid)`.

Earlier candidates, still open if requested:

**M3 (user-stated direction, 2026-10-23) — multi-input + stream I/O.**
"In a perfect world the web interface should allow adding more inputs
(video, audio, image, standard ffmpeg sources); outputs can be a file or a
stream." **M3a DONE + M3b DONE (2026-10-23)** — the full M3 vision is
implemented: N typed inputs (video/audio/image, local files or allowlisted
stream URLs, kind pinned via API `kind` field / CLI `id=kind:path`), file
or stream outputs. Security posture held: never-executes (PRD §10), argv
tokens only, scheme allowlist, stream destinations disclosed.

1. Deferred M3b scope if requested: HTTP/HLS *output* destinations,
   "stop streaming" negation handling, generic ffmpeg-source-spec inputs
   (e.g. named lavfi sources) beyond files + allowlisted URLs.
2. More ops beyond the current set (volume, concatenation, ...),
   LLM adapter evaluation against the 91-request benchmark.
3. UI niceties (history, drag-drop — PRD explicitly defers drag-and-drop).
4. Learning infra (consented-failure corpus + eval harness) before any fine-tuning.
Remote `origin` (github.com/suedenglehem/fass.git) exists since 2026-10-23;
commit/push with it only when asked.

## Environment notes
- Python 3.12.3, `.venv` (PEP 668 blocks system pip) — use `./.venv/bin/python -m pytest`.
- ffmpeg 6.1.1 + ffprobe on PATH (main box).
- Dispatcher: fass project was `single` mode + had zero commits — switched to
  team and committed initial state (local, no push) to enable delegation.
