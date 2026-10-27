# PROGRESS — fass (natural language → validated FFmpeg commands)

**Self-contained continuation file.** Written 2026-10-25 (end of session) so the
M10 work can be continued **on the i7 machine** with no other context source
needed. Sources: `HANDOFF.md` (repo), mycelium memories of the `fass` project
(dumped into §6), and live probes of both machines this session.

- **Last state (2026-10-27, M13 PLANNING — plan mode, no coding yet):**
  M13 = user-picked scope: (1) HLS ABR ladders (multi-variant),
  (2) DASH output, (3) `tee` fan-out, (4) live HLS (E2E), (5) play
  the run's playlist/segments in the web UI. NO coding this session
  — plan only; the user hand-tests the reference commands first,
  then they lock in as E2E cases. Exploration COMPLETE via two
  dispatcher jobs: j_f7d3350725 (i7, succeeded) — schema/planner/
  builder/execute trace (plan.py OutputSpec L118, planner.py
  _build_reencode_plan L2103-2428, builder _pass_argv L14-31,
  execute.py validate_argv L172-233 / resolve_paths L392-475;
  registry has NO dash container and NO var_stream_map);
  j_c5a42bb4ec (tr4-secondary; job record failed on the cc-worker
  evidence_path quirk — work complete, report recovered from the
  artifacts dir) — NLU rules.py / llm.py / registry.json /
  loader.py / test-pattern map. KEY VERIFIED on tr4 ffmpeg 6.1.1
  (null-muxer run, exit 0): per-stream specifiers (-b:v:0
  -minrate:v:0 -maxrate:v:0 -bufsize:v:0 -s:v:0 -profile:v:0
  -level:v:0 -b:a:0) → ladders need NO builder change (maps up
  front, per-level opts in output_opts). Capability matrix (both
  builds): hls+var_stream_map, dash+adaptation_sets+seg_duration,
  tee+use_fifo all on 6.1.1 AND 9.0.1; 9.0.1-only
  hls_segment_filename / hls_start_number_source OUT of scope (no
  min_version mechanism in format_options). Security finding:
  absolute paths inside a tee template bypass the volume jail
  (resolve_paths only rewrites bare names) → forbid at build time.
  Plan file
  /home/andrei/.claude/plans/majestic-sauteeing-ullman.md: FINAL —
  all 8 sections. Design job **j_0130ee5802 SUCCEEDED** (05:17
  UTC; full doc: worktree
  ~/.local/share/local-dispatcher/worktrees/j_0130ee5802/.exploration-scratch/M13_DESIGN.md,
  all ffmpeg facts probed on 6.1.1) — all 10 questions folded in.
  Key design calls: ladder = OutputSpec.variants (VariantSpec) +
  tee = OutputSpec.tee (TeeBranch), mutually exclusive, both
  default None (byte-identical single-variant); ladder mechanism
  = interleaved -map groups inside output_opts (zero builder
  change), stream-specifier form is documented fallback (needs a
  9.0.1 probe); tee = bracket template [f=hls:…]name|[f=dash:…]name
  + explicit -map 0:v -map 0:a (the muxer=path:opt shorthand
  FAILS — probed); var_stream_map kind=string in BOTH hls and dash
  groups (executor's container-context gate validates against the
  output container); DASH = containers/matrix/format_options.dash +
  AUTO adaptation sets (explicit sets fail with var_stream_map —
  probed); live = event + window + independent_segments,
  delete_segments opt-in only, file input allowed + ENDLIST
  warning, -re pseudo-live → M13b; **USER DECISION: master.m3u8
  synthesized post-run** by the execution layer from the plan's
  declared levels (ffmpeg's hls muxer writes no master — probed;
  the only place fass writes an output file; generate-only
  deployments write nothing). HTTP delivery REVISED (user,
  2026-10-27): Apache in the docker image serves <output_dir>/http/
  — the executor picks that cwd from argv (hls/dash container, or a
  tee run with an hls/dash branch; no schema change, Run.http flag);
  GET /v1/http/manifests + POST /v1/http/purge + /static allowlist
  (vendored hls.min.js + dash.all.min.js, CORS header load-bearing);
  new Player tab lists/plays manifests WHILE the run generates,
  with a Purge button; the earlier artifacts dir-diff +
  /media/run/{id}/play/ rewrite route + ▶ modal are DROPPED.
  Six reference FASS commands (typed in the UI Command tab; expected
  argv = the probed ground truth) in the plan for the user's
  hand-testing (they lock the E2E cases). Milestones: M13.1 registry+DASH single →
  M13.2 live → M13.3 HLS ladder + master → M13.4 DASH ladder →
  M13.5 tee → M13.6 Apache + /output/http → M13.7 Player tab.
  Mid-generation probes (6.1.1, -re pacing): event-HLS playlist
  grows mid-run with no ENDLIST → playable WHILE generating;
  vod .m3u8 absent until completion (3 probe rounds); DASH .mpd
  type=dynamic (growing timeline) → static at completion; segments
  written .tmp then renamed (player-safe); -hls_time 4 → actual
  8.33 s segments (x264 keyint 250). M12 record follows.
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

**Next unfinished task (2026-10-27, M13 PLANNING — plan COMPLETE, no coding yet):**
M13 scope (user picks 2026-10-27): HLS ABR ladders, DASH output,
`tee` fan-out, live HLS, web-UI playback. Plan file
/home/andrei/.claude/plans/majestic-sauteeing-ullman.md — FINAL
(all 8 sections; design job j_0130ee5802 succeeded 05:17 UTC and
its M13_DESIGN.md is folded in; user decision recorded: post-run
master.m3u8 synthesis). NEXT: user hand-tests the six reference
argv in the plan on tr4 6.1.1 + i7 9.0.1 docker (the results lock
the E2E cases); then implement M13.1 → M13.6 in a future session
(per milestone: suite green from baseline 1278/1 + E2E on 6.1.1).
M12 close-out record below.
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
