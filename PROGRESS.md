# PROGRESS — fass (natural language → validated FFmpeg commands)

**Self-contained continuation file.** Written 2026-10-25 (end of session) so the
M10 work can be continued **on the i7 machine** with no other context source
needed. Sources: `HANDOFF.md` (repo), mycelium memories of the `fass` project
(dumped into §6), and live probes of both machines this session.

- **Last state (2026-10-30, audio stream copy COMMITTED 91f1bf1 + version
  bump 3a15f7c, PUSHED + i7 DEPLOYED + live-verified; versioning rule
  commits now also pushed + on i7; "Enter" button COMMITTED 1cdefcd +
  version bump 25d7418, PUSHED (e0810f2..522b1d2) + i7 DEPLOYED +
  live-verified v1.137036.1cdefcd, the user is browser-testing; video
  stream copy INTEGRATED onto main — job j_5566e75d4d succeeded, feature
  98fc460 + bump 35370a2 (v1.137036.98fc460) merged as 2813db3 after a
  full diff review, version.py conflict resolved to the job's bump value,
  dispatch_integrate recorded — then PUSHED + i7 DEPLOYED; the i7
  live-verify caught a real bug (preflight KeyError "copy" → HTTP 500 on
  the user's exact instruction; the test suite's fake inputs never
  probe, so it missed it), FIXED in cac7cda + bump da6eeed
  (v1.137036.cac7cda) + regression test, suite 1594 passed / 2 skipped,
  i7 REDEPLOYED + live-verified: the user's exact instruction returns
  ready/preflight with -c:v copy -c:a aac -b:a 180k; second audio
  track (audio_add) + one second LLM try on unsupported COMMITTED
  d1c41d0 + bump 0f32519, PUSHED (d79c56b..b9184d2) + i7 DEPLOYED +
  live-verified v1.137036.d1c41d0 — the user's exact instruction
  (rules pass: unsupported) reached the SECOND LLM TRY and came back
  ready/preflight: -i titan.ts -i titan.mp4 -map 0:v:0 -map '0:a?'
  -map 1:a:0 -c:v copy -c:a copy, no -shortest; suite 1627 passed /
  2 skipped; the second LLM try REWORKED as a DIRECT raw-command call
  (no EditPlan schema) — COMMITTED 7f1a7ad + bump 918393e
  (v1.137036.7f1a7ad) PUSHED + i7 DEPLOYED + live-verified — both of
  the user's exact instructions came back ready on i7; the second try
  EXTENDED to parser-error + planner-rejected-plan first passes —
  COMMITTED aa5c5f9 + bump e6e282a (v1.137036.aa5c5f9), PUSHED
  (c916513..cec7bd2) + i7 DEPLOYED + live-verified — the user's
  bug-shape instruction came back ready via the direct second try
  (first pass: parser error); suite 1640 passed / 2 skipped; remote
  volume mapping (FTP/SFTP/S3) + S3 uuid naming COMMITTED 5eca3ab +
  bump 0771014 (v1.137036.5eca3ab) — LOCAL, i7 DOWN (user away),
  live-verified on tr4 host:8099 — s3/s3-uuid/ftp/sftp/none all green,
  suite 1710 passed / 2 skipped)):**
  (1) **Versioning rule (user, for life of project):** on EVERY code commit —
  recover that commit's short sha, write `__version__ = "v1.137036.<sha>"` in
  `fass/version.py`, commit again (the bump references the code commit it
  follows; no recursive bump; docs-only commits do not trigger a bump;
  VERSION_ID 1.137036 changes only when the user says so). Committed:
  `0828bd6` (ui no longer prepends v) + `dd7159f` (version.py
  v1.137036.0828bd6 + test_cli regex pin `fass v\d+\.\d+\.[0-9a-f]{7}`);
  first application on the audio copy: code `91f1bf1` + bump `3a15f7c`
  (v1.137036.91f1bf1). ALL four PUSHED + on i7 (the bump ride-alonged
  with the audio-copy deploy). `fass --version` →
  `fass v1.137036.91f1bf1`. **PENDING:** docs commit recording the rule
  (this bullet), rule memorization (mycelium + file memory), report to
  user. (2) **Audio stream copy (user bug: "convert file to
  video h264 baseline profile 600kbps video and copy audio as is" → LLM
  error "audio stream copy is not supported"):** DONE, COMMITTED
  `91f1bf1` (9 files, +315/−15) + version bump `3a15f7c`, PUSHED +
  i7 DEPLOYED + live-verified (see the deploy note at the end of the
  bullet).
  `AudioMode.COPY` (schema/plan.py; audio-only mode still rejects it,
  message updated); planner (planner.py): re-encode builder else-branch
  emits `-c:a copy` + defaults note (gif/no-audio container → `-an` + drop
  note); upfront validation block (before the `errors` raise): copy vs
  audio_resample / speed / audio-domain filter / combine / filtergraph /
  audio_bitrate_kbps / audio_codec_options / variants(ABR) / subtitles-mode
  / named-codec mismatch (known codec ≠ named → error; no metadata + named
  codec → error) / container can't carry the input codec (matrix audio
  list; empty list = drop, not error); device-output guard (plays decoded
  audio); ladder guard extended for COPY; copy+audio_replace → replace
  wins with a warning (warned after the raise); same-codec shape still
  full-remuxes (mode="copy" path unchanged). Rules NLU (rules.py):
  `_AUDIO_COPY` regex (copy the audio / as-is / untouched / unmodified /
  unchanged / as it is / bit-for-bit, before AND after the noun; no/without/
  don't re-encode the audio) → "copy" state in `_detect_audio` (copy beats
  keep; remove+copy → "conflict"); audio_replace nuls copy; ladder_conflict
  + subtitles mode_conflict include "copy"; conflict question generalized;
  new-plan + `_merge_revision` set `AudioMode.COPY`; "copy" now counts as
  an edit in the no-op check (`audio not in ("remove", "copy")`). llm.py
  prompt documents "copy" + its constraints (the LLM path that produced the
  original error). explain.py: "Audio: copied as-is (-c:a copy)". 18 new
  tests (user's exact case → `-c:a copy` + `-b:v 600k`; remux; named-codec
  match/mismatch; every conflict; gif drop; replace-warns) + benchmark
  bench-092/093 (test_m11_ablation count 91 → 93). User's exact
  instruction now yields: `ffmpeg -i clip.mp4 -map 0:v:0 -map 0:a? -c:v
  libx264 -b:v 600k -maxrate 600k -bufsize 1200k -profile:v baseline
  -pix_fmt yuv420p -c:a copy -movflags +faststart clip_out.mp4`.
  **i7 deploy (user-authorized "propagate audio-copy to i7"):** i7
  ff-merged 138a563→3a15f7c (the local `docker/compose.yaml` opt-in +
  `fass/probe/ffprobe.py` patch verified intact through the merge),
  `docker compose -f docker/compose.yaml build fass` +
  `up -d --force-recreate fass`. Live-verified: healthz ok; in-container
  + `/v1/env` version `v1.137036.91f1bf1`; the user's EXACT instruction
  on titan_double.mp4 → status ready with `-c:a copy` in the argv + both
  audio-copy notes ("audio copied as-is; the input audio codec must be
  supported by MP4" / "…(-c:a copy) — the input's codec and bitrate pass
  through untouched"); fass-worker / fass-rag unharmed. (3) **"Enter"
  button — IMPLEMENTED + VERIFIED + COMMITTED `1cdefcd` (2 files,
  +101/−3) + version bump `25d7418` (v1.137036.1cdefcd; local, NOT
  pushed):** on the FFmpeg pane, between Save and Mediainfo —
  for power users who write ffmpeg commands themselves: skips the
  LLM/NLU ENTIRELY, prefills the command line with the skeleton `ffmpeg -i
  /input/<in1> [-i /input/<in2> …] -o /output/<out>`. **UI (fass/ui.py, 3
  edits):** `#enterbtn` in the button row (after Save, before Mediainfo);
  `cmdButtons()` gains `$('enterbtn').disabled = !inp` (the instruction is
  IGNORED by design — that is the point; no new input listener, so the
  count pin of exactly four `addEventListener('input', cmdButtons)` is
  unchanged); `enterPath(v, isOut)` (bare filename → `/input/<name>` for
  inputs / `/output/<name>` for the output; a value already starting with
  `/` or carrying a `://` URL is used as-is — covers chained
  `/input/step1.mp4` and stream URLs); `enterCmd()` (mainInputs() split of
  `#inp` → the multi-`-i` list, + the `.extra-inp` rows, + the `#wm`
  watermark LAST; empty `#out` → no `-o`; then a SYNTHETIC ready render —
  `render({status:'ready', command, arguments: argv, summary, defaults,
  warnings:[], validation_level:'manual', checks:[], evidence:null,
  raw_llm:null})` — NO server call, which is what skips the LLM part;
  `arguments` IS the real skeleton argv so an UNEDITED Run executes a bare
  remux while an EDITED line goes through the same server-side
  revalidation as any generated command; render() sets lastReady so Save
  auto-enables). **Tests (tests/test_webinfo.py, 2 edits + 1 new):**
  button-row test order pinned Save < Enter < Mediainfo; fresh-pane needle
  now includes `id="enterbtn" disabled onclick="enterCmd()"` + the gating
  expression `$('enterbtn').disabled = !inp` pinned; new
  test_ui_enter_button_manual_skeleton pins the markup/title/order, both
  functions, NO `fetch(` in either function body, the prefix rules
  (`s.startsWith('/') || s.includes('://')` + the `/input`/`/output`
  ternary), the mainInputs/extra-inp/wm/`-o` assembly, the synthetic-ready
  fields, and `$('cmd').focus()`. **Verified:** test_webinfo + test_presets
  56 passed; Node DOM-shim harness on the REAL extracted JS 11/11 (bare
  in/out, multi `-i` no out, rtmp URLs as-is, chained /input/ out,
  extras+watermark-last, leading `-i` not double-counted, absolute paths
  as-is); full suite **1567 passed / 2 skipped (145.59 s)**; `node
  --check` on the whole served script block OK; headless-Chrome click test
  vs a live server on 8099 9/9 (page loads, script initialized, fresh-pane
  disabled, enabled after fill, skeleton `ffmpeg -i /input/a.ts -i
  /input/b.ts -o /output/out.mp4` EXACTLY in the textarea, result pane
  shown, Save enabled, Enter ignores the instruction field, Clear
  re-disables). (4) **Video stream copy — DELEGATED to a dispatcher
  worker (job j_5566e75d4d, backend i7 LLM; worker process + git worktree
  run on tr4 where the daemon lives, i7 = inference endpoint only, so
  i7 looks idle while the job runs), SUCCEEDED + REVIEWED + INTEGRATED
  onto main as merge 2813db3, PUSHED + i7 DEPLOYED + live-verified
  (incl. one live-only bug found by the i7 verify and fixed in
  cac7cda):** the mirror of 91f1bf1 for
  video — the user's failing instruction "transcode audio to aac 180kbps
  and copy video as is" → schema error "a video output needs a
  video_codec" (plan.py:374). Brief: add "copy" to the video_codec
  Literal + guard the codec-specific branches (ABR ladder,
  video_profiles_for, pixel_format_error, video_codec_options,
  preset/rc/cq/qp); mirror the audio-copy upfront planner block (no copy
  for diagnostics/subtitles/container-can't-carry/ladder) + builder branch
  `video_codec == "copy"` → `-c:v copy`, skip all encoder flags (leave
  full-remux `-c copy` untouched); rules `_VIDEO_COPY` (copy the video
  as-is / keep the video untouched / without re-encoding the video;
  copy beats default encode; explicit codec + copy → clarification; fix
  no-op check); llm.py prompt documents video_codec "copy" (REQUIRED —
  the failing instruction goes through the LLM path); explain.py line;
  12 + 5 test mirrors + bench-094 (user's exact) / bench-095 + ablation
  pin 93→95; MANDATORY version bump (contracts_text). Worker (cc-worker,
  xhigh; i7 LLM) SUCCEEDED: two commits — feature 98fc460 + bump 35370a2
  (v1.137036.98fc460), worktree suite 1592 passed / 2 skipped (1566 +
  26 new). Review verdict: APPLIED — the diff is a clean mirror of
  91f1bf1 (schema "copy" in the Literal + codec-branch guards, planner
  upfront conflict block + `-c:v copy` builder branch with the whole
  encoder block moved into its else, `_VIDEO_COPY` rules +
  clarification/no-op fixes + audio_bitrate now applied on the video
  branch (required — main never applied an audio bitrate on a video
  plan, so the user's 180k would have been silently dropped), llm.py
  prompt, explain.py, 15 planner + 5 rules tests, bench-094/095).
  Integrated: merge 2813db3 onto main; the predicted version.py conflict
  (v1.137036.1cdefcd vs v1.137036.98fc460) resolved to the job's bump
  value (98fc460 is now the newest code commit on main); full suite on
  the merged tree 1593 passed / 2 skipped / 0 failed (143.34s);
  dispatch_integrate(j_5566e75d4d, 2813db3) recorded (accepted).
  PUSHED (df67b8f..da6eeed) + i7 DEPLOYED (ff-merge + rebuild +
  force-recreate, compose opt-ins + ffprobe patch verified intact
  after the merge). i7 live-verify of v1.137036.98fc460 caught a
  live-only bug: the user's exact instruction POSTed to /v1/commands
  → HTTP 500, KeyError "copy" at pipeline.py:531 — generate()
  auto-probes a REAL local input (media is not None) which takes the
  preflight branch, and that branch indexed
  REGISTRY["video_codecs"][video_codec]["encoder"] without a "copy"
  guard (explain.py/planner were guarded; the preflight was the last
  unguarded lookup — the test suite missed it because the
  benchmark's fake clip.mp4 never probes, so media stayed None and
  the block was skipped). Fix cac7cda: skip the video-encoder check
  for "copy" (the muxer check stays; container-carries-codec is the
  planner's matrix contract) + regression test
  test_video_copy_preflight_with_real_probe (real probed input,
  asserts ready + -c:v copy -c:a aac -b:a 180k + validation_level
  preflight; confirmed to fail with KeyError "copy" on the unfixed
  code). Bump da6eeed (v1.137036.cac7cda). Suite 1594 passed / 2
  skipped (142.86s). i7 REDEPLOYED + live-verified: healthz ok,
  /v1/env v1.137036.cac7cda, Enter-button markers intact, the
  user's exact instruction → ready/preflight, command `ffmpeg
  -hide_banner -loglevel error -i /input/titan_double.mp4 -map
  0:v:0 -map '0:a?' -c:v copy -c:a aac -b:a 180k -movflags
  +faststart titan_double.mp4`, LLM 8080 → 200, containers
  unharmed (fass-http pre-existing Exited(127) ignored). (5)
  **Second audio track (audio_add) + one second LLM try on
  unsupported — IMPLEMENTED + COMMITTED `d1c41d0` (14 files,
  +722/−19) + version bump `0f32519` (v1.137036.d1c41d0); PUSHED
  (d79c56b..b9184d2) + i7 DEPLOYED + live-verified:** the user's remux — "copy audio and
  video tracks from the first video, add the audio track of the
  second video as a second audio track" → `ffmpeg -i A -i B -map
  0:v:0 -map '0:a?' -map 1:a:0 -c:v copy -c:a copy` (NO -shortest —
  the shorter added track must not cut the video). (a) audio_add:
  `AudioAddOp(asset_id)` (schema/plan.py; at most one per plan);
  rules NLU (rules.py) — "…add the audio track of <file> as a second
  audio track" phrasings, binding by named file, fallback to the sole
  other media asset (default note "only other media file"), no other
  asset → needs_clarification, audio_add beats audio_replace on the
  same asset; planner (planner.py) — audio_add is NOT in forced_ops
  (an audio_add-only plan stays copy mode), the second-audio input is
  the LAST input, copy plan = single `-c copy` + 0:v:0 / 0:a? / N:a:0
  maps, the user's `-c:v copy` request rides the re-encode builder
  (`-c:v copy -c:a copy`), validation rejects a remote stream /
  silent container / audio-only output / removed or replaced audio /
  GPU + QSV / a speed change / a resample target / an audio-domain
  filter / a main input without audio (exact PlanError string per
  conflict). 17 planner/builder tests (4 argv pins: the copy plan,
  the user's copy/copy case, hevc→h264 re-encode, watermark + aa
  input order; 13 exact-string PlanError tests) + 6 rules tests + 1
  media E2E (the generated argv really runs on lavfi clips; output
  probed — 1 video stream, 2 audio streams, h264 intact, full 15 s
  against a 3 s second input, proving no -shortest) +
  bench-096/097/098 (98 entries; test_m11_ablation's count test now
  counts the file instead of pinning 95). (b) Second LLM try: when
  the first pass (rules) answers unsupported, generate() runs ONE
  retry on the LLM adapter with a user-confirmed explicit-path query
  — `generate ffmpeg command for <instruction verbatim, `.` appended
  if not sentence-terminated> input1=<full /input path>, input2=…,
  output=<full /output path>`; ALL inputs in given order, full
  deployment paths via the volume dirs (bare names when unmounted);
  a READY retry plan replaces the result (adapter="llm" + defaults
  note "…this plan came from a second LLM pass over the instruction
  with the file paths made explicit"); a non-READY retry or no LLM
  configured leaves the first pass's unsupported unchanged. 3
  hermetic tests (the exact query string pinned). conftest.py:
  session-autouse fixture pops ANTHROPIC_API_KEY / FASS_LLM_API_KEY /
  FASS_LLM_HOST / FASS_LLM_PORT / FASS_LLM_MODEL / FASS_LLM_PROVIDER
  + points FASS_CONFIG at an empty `{}` (the tr4 shell carries a real
  ANTHROPIC_API_KEY — without the fixture the retry would reach the
  real provider). **Full suite: 1627 passed / 2 skipped / 0 failed
  (143.76 s).** PUSHED (d79c56b..b9184d2) + i7 DEPLOYED + live-verified
  (2026-10-30, user-authorized "push and deploy to i7, i will test
  it"): i7 ff-merge da6eeed→b9184d2 (compose opt-in + ffprobe patch
  verified intact), rebuild + force-recreate; live-verified from i7 —
  healthz ok, /v1/env = v1.137036.d1c41d0, the user's EXACT
  instruction ("copy audo and video tracks from second video, add
  audio track from first video as second audio track" — the "audo"
  typo + inverted first/second defeats the rules pass → unsupported)
  on titan.mp4 + titan.ts (deca3.mov absent on i7) → the SECOND LLM
  TRY fired (qwen3.8-27b @ 172.17.0.1:8080) and returned
  ready/preflight with input_id="second" + audio_add("main") — the
  LLM honored the inverted wording: `ffmpeg -hide_banner -loglevel
  error -i titan.ts -i titan.mp4 -map 0:v:0 -map '0:a?' -map 1:a:0
  -c:v copy -c:a copy -movflags +faststart test_1lm_2.mp4` (no
  -shortest); fass-worker / fass-rag unharmed, LLM 8080 → 200.
  **Next: the user is browser-testing. Carry-forward (user's call,
  not started): multi-input + blackdetect RAG E2E; optional
  docs/DEVELOPMENT.md codec list; OPEN NLU issue — CLI concat "…48kbps
  aac audio" emitted -b:a 160k not 48k.** (6) **The second LLM try
  REWORKED as a DIRECT raw-command call (no EditPlan schema) —
  IMPLEMENTED + COMMITTED `7f1a7ad` (4 files, +290/−89) + version bump
  `918393e` (v1.137036.7f1a7ad); LOCAL — NOT pushed, NOT deployed,
  awaiting the user's explicit green light:** the user's bug report —
  the shipped second try (d1c41d0) re-routed through `get_adapter("llm")`,
  whose system prompt is built from the EditPlan JSON schema, so the
  model answered with an EditPlan and the planner then rejected it
  ("audio-replacement asset 'in1' is not an audio file") while the raw
  output showed the plan JSON — but feeding the agreed prompt BY HAND
  ("generate ffmpeg command for: …") returns the correct raw command.
  Fix (`fass/pipeline.py`): `_llm_direct_command(instruction, assets,
  output_filename, evidence_block)` does ONE direct provider call —
  `llm._chat_openai(system, query)` / `llm._chat_anthropic(system, query)`
  — with a minimal system prompt ("You generate FFmpeg commands. Answer
  with the ffmpeg command only — no explanations, no markdown"; NO
  schema) and the query `generate ffmpeg command for: <instruction
  verbatim, `.` appended if not sentence-terminated> input1=<full
  /input path>, input2=…, output=<full /output path>` (user-specified
  colon after "for"; ALL inputs, main first, full deployment paths;
  RAG evidence block appended when present). The free-form reply is
  parsed by `_extract_ffmpeg_command` (fence marker lines dropped;
  first line starting with "ffmpeg" taken, bullet/bold markers allowed;
  backslash continuations joined with a single space; stops at prose
  after the command; None when no command), `shlex.split`-ed, and run
  through `validate_argv` — the SAME gate an expert hand-edit goes
  through (flags from the builder vocabulary; only the final token may
  be a bare path). On pass → SYNTHETIC ready response: status "ready",
  adapter "llm", arguments = the validated argv, command =
  render_posix(argv), plan None, validation_level "static", a summary
  note, a defaults note "the first attempt (structured plan) found no
  supported plan — the second approach asked the LLM directly for the
  ffmpeg command string, with the file paths made explicit, and it
  passed the command validation gate", and a warning per input not
  found on this machine. On ANY failure → the first pass's unsupported
  stands and `resp.errors.append("second LLM try failed: " + <the
  second try's own message>)` — "no LLM provider configured" /
  "LLM call failed: {e}" / "the reply contains no ffmpeg command — see
  the raw LLM output" / "the command has unbalanced quotes: {e}" /
  "the command failed validation: {gate problems}" — and the verbatim
  second reply becomes `resp.raw_llm` (raw_override). User's logging
  spec, both halves: the SUCCESS is logged (defaults note above) and
  the FAILURE shows the second try's own error message. UI
  (`fass/ui.py`): the unsupported render branch now shows
  `<h2>Second try (direct LLM)</h2>` + the errors list above the
  evidence. `fass/batch.py`: `_snippet` now LEADS with the first
  pass's unsupported fragment — resp.errors is meta about the retry,
  not the file's reason (the old errors-first order showed "second LLM
  try failed: no LLM provider configured" instead of "sepia: …" in the
  batch table; caught by
  test_run_batch_reports_a_rejected_file_and_keeps_going).
  `tests/test_pipeline_rag.py`: the 3 old schema-path retry tests
  replaced by 5 direct-try tests (query pinned VERBATIM incl. the
  colon; system prompt asserted schema-free; validated argv returned
  + render_posix command + raw reply + plan None + static level + the
  first-attempt/second-approach defaults note; no-command reply →
  first pass stands + "no ffmpeg command" error + raw reply surfaced;
  gate rejection (-definitely_not_a_flag) → "failed validation" /
  "unknown flag"; transport error → "LLM call failed" / "connection
  refused"; no LLM configured → "no LLM provider configured") + 6
  `_extract_ffmpeg_command` unit tests (bare, fenced with prose,
  backslash continuations, bullet start, stops at prose, absent).
  **Full suite: 1635 passed / 2 skipped / 0 failed (143.87 s).**
  **Done (green light "push and deploy to i7, i will test it"): PUSHED
  b9184d2..c916513, i7 ff-merged to c916513 (compose opt-in + ffprobe
  "Exiting with exit code" patch verified intact, 1/1), container
  rebuilt + recreated, live-verified v1.137036.7f1a7ad — and BOTH of
  the user's exact instructions POSTed to i7 came back READY (the
  multi-audio combine via the LLM first pass with the audio_add plan;
  the 720p/h264/mp2/mpegts combine via the rules pass). The verify
  also exposed the remaining gap — a first pass whose reply never
  parses, or whose plan the PLANNER rejects (the user's own
  'audio-replacement asset in1 is not an audio file' shape), still
  bypassed the second try → see (7).**
  (7) **The second LLM try EXTENDED to parser error +
  planner-rejected plans — IMPLEMENTED + COMMITTED `aa5c5f9`
  (2 files, +243/−28) + version bump `e6e282a`
  (v1.137036.aa5c5f9); PUSHED (c916513..cec7bd2) + i7 DEPLOYED +
  live-verified (2026-10-30, user-authorized):** on i7 the first pass
  IS the LLM
  schema pass (get_adapter("auto") → LLM when a provider is
  configured); a reply that does not parse into an EditPlan (parser
  error) and a plan the planner rejects (an audio-replacement asset
  that is a video — the user's bug-report shape) both returned
  status "error" with the direct second try never running, while the
  same prompt fed by hand returns the correct raw command. Fix
  (`fass/pipeline.py`): `_direct_ready_response()` builds the
  synthetic ready response in one shape for all three triggers
  (defaults note's first clause per trigger: 'found no supported plan'
  / 'failed to produce a valid plan' / 'was rejected by the planner' —
  the user-required first-attempt/second-approach logging kept); the
  parser-error path and the PlanError path now run
  `_llm_direct_command()` when `adapter.name == "llm"` — on a
  validated command the ready response is returned; on failure the
  first pass's error leads and 'second LLM try failed: <the second
  try's own error>' follows, the verbatim second reply becomes
  raw_llm. A rules-built plan being rejected stays a hard error
  (deterministic conflict — keeps the 13 exact-string PlanError
  tests hermetic). `tests/test_pipeline_rag.py` +5 (FakeSchemaLLM:
  parse() + _chat_openai; the bug-report plan shape — AudioReplaceOp
  over a VIDEO asset — over a two-video asset map: plan rejected +
  valid direct command → ready + 'rejected by the planner' note + the
  verbatim second-try query; plan rejected + direct fails → error,
  planner error leads + the second try's own error + raw reply; rules
  plan rejected → no 'llm' adapter request, the planner error stands
  alone; parser error + valid command → ready + 'failed to produce a
  valid plan' note; parser error + direct fails → error, 'parser
  error: …' + 'second LLM try failed: …'). **Full suite: 1640 passed
  / 2 skipped / 0 failed (143.92 s). Done (green light 2026-10-30):
  PUSHED (c916513..cec7bd2), i7 ff-merged + rebuilt + force-recreated,
  live-verified — healthz ok, /v1/env reports v1.137036.aa5c5f9, and
  the user's exact bug-shape instruction (720p/h264/mp2/mpegts
  combine, titan_double.mp4 + titan.ts, auto adapter) came back READY
  via the DIRECT SECOND TRY: the first pass died with 'parser error:
  Unterminated string starting at: line 6 column 7 (char 87)' (a hard
  error on the 7f1a7ad deploy), the second approach got the raw
  command, the gate passed, defaults logged 'the first attempt failed
  to produce a valid plan — the second approach asked the LLM
  directly …', first pass's error preserved in errors; the
  multi-audio instruction came back ready via the LLM first pass
  (audio_add plan). i7 now runs v1.137036.aa5c5f9 — the
  parser/planner-rejection wall is closed. The user's browser test
  confirmed "all is fine for now" (2026-10-30) — the round is CLOSED.
  Next (parked, the user's call): the OPEN 48kbps NLU issue (CLI
  concat "…48kbps aac audio" emitted -b:a 160k), multi-input +
  blackdetect RAG E2E, the optional docs/DEVELOPMENT.md codec list;
  the docs commit 6da28da (this round's PROGRESS/HANDOFF close-out)
  is LOCAL, unpushed — rides with the next user-authorized push.**
  (8) **Remote volume mapping (FTP/SFTP/S3) + S3 uuid naming —
  IMPLEMENTED + COMMITTED `5eca3ab` (10 files, +2352/−14) + version
  bump `0771014` (v1.137036.5eca3ab). i7 was DOWN all round (user away
  ~2 h) → NO i7 deploy; live-verified on the tr4 HOST dev server,
  port 8099, against the tr4 test stores (MinIO 127.0.0.1:9100
  path-style minioadmin, pure-ftpd 127.0.0.1:21 ftpuser, host sshd
  127.0.0.1:22 andrei, LocalStack 127.0.0.1:4566).** Each of the three
  volumes (/input, /output, /log) can now be mapped to a remote store:
  **config** — `RemoteVolumeConfig` (fass/config.py: type
  none|ftp|sftp|s3, host, port (None = default 21/22/443), user,
  password, bucket (s3), path, region (s3), secure (s3 http/https)),
  `extra="forbid"`, defaults make legacy config.json load unchanged;
  `VolumesConfig` gains sibling `input_remote` / `output_remote` /
  `log_remote` (siblings, not nesting — `dockerctl._binds` keeps
  reading the host-path strings untouched). **Client (new
  fass/remote.py)** — `RemoteVolume` ABC (fetch/upload/upload_tree/
  list/delete/test, streamed, top-level list), `FtpVolume` (stdlib
  ftplib, explicit passive, RFC 959 quoting via module `_q()` —
  Python-3 ftplib has no quotefn; root-absolute `"/" + base`),
  `SftpVolume` (paramiko lazy-imported, AutoAddPolicy, connect with
  allow_agent/look_for_keys off + timeouts; relative/empty `path`
  resolves against the REMOTE HOME via `sftp.normalize(".")`, the
  sftp-client initial-cwd semantics — absolute paths used as-is,
  `_base_abs` flag captured before `_norm_prefix` strips the slash),
  `S3Volume` (boto3 lazy; host set → `endpoint_url` +
  `addressing_style="path"` — MinIO 404s on virtual-host — scheme from
  `secure`; host empty → real AWS with region + keys),
  `make_volume()` dispatch + `remote_for(which)` live-reads
  `load_config()` per call (config changes need no restart; the
  running process never cached volumes before). **/input semantics**
  (download-first) — `execute.ensure_input_local(name)`: local-exists
  short-circuit, 30 s negative cache, 5 s error cache (dead-remote
  guard), per-name lock, FAIL-OPEN (a RemoteError never propagates —
  just a note), cached so the hot `resolve_input_name` path doesn't
  re-fetch; wired into `resolve_input_name` (covers every probe/plan/
  not-found consumer transparently), `resolve_paths` (run start), and
  `volume_file` (mediainfo/tsanalyze/vviz/frame//media endpoints).
  **/output + /log semantics** (upload-after) —
  `execute._sync_run_to_remote(run_id)` in the `finally` of
  `_watch` + `_watch_sequence` after the final `_update`: output
  volume — on status "success" only — the recorded output file, the
  verbose log `log/<id>.log`, and the `http/<id>/` subtree via
  upload_tree (HLS/DASH + master.m3u8); log volume — any status —
  `runs/<id>.log` + `runs/<id>.json`, only when `run_dir()` is under
  /log (docker/dev layout); upload failure → run-record note, never
  raises (runs in the finished watcher thread). **S3 uuid naming**
  (user option, s3-only, per-volume) — input: objects keyed by UUID,
  real basename in S3 metadata `fass_name` (S3_NAME_META); list()
  shows the metadata name, fetch downloads the UUID key and renames
  to the basename; output: stored under a generated UUID key with the
  real basename in metadata; bare top-level names only. **API
  (api.py)** — `GET /v1/files?dir=…` merges remote-only top-level
  entries with `"remote": true` (local wins on name collision,
  fail-open); `POST /v1/config/remote-test` tests the SUBMITTED
  unsaved values (llm-test precedent) → {ok, which, type, message},
  RemoteError → 400; `copy-to-input` uploads the copied file to the
  input remote (best-effort) so the remote stays the input source of
  truth. **UI (ui.py, non-f-string — escape-verified clean)** — three
  `<details class="remote-block">` under the Volumes row (input/
  output/log), each: type select, host, port, user, password
  (type="password"), path, bucket, region, secure + a **Test
  (unsaved values)** button; label notes ("port blank = default
  21/22/443"; "S3: host blank = AWS"; "under docker, on-host
  services = host.docker.internal"); JS `readRemote(p)` /
  `loadRemote(p, rc)` / `remoteTest(which, p)`; **`readCfg()`
  extended to send all three `*_remote` objects** — without it the
  full-replacement PUT /v1/config would clobber the mappings.
  **Docker** — compose `extra_hosts: ["host.docker.internal:host-
  gateway"]` on the fass service (host 127.0.0.1 unreachable from
  the container); dockerctl `recreate()` body HostConfig + `old_spec`
  capture + `compose_preview` all carry `ExtraHosts` or Apply would
  silently drop it. **Packaging** — pyproject `[storage] =
  [boto3, paramiko]` (ftp = stdlib); Dockerfile `.[api,storage]`.
  **Tests — tests/test_remote.py, 70 hermetic tests (no real
  network)**: config model (defaults / full round-trip / extra=forbid
  / legacy load), make_volume dispatch + missing-extra error,
  remote_for live-read, fake volume + monkeypatched remote_for →
  ensure_input_local download-then-cache / negative cache / error
  fail-open / no-remote no-op, resolve_input_name / resolve_paths /
  volume_file over a remote-only file, _sync_run_to_remote success-
  only output / always log+sidecar / upload_tree subtree / run_dir
  guard / failure→note, API files merge + remote-test ok/error +
  copy-to-input upload, FTP against a fake ftplib (incl. the real
  API pin: mkd/rmd, no quotefn), SFTP via a FAKE paramiko module
  injected in sys.modules (relative base → remote home; absolute base
  as-is). **Full suite: 1710 passed / 2 skipped / 0 failed
  (143.61 s).** **Live matrix on tr4:8099 (i7 down, host dev server
  reaches 127.0.0.1 directly) — ALL GREEN, per store: s3 17/17,
  s3-uuid 18/18, ftp 17/17, sftp 17/17, none (regression) 6/6:**
  remote-only input listed `remote: true`, remote-test ok ×3, a
  successful run (input downloaded locally, output + verbose log +
  runs/<id>.log + runs/<id>.json mirrored on the remote, verified by
  fetching them back), uuid mode raw-key check (UUID key +
  fass_name=out1.mp4 metadata), a forced-failing run (output NOT
  uploaded, log + record uploaded). **Next: push (same user-authorized
  sequence) — i7 deploy DEFERRED until i7 is back (the host was down
  all round); `_detect_distorted` scans local /output only (a
  distorted clip existing only on a remote output is out of scope);
  stream-purge deletions are not mirrored to remotes (noted, out of
  scope).**
- **Previous state (2026-10-29, programmatic-fill row re-gate + Run now restore —
  COMMITTED 138a563 + PUSHED, i7 DEPLOYED + live-verified 22:53Z,
  suite 1548 passed / 2 skipped):** two user-reported bugs from
  live-testing the deployed row-state build (dae2e31). (1) "buttons are
  disabled on opening … right click->'as input' … at that moment button
  'Mediainfo' must become active !" — the Files-tab right-clicks fill
  fields PROGRAMMATICALLY (no input event), so the rows never re-gated:
  "as input" fills #inp (FFmpeg row stayed all-disabled) and the same
  class for "tsanalyze" filling Info/Log's #mi-file. Fix: `cmdButtons();`
  at the as-input fill site, `miButtons();` at the tsanalyze fill site;
  the other programmatic fills (clearCmdPane/usePrompt/loadPreset)
  already re-gated. (2) "executed a generation … it finished ok, but …
  last button got stuck in state 'starting...'" — runCmd() restored
  '▶ Run now' only in the two failure branches; a single restore() now
  runs on success / HTTP error / network error (the old failure restore
  also dropped the ▶ glyph). **Tests:** row-state test +4 assertions
  (re-gate pinned at both fill sites) + new
  test_ui_run_now_restores_after_every_outcome (restore() count == 3).
  **Verified:** headless-Chrome click test ALL GREEN (the user's exact
  as-input scenario → Mediainfo+Clear on, Generate/Save off; tsanalyze
  row all four on; Run now across all three stubbed outcomes) — one
  false alarm: the first run "failed" on the failure branch because the
  TEST's fetch-stub swap was a call, not an assignment (missing `=`), so
  the "failure" case hit the success stub; the app path was green in
  isolation. **i7 deploy:** pushed 2fc427f..138a563; i7 ff-merged
  dae2e31→138a563 (compose opt-ins + ffprobe "Exiting with exit code"
  patch intact); compose build + force-recreate; live-verified: healthz
  ok, served-page markers (both re-gate calls ×1, restore def ×1,
  restore() ×3, old stuck string ×0, ▶ Run now markup ×1),
  worker/rag unharmed, fass-http pre-existing Exited(127), LLM 8080 →
  200. **Next unfinished task:** user re-test on i7 (as input →
  Mediainfo/Clear come on at the fill moment; Run now restores after a
  run, success or failure). Carry-forward unchanged: user browser E2E of
  multi-input cases + blackdetect RAG; OPTIONAL docs/DEVELOPMENT.md
  codec list; the OPEN CLI concat of "…48kbps aac audio" that emitted
  `-b:a 160k`, not 48k (NLU audio-bitrate-parsing).

- **Previous state (2026-10-29, FFmpeg button row follows its fields —
  COMMITTED 880130e + PUSHED, i7 RE-DEPLOYED + live-verified 22:14Z,
  suite 1547 passed / 2 skipped; user browser-test in progress):** user: "on opening FFmpeg tab very
  first time, after clearing it, deactivate buttons 'Generate' etc, as
  nothing is entered they cant work" — the same row-state pattern as the
  Info/Log fix (c0402a5). The whole FFmpeg row (Generate | Save |
  Mediainfo | Clear) now starts `disabled` in the markup; `cmdButtons()`
  (replaces `syncMiBtn()`, whose single rule moves inside) re-gates it on
  every keystroke of `#instr` (with setCompareMode) / `#inp` / `#out` /
  `#wm` + each extra-input row's field (listener attached in
  addExtra()) + once at load + after every programmatic fill that
  bypasses the listeners (clearCmdPane, usePrompt, loadPreset,
  runMediaInfo's tail): Generate iff instr AND inp non-empty; Mediainfo
  iff inp non-empty AND instr empty (its original rule); Clear iff any of
  instr/inp/out/wm/extra-input has content; Save unchanged (gated on
  lastReady via render()/clearCmdPane()). **Tests:** test_webinfo.py — 3
  syncMiBtn pins reworked (function name, the mi-btn gating expression,
  usePrompt comment) + new `test_ui_ffmpeg_button_row_follows_the_fields`
  (row starts disabled in markup; the three gating expressions; the four
  plain `addEventListener('input', cmdButtons)` listeners + the instr
  wrapper; clearCmdPane re-derives the row). **Verified:** headless-Chrome
  click test ALL GREEN (fresh pane: all four disabled; instruction only:
  just Clear; + input: Generate on, Mediainfo off (instruction present);
  input only: Mediainfo on; nothing: all disabled again; extra-input
  content: Clear enabled; output-only: just Clear; after Clear: fields
  emptied + no extra rows + all four disabled; no page errors) — throwaway
  server on tr4 port 8099, exact-PID killed, temp dirs in
  /tmp/fass_ui_test; full suite 1547 passed / 2 skipped (142.50 s).
  **Context:** the PRESETS feature (8b54574 + docs 812e236) was pushed +
  deployed to i7 + live-verified earlier this session (21:35Z: presets
  round-trip in /config/presets, 404 guard, opt-ins + ffprobe patch
  intact, LLM 8080 untouched); this row fix rode its own deploy round on
  top (22:14Z, same recipe — pushed 812e236..dae2e31, ff-merged
  812e236→dae2e31 with opt-ins + ffprobe patch intact, compose build +
  force-recreate; live-verified: healthz, all row-state markers + exactly
  4 cmdButtons listeners + zero syncMiBtn, presets regression round-trip
  OK, worker/rag unharmed, LLM 8080 200). **Next unfinished task:** user
  browser test of the row states (fresh tab all-disabled; instruction →
  just Clear; + input → Generate on, Mediainfo off; no instruction →
  Mediainfo on; Clear → wiped + all-disabled) + the presets flow. Carry-forward unchanged: user browser
  E2E of multi-input cases + blackdetect RAG; OPTIONAL
  docs/DEVELOPMENT.md codec list; the OPEN CLI concat of
  "…48kbps aac audio" that emitted `-b:a 160k`, not 48k (NLU
  audio-bitrate-parsing).

- **Previous state (2026-10-29, PRESETS feature — PUSHED + i7 DEPLOYED +
  live-verified 2026-10-29 21:35Z (code 8b54574, docs 812e236); user
  browser-test in progress; suite 1546 passed / 2 skipped):** user spec: a preset = a successfully generated ffmpeg
  command set; 'Save' button next to 'Generate' on the FFmpeg pane
  storing Instruction, inputs, outputs, generated ffmpeg string into a
  JSON file named by md5 over instruction+inputs+command; a 'PRESETS'
  block under 'WHAT TO LOOK AT' in Info/Log with a selector whose
  selection shows the full content line-by-line in a small div; on
  confirm the FFmpeg pane fields are filled from the preset and set to
  the generation-accomplished state. **Server (fass/api.py):**
  `PresetSaveRequest` (extra=forbid; instruction/command min_length=1);
  `_presets_dir()` = `$FASS_PRESETS_DIR` (tests) else `config_path().parent
  / "presets"` → container `/config/presets`, persisted by the EXISTING
  /config volume — NO compose change; `_preset_name()` = md5 over
  `instruction + "\n" + json.dumps(assets, sort_keys=True,
  separators=(",",":")) + "\n" + command` + `.json` (assets key order
  does not change the name); `GET /v1/presets` (newest first, corrupt /
  non-preset files skipped, never a 500; rows: name/instruction/
  command[:120]/saved_at/size); `POST /v1/presets` (idempotent
  overwrite; file written WITHOUT sort_keys so the assets' insertion
  order = the -i order survives for the preview; saved_at =
  `time.strftime('%Y-%m-%dT%H:%M:%SZ', gmtime())`); `GET
  /v1/presets/{name}` (raw text, content-type application/json; strict
  `re.fullmatch(r"[0-9a-f]{32}\.json", name)` = the path-traversal
  guard; unsaved → 404, corrupt → 500). **UI (fass/ui.py):** `#savebtn`
  (ghost, next to Generate, `disabled` until a ready render) + `#save-
  status`; `let lastReady = null` — `render()` sets it to the response
  when `d.status === 'ready'` else null and re-gates the button;
  `clearCmdPane()` resets it. `collectAssets()` extracted verbatim from
  `submit()` (compare main/distorted; non-cmp `mainInputs()` split →
  main+m1..mn, watermark, `.extra-inp` rows reading kind via
  `.closest('.row')`) and shared by `submit()` and `savePreset()` — a
  preset always stores exactly what the form would send. `savePreset()`:
  posts instruction + collectAssets() + `$('cmd').value` + output +
  adapter + the three checkboxes + lastReady's arguments/summary/
  defaults/warnings/validation_level/checks/evidence → status "saved
  as <name> (Info / Log tab → presets)". Info/Log: 4th radio
  `#sel-presets` → `#info-presets` (lazy-loaded by `showInfo`, like
  prompts): `#preset-sel` (newest first, option text = instruction +
  saved_at) + ↻ refresh + `#preset-status` + `#preset-preview` (pre,
  max-height 220px, white-space:pre-wrap — the FULL raw file, line by
  line) + `#preset-loadbtn` (ghost, disabled until a preview loaded).
  `previewPreset()` fetches the raw text, sets `presetCurrent`, enables
  Load. `loadPreset()` = restore-by-synthesis: fills instr/inp/out/wm
  (cmp branch when the stored instruction is compare-detect — same test
  the next Generate runs), adapter + the three checkboxes, recreates the
  extra-input rows with their ORIGINAL `inN` ids (so a re-save lands on
  the same md5) bumping `extraN` past them, then `render({status:
  'ready', command, arguments, summary, defaults, warnings,
  validation_level, checks, evidence, raw_llm: null})` +
  `showTab('cmd')` — pixel-identical generation-accomplished state, no
  re-generation (no LLM nondeterminism). **Tests:** NEW
  tests/test_presets.py, 10 tests, ALL PASS (list-empty dir;
  save+list roundtrip + full file shape + assets key order preserved;
  idempotent re-save; name tracks instruction/inputs/command +
  key-order invariant; raw-JSON get; non-preset-name 404s incl. no-.json
  and no-hex; corrupt/foreign files skipped by the list + corrupt
  direct read → 500; malformed bodies 422 (empty command/instruction,
  extra field); Save-button UI markup; PRESETS-block UI markup).
  tests/test_webinfo.py: the `verbose: $('verbose').checked` count
  assertion 2 → 3 (savePreset() is the third site, legitimately).
  **Full suite 1546 passed / 2 skipped (142.76 s).** **Next unfinished
  task:** user green light for push + i7 deploy (deploy = `git fetch
  origin && git merge --ff-only origin/main` + `docker compose -f
  docker/compose.yaml build fass` + `up -d --force-recreate fass` — NO
  compose change; presets land in /config/presets on the existing
  volume; verify opt-ins + ffprobe patch intact after merge), then
  user browser test: Save a generation → Info/Log → presets → select →
  read the preview → Load → FFmpeg pane filled + generation-accomplished
  state; re-Save from the restored pane → same md5 (idempotent). Carry-
  forward still open: user browser E2E of multi-input cases +
  blackdetect RAG; OPTIONAL docs/DEVELOPMENT.md codec list; the OPEN
  CLI concat of "…48kbps aac audio" that emitted `-b:a 160k`, not 48k
  (NLU audio-bitrate-parsing).

- **Previous state (2026-10-29, Info/Log row follows the File field (user
  round 2) — COMMITTED c0402a5 + PUSHED, i7 RE-DEPLOYED + VERIFIED, suite
  1536 passed / 2 skipped):** user: opening Info/Log → ALL buttons disabled
  (nothing can run until a file is selected); file name entered → ALL
  enabled; Clear press → the File field is emptied too. All four buttons
  (Show media info / TSAnalyze / Bitrate / Clear) start `disabled` in the
  markup and in `fillMi()`; `miButtons()` on the File input's `oninput`
  enables all four iff the field is non-empty (typed or auto-filled) and
  re-disables when emptied; `clearMiPane(wipeFile)` — Clear calls
  `clearMiPane(true)` — additionally empties the field (→ row disabled),
  while `showVviz()`'s internal `clearMiPane()` keeps the file; `miBusy`
  keeps the vviz row lock in force mid-generation. `showMediaInfo()` still
  re-gates TSAnalyze/Bitrate by the probed format/codec (server enforces
  the same checks). Also fixed: `showTSAnalyze()` re-enables its button in
  a `finally` (a 400 previously left it stuck disabled — now a wide path
  since any entered file enables the row). test_api.py +5 assertions.
  **Verified:** headless-Chrome click test ALL GREEN (tab open no file →
  all four disabled; file entered → all four enabled; Clear → field
  emptied + row disabled; Bitrate flow unchanged: chart 792×638 in the
  sandboxed blob iframe, modebar hidden, lock/clear/restore, no JS errors);
  full suite 1536 passed / 2 skipped; i7 re-deploy (ff-merged → c0402a5,
  compose opt-ins + ffprobe.py patch intact, compose build +
  force-recreate) live-verified (healthz ok; UI tokens incl.
  `oninput="miButtons()"` / `clearMiPane(true)` / disabled mi-showbtn;
  titan.mp4 → 200 + plotly; fass-worker/fass-rag healthy). **Next
  unfinished task:** user browser re-test of the fixed build on i7, then
  the queued PRESETS feature (Save button next to Generate on the FFmpeg
  pane; PRESETS block under "WHAT TO LOOK AT" in Info/Log; preset JSON
  files named by md5 over Instruction+inputs+ffmpeg command; selector →
  line-by-line preview in a small div → confirm fills the FFmpeg pane
  fields and leaves it in the generation-accomplished state). Carry-forward
  still open: OPTIONAL docs/DEVELOPMENT.md codec list; the OPEN (user's
  call) CLI concat of "…48kbps aac audio" that emitted `-b:a 160k`, not 48k
  (NLU audio-bitrate-parsing).

- **Previous state (2026-10-29, vviz UI fix batch (user's first live test) —
  COMMITTED 255fc2e + PUSHED, i7 RE-DEPLOYED + VERIFIED, suite 1536 passed /
  2 skipped):** user live-tested the i7 build: clicking Bitrate gave a "blank
  browser page, and then it is downloaded"; "the button does not
  disable/dim"; plus: clear the previous outputs (mediainfo/tsanalyze/other
  vviz) before the chart; the chart must NOT be downloadable (everything
  stays inside the web UI); button "Bitrate"; while processing all other
  row buttons dim + disable; a "Clear" button next to "Bitrate". **Root
  cause of the blank page (empirical A/B matrix, /tmp/vviz_e2e/iframe_diag7-
  13.py):** a SANDBOXED SRCDOC document's root box stays 0×0 in Chrome
  (srcdoc takes precedence over src; the frame is about:srcdoc) → plotly.js
  measures 0×0 and falls back to a 700×450 svg (invisible chart). Same
  document: no sandbox → renders; sandbox + real URL → renders; only
  srcdoc+sandbox breaks. **Fixes (fass/ui.py + fass/api.py):** `showVviz()`
  loads the self-contained Plotly document via a blob: URL in the sandboxed
  iframe (`frame.src = URL.createObjectURL(new Blob([text],{type:'text/html'}))`,
  previous blob revoked, unhide before load) — sandbox `allow-scripts`
  stays, so download/popup/navigation remain structurally blocked;
  `hideVvizFrame()` (hidden + src=about:blank + revoke) centralises the
  one-output-at-a-time clearing (fillMi/clearMiPane/showMediaInfo/
  showTSAnalyze); row lock disables all four buttons while generating
  (restored in `finally { unlock(); }`) + `#info-mi button:disabled{opacity:.5}`
  dim + remux status text; `#mi-clearbtn` (Clear) wipes report + chart +
  status; "Bitrate" label. `_vviz_finalize_html()` (renamed from
  _vviz_strip_modebar) injects into the head: `html,body{height:100%;margin:0}`
  + `body>div{height:100%}` (vviz's plot div is height:100% inside an
  auto-height wrapper — the percentage chain cannot resolve without it) +
  `.plotly-graph-div{width:100vw!important;height:100vh!important}`
  (viewport-pinned, structure-independent backstop) +
  `.modebar{display:none!important}` (plotly's download bar). **Verified:**
  headless-Chrome click test on tr4 ALL GREEN (chart renders INSIDE the
  sandboxed iframe at 792×638 via the blob URL, svg matches the frame,
  modebar display:none, no new window, lock/clear/restore, no JS page
  errors); full suite 1536 passed / 2 skipped; i7 re-deploy (ff-merged →
  255fc2e; compose opt-ins + ffprobe.py patch intact; `docker compose build
  fass` + `up -d --force-recreate fass`) live-verified (healthz ok; UI
  tokens incl. createObjectURL/hideVvizFrame; titan.mp4 → 200 + plotly + all
  new CSS rules; titan.ts remux → 200; no `.vviz-*` leftovers;
  fass-worker/fass-rag healthy; LLM 8080 → 200). **Next unfinished task:**
  user browser re-test of the fixed build on i7, then the queued PRESETS
  feature (Save button next to Generate on the FFmpeg pane; PRESETS block
  under "WHAT TO LOOK AT" in Info/Log; preset JSON files named by md5 over
  Instruction+inputs+ffmpeg command; selector → line-by-line preview in a
  small div → confirm fills the FFmpeg pane fields and leaves it in the
  generation-accomplished state). Carry-forward still open: OPTIONAL
  docs/DEVELOPMENT.md codec list; the OPEN (user's call) CLI concat of
  "…48kbps aac audio" that emitted `-b:a 160k`, not 48k (NLU
  audio-bitrate-parsing).

- **Previous state (2026-10-29, vviz bitrate visualisation — COMMITTED 3e3a25b +
  PUSHED, i7 DEPLOYED + VERIFIED, suite 1535 passed / 2 skipped):**
  vviz_py (bitrate/GOP/fragment/frame Plotly chart; reads mp4 only, needs
  Bento4 mp4dump) baked into the image: new `bento` builder stage → static
  mp4dump (SDK 1-6-0-641) at /usr/local/bin; venv RUN += `plotly==5.24.1
  pandas scipy pytz` + `git clone --depth 1 wabiloo/vviz_py /opt/vviz_py` +
  pkt_pts→pts sed patch (ffprobe≥5 dropped `pkt_pts`). Server: `POST
  /v1/vviz {target: input|output, file, confirm=false}` — bare-name jail →
  ffprobe codec gate {h264,hevc,av1,vp9} (400; "av2" in the request = VP9,
  confirmed) → mp4 sources BYTE-COPIED, non-mp4 `ffmpeg -c copy -f mp4`
  remux with a 409 confirm gate when duration > 20 min (UI `confirm()`s the
  detail then re-POSTs confirm:true) → 507 when output free ≤ source size →
  temp workdir `.vviz-*` ALWAYS in /output (vviz shells out with shell=True,
  so a space-free temp name) → `vviz.py -f interactive` → its Plotly HTML
  (written next to the target) served as HTMLResponse; workdir rmtree'd in
  finally. UI: "bitrate visualisation" button + 640px hidden iframe under
  the Info/Log mediainfo block, gated on the probed video codec (mirrors
  tsbtn's mpegts gate). +13 tests (503 unavailable, schema 400/422, 404,
  codec gate + audio-only, output-volume source, mp4 copied-not-remuxed,
  409→confirm→remux argv, short non-mp4 direct, remux-failure 400, 507
  faked disk_usage, unmounted output 503, E2E skipped without stack, UI
  marker). **tr4 in-container E2E (port 8099):** all four codecs ×
  mp4-direct/remux → 200 + Plotly (~4.6 MB each); mpeg4 → 400; 21-min ts →
  409 w/o confirm, 200 + 6.95 MB chart in 5.3 s w/ confirm; clean /output;
  UI markers present. **i7 deploy:** ff-merged b7ad2eb→3e3a25b (`git fetch
  origin && git merge --ff-only origin/main`; compose.yaml opt-ins +
  ffprobe.py patch preserved and verified), `docker compose build fass`
  (clean) + `up -d --force-recreate fass`. **Live-verified:** healthz ok;
  UI markers; titan.mp4 (mp4 direct) 200+plotly; titan.ts (remux)
  200+plotly; no output leftovers; fass-worker/fass-rag unharmed; fass-http
  already `Exited (127)` 14 h pre-deploy (pre-existing, NOT caused by the
  deploy); LLM on 8080 untouched. **Next unfinished task:** user browser
  E2E of the vviz button on i7, then the queued PRESETS feature (Save
  button next to Generate; PRESETS block under "WHAT TO LOOK AT" in
  Info/Log; preset JSON named md5 over Instruction+inputs+ffmpeg command;
  selector → line-by-line preview → confirm fills the FFmpeg pane in the
  generation-accomplished state). Carry-forward still open: OPTIONAL
  docs/DEVELOPMENT.md codec list; the OPEN (user's call) CLI concat of
  "…48kbps aac audio" that emitted `-b:a 160k`, not 48k (NLU
  audio-bitrate-parsing).

- **Previous state (2026-10-29, VOD-HLS truncation: `hls_list_size` — COMMITTED
  b7ad2eb + PUSHED, i7 DEPLOYED + VERIFIED, suite 1523 passed / 1 skipped,
  E2E ffmpeg 9.0 13/13 segments, live API emits `-hls_list_size 0`):**
  FFmpeg 9.0 changed the hls muxer's default `hls_list_size` from 0 (keep all)
  to 5, so a finite (non-event) HLS transcode silently rolled the playlist down
  to its last 5 segments — Shaka played only the clip's tail. Fix (planner.py):
  inject `-hls_list_size 0` for a VOD hls output when the user hasn't set one,
  in BOTH the plain-hls path and the tee hls branch; an explicit user value and
  an `event` (live) playlist are left untouched. **+3 tests** (default /
  explicit / event) + 3 existing pins updated to the new VOD argv shape.
  **i7 deploy:** ff-merged f166b06→b7ad2eb (`git fetch origin && git merge
  --ff-only origin/main`; local compose.yaml opt-ins + ffprobe.py patch
  preserved through the merge), `docker compose -f docker/compose.yaml build
  fass` + `up -d --force-recreate fass`. **Live-verified:** healthz ok;
  `POST /v1/commands` (the user's exact instruction on titan_double.mp4) →
  status ready, command ends `-c:a aac -b:a 32k -hls_list_size 0
  titan_double.m3u8` + the "VOD HLS keeps the whole clip — -hls_list_size 0"
  default note; fass-http/fass-worker/fass-rag unharmed. **Doc-recording
  (HANDOFF.md + PROGRESS.md) COMMITTED fd86ba1 + PUSHED** (origin/main; i7
  checkout still at b7ad2eb — docs-only, no i7 runtime impact). **Next
  unfinished task:** carry-forward still open from the multi-input era: user
  browser E2E on i7 (2 multi-input cases + `blackdetect` in the RAG tab);
  OPTIONAL docs/DEVELOPMENT.md codec list; and the OPEN (user's call) CLI
  concat of "…48kbps aac audio" that emitted `-b:a 160k`, not 48k (an NLU
  audio-bitrate-parsing matter).

- **Previous state (2026-10-29, multi-input "Generate does nothing" + RAG
  `blackdetect` + dead compare regex — COMMITTED 5e38838 + PUSHED, i7
  DEPLOYED + VERIFIED, suite 1520 passed / 1 skipped; README.md committed in
  f166b06):** three bugs fixed. (1) Multi-input: the web UI "input video"
  field accepted `-i a -i b`; the server `_strip_i_prefix` (api.py) splits on
  the FIRST whitespace, so the field collapsed to the broken path `a -i b` →
  dead Generate. Fix (ui.py): client-side `mainInputs()` splits the main field
  on standalone `-i` tokens before submit → `assets.main` + `assets.m1…`;
  add-input `kind` read via `.closest('.row')` (was `parentElement` — the
  `<select>` is a sibling of the input wrapper, not its parent). (2) RAG
  `blackdetect` → nothing: ffmpeg groups filters under comma-joined headings
  ("blackdetect, blackdetect_vulkan") with `component_name` NULL (11 such
  family chunks), so the exact `component_names` SQL filter on a NULL column
  returned 0 rows. Fix (rag/retrieval.py, code-only, NO re-ingest):
  `_component_names_for` derives a chunk's names from `component_name` + the
  heading-leaf comma-tokens (gated by `_IDENT_RX = ^[a-z0-9_-]+$`), the index
  maps every name → chunk, and `component_names` is applied as a PYTHON
  post-filter with the exact boost re-applied → `blackdetect` now returns
  `supported`. (3) Bonus: `CMP_RX` (ui.py:546) compare-detection was DEAD — a
  single `\b` in the one big non-f-string UI_HTML is a Python backspace (0x08)
  baked into the served page, so `isCompare()` was always false; doubled all JS
  regex escapes (`\\b`/`\\s`). **Tests:**
  `test_ui_multi_input_generate_sends_all_inputs` (webinfo; its global
  `"\x08" not in html` invariant is what surfaced CMP_RX) +
  `test_component_name_filter_multi_name_heading` (rag). Full suite **1520
  passed / 1 skipped** (exit 0). **i7 deploy:** ff-merged 0ffc75a→5e38838
  (`git fetch origin && git merge --ff-only origin/main`; the local
  compose.yaml opt-ins + ffprobe.py patch untouched), `docker compose build
  fass fass-rag` (the retrieval fix is baked into the **fass-rag image** —
  Dockerfile.rag re-ingests the corpus at build time, NO volume) + `up -d
  --force-recreate fass fass-rag`; fass-worker/fass-http untouched.
  **Live-verified:** `POST /v1/rag/query` "blackdetect" → `supported` + the
  family chunk (was NOTHING pre-fix), "blackdetect_vulkan" supported,
  "scale_cuda" control intact; served page has no control byte + all
  multi-input markers. **README.md (committed in f166b06):** new codecs
  (AV1-libaom default / SVT-AV1 / VP8 / Theora / VVC) + OGV in the container
  list; the `-i`-tokens-in-main-field input method; the full UI tab list (was
  "Command / Runs / Config"). **Next unfinished task (as of that state):**
  (a) commit README.md + push — DONE in f166b06; (b) OPTIONAL —
  docs/DEVELOPMENT.md line 277 codec list still reads "(H.264 / H.265 / VP9,
  AAC / Opus)", add the new codecs;
  (c) user browser E2E on i7 (2 multi-input cases + `blackdetect` in the RAG
  tab); (d) OPEN (user's call) — the CLI concat of "…48kbps aac audio" emitted
  `-b:a 160k`, not 48k (an NLU audio-bitrate-parsing matter, not yet touched).
- **Previous state (2026-10-28, i7 LLM timeout fix — COMMITTED fbcd8ab +
  PUSHED, i7 DEPLOYED + VERIFIED; suite 1473 passed / 1 skipped):**
  User report: "test llm" OK, but generating a command on i7 fails with
  `parser error: LLM provider unreachable: timed out`. Root cause:
  `LLMConfig.timeout` in fass/config.py defaulted to **10 s** and feeds
  every generated adapter (`_llm_from_config` in fass/nlu/__init__.py,
  the llm-test endpoint) — silently shadowing `LLMAdapter.__init__`'s
  own 90 s default. i7's local LLM (strata server, small 3-bit-quantized
  thinking model behind the qwen3.8-27b name, host 172.17.0.1:8080)
  needs 10–21 s for the full ~54 KB fass system prompt, so every
  generation died at exactly 10 s; the tiny "LLM test" prompt fit under
  the limit, masking it. Proven by a urlopen tracer in the container
  (`timeout=10 FAIL after 10.0s`) + raw probes (10–21 s, RAG
  evidence-independent). Fix: config default 10 → **90** (still
  user-settable 1–300 via the Config tab); UI hint/placeholder
  updated (fass/ui.py); new test
  `test_llm_timeout_tracks_adapter_default` (test_m6_config.py) pins
  the config default to the `LLMAdapter.__init__` `timeout` parameter
  via `inspect.signature` so the two cannot drift apart; 5 existing
  pins updated (test_m6_config ×4, test_m6_api ×1, test_m6_llm ×1) →
  **1473 passed / 1 skipped** (1472/1 + 1 new). i7 deploy: repo
  ff-merged 0240b51→fbcd8ab via `git fetch origin && git merge
  --ff-only origin/main` (i7's main has NO upstream tracking — plain
  `git pull` fails with the set-upstream hint); `docker compose build
  fass` + `up -d --force-recreate fass` (local compose opt-ins
  untouched; fass-worker/fass-http/fass-rag unharmed); in-container
  `load_config().llm.timeout == 90`. Verified via i7's API — the
  user's exact flow: `POST /v1/commands` "pseudo-live hls with a 10
  segment window" → `status: ready`, `adapter: llm`, `realtime_input:
  true`, argv `ffmpeg -hide_banner -loglevel error -re -i
  /input/titan.mp4 -map 0:v:0 -map 0:a? -f hls -c copy
  -hls_list_size 10 titan.m3u8`, wall **14.8 s** (previously
  hard-failed at 10 s). Debug artifacts cleaned from i7 /tmp + tr4
  (kept: /tmp/compose.yaml.i7-optins, /tmp/ffprobe-i7.patch,
  /tmp/PROGRESS.md.i7 — i7 local-state safety backups). Next: user
  browser E2E on i7 (Command tab → "pseudo-live hls with a 10 segment
  window" → serve over http → Player tab → play while generating).
- **Previous state (2026-10-28, M13b (-re pseudo-live input pacing) —
  COMMITTED + PUSHED as 0240b51, i7 DEPLOYED + API-verified; suite
  1472 passed / 1 skipped):** The last
  M13-series item. "pseudo-live" / "read at real time" → `ffmpeg … -re
  -i in …` — the MAIN input is read at native frame rate (real time)
  instead of as fast as ffmpeg can, so e.g. a live (event) HLS playlist
  is followed by a player while it is still being generated. Schema:
  `EditPlan.realtime_input: bool = False` (plan.py, between `trim` and
  `bitstream_filters`; LLM adapter auto-exposed via the
  model_json_schema prompt; unset → byte-identical argv, the whole
  suite is the regression gate). NLU (rules.py): house tri-state
  detector — `_REALTIME_ON` (`pseudo-live`/`pseudo live`/`pseudolive`,
  `read [the input] at real time`) → True; `_REALTIME_OFF` (`no
  pacing`/`without pacing`/`don't pace`/`not at [in] real time`) →
  False; silence → None (merge keeps the plan's value). "pseudo-live
  hls" matches the M13.2 live-HLS regex AND `_REALTIME_ON` ON PURPOSE
  (hyphen = word boundary) — reference case 6. Pacing-only phrases
  without an action stay needs_clarification per house behavior (tests
  carry an action word). Planner: `-re` appended to
  `ep.inputs[0].pre_opts` at the main return — AFTER
  `_attach_stabilize_passes` (needs `ep.passes`) and BEFORE
  `ep.warnings.extend(warnings)` (a first version appended after the
  merge and the stream-input warning was silently lost — caught by
  test, moved). Main input ONLY (watermark/replacement audio never
  paced); multi-pass (stabilize) earlier passes get a plain InputSpec
  copy — only the FINAL (transform) pass is paced; diagnostic plans:
  no `-re` + a defaults line; stream input: `-re` emitted + "no
  effect" warning. Executor: `-re` added to `_BOOL_FLAGS` — the E2E
  run was refused ("unknown flag '-re' (not in the builder's
  vocabulary)") until then; the suite checks argv shape, not the
  executor vocabulary. Tests: 11 new (test_rules.py: reference case 6
  exact argv, plain output, phrasings, word variants, "live hls"
  untouched, negation, finite ENDLIST coexists, stream no-effect,
  main-only pacing, revision on/off) + 2 (test_stabilize.py: NLU
  combined phrase; `build_argv_all` — `-re` in transform argv, NOT in
  detect argv) → **1472 passed / 1 skipped** (baseline 1459/1 + 13).
  E2E both halves: tr4 host 6.1.1 via `execute.start_run` on
  titan.mp4, "trim the first 6 seconds, pseudo-live" → `ffmpeg … -ss
  0 -re -i titan.mp4 … -t 6 …` success exit 0, **wall 6.5 s** paced
  vs **1.20 s** unpaced control (5.4× = real-time pacing); docker
  `fass:m133-verify` (9.0.1) same argv in-container → **wall 6.9 s**,
  1.68 MB out.mp4. **Next unfinished task:** commit M13b; user
  browser E2E of live HLS/DASH playback on tr4/i7 still owed
  (serve_http run → Player tab → play while generating; M13b is its
  paced generator — "pseudo-live hls" = reference case 6).
- **Previous state (2026-10-28, M13.7 Player tab + shaka-player swap +
  fass-http CORS — COMMITTED & PUSHED as fc25cd5 (origin/main); suite
  1459 passed / 1 skipped):** The last numbered M13 milestone. Player
  tab (between Runs and Info) over the fass-http sidecar's stream
  tree: manifest list (name/kind/state/size/modified) with a `live`
  badge while the owning run is still running (else `vod`), ~5 s
  auto-refresh while the tab is open, `served at <base_url>` line,
  per-row **play**, and a **Purge stream tree** button (confirm) →
  `POST /v1/streams/purge` (409 while ANY run is running; else counts
  + rmtree's `{output}/http` → `{"purged": true, "files": n}`).
  api.py: `GET /v1/streams` entries gained `size`/`mtime`/`live`
  (run_id from the `http/<run-id>/…` path prefix, live = that run's
  status "running"); `GET /static/{name}` = FIXED allowlist (only the
  vendored player lib — no CDN, no directory browsing; any other name
  or path-separator → 404). execute.py: `Run.http` flag (serve_http
  runs) + `to_dict`. ui.py: `serve over http` checkbox on the Command
  tab (runCmd body `serve_http`); the Runs tab's output cell links
  http runs to the Player tab. **Player library — user directive to
  replace the hls.js + dash.js pair with shaka-player:** ONE vendored
  UMD build `fass/static/shaka-player.compiled.js` (shaka-player@5.3.2,
  886,066 B, Apache-2.0, global `shaka`; + LICENSE + SHA256SUMS +
  VENDORED.md pin table) plays BOTH .m3u8 and .mpd through one API.
  v5 API facts (v4-era assumptions are WRONG): no static `create` —
  `new shaka.Player(video)`; `await player.load(url)`; `destroy()`;
  `await shaka.Player.isBrowserSupported()`; `sh.Player.version` is
  the STRING "v5.3.2" (not a function); fatal errors via
  `addEventListener('error', e => e.detail)` with `severity ===
  'fatal'`; node smoke test needs `globalThis.self = globalThis`
  first. **fass-http CORS (dispatched job j_a9a4961bd0 on
  tr4-secondary, cherry-picked from 787ca79):** new
  `docker/fass-http/httpd-fass.conf` — `FilesMatch
  "\.(m3u8|mpd|ts|m4s)$"` → `Access-Control-Allow-Origin *`;
  `FilesMatch "\.(m3u8|mpd)$"` → `Cache-Control no-cache` (live
  playlists grow in place; browsers would otherwise serve a stale
  copy); compose.yaml: fass-http runs `httpd-foreground -c "Include
  conf/extra/httpd-fass.conf"` + read-only conf mount (`FASS_HTTP_CORS_CONF`
  override) — the stock httpd:2.4-alpine httpd.conf has NO live
  conf/extra glob include (all commented except proxy-html), so the
  include is explicit; mod_headers is loaded by default. E2E-verified
  by the worker on port **8099** (host 8080 is taken by the strata
  LLM server — never 8080 for test containers). Tests: +7 (+2
  assertions) — static serve/allowlist/traversal, streams
  size/mtime/live incl. live-while-running (monkeypatched
  `execute.list_runs`), purge empty/real/409, UI wiring incl.
  `new shaka.Player(`; + 2 pre-existing pin fixes (the JS-syntax
  test now selects the line containing "saved config?" — the purge
  confirm is the first `confirm(` in the served script; the files-tab
  pin `.concat('rag','files')` → `.concat('rag','files','player')`).
  Gates actually run: full suite `./.venv/bin/python -m pytest -q` =
  **1459 passed / 1 skipped (142.69 s)**; served `<script>` (36,625
  ch) `node --check` OK; `sha256sum -c SHA256SUMS` OK. **Known
  problem / owed:** no browser exists in this environment — live
  playback E2E is owed on a machine with one (tr4/i7 web UI); the
  node smoke (UMD load + v5 API surface) is the verification ceiling
  here. **Next unfinished task:** user browser E2E of live
  HLS/DASH playback on tr4/i7 (serve_http run → Player tab → play
  while generating); then M13b (-re pseudo-live).
- **Previous state (2026-10-28, i7 docker volume mapping + install script —
  UNCOMMITTED at the time, commit only on user request):** i7 volumes remapped to
  `/dd2/andrei/fass/media/{input,output,log}` (user-created folders;
  persistent). Root cause of the empty Files tab + failed Apply:
  containers bound **stale inodes** of the deleted+recreated
  `docker-data/{input,output,log}` dirs (container saw sdh3 inodes,
  host dirs live on sdi1); fresh binds on new paths resolve correctly.
  New mechanism (repo-tracked, works on tr4 too): `docker/install.sh`
  asks ONLY for host folders at install (input/output/log/config,
  defaults `<repo>/media/*` + `<repo>/docker-data/config`; LLM model
  NOT asked — Config tab's job), writes `docker/.env` (git-ignored,
  machine-local) + seeds/merges the `config.json` volumes section,
  builds + starts. `compose.yaml` interpolates `${FASS_INPUT_DIR:-
  ../media/input}` etc. (repo-relative defaults — compose resolves
  relative to the compose file's dir). **Config tab owns paths after
  install**: Apply re-creates from config.json (verified: apply-result
  "applied", media binds, GPU + docker.sock carried over), and
  `launcher.sh sync_env` re-syncs `.env` from config.json before every
  compose up (verified by simulated UI change → .env rewritten →
  restored). i7 local compose keeps its opt-ins (socket mount,
  FASS_DOCKER_MANAGE=1, GPU pin device_ids ["1"]) — compose.yaml stays
  UNCOMMITTED on i7 per PRD 6.7; install.sh/launcher.sh/.gitignore/
  docker README are repo changes. Gotcha found: `compose up` does NOT
  recreate an unchanged fass-worker → its /config bind stayed stale;
  fixed with `up -d --force-recreate fass-worker` (do that whenever
  config dirs are remapped). E2E verified on i7: Files tab lists 6
  assets (titan.mp4 + test set copied into media/input), webm run
  success → titan.webm in media/output, run record+log in
  media/log/runs. i7 `docker/.env` = media paths; host
  `docker-data/config/config.json` volumes = media paths.
- **Previous state (2026-10-27, M13 IMPLEMENTATION — M13.1 + M13.2 +
  M13.3 + M13.4 COMPLETE on tr4 6.1.1, DUAL-BUILD E2E GREEN (6.1.1 +
  9.0.1), suite 1365/1):**
  Plan (unchanged, FINAL, ca52114):
  /home/andrei/.claude/plans/majestic-sauteeing-ullman.md. M13.1
  (registry 2.9.0 + single-variant DASH) is implemented and
  E2E-verified on tr4 6.1.1 — suite **1301 passed / 1 skipped**
  (M13.1 + M13.2 + Job A + Job B together; Job B alone in its
  worktree 1275/1)
  (main tree, M13.1 + Job A + Job B together; Job B alone in its
  worktree 1275/1). E2E 3/3 via /tmp/fass_e2e_m13/run.py
  (start_run on media/titan.mp4): DASH copy
  (`-f dash -c copy dash_e2e.mpd`), DASH re-encode
  (`-vf scale=-2:480 -c:v libx264 … dash_reenc.mpd`), and the
  user-reported .ts/movflags bug repro (defaulted mp4 + .ts
  destination now reconciles to mpegts, no -movflags in the .ts
  argv; ffprobe: .mpd = dash 20.0 s, .ts = h264 1280x720 + aac).
  M13.1 change set (UNCOMMITTED in the working tree — commit only
  on user request): registry.json 2.9.0 (containers/matrix/
  format_options.dash 6 opts, hand-formatted), loader.py
  `_FORMAT_OPTION_GROUPS` += dash, plan.py container Literal +=
  dash, rules.py dash/mpd words + `.mpd` phrase + SUPPORTED_SUMMARY,
  planner.py DASH special case (muxer="dash" + force .mpd, mirrors
  HLS L1501-1511) + file-destination container-consistency PlanError
  (AFTER the overwrite guards), pipeline.py
  `_reconcile_output_destination` file branch (defaulted container
  follows the destination extension; explicit conflict → planner
  error). 6.1.1 DASH facts: works with -c copy AND libx264; output
  .mpd + init-streamN.m4s + chunk-streamN-NNNNN.m4s; ffprobe
  format_name=dash. M13.2 (live HLS) is also implemented +
  E2E-verified on 6.1.1 — see the M13.2 record below. **9.0.1
  docker half DONE**: base jrottenberg/ffmpeg:9.0-nvidia2404
  pulled; fass:m132-verify image BUILT from the working tree
  (no commit needed — docker build uses the tree); in-container
  E2E (script /tmp/fass_e2e_m13/run901.py, container fass-m132-e2e
  with media/ on /input) — all three runs success exit 0 on
  9.0.1: DASH copy (`-f dash -c copy`), DASH re-encode
  (scale=-2:480 libx264), live HLS (event + list_size 10 +
  independent_segments → EVENT playlist, 11 segments, ENDLIST,
  byte-identical playlist shape to 6.1.1). Both .mpds
  well-formed (type=static, PT20.0S, video+audio sets),
  cross-probed with host 6.1.1 ffprobe → `dash` 20.0 s.
  **9.0.1 BUILD LIMITATION found (not a fass bug)**: the
  jrottenberg build has NO libxml2 → its ffprobe/dash demuxer
  cannot parse ANY mpd (fails identically on 6.1.1-made mpds;
  the demuxer is present, MPD parsing just fails) — verify
  DASH on 9.0.1 structurally (XML + segments) or cross-probe
  with a libxml2-enabled ffprobe. **DASH SEGMENT-NAME COLLISION
  noted for M13.6**: the dash muxer names segments
  init-streamN.m4s / chunk-streamN-NNNNN.m4s independent of the
  .mpd name — two DASH runs in one shared output dir (the
  future http/ folder) overwrite each other's segments; the
  M13.6 design must address it (document / single_file / unique
  dirs). i7's live fass image deliberately NOT rebuilt
  (worker follow-up; needs a committed push — parked with the
  M13.1 commit).
  Two delegated jobs both INTEGRATED into main this session:
  **Job A j_bf5977d1b3** (tr4-secondary) — editable Command tab,
  revalidated server-side (api.py RunRequest.command + shlex split
  + 422s; ui.py textarea; 7 tests) — ff-merged, **5e4b2bd**,
  dispatch_integrate "accepted". **Job B j_ae29ca471d** (i7) —
  RAG baked into the fass-rag image + RAG default in compose
  (Dockerfile.rag ingests rag/corpus/ at build time, no volume;
  compose.yaml fass-rag default service + FASS_RAG_ENABLED=1/
  FASS_RAG_URL; compose.rag.yaml retired; README; 32 tests) — the
  dispatcher record transiently showed failed/exit-1 but the work
  was intact in the tr4 worktree (commit e87c708 on
  dispatch/j_ae29ca471d); reviewed, rebased onto main, ff-merged
  as **64a0111**, dispatch_integrate "accepted" (record now
  succeeded/exit-0). i7 live: docker-fass-rag-1 up healthy with
  baked corpus (1820+836 chunks), compose.yaml.bak-rag backup
  kept, i7 live compose hand-merged per PRD 6.7.
  M13.2 (live HLS) IMPLEMENTED + E2E-verified on 6.1.1 this session
  (suite **1301/1**): rules.py `_detect_format_options` live block
  ("live hls" → hls_playlist_type=event + hls_flags=
  independent_segments; "…with a N segment window" → hls_list_size=N;
  "…delete old segments" → delete_segments OPT-IN only; explicit
  key=value wins over the NL map); planner finite-input warning
  (event + file input → "#EXT-X-ENDLIST … for true live use a stream
  input (or -re)"; stream input → no warning); SUPPORTED_SUMMARY.
  6 tests. E2E (titan.mp4): `… -f hls -c copy -hls_flags
  independent_segments -hls_list_size 10 -hls_playlist_type event
  live_e2e.m3u8` → success exit 0, EVENT playlist + ENDLIST (finite)
  + 11 segments. LATENT BUG FIXED en route: the flagset validator
  splits on '+' (FFmpeg flag syntax), but the registry descriptions
  for hls_flags / flvflags and the llm.py prose said "comma-joined" —
  a comma-joined multi-flag value fails validation; all three now
  say '+'. M13.1 + M13.2 9.0.1 docker halves ride on the
  fass:m132-verify build (base jrottenberg/ffmpeg:9.0-nvidia2404
  pulled on tr4; image built from the working tree — no commit
  needed); in-container E2E GREEN (3/3 runs success, DASH + live HLS
verified — see Last state block for the 9.0.1 libxml2 limitation +
DASH segment-name collision notes). M13.3 (ABR HLS ladder +
  post-run master.m3u8) and M13.4 (DASH ladder, auto adaptation sets)
  are now COMPLETE — records below; then M13.5 (tee bracket
  template) → M13.6 (Apache + /output/http) → M13.7 (Player tab).
  M13.1 planning/design record (exploration jobs j_f7d3350725 /
  j_c5a42bb4ec / j_0130ee5802, capability matrix, design calls,
  mid-generation probes) remains valid as context — see the M13
  plan file. M12 record follows.
- **M13.3 (ABR HLS ladder + post-run master.m3u8) COMPLETE 2026-10-27
  — DUAL-BUILD E2E GREEN, suite 1360/1, UNCOMMITTED (same working
  tree).** Schema: VariantSpec (width/height 16..8192,
  video_bitrate_kbps 100..200000, audio_bitrate_kbps 32..3840,
  video_profile baseline/main/high, video_level ≤8 chars) +
  OutputSpec.variants (max 8; container must be hls/dash; re-encode
  path only — copy cannot make levels; planner caps levels at the
  probed input height, never upscales; unset per-level fields fall
  back to the default-table row for that height). Default table
  (planner module constant): 216p/300k/600k/main/3.0/56k,
  360p/700k/1400k/main/3.1/64k, 480p/1500k/3000k/main/3.2/80k,
  720p/2500k/5000k/main/3.1/96k, 1080p/4500k/9000k/main/4.0/128k
  (bufsize = 2× video kbps); default count 3 → a 720p input gets
  360/480/720. **Mechanism deviation from the plan (empirically
  refuted):** the plan's interleaved bare-option ladder form is WRONG
  on 6.1.1 — a bare per-stream option applies to ALL matching output
  streams, last occurrence wins (every variant came out at the top
  level's bitrate). PRIMARY form = stream-specifier: all
  `-map 0:v:0 -map 0:a:0` pairs first, shared bare `-c:v libx264
  -c:a aac -pix_fmt yuv420p`, per-level 16-token blocks
  (`-b:v:N -minrate:v:N -maxrate:v:N -bufsize:v:N -profile:v:N
  -level:v:N -s:v:N -b:a:N`; width = input aspect × level height,
  rounded even), then `-var_stream_map "v:0,a:0 v:1,a:1 …"` as ONE
  argv element (registry kind=string in BOTH the hls and dash
  format-option groups; registry hand-bumped 2.9.0 → 2.10.0).
  execute.py: `_SPEC_RX`/`_spec_base` strip the `:N` index (keeping
  the `:v` type for b/c/profile) so specifier options get the base
  flag's value checks; new vocab `-minrate` (rate), `-level`
  (`^\d+(\.\d+)?$|^\d{1,2}$`), `-s` (`^\d+x\d+$`). NLU: "abr hls" /
  "hls ladder" / "bitrate ladder" / "adaptive hls" / "multi-quality
  hls" → variants (explicit "levels 216p 300k, 480p 1500k" → filled
  VariantSpecs; count-only → variants=[] → default table); "dash
  ladder" / "abr dash" → dash + variants; the generic key=value scan
  never captures var_stream_map (the planner builds it). **Master
  synthesis deviation (plan §5.5):** NOT from Run-record metadata —
  `_ladder_from_argv` (execute.py) derives the ladder from the argv
  itself at start_run (requires -var_stream_map + per-level specifier
  options + a .m3u8 destination containing %v; a .mpd or other
  destination → None, so DASH runs get no master); bandwidth =
  (video+audio kbps)×1000, CODECS from _AVC_PROFILE_ID
  (baseline=66/main=77/high=100) + mp4a.40.2, RESOLUTION from the
  level; `_synthesize_master` writes master.m3u8 into the run's cwd +
  a Run note; the Run record carries the derived ladder. Ladder + a
  filter edit in one request → clarification. E2E 6.1.1 ALL PASS
  (script /tmp/m133/e2e_ladder.py, titan.mp4): case A "abr hls with
  levels 216p 300k, 480p 1500k" → stream_0/1.m3u8 + .ts (2 levels
  384x216 L3.0 / 854x480 L3.2), case B "abr hls" → 3 levels
  640x360 L3.1 / 854x480 L3.2 / 1280x720 L3.1; master.m3u8
  BANDWIDTH/RESOLUTION/CODECS exact (avc1.4d401e/4d401f/4d4020 +
  mp4a.40.2); per-variant bitrate checked via segment-bytes ÷
  #EXTINF durations (±15%) — **ffprobe .ts stream.bit_rate is 0/N/A
  (MPEG-TS carries no per-stream bitrate metadata)**; single-variant
  HLS argv byte-identical to pre-M13 (regression guard). E2E 9.0.1
  in-container (fass:m133-verify, run901_ladder.py) — "## 9.0.1
  ladder E2E OK": case A overall 376868/759902/1589533 vs targets
  356000/764000/1580000, case B 757123/1574510/2627745 vs
  764000/1580000/2596000, master OK, 18 .ts/case; variant
  geometry/levels cross-probed with host 6.1.1 ffprobe (the 9.0.1
  ffprobe has no libxml2, and .ts bit_rate=0 anyway).
- **M13.4 (ABR DASH ladder) COMPLETE 2026-10-27 — DUAL-BUILD E2E
  GREEN, suite 1365/1, UNCOMMITTED.** Reuses M13.3's emitter
  unchanged (-f dash + var_stream_map + AUTO adaptation sets — never
  explicit); format_options (seg_duration etc.) ride the ladder path
  (the planner emits them after the variant block). 5 new tests
  (test_planner_builder.py: DASH ladder argv exact — -f dash, map
  pairs, per-level specifier blocks, var_stream_map, .mpd destination,
  no -adaptation_sets; dash seg_duration emitted before the output
  path; test_rules.py: dash ladder explicit levels, "abr dash", dash
  word + ladder in one phrase). **E2E-verified MPD structure (the
  plan's "auto sets" form, now pinned empirically):** one
  (video, audio) AdaptationSet PAIR PER VARIANT in variant order —
  NOT a single pooled video AS; root type=static +
  mediaPresentationDuration=PT20.0S; one Representation per video AS
  at the declared resolution with avc1.4d<profile><level> ids
  (main=0x4d; 3.0=0x1e, 3.1=0x1f, 3.2=0x20); audio reps all
  mp4a.40.2. **Init segments are per STREAM:** N variants → 2N init
  + 2N chunk streams. No master.m3u8 for .mpd destinations (Run
  record ladder=None). E2E 6.1.1 ALL PASS (script
  /tmp/m133/e2e_dash_ladder.py): case A "dash ladder with levels
  216p 300k, 480p 1500k" → titan.mpd static PT20.0S, 2 AS pairs,
  avc1.4d401e/4d4020; case B "abr dash" → 3 AS pairs
  640x360/854x480/1280x720, avc1.4d401f/4d4020/4d401f; host ffprobe
  (libxml2) parses the .mpd. E2E 9.0.1 in-container (fass-m134-e2e,
  run901_dash_ladder.py; structural XML via ElementTree since the
  9.0.1 ffprobe cannot parse any .mpd) — "## 9.0.1 dash-ladder E2E
  OK": case A 2 (video,audio) AS pairs [(384,216),(854,480)]
  avc1.4d401e/4d4020, 4 init + 14 chunks; case B 3 AS pairs
  [(640,360),(854,480),(1280,720)] avc1.4d401f/4d401f/4d4020, 6
  init + 21 chunks — identical structure to 6.1.1.
- **M12 (COMPLETE 2026-10-27 — all mandated work done):**
  **M12 = the PRD-deferred scope (watermarks / HDR / batch) is all
  on main.** VMAF/compare convention b8ed8ff (Netflix vmaf docs CLI
  order: reference input 0, distorted input 1; pads
  [distorted][reference]; distorted scaled to reference size;
  -f null -; scores in run log only, web-UI Log tab; setpts
  probe-conditional — only for pads whose probe carries pts).
  **M12.WM ea9919e** — multiple watermarks: N image overlays chain
  in plan order (inputs 1..N); rules.py `_detect_watermarks`
  (every image the request refers to becomes one op — explicit
  mention wins, else every bound image; one position phrase per
  watermark in text order, rest bottom-right); plan.py validator
  renamed `_at_most_one_single_use_op` (watermark exempt); E2E on
  titan.mp4 with two images; suite 1233/1. **M12.BATCH 9967441**
  (job j_65e7374aef SUCCEEDED on tr4-secondary; integrated,
  disposition accepted) — `--batch FILE|DIR …` + `--batch-out DIR`:
  one instruction over a directory's top-level video files (sorted)
  or an explicit list, sequential per-file pipeline runs, a failing
  file reported + skipped, exit 0 only if all succeeded; rejects
  positional/-o/--previous/--metadata; instruction-after-files
  parses (trailing-token disambiguation); single-file CLI path
  unchanged; suite 1254/1. **M12.HDR 8c2e11a** (job j_ba4899bb09 —
  work good, job record FAILED/invalid_result: outcome
  'success_pending_review' outside the enum + missing criterion
  keys + scope_violations [".venv"] → dispatch_integrate REFUSED
  by the server gate "only succeeded jobs can be integrated";
  manually reviewed + merged, worktree retained — 8c2e11a is the
  integration record) — ToneMapOp (no options); NLU `_TONEMAP_RX`/
  `_detect_tonemap` (tone-map words or hdr↔sdr pairings ≤60 chars),
  gated on probe (PQ smpte2084 / HLG arib-std-b67); planner chain
  `format=gbrpf32le,tonemap=tonemap=hable:desat=0,format=yuv420p`;
  default 8-bit Rec.709 output, `main10` 10-bit re-encode keeps the
  HDR; HDR10+/Dolby Vision named-unsupported; conflicts:
  audio-mode ops, subtitles mode, stabilize; registry 2.8.0
  (tonemap, min_version 4.2); tests/test_tonemap.py (24); worker
  E2E on i7 docker 9.0.1 + re-verified on tr4 6.1.1 (synthesized
  HDR10 main10 10-bit PQ source → 8-bit SDR h264, chain +
  disclosure correct); suite **1278 passed / 1 skipped**.
  **README job 6bca1a3** (j_0214afe7db SUCCEEDED on i7; integrated,
  accepted) — quality-comparison section + stream allowlist rtp +
  spec link; worker was told NOT to document the unshipped M12
  features. **M12 docs close-out (with this update)** — README
  (shape line, Watermarks multiple, new HDR→SDR + Batch bullets) +
  docs/DEVELOPMENT.md (CLI table --batch/--batch-out, feature
  bullets M12.WM/HDR/BATCH, out-of-scope example fixed, roadmap
  M11.1→[x] + M11.2/3/4 [x] + M12 [x], "deferred (PRD §5)" line
  removed). i7 docker quirk: jrottenberg:9.0 ENTRYPOINT is ffmpeg
  → pass fass argv[1:]. Dispatcher drift: three jobs now (c27a0d8,
  j_3d4505da56-a1, j_ba4899bb09) left good work behind a false
  "failed" via non-schema result vocabulary — recommend the result
  contract in the cc-worker profile. **M11 series, COMPLETE (prior
  state): M11.1 COMPLETE
  (181c1e5 retrieval core + fass-rag service; 0ccdb7f §9.1 benchmark, ALL
  9 gates green — exact_top1 1.0, recall8 0.9095, warm_p95 ~43 ms).
  **M11.2 commits landed on main:** d60c167 §6.6 RagConfig + FASS_RAG_*
  env; 1621b8b/63a2a35 §6.1 registry 2.5.0 (doc_ref + aliases +
  provenance, 87 filters); 56324af §6.2 query builder + fail-open
  client; d005578 §6.2 FIX (alias mentions now resolve to canonical
  registry keys — matched text was leaking into component_names);
  9bd4401 §6.3 evidence block + LLM user-turn augmentation; 09060a3
  §6.4/§6.5 pipeline RAG hook + citation derivation (citations.py +
  pipeline.py + 19 tests; suite 1100 passed / 1 skipped).
  **M11.2 commit 8 — §9.3 safety gates — COMMITTED as 5704ff0**
  (pipeline fail-open guard around rag_retrieve + "evidence not used →
  command unaffected" warning in build_evidence + tests/test_rag_safety.
  py, 9 tests covering all 6 gates: poisoned doc, wrong-version corpus,
  RAG down/raising client, absent filter, adversarial filenames with
  RAG on, rules-path determinism RAG on vs off; **suite 1109 passed / 1
  skipped**). **M11.2 commit 7 — §6.7 deployment — INTEGRATED** (job
  j_3d4505da56, tr4-secondary: rag/health.py, docker/Dockerfile.rag,
  docker/compose.rag.yaml overlay, rag/server.py FASS_RAG_DATA/
  FASS_RAG_BIND + parse_bind, tests/test_rag_deployment.py — 35 tests;
  attempt 1 crashed exit 0 (report-schema rejection), attempt 2
  succeeded; worktree suite 1132/3 skipped + 1 environmental benchmark
  failure (gitignored corpus chunks absent in clean worktrees); merged
  --no-ff as 97c0552, integration accepted via ladd). **M11.2 commit 6 — UI Sources panel
  + config-tab rag block + GET /v1/rag status — COMMITTED as
  bb9bcc0** (suite 1147 passed / 1 skipped).
  **Delegated (user requests 2026-10-26) — status 2026-10-26:** RAG
  tab (tr4-secondary, j_1f86a0ad30) — INTEGRATED as 15a0baa (POST
  /v1/rag/query + web-UI tab, 4 new TestClient tests, worktree suite
  1151/1); M11.4 held-out set (i7, j_db1025fa7f) — INTEGRATED as
  5308bd7 (tests/benchmark/ablation_heldout.json: 79 paraphrase + 20
  ambiguity + 20 unsupported + 10 adversarial; 13 shape tests derived
  from fass/schema/plan.py + held-out property vs fass/nlu/rules.py);
  Files tab (j_2d6b4287ee, tr4-secondary) — INTEGRATED as 38ce046
  (GET /v1/files, GET /v1/files/inspect ffprobe|mediainfo (503 when
  absent), POST /v1/files/frame 360-px JPEG fail-open, Files tab +
  right-click menu + popup; 10 new tests; worktree suite 1158/3 + the
  known env corpus failure; main suite after merge 1187/1).
  **M11.4 §9.2 ablation harness — COMMITTED as 1d543a6 (commit 9):**
  scripts/m11_ablation.py + tests/test_m11_ablation.py +
  scripts/m11_rag_budgets.py + HANDOFF/PROGRESS; suite 1177/1 at
  commit. 4 arms × **220 entries (requests.json 91 + ablation_heldout.json
  129, both datasets in ONE run — 880 arms; the former separate run 2
  was absorbed, the §9.2 go/no-go is computed from the held-out subset
  of run 1's report)** vs tr4 vllm vllm-qwen38-27b-dual-max :8091;
  **Ablation status 2026-10-27 ~00:28: COMPLETE 880/880.** Run 1 (pid
  1186350, out /tmp/ablation-run1) crashed at abl-121's rules arm; the
  crash → ArmResult(error) wrapper in run_rules landed as **4a6a891**
  and run 2 (pid 1774958) RESUMED APPENDING to the same results.jsonl
  and finished 880/880; log /tmp/ablation-run2.log. Report written
  (report.md + report.json); artifacts copied reboot-proof to
  /dd2/andrei/ablation-run1/ (+ /dd2/andrei/m114_gonogo.py,
  /dd2/andrei/rag-budgets/). **§9.2 VERDICT: NO-GO → ship arm 3
  (llm_schemas) + static doc_ref citations.** Pre-registered rule:
  arm 4 must beat arm 3 by ≥ +0.05 held-out PARAPHRASE param_acc with
  no held-out regression. Result: paraphrase (n=79) param_acc
  arm3=1.0 arm4=1.0 → delta 0.0; NO regressions (arm 4 ≥ arm 3 on
  every held-out axis — status 0.814>0.7984, clarif 0.85>0.75,
  unsupported equal). No code change: RagConfig.enabled defaults False
  (RAG opt-in, fass/config.py), LLM path already uses the schemas
  block, citations already derive from static registry doc_ref
  (derive_citations works with rag_result=None); arm 1 rules stays
  default either way; evidence augmentation stays an opt-in
  experiment (it leads on every other held-out axis — revisit if the
  gate ever becomes status/ops-based). 19 error rows: 7× 180s LLM
  timeouts, 7× plan validation (abl-009 ×3 pixel format, abl-048 ×3
  filter, abl-072 video-needs-encoder), 2× malformed LLM JSON
  (abl-028/abl-090), 3× rules-arm (≤0.2s).
  **§9.5 budgets ALL MEASURED (final):** warm retrieval p95 27 ms
  (≤300 PASS; first-query cold 33 ms, ≤1000 PASS) · evidence 4000ch
  + 263 wrapper (≤4000 PASS) · RAG generation overhead arm4−arm3 =
  −3.104 s mean / −69.114 s p95 (≤400 ms PASS — negative, vLLM
  latency variance dominates; retrieval ~25 ms) · overall generation
  p95: rules 3 ms PASS (default); LLM arms 73–82 s NOT met by local
  qwen3.8-27b @ tr4 dual-model 3090 serving (recorded per PRD's
  "existing PRD target; now measured") · RAG memory 70 MB (≤4 GB
  PASS). Budgets JSON: /dd2/andrei/rag-budgets/{warm,cold}.json.
  **§9.4 gates (ALL GREEN):** suite 1218/1 (tr4 host 6.1.1) PASS;
  battery host 6.1.1 = **21/21** (scripts/m10_battery.sh local; 3
  feature-SKIPs: fdk_vbr4, libvmaf, scale_cuda — host build lacks
  them, same as M10.6 baseline); **i7 9.0.1 docker half = 21/21 with
  ZERO skips (2026-10-27 01:15)** — all three 9.0.1-only cases
  executed clean on the nvidia build (log
  battery-out/m114_battery_i7_901.log; run from tr4:
  `scripts/m10_battery.py --mode docker --api
  http://192.168.0.33:8000` after i7 repo sync 4ff3393→7866a93 via
  bundle + fass:latest rebuild 58b9eba7(2.2.0)→cc5eda7(2.7.0) +
  `docker compose up -d fass`; clips scp'd to i7
  docker-data/input, md5-verified; fass-worker left on old code
  pending user decision). Determinism: rules plans byte-identical
  with RAG on vs off, 91/91 (M11.2 §9.3). **M11 series COMPLETE.** **M11.3 COMPLETE (2026-10-26):** fixtures
  adb8c60/f13ae07 (ebur128/signalstats/blackframe, verbatim real
  corpus, edge cases); rag/author + rag/gaps integrated as d47f96e
  (job j_5c845355c5); parser round 1 (10b1fe5: grouped <dt>s,
  alias-group <dt> comma split, prose form (c), marker-without-<dl>
  guard, kind↔type compat map) + round 2 (0116554: build-facts union
  of ALL `AVOptions:` blocks — alias headers
  acompressor/sidechaincompress, shared class (h|v)stack, shared
  component framesync; comma-group section headings register every
  listed name; number↔duration compat); 3 real-corpus drafts all
  verified_both / 0 option_diffs → **registry 2.6.0 committed as
  b122e36** (audio ebur128 + video signalstats + video blackframe,
  surgical line insertion — hand-formatted file, never re-serialize;
  6 read-only ebur128 log exports excluded; doc_ref/aliases/provenance
  as last three keys; test count bumps 90 / 53-37). **Parser round 3
  (f2a1dd8):** singular form-(b) marker ("It accepts the following
  option:"), token-based decimal-safe default extraction ("Default is
  0.5.", "Default value is 2048.", "Default mode is merge.",
  "default is: X", "Defaults is X"), enabled/disabled↔true/false,
  "all" vs expanded flag-sum comparison — 18 author tests, suite
  **1213 passed / 1 skipped**. **10-draft triage FINAL:** 6
  verified_both 0-diff → merge at 2.7.0 (edgedetect, freezeframes
  multi_input 2, thistogram, ahistogram, aspectralstats, showvolume);
  4 genuine docs↔build (histogram: 12 rows = 6 undocumented 1-letter
  aliases in both builds; tinterlace: docs-only `flags`, neither
  build has it; blockdetect/blurdetect: `planes` "first only" vs
  bitmask "1"). **31 drift rows TRIAGED:** 16 intentional
  (freezedetect/silencedetect n/noise `string` kind — builds report
  `<double>` but accept `-30dB` empirically; verified on system
  ffmpeg 6.1.1), 6 → acompressor refresh (M10.0 entry had the
  pre-5.0 option set; both corpora carry the modern 12-option set,
  level_sc from the shared acompressor/sidechaincompress block;
  drafts/acompressor.json verified_both), 9 genuine 9.x evolution
  (adynamicequalizer auto/direction/mode, anullsrc r/sample_rate,
  libvmaf cpuid/log_format/pool, overlay alpha) — no registry change
  (dual-build target, 6.1.1 common denominator). **Merge package
  /tmp/m114_registry_merge.py ready + DRY-RUN VERIFIED on a scratch
  copy** (plan said 2.6.0 → 2.7.0: +6 filters, acompressor in-place
  refresh, 96 filters 56/40) — SUPERSEDED: the merge that actually
  landed as **5c34d01 included all 10 review-merge filters → registry
  2.7.0 = 100 filters (60 video / 40 audio)**. Suite **1213 passed / 1 skipped**. Gaps re-run round 3:
  drift identical (31) — the parser fixes don't touch registry-vs-
  build comparison; 6.1.1 unresolved = [blackdetect_vulkan]
  (genuine — manual predates the comma group).
  **DONE since that plan (2026-10-26 → 00:16): (2) registry 2.7.0
  MERGED as 5c34d01 — all 10 review-merge filters IN (edgedetect,
  freezeframes, thistogram, histogram, blockdetect, blurdetect,
  tinterlace, ahistogram, aspectralstats, showvolume; 100 filters
  60/40; freezeframes = the 10th combiner, `multi_input: 2`, planned
  by the generic combine machinery with ZERO planner changes;
  acompressor refreshed in-place — 12 options identical in both
  corpora, level_in/level_sc [0.015625,64] default 1). Merge
  decisions: histogram long-names-only (1-letter aliases left OUT,
  open question), tinterlace `mode` only (docs-only `flags` noted),
  blockdetect/blurdetect `planes` documented as "1". 4a6a891 =
  no-audio containers (e.g. GIF) drop the input track with -an + the
  ablation run_rules crash wrapper. **Drift 31 → 25 (2ce5e95):** the
  6 removed rows were DETECTOR artifacts, not real drift — (i)
  gaps.py registry_default truncated at the first ')' (showvolume c's
  nested-paren expression default) → balanced-paren scan; (ii) gaps
  drift lacked author.py's 'all' ≡ 'all+...' flag-sum normalization
  (aspectralstats measure); (iii) the acompressor level_sc review
  note sat INSIDE the (default 1) parens in the registry. 2
  regression tests (tests/test_rag_gaps.py); the 25 remaining rows
  all verified known/expected (16 freezedetect/silencedetect
  string-modeling + 9 9.0.1-evolution). Suite **1218 passed / 1
  skipped** (120 s).**
  **Next (user-decision items only — the mandated M-series + RAG
  PRD work is fully done):** (1) 2.7.0 merge questions: histogram
  1-letter aliases (register or not), tinterlace docs-only `flags`,
  blockdetect/blurdetect `planes` phrasing. (2) Minor
  optional: gaps/ note on auto-adapter 401-no-fallback on tr4 (no
  ANTHROPIC_API_KEY → E2E must use `--adapter rules`); dispatcher
  cc-worker result-format drift note. (3) Rebuild fass:latest from
  current main — image cc5eda7 was built from 7866a93 (pre-M12), so
  the i7 deployment does not run batch/watermarks/tonemap yet.
  (DONE 2026-10-27, user-requested: i7 fass-worker RECREATED from
  the current fass:latest cc5eda7/2.7.0 — it sat on 58b9eba7/2.2.0
  from 10-25; `docker compose up -d --force-recreate fass-worker`;
  healthz verified fass→worker:8001 = {"ok":true}.) Context: PUSHED 2026-10-27
  (user's explicit "push it") — origin/main = 237d554 (844943c..
  237d554, 6 commits fast-forward: b8ed8ff, ea9919e, 9967441,
  8c2e11a, 6bca1a3, 237d554); i7 mirror ff-merged from origin
  (local state preserved). Unpushed: only this push-record
  commit. M11 series DONE: all PRD gates
  measured, both battery halves 21/21. Standing: advisory evidence
  only — the registry/builder/argv gate stays sovereign; treat
  evidence_status 'supported' with skepticism (M11.1 calibration
  note).
- **M1–M9+BSF + M10.0–M10.5 + M11.0 shipped (M10.5 dual-build
  verified, M11.0 committed that milestone). M10.5 two-pass
  vidstab COMPLETE 2026-10-26** (on tr4):
  `stabilize the shaky clip` / `remove the camera shake` / `get rid of the
  shaky handheld look` → `StabilizeOp`; the planner builds a two-pass
  execution — pass 1 `vidstabdetect=result=<trf>` + `-f null -` (stream
  discarded), pass 2 `vidstabtransform=input=<trf>` prepended to the
  re-encode vf chain; bare trf name `<output-stem>.vidstab.trf` next to the
  output; both passes run in ONE cwd (the final output's) so no path
  rewriting. `ExecutionPlan.passes: list[Pass]` + `intermediates`;
  top-level fields mirror the FINAL pass; `build_argv_all()` returns every
  argv (final last); `GenerationResponse.steps` (pass/purpose/arguments/
  command/output). Executor: `RunRequest.steps` (max 8, `steps[-1] ==
  arguments` enforced by a model validator), `start_run_sequence` runs
  steps sequentially with a per-step timeout, one Run record (argv = final
  step), `_ACTIVE[run_id]` = current step's proc so stop works. CLI
  `--execute` runs the steps in order. Conflict matrix (planner PlanErrors,
  the two passes need 1:1 frame correspondence): trim, speed, watermark,
  audio_replace, subtitles, combine, any bitstream filter, any video-domain
  filter, diagnostic filters, stream destination; ALLOWED: resize,
  grayscale, crop, rotate, audio filters, audio_resample, codec options.
  NLU bug fixed: stabilize now conflicts at NLU level with audio/subtitles
  output modes (was silently dropped → a plain mp3 plan for "stabilize the
  clip to mp3"). GPU consume rule (M10.5): `resize to 1080p on the gpu` →
  `scale_cuda=w=-1:h=1080` + CPU encoder (note says the GPU mention applies
  to the filter, not the encoder); `deinterlace on the gpu` → `yadif_cuda`
  + CPU encoder; an explicit encoder word (nvenc anywhere, or gpu/nvidia/
  hardware-accel within 40 chars of an encode word) re-enables nvenc; a
  bare gpu mention without a CUDA filter keeps the M6 nvenc behavior.
  denoise stays unsupported; "stabilize and denoise" reports the denoise
  (the unsupported scan runs before detection — honest).
  `tests/test_stabilize.py` 19 tests incl. a real-media two-pass E2E
  (detect writes the .trf — header `VID.STAB 1` or `#` — transform consumes
  it, output probed h264 320x240). bench-068 flipped unsupported→ready
  (ops=[stabilize]); two M6 GPU tests retargeted to non-CUDA phrasings
  (resize+gpu is now the scale_cuda case; revision toggle uses grayscale).
  **Suite 910 passed, 1 skipped on tr4 host 6.1.1 (99 s)** — the skip is
  the libvmaf E2E (no libvmaf in the 6.1.1 build, unchanged). **9.0.1
  container verification COMPLETE (i7 job j_b55343a9be at commit aa0e534,
  image fass:m105-verify efc5f125149a): 910 passed, 1 skipped in 103.19 s**
  — the sole skip is the node JS syntax check (tests/test_api.py:311),
  the libvmaf E2E RAN and passed (16.53 s, vendored model
  /opt/vmaf-models) → **M10.5 DUAL-BUILD VERIFIED** (evidence:
  /home/andrei/.local/share/local-dispatcher/artifacts/j_b55343a9be/
  attempt-1/pytest_full.log). M10.4 below remains as delivered.
- **M11.0 pinned corpora COMPLETE 2026-10-26** (commits e1ff741
  acquisition manifests + BUILD-RECIPE.md, cherry-picked from the
  tr4-secondary worker job; c9e4f55 parser/chunker/ingest/fixtures/tests;
  0081cd3 manifest refresh): RAG corpus foundation per PRD §10.3.
  6.1.1 docs from the Ubuntu deb ffmpeg-doc 6.1.1-3ubuntu5; 9.0.1 from
  the official tarball via `make doc` (texinfo 7.1 toolchain;
  libtext-unidecode-perl REQUIRED — makeinfo dies without it).
  **Reproducibility proven:** document sha256 lists byte-identical across
  TWO independent builds (tr4 primary + tr4-secondary worktree, different
  machines). **Parser 1.1** (`rag/parser.py`): makeinfo HTML →
  Document/Block tree; stacked deflist pairing fixes the table idioms —
  shared `<dd>` (w/h → one entry per name), continuation `<dd>` (multi-
  paragraph desc merges into the last entry), nested `<dl>` value tables
  become the entry's description (eof_action → repeat/endall/pass), bare
  `<dt>` kept with empty desc; TOC div excluded; canonical
  `document_to_dict` for fixture round-trips. **Chunker**
  (`rag/chunker.py`): structure-first; TOKEN_MIN 350 / TOKEN_MAX 700;
  heading paths repeated on child chunks; option units "term: desc" with
  nested value tables rendered inline; examples stay with their
  introducing paragraph; oversized blocks → own flagged chunk, never cut;
  build_help per-filter text files. **Ingest CLI** (`rag/ingest.py`):
  sha256 drift gate + missing-document gate; re-ingest writes a NEW
  immutable generation (explicit collision → ValueError "immutable");
  atomic generations.json with history; previous gen kept on disk for
  rollback; dedup on (source_url, content_hash); manifest refresh
  (parser_version, total_chunks). Final corpus state: 9.0.1 manual
  gen 3 active = 1820 chunks, build_help gen 2 = 836 (516 docs); 6.1.1
  manual gen 2 = 1687, build_help gen 2 = 849 (562 docs); the
  parser-1.0 gens (9.0.1 manual 1665 chunks) preserved on disk.
  **2 parser fixtures round-trip byte-identical** (filter_options,
  nested_headings); **36 RAG tests** (parser 14 + chunker 12 + ingest 9)
  → **full host suite 946 passed, 1 skipped** (skip = node JS check on
  the tr4 host). **Exit gate met:** both corpora built and reproducible;
  fixtures pass.
- **M10.4 comparison COMPLETE 2026-10-26** (on tr4, committed 99cbcab)
  (on tr4, uncommitted): `psnr`/`ssim`/`libvmaf` against a reference
  input as a standalone diagnostic analysis (null muxer, loglevel info,
  summary in the run log). Registry 2.4.0 (`diagnostic` +
  `needs_reference` flags; libvmaf min_version 7.0;
  `comparison_filters()`). Schema: `CompareOp`, one per plan,
  filtergraph conflict rejected. NLU: compare verb / quality-loss
  phrasing / bare metric word; metric-less → PSNR-vs-SSIM clarification
  (VMAF only if named); combiner-style reference binding; compare + any
  other edit → standalone clarification; non-video output → unsupported;
  revision replaces the previous compare. Planner: reference must be a
  different bound local video asset (probed); conflicts = plan errors
  (trim/watermark/audio_replace/subtitles/any other op/non-video);
  labeled graph `[0:v][1:v]metric[cmp]` — reference scaled to the
  main's size only on dimension mismatch, fps/pix_fmt mismatches
  warnings only; **`[cmp]` is explicitly `-map`ped (filtergraph outputs
  are not auto-mapped — E2E-caught bug)**; libvmaf model from
  `FASS_VMAF_MODEL`/`FASS_VMAF_MODEL_DIR` emitted as `model=path=<file>`
  (**the filter takes a spec, not a bare path**). The 9.0.1 base image
  has libvmaf but NO model → `docker/models/vmaf_v0.6.1.json` vendored
  (official Netflix model, 19 KB) + Dockerfile COPY +
  `ENV FASS_VMAF_MODEL=/opt/vmaf-models/vmaf_v0.6.1.json`.
  `tests/test_comparison.py` 47 tests incl. psnr/ssim E2E (metric line
  in stderr, no file written) + libvmaf E2E (gated on filter+model).
  **Suite 891 passed, 1 skipped on tr4 host 6.1.1 (96 s).** M10.3:
  9 combiners as labeled `-filter_complex` graphs, registry 2.3.0
  markers, `combine` op, two-input NLU phrasings, xfade offset math,
  concat pad order — `tests/test_combiners.py` 62 tests, 845 passed
  (pre-M10.4). i7 unreachable 2026-10-26 (no route; dispatcher backend
  offline) → 9.0.1 verified on tr4 via the same Dockerfile;
  `fass:m103-verify` built (M10.3 code), `fass:m104-verify` built
  (M10.4 + model); container runs mount the working tree at /host
  (verified: imports resolve to /host/fass), so the in-container suite
  verifies the current tree on 9.0.1. **DUAL-BUILD VERIFIED 2026-10-26:
  891 passed, 1 skipped on BOTH builds** — host 6.1.1 (skip: libvmaf
  E2E, no filter in 6.1.1) and `fass:m104-verify` 9.0.1 (skip: node JS
  syntax check, no node in image; libvmaf E2E RAN + passed via the
  vendored model). Container suite needs `pip install pytest httpx` in
  the image venv first (image installs only `.[api]`). Verification
  exposed 3 test bugs, all fixed build-agnostically:
  `test_env_reports_qsv_coders` (asserted `present is True`; user
  confirmed tr4 = AMD CPU → QSV registered-but-unusable there, 9.0.1
  nvidia image built without QSV → now cross-checks `present` against a
  raw `ffmpeg -encoders` parse);
  `test_filters::test_presence_probe_covers_new_filters` (asserted
  libvmaf+blackdetect_vulkan absent — real matrix: 6.1.1 lacks both
  (blackdetect_vulkan is 7.0+), 9.0.1 nvidia HAS libvmaf but NO vulkan
  at all → both cross-checked against a raw `ffmpeg -filters` parse;
  probe set == raw set on both builds); `test_comparison._libvmaf_works`
  (gate mini-run used bare `libvmaf`, which fails to init without a
  compiled-in model → now resolves the model via `vmaf_model_path()`
  exactly as the planner does).
- **M10.6 verification COMPLETE 2026-10-26 (dual-build verified):**
  per-category E2E battery `scripts/m10_battery.sh local|docker` →
  `scripts/m10_battery.py`, 21 cases across all M10 categories
  trim/resize/grayscale/watermark/speed/prores/opus/fdk_vbr4/rotate/
  acompressor/yadif/loudnorm/blackdetect/volumedetect/xfade/concat/
  psnr/libvmaf/stabilize (two-pass)/h264_metadata bsf/scale_cuda);
  generates through the REAL pipeline (rules adapter, preflight
  on), checks needles as substrings of the rendered argv, then EXECUTES
  against the real ffmpeg of each build. Build-gated cases (fdk_aac VBR,
  libvmaf, scale_cuda) expect a preflight warning + skip on the 6.1.1
  host and RUN on 9.0.1. **Host 6.1.1: battery 21/21, M8 battery 10/10,
  suite 949 passed / 1 skipped** (skip = node JS check). i7 9.0.1 first
  attempt (job j_e478994233, image d3bae5d1aba6): suite green
  (946/1 skipped at the time) but battery 17/21 — the 4 multi-input
  cases (xfade, concat, psnr, libvmaf) failed with "could not be probed":
  a GENUINE BUG, the planner probed combiner extra inputs, the combiner
  main fallback, and the comparison reference with the RAW bare filename
  (cwd-relative) instead of resolving against the input volume — fatal
  in the container where inputs live in /input. **Fixed in b989e31**:
  the 4 planner probe sites wrapped in `resolve_input_name` (same
  mechanism pipeline.py:265 already used for the main asset) + 3
  regression tests (both media tests proven to FAIL on the unfixed
  planner) + **web-UI `-i <file>` input convention** (user ask): the API
  strips the `-i ` prefix from any asset path before planning
  (`_strip_i_prefix` in fass/api.py — server-side, so every API client
  gets it; streams/source specs pass through untouched); bare names
  resolve against $FASS_INPUT_DIR / /input at probe time AND run time.
  **i7 9.0.1 attempt 2** (job j_0a8a17f0c1, image fass:m106b at
  b989e31): suite 949 passed / 1 skipped, battery 19/21 — the probe
  bug is gone; xfade + concat now fail the API's `/v1/runs` argv gate:
  (1) the planner always normalizes xfade inputs with `settb=AVTB` but
  `settb` was missing from the registry filter allowlist; (2) the
  validator's filtergraph parser stripped only TWO leading input
  labels, so the 4-pad concat graph `[0:v][0:a][1:v][1:a]concat=...`
  misparsed ("'[1:v][1:a]concat' is not a filter name" — the error's
  "(in ...)" context prints the instance AFTER one label was already
  stripped, which initially masked the real shape). Both bugs are
  BUILD-INDEPENDENT and reproduce on the host — the local battery had
  been bypassing `validate_argv` (docker mode enforces it at
  /v1/runs), so the earlier host 21/21 was hollow for xfade/concat.
  (The worker's concat error showed a 3-label shape only because the
  error context prints the instance after one label was already
  stripped — the generated 4-pad graph is correct.)
  **Fixed in this commit**: `settb` registry entry + docs_provenance
  URL (51 video / 36 audio = 87); `loader._validate_instance` strips
  ALL leading labels; the local battery now runs `validate_argv` on
  every generated argv before executing (local + docker share
  acceptance semantics); 2 regression tests (4-pad concat graph,
  xfade settb chain) + count assertion updated; DEVELOPMENT filter
  count 86→87. **Host 6.1.1 re-run: battery 21/21 with the gate
  ACTIVE, suite 953 passed / 1 skipped.** Final i7 21/21
  re-verification resubmitted at this commit as **job j_0a7359da46**
  (i7, image fass:m106c, serve port 18107, suite expected 953/1);
  docs evidence lines then get the real container numbers. **i7 machine
  WENT DOWN** (worker died ~06:11, daemon flagged 12:40, job
  j_0a7359da46 = restart_no_result/exit_unknown → CANCELLED, worktree
  retained at c866f64): partial evidence was battery 12/21 ALL PASS
  incl fdk_vbr4 RUN+clean + an incomplete pytest log (30%). User
  directive 2026-10-26: when i7 is down, validate ALL ffmpeg against
  the tr4 SYSTEM ffmpeg 6.1.1 (not docker). **Takeover on tr4 COMPLETE,
  2026-10-26:** container suite in the worktree @ c866f64 (image
  fass:m106c, b4a880e9e507) **953 passed / 1 skipped** (skip = node JS
  check); docker battery **21/21** with fdk_vbr4, libvmaf, scale_cuda
  ALL EXECUTED (tr4 has 3 NVIDIA GPUs — "tr4 is AMD, no CUDA" was right
  about QSV and wrong about GPUs; the container ran --gpus all); fresh
  host 6.1.1 official record: suite **953 passed / 1 skipped** (skip =
  libvmaf E2E — the distro build lacks the filter) + local battery
  **21/21** with the gate. VMAF provenance: the official Netflix/vmaf
  v0.6.1 model is byte-identical to docker/models/vmaf_v0.6.1.json
  (JSON-only distribution; note in docker/models/README.md) and
  `vmaf_model_path()` now resolves .json under FASS_VMAF_MODEL_DIR
  (+ test). **M10.6 = dual-build verified. DONE.**
- **Next task:** **web-UI "mediainfo/log" tab + "verbose log" toggle
  (user, 2026-10-26) — DELEGATED as job j_dc6d73d9dd on tr4-secondary**
  (base 88cd63b): Info/Log tab (media info of the input-or-output file
  via ffprobe JSON, OR a CLI run's log, selector-toggled; ALL
  mediainfo controls on the tab; logs >10000 lines served as the last
  1000 + banner) + MAIN-page "verbose log" checkbox (-loglevel verbose
  in every pass, run log → {FASS_OUTPUT_DIR}/log/{run_id}.log) +
  endpoints /v1/mediainfo, /v1/logs, /v1/logs/{name}; on landing:
  review diff, full suite, merge. Then **M11.1** —
  part A = FTS5 lexical core + fass-rag service (DISPATCHED to
  tr4-secondary, job j_7dea91b80b at b989e31, integration pending);
  part B = §9.1 benchmark (84 exact + 50 option-level + 60 NL + 10
  version-mismatch + 5 insufficient) + gates + lexical-vs-hybrid
  decision → M11.2 fass integration (advisory evidence only — registry/
  builder/argv gate remain sovereign) → M11.3 authoring → M11.4
  ablation kill criterion.
- **Planning 2026-10-25 (later session): joint PRD delivered** —
  `ffmpeg-assistant-rag-prd.md` merges the assistant scope with the
  documentation-RAG idea. Verdict: RAG is viable as an **advisory evidence
  layer only** (LLM prompt grounding, plan-derived citations, offline
  registry authoring); free-form LLM+RAG command generation is explicitly
  rejected. Registry/builder/argv gate remain sovereign. M11.0–M11.4
  sequenced parallel to M10.1–M10.6 with a pre-registered ablation kill
  criterion. No code changes; last suite run this session = 683 passed.

---

## 1. Where we are (verified 2026-10-27, post-M12)

| fact | value |
|---|---|
| git | `main` = **237d554** (M12 close-out docs) + this push-record commit; `origin/main` = **237d554** (PUSHED 2026-10-27 on the user's explicit "push it": 844943c..237d554, 6 commits fast-forward — the whole M11 arc + all M12 work is inside it); i7 mirror ff-synced to 237d554 (compose.yaml local opt-ins preserved) — everything after is LOCAL ONLY, push only when asked. Unpushed: only this push-record docs commit |
| working tree | clean except untracked `.resume` (per-machine — leave alone), `drafts/` + `gaps/` (working dirs — keep untracked), and 3 stray `media/` files (Beauty_...HEVC_RAW.hevc, src01_hrc00/01_576x324.yuv — NOT from our work, leave untracked/uncommitted); RAG corpus data under rag/corpus/* is gitignored (rebuilt by rag/tools/build_corpus.sh, recipe rag/corpus/BUILD-RECIPE.md); rag/benchmark/results.json gitignored |
| test baseline | **1278 passed / 1 skipped** (146 s, tr4 host 6.1.1, 2026-10-27, post-M12.HDR) — includes 25 M10.7 webinfo tests, the RAG benchmark gate run (skipif-guarded on the gitignored corpus), 18 rag/author tests, 2 gaps drift regression tests, 13 held-out shape tests, +24 M12.WM multi-watermark tests, M12.BATCH tests, 24 M12.HDR tonemap tests (tests/test_tonemap.py) |
| local machine | **session machine = tr4 (b760one)**; `/dd2/andrei/fass`, host ffmpeg **6.1.1-3ubuntu5**, `.venv` Python 3.12.3, `media/titan.mp4` (20 s test clip). vLLM: tr4-primary 127.0.0.1:8091 (qwen3.8-27b — the ablation endpoint, healthy) + tr4-secondary 127.0.0.1:8080 (swift-1.5-iq3_xxs — weak); fass-rag service live on 127.0.0.1:8100 (pid **1918855** — restarted 2026-10-27 00:4x after the ablation finished; old pid 959120 killed by exact pid) |
| i7 (192.168.0.33) | `ssh i7`; repo mirror at the SAME path `/dd2/andrei/fass` (HEAD **5354cc7** per the M10.0 push; not re-probed this session; true git mirror, github remote); host ffmpeg **6.1.1-3ubuntu5**; `.venv` present (pytest 9.1.1); docker deployment: containers **fass** + **fass-worker** (image `fass:latest` = cc5eda7/registry 2.7.0, built from 7866a93 pre-M12; FFmpeg **9.0.1** — worker recreated onto the current image 2026-10-27 on user request, healthz ok from the fass container). was **OFFLINE 2026-10-26** (no route to host; dispatcher backend offline) — i7's role (docker 9.0.1 E2E) temporarily taken over by tr4 rebuilding the same Dockerfile; **vLLM backend back ONLINE 2026-10-27** (192.168.0.33:8080, qwen3.8-27b, 230k ctx, per dispatcher health) |
| tr4 (192.168.0.30) | `ssh tr4`; hostname **b760one**; repo mirror at the SAME path `/dd2/andrei/fass`, HEAD **5354cc7** (synced from i7 2026-10-25; suite **683 passed / 86 s**), host ffmpeg **6.1.1-3ubuntu5**, `.venv` Python 3.12.3; docker runs **vllm-qwen38-27b-dual-max** + **facecat-db** (no fass containers); 3 GPUs (3090, 3080 Ti, 3090); **ONLINE 2026-10-26 → now the SESSION MACHINE (2026-10-27)** — local main at **6bca1a3** + M12 close-out commit (M11 arc synced from origin at 4ff3393 earlier, pushed as c949b9e); suite **1278/1** verified here 2026-10-27 (post-M12.HDR, 146 s); docker/ untouched (canonical tracked compose; rag overlay docker/compose.rag.yaml, profiles ["rag"]); docker runs vllm-qwen38-27b-dual-max (:8080/:8091) + facecat-db — do NOT touch; fass-rag :8100 (pid 1918855); ablation COMPLETE 880/880 (results at /dd2/andrei/ablation-run1/; /tmp copy may be gone post-reboot) |

Milestone history (all committed): M1 CLI+5 ops (162 tests) → M2 FastAPI web
(178) → M3a multi-inputs (218) → M3b stream I/O (265) → M4 all 8 FFmpeg
components (364) → M5 subtitles (452) → M6 docker deployment (530) → M7
capability expansion (588, f2ddffb) → M8 per-encoder codec options (627,
133a189) → M9 chained processings + BSF user-named bitstream filters (670,
7ad1d2e) → HANDOFF docs (bdfb592, 3634bf4).

> ⚠️ **Git-history warning:** `plan0.md` was removed from the repo AND from all
> git history on 2026-10-24 (`git filter-repo --invert-paths` + force-push).
> Any commit hash quoted in docs/memory dated **before 2026-10-24** no longer
> resolves. Post-rewrite tip was 028ce91; current tip 3634bf4.

---

## 2. What fass is / architecture

Pipeline: `instruction → NLU adapter (deterministic rules = default; optional
LLM — Anthropic or OpenAI-compatible — same pydantic envelope) → strict
EditPlan (pydantic, extra="forbid") → planner (remux vs re-encode from ffprobe
metadata) → builder → argv list[str] → render_posix (shlex.join) → optional
execution layer (validate_argv → Popen, shell=False)`.

Invariants (never break these):
- **argv is the source of truth.** The model never produces shell; argv is
  rendered with `shlex.join` for display only, never parsed back. No shell,
  no eval.
- **No execution by default** — generation only; a human reviews and runs.
  Execution is opt-in per deployment (`FASS_ALLOW_EXECUTE=1`; the docker image
  sets it). Even there, only argv from the builder's fixed vocabulary runs —
  every flag/codec/format/filter/value validated against the registry before
  the process starts.
- trim always re-encodes (`-ss` input option + `-t`); stream copy only when
  ffprobe proves the codecs match (auto bitstream filters both directions:
  `h264/hevc_mp4toannexb` mp4→ts, `aac_adtstoasc` ts→mp4).
- `-y` is never emitted; output never overwrites the input; output
  de-collides with `_out` suffix; `-o == input` is a hard error.
- run output is served back only when the file lives inside the input/output
  volume; asset URLs restricted to an allowlist of streaming schemes
  (`file://`, `ftp://` rejected).
- timecode: 2 fields = MM:SS, 3 fields = H:MM:SS.
- every API outcome (ready / needs_clarification / unsupported / error) is a
  structured **200**; 422 only for malformed requests.

Key code locations:
- `fass/schema/plan.py` — EditPlan, ops. `FilterOp` at :457 (one filter or
  comma-joined same-domain chain; pad labels `[x]` rejected — "they belong to a
  full filtergraph"); `EditPlan.filtergraph: str | None` at :517 with
  `_filtergraph_consistent` validator at :571.
- `fass/registry/loader.py` — `filtergraph_problems(graph)` at :754 (splits
  top-level `;`/` `, honors escapes/quotes, every filter name against the
  allowlist), `filtergraph_output_labels(graph)` at :782 (output pad labels
  for `-map`; empty → FFmpeg auto-maps); filter-expr validator ~:860; BSF
  validation machinery (the M10 template — option kinds choice/number/
  flagset/bool/rational/string, min_version, presence probe, `bsf_errors`).
- `fass/registry/registry.json` — `filters` block: **34 name-only entries
  today (18 video + 16 audio), NO option schemas** (options hard-coded in
  planner); `sources.input.lavfi` (options:false) — the anullsrc fit;
  `streaming` block (M3b allowlists).
- `fass/planner/planner.py` — `plan_execution` at :383, `_audio_filter_chain`
  at :221; BSF additions incl. `local_ffmpeg_version()` (cached probed build
  version — the M10 min_version-warning pattern).
- `fass/builder/builder.py` — :19-20 emits `-filter_complex ep.filtergraph`.
- `fass/execute.py` — `validate_argv` gate (Popen, shell=False).
- `fass/api.py` — FastAPI (`create_app()` + module-level `app`): POST
  /v1/commands, POST /v1/runs, media endpoints, GET /v1/support, /v1/env,
  /healthz, self-contained UI at GET /. `fass serve` subcommand in cli.py.

Run tests: `./.venv/bin/python -m pytest` from the repo root (fass is imported
from cwd, not pip-installed).

---

## 3. Environments & operations

### Both machines (identical layout)
- Repo: `/dd2/andrei/fass` — `fass/` (code), `tests/`, `docker/`, `docs/`,
  `media/titan.mp4`, `scripts/`, `serve.sh` (idempotent local start/stop
  launcher for the NON-docker deployment), `battery-out/` (E2E artifacts).
- Host FFmpeg: **6.1.1-3ubuntu5** on both. Docker image: **9.0.1**
  (`jrottenberg/ffmpeg:9.0-nvidia2404` base, `docker/Dockerfile`,
  `docker/compose.yaml`, `docker/entrypoint.sh`, `docker/launcher.sh`).
- "The two fass versions" for the dual-build rule = **python/host build
  (6.1.1)** and **docker build (9.0.1, i7)**.

### i7 specifics
- `ssh i7` = 192.168.0.33. Repo is a **true mirror** of local (its .git was
  replaced with local's on 2026-10-25; before that it was a disposable fork
  whose history no longer exists locally — the rsync that replaced it: first
  attempt silently NESTED `.git/.git` because the source lacked a trailing
  slash; fixed with `rm -rf .git` + `rsync -a src/.git/ dst/.git/`).
  **Always use trailing slashes when rsyncing in place.**
- **NEVER copy the committed `docker/compose.yaml` over i7's.** i7's copy has
  intentional local opt-ins: fass-worker `docker.sock` mount +
  `FASS_DOCKER_MANAGE=1` (needed for the Config tab's Apply-to-recreate) and
  GPU pin `device_ids: ["1"]`. After any docker/ sync, re-check/re-apply these.
- **GPU allocation (fixed 2026-10-24, pinned memory #304):** strata LLM
  (qwen3.8-27b @ 172.17.0.1:8080) runs on **GPU 0 (RTX 4080 Super)**; the fass
  docker deployment is pinned to **GPU 1 (RTX 4060 Ti)** via compose
  `device_ids: ["1"]` + Config-tab `gpu.device="1"`. The container sees
  exactly one GPU (re-indexed CUDA 0), so nvenc can never collide with the LLM.
- Launcher: `docker/launcher.sh` — start / stop / rebuild / status + fires
  Chrome at http://127.0.0.1:8000. Honors **WAYLAND_DISPLAY, not just DISPLAY**
  (GNOME Wayland bug, fixed 26772d9). i7 often sits at the Wayland login
  screen (no user session) — the browser only fires from a logged-in desktop
  terminal.
- Rebuild after code changes: `docker/launcher.sh rebuild` (or
  `docker compose -f docker/compose.yaml up -d --build`), then smoke:
  `curl -s localhost:8000/healthz`, `curl -s localhost:8000/v1/env`.
- Kill rule: **NEVER `pkill -f "fass serve"`** — kill by EXACT PID only.

### tr4 sync (2026-10-25)
- Code fast-forwarded **3634bf4 → 5354cc7** (M10.0); old local state backed up
  at `/dd2/andrei/fass-sync-backup-20261025-141335` (old HANDOFF.md M10 PLAN,
  old PROGRESS.md, ffmpeg-rag-prd.md, `.resume`, docker/).
- Docs copied from i7: `PROGRESS.md`, `ffmpeg-assistant-rag-prd.md` (new joint
  PRD), `ffmpeg-rag-prd.md` (hash-identical).
- `docker/` was **not** touched: tr4 carries the canonical tracked compose
  (`ddfdc822…`), not i7's working-copy docker.sock / `FASS_DOCKER_MANAGE` /
  GPU-pin changes. A fass docker deployment on tr4 needs its own GPU pin
  (3 GPUs: 3090 / 3080 Ti / 3090) and port config.
- `.resume` is per-machine (Claude session pointer) and was left as-is.
- Verification on tr4: `git status` clean except the untracked docs; registry
  2.2.0 with 84 filters; full suite **683 passed, 1 warning, 86.46 s**.

### Dispatcher (delegation)
- MCP tools: `dispatch_status` / `dispatch_workers` / `dispatch_submit` /
  `dispatch_job` / `dispatch_cancel` / `dispatch_integrate` / `dispatch_resume`.
  **`project_id: "fass"` is required** for submit.
- Backends: **i7 (use first — more resources)** and tr4-secondary. Check
  availability at session start; the main session runs on the strongest
  worker — delegate only when the main session is itself busy with substantial
  work, or when data-heavy authoring is parallelizable (M10.0 registry
  entries are the candidate).
- **Verification-job pattern (proven):** pre-stage a `git diff` patch at
  `/tmp/<name>.patch` on the worker, the job runs `git apply`, then
  `./.venv/bin/python -m pytest -q`, and reports `PATCH:` / `FFMPEG:` /
  `PYTEST:` lines. A FRESH worker .venv needs `pip install -e ".[api,dev]"`
  **PLUS httpx** (fastapi TestClient fails without it; starlette's
  "install httpx2" warning is benign, httpx 0.28.1 works).
- Worker result contract (local-dispatcher `results.py`): top-level
  job_id, attempt_id, outcome ∈ completed|incomplete|blocked|failed, summary,
  changed_files, commit (null or sha); `tests` is a **LIST** of {command,
  status ∈ passed|failed|not_run, evidence_path}; `acceptance_criteria` is a
  list of {criterion, status ∈ met|unmet|unknown, evidence} — "pass" is
  invalid and any "unmet" fails the job.
- **Blocked jobs — do NOT touch:** j_5cdea1a8ff, j_44dff248bb, j_2c8952fd96,
  j_bc96773bee, j_d62b099638, j_ea08c82124, j_688cc282bc.
- Orphaned i7 worktree dirs (inert garbage after the .git mirror — lost git
  registrations): `~/.local/share/local-dispatcher/worktrees/{j_cec61c3098,
  j_848d7f8076, j_3c90b56228}` — **user decision pending: delete or keep.**

### Media / E2E conventions
- Real-media testing uses **media/titan.mp4** (20 s). "prores is mov file"
  (ProRes samples are .mov — treat as such in tests).
- Live E2E against the i7 deployment: `POST /v1/commands` (adapter "rules"),
  then `POST /v1/runs` with the returned `arguments`, poll `GET /v1/runs/<id>`
  until status != running. Reference script pattern: /tmp/bsf_live_i7.py
  (from the BSF milestone — regenerate if gone).

---

## 4. The M10 plan (full — work starts 2026-10-26)

Trigger (user, verbatim): "prepare for a big job, you'll have to implement:"
+ audio list (29): acompressor, acrossfade, adeclick, adrc,
adynamicequalizer, aecho, afade, amerge, amix, aresample, asetrate,
ashowinfo, astats, atempo, atrim, channelsplit, compand, dynaudnorm,
equalizer, hdcd, highpass, join, loudnorm, lowpass, silencedetect,
silenceremove, volume, volumedetect, anullsrc; video list (40;
deinterlace_d3d12 **dropped on user request** — Windows-only): ass, bbox,
blackdetect, blackdetect_vulkan, boxblur, chromakey, colormatrix,
colorspace, crop, cropdetect, decimate, drawbox, drawtext, fade, fps,
framerate, freezedetect, hue, idet, libvmaf, loop, overlay, pad, psnr,
rotate, scale, setdar, setsar, setparams, showinfo, ssim, subtitles, trim,
vidstabdetect, vidstabtransform, vstack, xfade, yadif, scale_cuda, yadif_cuda.
Standing rules: dual-build (local 6.1.1 + i7 docker 9.0.1), titan.mp4 E2E,
delegate to dispatcher where possible.

### Evidence (probed 2026-10-25)
- Registry today: 34 filters (18 video + 16 audio) as NAME-ONLY allowlist, no
  option schemas (options hard-coded in planner).
- Of the 69: **19 already in the registry** (audio: aecho afade aresample
  atempo atrim highpass loudnorm lowpass volume; video: ass boxblur crop
  drawtext fade hue overlay pad scale subtitles) → **50 NEW** (20 audio +
  30 video).
- Existing machinery to reuse (verified in code): `FilterOp`
  (registry-allowlisted same-domain chain, `filter_expr_problems`),
  `EditPlan.filtergraph` (labeled filter_complex, `filtergraph_problems` +
  `filtergraph_output_labels` for `-map`), builder already emits
  `-filter_complex`, execute gate validates filter exprs, N typed inputs
  already exist (M3) — combiner filters need planner wiring only.
- Build availability (probed `ffmpeg -filters`): **local 6.1.1 = 67/69**
  (missing blackdetect_vulkan, libvmaf); **i7 docker 9.0.1 = 68/69** (missing
  blackdetect_vulkan; **libvmaf filter PRESENT but NO model files** — must
  ship one, e.g. vmaf_v0.6.1.xml, into the image/volume).
- Overlaps with existing ops: `trim` (video filter) ≈ TrimOp, `rotate` filter
  ≈ RotateOp (transpose); arbitrary-angle rotate still needs the real filter.
  `fps`/`framerate` not in registry — check whether the planner already emits
  framerate changes before adding. `anullsrc` fits the existing lavfi source
  allowlist (registry `sources.input.lavfi`).

### Design decisions
1. **Registry-driven like BSF:** extend `filters.video`/`filters.audio` with
   option schemas (kinds: choice/number/flagset/bool/rational/string,
   min_version) + new flags: `diagnostic` (log-only output), `multi_input: N`
   (labeled graph), `needs_reference` (2nd input = reference), `gpu`
   (cuda/vulkan presence-gated).
2. **Presence probe:** `FfmpegInfo.filters` from `ffmpeg -filters` (same parser
   style as bsfs — note the proven parser: first column must match
   `^[TSC.-]+$` and second column != "="; the old substring test captured
   description words); `/v1/env` → `filters: {name: {present}}`; `/v1/support`
   → full option table (like bitstream_filters); min_version/absent warnings
   compare the PROBED local build (`local_ffmpeg_version()`, cached).
3. **Option validation:** `filter_options_problems(name, options)` in loader,
   wired into `filter_expr_problems` per-instance, schema, and the execute
   argv gate (-vf/-af branch).
4. **Plan shape per category:**
   - **Transform** (single chain): FilterOp on -vf/-af; NLU phrasing;
     semantic-trap warnings (asetrate = pitch AND speed, unlike atempo;
     decimate = frame drops; chromakey → alpha-capable output or overlay
     needed; colorspace/colormatrix = metadata; loop).
   - **Diagnostic** (10: blackdetect, cropdetect, freezedetect, idet,
     silencedetect, volumedetect, astats, ashowinfo, showinfo, bbox): plan
     forces `-f null -` (output discarded, results in log); execution layer
     already streams logs; structured parsing of results = stretch/M11.
   - **Combiner** (multi-input: amix, amerge, join, acrossfade, xfade,
     vstack, overlay video-on-video): planner emits labeled filtergraph;
     xfade offset = dur(main) − transition (probed); validate input count,
     duration sanity, audio inputs present.
   - **Comparison** (psnr, ssim, libvmaf): `[0:X][1:X]` graph, 2nd input =
     reference, results in log; libvmaf docker-only + model file.
   - **Two-pass:** vidstabdetect (dumpfile into /log or input folder, no
     media out) → vidstabtransform (reads it); "stabilize the video" = 2-step
     plan/chained runs; paths stay in volumes.
   - **Source:** anullsrc via lavfi allowlist.
   - **channelsplit:** full multi-OUTPUT export OUT OF SCOPE (fass is
     1-output/command) — M10 supports it in-graph only (e.g. per-channel
     astats diagnostic); multi-output = separate design (user decision).
   - **GPU/platform:** scale_cuda, yadif_cuda (presence-gated — i7 docker GPU
     yes, local no → warning); blackdetect_vulkan (absent in both builds —
     entry + presence gate).
5. **NLU:** phrasing table per filter; length-preserving mask for option
   tokens (BSF pattern — so keyword detectors can't double-read filter option
   tokens); jargon mapping (stabilize→vidstab, remove silences→silenceremove,
   green screen→chromakey, quality loss→ssim/psnr, deinterlace→yadif,
   crossfade→xfade/acrossfade); ambiguity (dynaudnorm vs loudnorm vs compand)
   → precedence + clarification when unclear.

### Phases (work order)
- **M10.0 foundation:** presence probe, registry table (50 entries + option
  schemas), loader validation, /v1/env + /v1/support, argv gate. (Registry
  authoring is data-heavy → candidate for delegation to i7 worker with an
  exact contract; core wiring local.)
- **M10.1** transform filters (20 audio + ~15 single-chain video): NLU +
  planner + tests.
- **M10.2** diagnostic (10): diagnostic plan mode, `-f null -`, E2E.
- **M10.3** combiners (7): labeled graphs, xfade math, tests.
- **M10.4** comparison (3): reference input, libvmaf model ship.
- **M10.5** two-pass vidstab + GPU + channelsplit-in-graph + anullsrc.
  DONE 2026-10-26, DUAL-BUILD VERIFIED (910 passed / 1 skipped on both
  host 6.1.1 and i7 9.0.1 container).
- **M10.6** verification & docs: local + i7 docker E2E battery per category on
  titan.mp4, full suite, i7-host suite via dispatcher job,
  README/DEVELOPMENT/HANDOFF.
- **M11.0** RAG corpus foundation (PRD §10.3): pinned 6.1.1 + 9.0.1 corpora,
  parser 1.1, chunker, ingest CLI, fixtures, tests. DONE 2026-10-26
  (header bullet; 946 passed host; corpora reproducible across two
  independent builds).

### Acceptance (standing rules)
Suite green locally (6.1.1) and via i7 host job; live E2E on i7 docker
(9.0.1) per category; per-build presence in /v1/env; **670-test baseline, no
regressions.**

### Risks / open
libvmaf model = docker image change (rebuild + compose on i7); xfade duration
math on mismatched inputs; NLU jargon ambiguity; channelsplit multi-output
deferred.

---

## 5. The BSF milestone (immediately prior — its machinery is the M10 template)

**User-named bitstream filters in stream-copy plans** (11 requested; `trim`
is a pseudo-entry — NOT an FFmpeg bsf — `copy_cut: true` switches TrimSpec to
stream-copy mode `-ss`/`-t` + forced `-c copy`; every layer rejects
`-bsf:v trim` with an explanation). 12 real bsfs registry-validated with
per-option kinds (level = 22-value choice, flip = flagset, rotate = number,
discard = choice, time_base = rational, …); auto bsfs (h264/hevc_mp4toannexb,
aac_adtstoasc) unchanged; user bsfs chain AFTER the auto ones, one
`-bsf:v`/`-bsf:a` flag each, options sorted + colon-joined; duplicates of the
auto bsf dedupe. Layers: NLU `_detect_bitstream_filters` (name + key=value
tokens) → length-preserving mask → schema `BitstreamFilterSpec` (+EditPlan max
8) → planner (re-encode conflict, device/subtitles conflict,
audio-bsf-on-no-audio, codec match, per-build min_version) → execute
`validate_argv` (name + option kinds; `-c copy` now accepted). Per-build
availability: `FfmpegInfo.bsfs` probed from `ffmpeg -bsfs`; `/v1/env` reports
`bsfs: {name: {present}}` (showinfo = 6.1.1: False / 9.0.1: True);
`/v1/support` exposes the full spec table; min_version warnings compare the
PROBED local version.

Results: local 670 passed (635 baseline + 35); E2E bsf chain
`h264_mp4toannexb` + `h264_metadata=level=4.1` → mpegts (SPS level 41 probed
back), copy-trim 5.079 s out of 20 s; i7 docker 9.0.1 LIVE-VERIFIED (showinfo
present, no 7.1 warning on 9.0.1, bsf chain + copy-trim executed exit 0);
i7 host suite job j_848d7f8076: 670 passed / 6.1.1. Files:
fass/registry/{registry.json, loader.py, __init__.py}, fass/schema/{plan.py,
__init__.py}, fass/nlu/rules.py, fass/planner/planner.py, fass/execute.py,
fass/probe/ffprobe.py, fass/api.py, tests/test_bsf.py (13 → 48 tests),
docs/DEVELOPMENT.md.

---

## 6. Mycelium memory dump (project `fass`, 23 memories, 2026-10-25)

If a mycelium MCP is available in the continuing session, the ids below are
queryable; otherwise this section is the copy.

### Hubs / pinned
- **#307 (pinned) — project hub.** Architecture + invariants (see §2). Key
  decisions: trim always re-encodes; stream copy only when ffprobe proves
  codecs match; -y never emitted; output de-collides `_out`, -o==input hard
  error; timecode 2=MM:SS/3=H:MM:SS. i7: fass pinned GPU 1 (RTX 4060 Ti,
  compose device_ids ["1"] AND config gpu.device="1"), strata LLM
  (qwen3.8-27b @ 172.17.0.1:8080) on GPU 0; docker/launcher.sh fires Chrome
  at the GUI (honor WAYLAND_DISPLAY). plan0.md removed from ALL git history
  2026-10-24 — pre-2026-10-24 commit hashes don't resolve. Tests:
  `./.venv/bin/python -m pytest` from repo root (fass imported from cwd, not
  pip-installed).
- **#304 (pinned) — i7 GPU allocation.** strata LLM (qwen3.8-27b) on GPU 0
  (RTX 4080 Super); fass docker pinned to GPU 1 (RTX 4060 Ti) via
  gpu.device="1" Config tab + Apply. Container sees exactly one GPU
  (DeviceIDs:["1"], re-indexed CUDA 0) → nvenc can never collide with the
  LLM's card. Set 2026-10-24.
- **#290 (pinned) — M2 API contract.** POST /v1/commands (CommandRequest:
  instruction, assets id→local path, output_filename, target_shell
  Literal["posix"], previous_plan, media ffprobe-JSON, adapter, preflight);
  GET /v1/support, /v1/env, /healthz; self-contained UI at GET /. Every
  domain outcome (ready/needs_clarification/unsupported/error) = structured
  200; 422 only for malformed requests; service never executes commands, no
  upload endpoints (PRD §10). API tests pin adapter="rules" (in this env
  "auto" picks the LLM adapter; ANTHROPIC_API_KEY set but invalid →
  structured "error", never 5xx). Bug lesson: probe_ffmpeg() parsed
  `ffmpeg -filters` with a substring test that only matched "TS." rows and
  captured description words — fixed with first column `^[TSC.-]+$` and
  second column != "=" (555 filters vs 21 bogus on 6.1.1). **The M10 filter
  presence probe must use the fixed parser style.**

### Milestone checkpoints (condensed)
- **#322** [latest] — M10 preparation: PLAN finalized in HANDOFF.md, no code
  yet; 69 filters (d3d12 dropped), 50 new / 19 existing; phases M10.0→M10.6;
  availability local 67/69, i7 docker 68/69, libvmaf needs model files.
  Next: M10.0 on 2026-10-26. State: origin/main = 3634bf4, i7 mirrored,
  670-test baseline.
- **#321** — M10 plan detail (categories: transform / diagnostic / combiner
  multi-input (amix, amerge, join, acrossfade, xfade, vstack, overlay) /
  comparison (psnr, ssim, libvmaf 2nd input = reference) / two-pass
  (vidstabdetect→transform) / anullsrc via lavfi / channelsplit in-graph only
  (multi-output deferred) / GPU-gated (scale_cuda, yadif_cuda;
  blackdetect_vulkan presence-gated)).
- **#315** — M8 (per-encoder codec options) complete, dual-build verified:
  core 133a189 (627 tests); i7 docker j_69d3f78ec0 VERIFIED (10/10 docker
  battery, fdk_vbr4 executed — 9.0.1 has libfdk_aac, QSV skip no-device,
  /v1/support 16 tables); tr4-secondary j_c7320e8dfe VERIFIED (10/10 + 627
  passed); pushed, origin/main then = 3db9a64.
- **#312** — M8 docker verification detail: m8_battery.sh docker 10/10; i7
  compose.yaml needs docker.sock mount + FASS_DOCKER_MANAGE=1 uncommented
  for docker mode.
- **#310** — M7 capability expansion: 21/21 real-ffmpeg battery both builds;
  lesson: **ffmpeg 9.x mxfenc needs `-g 120`** (frame offsets ±127 limit) —
  MXE/MXF E2E on 9.0.1 fails GOP-less; suite 588; commit f2ddffb. Answer to
  "why not ALL containers": registry-driven — support = data rows in
  registry.json = the practical full common set of both builds.
- **#309** — capability expansion slice (audio/video/subtitle output modes,
  extended container matrix, filters+filtergraph, format options, srt/http):
  planner/schema/registry/tests 5afac91 (534 tests); execution layer was
  delegated job j_18eb0c649c; NLU rewrite of rules.py + llm.py followed.
- **#308** — critical UI bug lesson: the ENTIRE web UI was dead because a
  `confirm()` string in fass/ui.py held `\n` inside a JS single-quoted string
  that PYTHON interpreted while building UI_HTML → real line break in served
  JS → SyntaxError → whole <script> rejected. Source `node --check` passed
  (the 2-char escape is in the source); only the SERVED bytes break. Fix:
  `\\n`; regression test `test_ui_script_is_valid_javascript` runs
  `node --check` on the served bytes (skipif no node). **Any future ui.py
  edit: keep served JS on single physical lines or escape properly, and
  re-run that test.**
- **#305 / #306** — docker deployment: GPU pinning E2E; launcher.sh built +
  tested (start/stop/rebuild/status, stub-browser proof); Wayland guard bug
  (check WAYLAND_DISPLAY, not just DISPLAY — 26772d9); plan0.md removed from
  repo AND history (filter-repo --invert-paths, refs/original purged,
  gc --prune=now, force-push; post-rewrite tip 028ce91); stale worktrees
  cleaned.
- **#303** — M6 docker packaging: 530 tests; jrottenberg/ffmpeg:9.0-nvidia2404
  base; Config tab (volumes/streams/GPU/LLM); argv-validated in-container
  execution; fass-worker sidecar for Apply (a container cannot recreate
  itself); containers fass + fass-worker.
- **#301** — M5 subtitles: SubtitleOp burn (libass `subtitles=`/`ass=`, forces
  re-encode) + soft (mov_text mp4/mov, srt mkv; `-c:s` after `-c copy` →
  stream-copy compatible; webm/mpegts burn-only); filter-path double-escaping
  locked by round-trip tests + E2E on a file literally named `it's.srt`;
  colons in filenames rejected; registry 1.6.0; 452 tests.
- **#297** — M4 all 8 FFmpeg components: core ops ced889c, rules NLU 43251a0,
  test suite 515d443, audio remux bsf aac_adtstoasc 645f903 (delegated),
  docs ed067ba (delegated); 364 tests.
- **#293 / #294** — M3b stream I/O (1f4ade5): registry 1.3.0 `streaming`
  block = source of truth — input allowlist http(s)/rtmp(s)/rtsp(s)/rtp/
  udp/tcp/srt/tls; output scheme→container rtmp(s)→flv, rtsp(s)→rtsp,
  srt→srt, udp/rtp/tcp→mpegts (with -f muxer injection); raw udp/rtp/tcp
  inputs get `-f mpegts` pre-opt; `_reconcile_output_destination` applies
  the -o stream URL the NLU never sees (scheme authoritative only for a
  defaulted container). Empirical FFmpeg: udp muxer sends variable-length
  datagrams (validate by reassembly: total % 188, 0x47 sync, PAT PID 0);
  tcp:// input defaults to CONNECT — E2E uses `?listen=1`; **udp:// input has
  NO end-of-stream (a short feed hangs forever)**; UDP datagram max 65507 B.
- **#292** — M3a multi local typed inputs + AudioReplaceOp (69d5595):
  planner main=0 → watermark=1 → audio-last, `-map N:a:0` + `-shortest`,
  encoded to container codec (forces re-encode); CLI `--asset id=kind:path`
  pins kind for extensionless stream URLs.
- **#286** — M1 benchmark: tests/benchmark/requests.json (78 entries) +
  test_benchmark.py; rules.py bug lessons: range units ('5 to 25 seconds'),
  'keep' as trim verb, crop regex must match bare 'crop', 'stabilis' misses
  'stabilize', revision negation ('no watermark') was re-added by detection.

### Operational lessons (provisional)
- **#317** — verification jobs: fresh .venv needs `pip install -e
  ".[api,dev]"` **PLUS httpx** (fastapi TestClient fails without it;
  starlette 1.10 "install httpx2" warning benign, httpx 0.28.1 works). Full
  suite on 6.1.1-3ubuntu5: 670 passed, 1 warning, ~88 s.
- **#296** — exact local-dispatcher result.json shape that passes
  validate_result (see §3 dispatcher section).
- **#287** — transparent PNG assets: `color=c=black@0.0` and
  `drawbox=...:color=white@1.0` do NOT write the alpha plane (PNG fully
  opaque). For a transparent PNG use solid core + transparent pad:
  `-f lavfi -i color=c=white:s=64x64:d=1 -vf
  format=rgba,pad=128:128:32:32:color=#00000000 -pix_fmt rgba` (verified via
  RGBA alpha histogram).
- **#302** — serve.sh (repo root): one idempotent start/stop script for the
  LOCAL (non-docker) deployment; logs to /tmp/fass-serve.log; detached
  server on 0.0.0.0:8000 in the i7 history.

### Current session checkpoints (superseded chain, kept for traceability)
#319 (BSF complete — committed 7ad1d2e, pushed, i7-synced; superseded by #322
and forgotten), #318/#316 (forgotten, same chain).

---

## 7. Standing user rules (verbatim — in force)

- "for all new ffmpeg-related features you should work on both, docker and
  python versions of fass" (dual-build: host 6.1.1 + i7 docker 9.0.1)
- "don't be lazy, don't ask me!"
- "don't forget, you can delegate some tasks to dispatcher's workers"
- "use dispatcher's backends to delegate tasks then possible"
- "2 backends of dispatcher are idling / use i7 first, it has more ressources"
- use media/titan.mp4 for real-media testing
- "prores is mov file"
- `/dd2/andrei/fass/CLAUDE.md` was DELETED by the user — must NOT be recreated
- NEVER `pkill -f "fass serve"` — kill by EXACT PID only
- commit ONLY when asked; push ONLY when explicitly asked
- git commit messages end with `Co-Authored-By: Claude Code
  <noreply@anthropic.com>`; PR descriptions end with `🤖 Generated with
  [Claude Code](https://claude.com/claude-code)`
- global CLAUDE.md habits: read files in targeted ranges; search for symbols
  before reading whole files; no dumping generated files/lockfiles/full logs
  into the conversation; incremental edits; maintain HANDOFF.md and update at
  milestones; at session start check dispatcher status/workers; delegate only
  when the main session is busy with substantial work

---

## 8. Pending user decisions + next unfinished task

**Next unfinished task (2026-10-27, M13 IMPLEMENTATION):**
M13.1 is functionally complete + E2E-verified on tr4 6.1.1
(suite 1295/1, E2E 3/3) but UNCOMMITTED (12 files in the working
tree — commit/push only on user request; the i7 fass-image rebuild
is parked on that commit). Remaining close-out: (1) 9.0.1 docker
half — base image pulling on tr4, then build fass:dev from the
working tree + in-container DASH E2E; (2) update HANDOFF.md;
(3) /savework checkpoint. Then **M13.2 live HLS** (plan §3 —
details in the top Last-state block), then M13.3 → M13.7 in plan
order; per milestone: suite green + E2E 6.1.1 + the 9.0.1 docker
half (reuse the fass:dev build) + the user hand-tests the
reference fass commands as they land. M12 close-out record below.
**M11 series COMPLETE** (the ~00:55 block above remains the M11
record; its two "remaining" items are superseded — i7 9.0.1 battery
ran 21/21 zero-skips and landed as c949b9e, and origin/main was
pushed 2026-10-27 on the user's explicit "push it": first to c949b9e,
then to 844943c (the "push landed" docs commit), i7 synced). M12 =
the PRD-deferred scope (multiple watermarks, HDR workflows, batch
processing) — ALL LANDED:
- **VMAF/compare convention — COMMITTED b8ed8ff.** Compare follows
  the Netflix vmaf docs CLI form: `-i reference -i distorted`,
  metric pads `[distorted][reference]` (c1/c0), distorted scaled to
  reference size, `-f null -`, score in run log only (web-UI Log
  tab). setpts is probe-conditional (probe_has_pts(), ffprobe
  packet csv, first 8 packets): raw bitstreams skip it with a
  warning, compared in arrival order. Suite 1223 passed / 1 skipped
  (tr4 6.1.1); i7 9.0.1 docker battery 4/4. i7 docker quirk:
  jrottenberg:9.0 ENTRYPOINT = ffmpeg → pass fass argv[1:].
- **M12.WM (multiple watermarks) — COMMITTED ea9919e.**
  Schema: `_at_most_one_watermark` → `_at_most_one_single_use_op`
  (watermark exempt from the at-most-one rule); planner: wm_paths
  list, wm_n/wm_total ordinals, one overlay per watermark in input
  order, overwrite guard; NLU `_detect_watermarks` plural rewrite —
  an image named in the request binds, with none named every bound
  image is used, one position phrase per watermark in text order,
  the rest default bottom-right; tests/test_schema.py test flipped
  to test_multiple_watermarks_are_allowed; suite 1233/1; two-
  watermark E2E smoke passed.
- **Dispatcher jobs — MERGED.**
  M12.BATCH j_65e7374aef: SUCCEEDED → dispatch_integrate
  accepted → committed 9967441 (suite 1254/1). M12.HDR
  j_ba4899bb09: job record failed invalid_result only (result-JSON
  formatting: outcome 'success_pending_review' outside the enum,
  acceptance_criteria missing 'criterion' keys; scope_violations
  [".venv"]) — the work itself was complete + E2E-verified;
  dispatch_integrate REFUSED (server gate: only succeeded jobs can
  be integrated, no override) → manually reviewed + merged; commit
  8c2e11a is the integration record, worktree retained; tr4 6.1.1
  re-verified (registry 2.8.0 with tonemap; suite 1278/1 after).
  Third case of the cc-worker result-vocabulary drift (after c27a0d8
  and j_3d4505da56) — recommend adding the result-schema contract
  to the cc-worker profile prompt.
- **After merges (this segment):** M12 close-out — README +
  docs/DEVELOPMENT + HANDOFF + PROGRESS updated, commit, /savework
  per standing mandate. The RAG PRD (M11.0–M11.4) is already DONE,
  so the mandated scope is fully complete; what remains are
  user-decision items only (see "Decisions pending" + the Next
  block at the top).

**Decisions pending:**
1. **channelsplit** — RESOLVED by the M13 scope (2026-10-27 user
   picks): muxer-level multi-output (HLS/DASH/tee already write N
   files; the plan model stays one logical output), NOT per-channel
   N-file export.
2. **Orphaned i7 worktree dirs** — delete
   `~/.local/share/local-dispatcher/worktrees/{j_cec61c3098, j_848d7f8076,
   j_3c90b56228}`?

**M10.0 foundation — DONE 2026-10-25** (all five items delivered as commit
5354cc7, pushed): registry 2.2.0 = 84 filters (48v + 36a) with option schemas;
`FfmpegInfo.filters` presence probe (fixed parser style: first column
`^[TSC.-]+$`, legend rows rejected); `filter_options_problems` +
`_parse_filter_args` in loader.py wired into `filter_expr_problems`
per-instance, the schema, and the -vf/-af/-filter_complex argv gate; /v1/env
+ /v1/support filter surfaces. 683 passed verified 2026-10-25 on BOTH builds
(i7 host 6.1.1 earlier same day; local 6.1.1 116 s); docker 9.0.1 rebuilt +
argv E2E per header.

**M10.1 transform filters — DONE 2026-10-25** (commit 6da37f9, pushed):
- NLU (fass/nlu/rules.py): `_TRANSFORM_JARGON` bare-name claim set (excludes
  ordinary words the existing detectors own — loop/trim/crop/scale/fade/
  volume/rotate/equalizer), phrase table (~20 audio + ~15 video),
  `_TRANSFORM_NUMBERED` adjacent-number claim (fps/framerate/asetrate),
  expression form `name=…` → named k=v pairs + positional values mapped to
  canonical schema keys in order (alias keys deduped by identical
  description); claimed name/option tokens blanked length-preservingly (the
  BSF mask pattern) so resample/bitrate/codec-option detectors can't
  double-read them. Ambiguity: dynaudnorm phrase wins over loudnorm;
  compress-audio suppresses the vague-size clarification; "silence the
  audio" stays mute (anull), "remove the silences" is silenceremove.
- Planner: FilterOp on -vf/-af; per-filter disclosures on the probed build —
  presence, min_version, GPU, `_TRAP_NOTES` semantic warnings (asetrate =
  pitch AND speed; decimate = frame drops; chromakey → alpha; colorspace/
  colormatrix = metadata-only; fps = playback speed unchanged; setdar/setsar
  not rescaled; rotate pads the frame; loop longer than input;
  silenceremove timestamps; trim), deduped per filter name. Arbitrary-angle
  rotate = the real rotate filter (quarter turns keep the transpose op).
  colorspace phrase emits `iall=X:all=X` — the filter rejects frames with
  unknown primaries (the common web-video case; verified on 9.0.1/titan.mp4).
- Registry fix: `acompresor` → `acompressor` (M10.0 misspelling; the probe
  reported it absent in both builds). `probe_ffmpeg()` cached per process.
- Verified: suite **738 passed** local 6.1.1 (77 s); docker 9.0.1 generation
  battery 20/20 (rules adapter; trap warnings surface) + live execution
  battery 10/10 on titan.mp4 (yadif, acompressor, fps, rotate45, drawbox,
  colorspace, asetrate, silenceremove, decimate, chromakey — all exit 0).
  README "Transform filters" bullet added. tr4 offline — not synced.

**M10.2 diagnostic filters — DONE 2026-10-25** (commit 4ff3393, pushed):
- NLU (fass/nlu/rules.py): `_DIAGNOSTIC_JARGON` claim set (11 names merged
  into `_detect_transform_filters`' claim scan, domain from registry
  membership, length-preserving mask) + measurement-verb phrase table
  (`detect|find|locate|report|measure|check|show|list|analyse` + nouns).
  Verb decides, windows never overlap: "detect silences" = silencedetect,
  "remove the silences" stays silenceremove, "silence the audio" stays
  anull; "detect interlacing"/"is this video interlaced" = idet,
  "deinterlace" stays yadif; "black frames" = blackdetect, "black bars"/
  "letterbox bars" = cropdetect; "how loud"/"peak volume" = volumedetect
  (volume gain needs a number); "audio frame info" = ashowinfo, "frame
  info" = showinfo (audio-lookbehind).
- Planner: all-diagnostic filter plan → mode `diagnostic` via
  `_build_diagnostic_plan`: `-f null -` (registry `diagnostic_output`
  section, loader `diagnostic_output()`), `ExecutionPlan.loglevel="info"`
  (diagnostic filters log at info; builder `-loglevel` now from the plan),
  maps=[], no codec flags, no file; trim → `-ss` pre-opt + `-t`; audio
  assets accepted (allowed_kinds relaxation); stream URL / audio device
  destinations → PlanError ("writes nothing"); output filename ignored
  (defaults note); per-filter `_DIAG_NOTES` warnings. Mixed plans (any
  non-diagnostic op) stay normal encodes.
- Gate (fass/execute.py): null muxer in the `-f` vocabulary; bare `-`
  recognized as the final output path; `resolve_paths`/`start_run` treat
  `-` as no-file ("diagnostic run — output discarded").
- Registry: silencedetect/freezedetect `n`/`noise` kind number→string —
  ffmpeg's av_parse_number accepts the dB suffix on `<double>` options
  (`-30dB` works, `-30db` fails); NLU `_fix_db` restores canonical case
  after instruction lowercasing (verified live on 6.1.1).
- Explain: diagnostic summary branch (no Encode/Audio-encode lines;
  "Analyzed range: A to B").
- Verified: suite **783 passed** local 6.1.1 (75 s); docker 9.0.1 rebuilt,
  generation battery 12/12 (`-f null -` + `-loglevel info` argv) + live
  battery 11/11 on titan.mp4 (exit 0; mean_volume, crop, pts_time, crop=,
  nb_samples, Stream mapping verified in run logs). README "Diagnostic
  analysis" bullet added. tr4 offline — not synced.

**M10.3 combiners — DONE 2026-10-26 (uncommitted; details in HANDOFF.md
"Current state"):** 9 combiners (xfade/concat/hstack/vstack/overlay +
acrossfade/amix/amerge/join) as labeled `-filter_complex` graphs;
registry 2.3.0; `combine` plan op (one per plan, conflict-rejected,
revision-replaces); NLU two-input phrasings + asset binding (name /
only-other-bound / clarify); planner probes extras with
`require_video=False`; xfade offset math + pad normalization + size
scaling; concat per-input pad order + `-an` when audioless; 62 new
tests with E2E fixture execution. Suite 845 passed on tr4 6.1.1.
9.0.1 verification: i7 down (no route to host) → tr4 rebuilds the same
Dockerfile and runs the full suite in the 9.0.1 container (in progress).

**Next unfinished task (M10.4 comparison):**
1. Comparison filters: psnr, ssim (present in both builds) and libvmaf
   (9.0.1 only on the tested builds; presence-gated + min_version) —
   registry option schemas cross-checked on both builds.
2. Plan-level reference input: a second media asset the user names
   ("compare against ref.mp4"); the planner builds a labeled graph
   `[0:v][1:v]psnr[out]`-style, null-muxer output for the metric,
   log-level info so the summary line (e.g. `PSNR ... mean:`) is
   captured from the run log (diagnostic plan mode, M10.2 pattern).
3. libvmaf model ship: vendor the model file into the docker image
   (docker/ + Dockerfile COPY) so `-libvmaf=model=...` resolves.
4. Tests (schema/NLU/planner/argv + E2E where the build allows);
   keep 845 green on both builds (dual-build rule).

**RAG workstream (M11) — joint PRD DELIVERED 2026-10-25**
(`ffmpeg-assistant-rag-prd.md`, 500+ lines; untracked, no code changes).
It merges the assistant scope with the documentation-RAG idea. **Verdict:**
yes, viable — as an advisory evidence layer only. Four uses: LLM prompt
grounding, plan-derived citations (never model-generated), offline
registry authoring (docs + `ffmpeg -h` cross-check), and closest-supported
explanations for unsupported requests. Explicitly rejected: free-form
LLM+RAG command generation. Registry/builder/argv gate remain the
executable truth; the RAG PRD's capability/compatibility endpoints were
dropped (fass probe is authoritative). Sequencing: M11.0 (pinned 6.1.1 +
9.0.1 corpora) and M11.1 (retrieval API + benchmark) are independent of
fass code and can run in parallel with M10.1 — delegate to i7. M11.2
(fass integration) starts after M10.1; M11.3 (authoring pipeline) feeds
M10.2–M10.5; M11.4 closes with the ablation. Pre-registered kill
criterion: if registry-rendered option schemas match LLM+RAG on the
ablation, ship static citations only and keep RAG for authoring.

Then M10.2 diagnostic → M10.3 combiners → M10.4
comparison (+ ship a libvmaf model file into the docker image) → M10.5
two-pass vidstab / GPU / channelsplit-in-graph / anullsrc → M10.6
verification (local suite + i7-host suite via dispatcher job + i7 docker
live E2E battery per category on titan.mp4) and docs (README /
docs/DEVELOPMENT.md / HANDOFF.md).

**Resume checklist (i7 = current dev box; tr4 = handoff target):**
```bash
cd /dd2/andrei/fass
git status --short        # expect: M docker/compose.yaml, ?? .resume, ?? PROGRESS.md, ?? ffmpeg-rag-prd.md, ?? ffmpeg-assistant-rag-prd.md
git log --oneline -3      # expect tip 4ff3393 (M10.2)
./.venv/bin/python -m pytest -q   # expect 783 passed, ~75 s
docker ps | grep fass     # i7 only: fass + fass-worker Up (tr4 has NO fass containers)
curl -s localhost:8000/healthz    # i7 only
```

### tr4 handoff details (2026-10-26)
- **Sync procedure** (tr4 is a true git mirror with the github remote):
  `cd /dd2/andrei/fass && git fetch origin && git reset --hard origin/main`
  → expect tip **4ff3393** (M10.2). Then copy the untracked docs from i7
  (`rsync -a /dd2/andrei/fass/PROGRESS.md tr4:/dd2/andrei/fass/` and the two
  PRD files; trailing slashes when rsyncing directories). **Do NOT touch
  `docker/`** — tr4 carries the canonical tracked compose, not i7's
  working-copy docker.sock / `FASS_DOCKER_MANAGE` / GPU-pin changes.
  `.resume` is per-machine, leave as-is.
- **Verification on tr4:** host suite — `./.venv/bin/python -m pytest -q`
  → expect **845 passed** (~95 s on tr4; 783 baseline + 62 M10.3
  combiners). tr4 runs **no fass docker containers** (it runs
  vllm-qwen38-27b-dual-max + facecat-db); the docker 9.0.1 E2E battery is
  i7's job (battery script: `/tmp/m102_e2e.py` on i7, generation + live
  runs against `localhost:8000`). **i7 OFFLINE 2026-10-26** (no route to
  host) → interim 9.0.1 verification on tr4: `docker build -f
  docker/Dockerfile -t fass:m103-verify .` (same base
  `jrottenberg/ffmpeg:9.0-nvidia2404` as i7's `fass:latest`), then run
  the suite inside: `docker run --rm -v /dd2/andrei/fass:/host -w /host
  fass:m103-verify /opt/venv/bin/python -m pytest tests/ -q`.
- **M10.3 combiners — DONE (registry 2.3.0, uncommitted 2026-10-26):**
  all nine present with `multi_input` markers: video `xfade` (2;
  transition/duration/offset/expr), `concat` (0; n/v/a), `hstack` (0;
  inputs/...), `vstack` (0), `overlay` (2; x/y/eof_action/eval/shortest/
  format — option schema added); audio `acrossfade` (2; nb_samples/
  duration/overlap/curve1/curve2), `amerge` (0), `amix` (0), `join` (0).
  Full state in HANDOFF.md "Current state".
- **xfade math (the §4 item):** `offset` = duration of the FIRST input
  minus the transition `duration` (from the ffprobe of input 1); the plan
  must compute it, never ask the user. Labeled 2-input graphs
  (`[0:v][1:v]xfade=…[v]`) go through `-filter_complex`, not `-vf`.
- **Standing rules for tr4:** dual-build rule applies to i7 (host 6.1.1 +
  docker 9.0.1); tr4 verifies the host suite. titan.mp4 for real-media
  testing. NEVER `pkill -f "fass serve"` — exact PID only. Commit only when
  asked; push only when explicitly asked.
