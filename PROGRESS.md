# PROGRESS — fass (natural language → validated FFmpeg commands)

**Self-contained continuation file.** Written 2026-10-25 (end of session) so the
M10 work can be continued **on the i7 machine** with no other context source
needed. Sources: `HANDOFF.md` (repo), mycelium memories of the `fass` project
(dumped into §6), and live probes of both machines this session.

- **Last state:** M1–M9+BSF + M10.0–M10.5 + M11.0 shipped (M10.5
  dual-build verified, M11.0 committed this milestone). **M10.5 two-pass
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
- **M10.6 verification IN PROGRESS 2026-10-26** (host half DONE):
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
  **Fixed in this commit**: `settb` registry entry + docs_provenance
  URL (51 video / 36 audio = 87); `loader._validate_instance` strips
  ALL leading labels; the local battery now runs `validate_argv` on
  every generated argv before executing (local + docker share
  acceptance semantics); 2 regression tests (4-pad concat graph,
  xfade settb chain) + count assertion updated; DEVELOPMENT filter
  count 86→87. **Host 6.1.1 re-run: battery 21/21 with the gate
  ACTIVE, suite 953 passed / 1 skipped.** Final i7 21/21
  re-verification resubmitted at this commit; docs evidence lines
  then get the real container numbers.
- **Next task:** **M10.6** — final i7 21/21 re-verification running at
  the fix commit (expect suite 953/1 skipped, battery 21/21 with
  fdk_vbr4/libvmaf/scale_cuda executing); on success: DEVELOPMENT
  Testing evidence line + README/HANDOFF i7 numbers + commit, then
  **M11.1 retrieval API** per the PRD — part A = FTS5
  lexical core + fass-rag service, DISPATCHED to tr4-secondary (job
  j_7dea91b80b at b989e31); part B = §9.1 benchmark (84 exact + 50
  option-level + 60 NL + 10 version-mismatch + 5 insufficient) + gates
  + lexical-vs-hybrid decision recorded → M11.2 fass integration
  (advisory evidence only — the registry/builder/argv gate remain
  sovereign) → M11.3 authoring pipeline → M11.4 ablation kill criterion.
- **Planning 2026-10-25 (later session): joint PRD delivered** —
  `ffmpeg-assistant-rag-prd.md` merges the assistant scope with the
  documentation-RAG idea. Verdict: RAG is viable as an **advisory evidence
  layer only** (LLM prompt grounding, plan-derived citations, offline
  registry authoring); free-form LLM+RAG command generation is explicitly
  rejected. Registry/builder/argv gate remain sovereign. M11.0–M11.4
  sequenced parallel to M10.1–M10.6 with a pre-registered ablation kill
  criterion. No code changes; last suite run this session = 683 passed.

---

## 1. Where we are (verified 2026-10-25)

| fact | value |
|---|---|
| git | `main` = `origin/main` = **4ff3393** (`M10.2: diagnostic filters — NLU phrasing, diagnostic plan mode, null-muxer gate`); origin = `git@github.com:suedenglehem/fass.git` |
| working tree | `M docker/compose.yaml` (local opt-ins: docker.sock mount + `FASS_DOCKER_MANAGE=1` uncommented, uncommitted by design) + untracked `.resume`, `PROGRESS.md`, `ffmpeg-rag-prd.md` (source idea), `ffmpeg-assistant-rag-prd.md` (joint PRD, 2026-10-25) |
| test baseline | **783 passed** (738 M10.1 baseline + 45 M10.2 diagnostic tests) — verified 2026-10-25 on local 6.1.1 (75 s); docker 9.0.1 E2E 12/12 generation + 11/11 live runs on titan.mp4 (diagnostic results verified in run logs) |
| local machine | `/dd2/andrei/fass`, host ffmpeg **6.1.1-3ubuntu5**, `.venv` present, `media/titan.mp4` (20 s test clip) |
| i7 (192.168.0.33) | `ssh i7`; repo mirror at the SAME path `/dd2/andrei/fass` (HEAD **5354cc7** per the M10.0 push; not re-probed this session; true git mirror, github remote); host ffmpeg **6.1.1-3ubuntu5**; `.venv` present (pytest 9.1.1); docker deployment: containers **fass** + **fass-worker** (image `fass:latest`, FFmpeg **9.0.1**). **OFFLINE 2026-10-26** (no route to host; dispatcher backend offline) — i7's role (docker 9.0.1 E2E) temporarily taken over by tr4 rebuilding the same Dockerfile |
| tr4 (192.168.0.30) | `ssh tr4`; hostname **b760one**; repo mirror at the SAME path `/dd2/andrei/fass`, HEAD **5354cc7** (synced from i7 2026-10-25; suite **683 passed / 86 s**), host ffmpeg **6.1.1-3ubuntu5**, `.venv` Python 3.12.3; docker runs **vllm-qwen38-27b-dual-max** + **facecat-db** (no fass containers); 3 GPUs (3090, 3080 Ti, 3090); **ONLINE 2026-10-26** — synced 5354cc7 → **4ff3393** (M10.2) via `git fetch origin && git reset --hard origin/main`; docs re-copied from i7; suite **783 passed / 63 s** verified on tr4 2026-10-26; docker/ untouched (canonical tracked compose); handoff details in §3 "tr4 handoff details" |

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

**Decisions pending:**
1. **channelsplit** — defer multi-output export (current plan: in-graph only),
   or implement N-file output (bigger output-model change)?
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
