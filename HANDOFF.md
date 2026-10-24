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

## Current state (2026-10-24, M6 COMPLETE — committed 2344be3)
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
- Current i7 containers: fass **69d06f0a091b** (compose-recreated after
  the launcher + compose GPU-pin changes; same config), fass-worker up;
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
docker/{Dockerfile,entrypoint.sh,compose.yaml,README.md}, .dockerignore
(M6), serve.sh (launch script),
media/titan.mp4, pyproject.toml,
.gitignore, README.md, docs/DEVELOPMENT.md, ffmpeg-assistant-prd.md,
HANDOFF.md

## Test results
`./.venv/bin/python -m pytest -q` → **530 passed** in ~67 s, no failures
(2026-10-24, M6: 452 pre-M6 + 78 test_m6_*.py incl. the worker sidecar
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
**M6 committed as 2344be3** (31 files, +3572/−235) and **pushed** —
nothing outstanding. **main = origin/main = 54e2a65** (M6 + HANDOFF
records incl. i7 GPU pinning, stream in/out E2Es, docker/launcher.sh).
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
4. plan0 §7 learning infra (corpus + eval harness) before any fine-tuning.
Remote `origin` (github.com/suedenglehem/fass.git) exists since 2026-10-23;
commit/push with it only when asked.

## Environment notes
- Python 3.12.3, `.venv` (PEP 668 blocks system pip) — use `./.venv/bin/python -m pytest`.
- ffmpeg 6.1.1 + ffprobe on PATH (main box).
- Dispatcher: fass project was `single` mode + had zero commits — switched to
  team and committed initial state (local, no push) to enable delegation.
