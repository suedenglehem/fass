# HANDOFF — fass (natural language → validated FFmpeg commands)

## Goal
Implement plan0.md milestone 1: CLI-first pipeline (instruction → NLU → strict
EditPlan → planner → builder argv → POSIX render), 5 MVP ops (trim, resize,
grayscale, encode/convert, one watermark), 70+ deterministic tests, then a
50-request benchmark, real media-fixture outcome tests, README. No web UI yet.
Spec: ffmpeg-assistant-prd.md; guide: plan0.md.

**MILESTONE 1 COMPLETE (2026-10-23).** 162 tests pass (33 s): 70 deterministic
(schema, rules, planner/builder, render-adversarial, cli) + 78 language
benchmark + 10 media outcomes (synthetic lavfi) + 4 real-media (titan.mp4).

**MILESTONE 2 COMPLETE (2026-10-23).** FastAPI web layer over `generate()`
(plan0: "add the web interface after that path works end to end"). 178 tests
pass: +16 API tests (PRD §9 contract, security invariants). `fass serve`
smoke-tested live (healthz, /v1/commands, UI).

## Current state (2026-10-23)
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

## Changed files (milestone 1 + 2 + M3a + M3b + M4)
fass/{__init__,__main__,cli,pipeline,explain,feedback,api}.py,
fass/schema/plan.py, fass/registry/, fass/probe/ffprobe.py,
fass/planner/, fass/builder/, fass/nlu/{rules,llm}.py,
tests/{conftest,test_schema,test_rules,test_planner_builder,
test_render_adversarial,test_cli,test_api}.py,
tests/benchmark/{requests.json}, tests/test_benchmark.py,
tests/test_media.py, tests/test_titan.py, tests/test_bsf.py,
media/titan.mp4, pyproject.toml, .gitignore, README.md, HANDOFF.md

## Test results
`./.venv/bin/python -m pytest -q` → **364 passed** in ~55 s, no failures
(2026-10-23, after both M4 delegations integrated; 265 at M3b: +56 M4
core suite 515d443, +30 M4 NLU tests in 43251a0, +13 bsf tests in
645f903). 89 benchmark entries, 28 API tests.
Real-media sanity on media/titan.mp4: udp stream-out renders
`-f mpegts … udp://127.0.0.1:9999` + trim warning; `-o rtmp://…` → flv +
`-f flv` + reconciliation default note + disclosure in summary.
Live server smoke-tested: `fass serve` + curl (healthz, command, UI).
venv has pydantic 2.14.1, pytest 9.1.1, fastapi+uvicorn, httpx 0.28.1
(TestClient dep, added milestone 2); fass is NOT pip-installed (cwd import) —
run tests from repo root.

## Next steps
Milestones 1+2+3 and M4 (core + NLU + audio bsf + docs) all done and
committed. The 8-component build goal is now code-complete AND
documented for all 8 components (1-3, 7, 8 since M4 core; 4 now both
directions after 645f903; 5+6 since M3b). Both delegated jobs
(j_893e3a47a6, j_bf063ade65) integrated, accepted, no jobs in flight.
364 tests green. Nothing outstanding unless the user asks.

1. i7 redeploy (kill EXACT PID 660597 → rsync → restart detached) ONLY if
   requested — i7's fass serve still runs the pre-M4 build.

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
2. More ops beyond the M4 set (volume, subtitles, concatenation, ...),
   LLM adapter evaluation against the 89-request benchmark.
3. UI niceties (history, drag-drop — PRD explicitly defers drag-and-drop).
4. plan0 §7 learning infra (corpus + eval harness) before any fine-tuning.
Remote `origin` (github.com/suedenglehem/fass.git) exists since 2026-10-23;
commit/push with it only when asked.

## Environment notes
- Python 3.12.3, `.venv` (PEP 668 blocks system pip) — use `./.venv/bin/python -m pytest`.
- ffmpeg 6.1.1 + ffprobe on PATH (main box).
- Dispatcher: fass project was `single` mode + had zero commits — switched to
  team and committed initial state (local, no push) to enable delegation.
