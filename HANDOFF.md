# HANDOFF — fass (natural language → validated FFmpeg commands)

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

## Current state (2026-10-27 04:14, **M12 COMPLETE — the PRD-deferred scope is all on main: multiple watermarks (ea9919e), batch mode (9967441), HDR→SDR tone-mapping (8c2e11a) + README/DEVELOPMENT docs updated** — main suite **1278 passed / 1 skipped** (tr4 6.1.1, 146 s). All mandated work (M-series filters + the RAG PRD, M11.0–M11.4) is now DONE; what remains are user-decision items below. The 2026-10-27 01:15 state is demoted to the next "Previous state" section.)
**What closed out this segment (all on main, in order):**
- **M12.WM (ea9919e) — multiple watermarks.** N image overlays chain in plan order (inputs 1..N, per-image `overlay` in the labeled `filter_complex`). NLU `_detect_watermarks` (rules.py): every image the request refers to becomes one `watermark` op — an explicit mention wins, with none named every bound image asset is used; each position phrase in text order is assigned to the next watermark, the rest default to bottom-right with a note. plan.py single-use-op validator renamed `_at_most_one_single_use_op` with watermark exempt. E2E on titan.mp4 with two bound images; suite 1233/1.
- **M12.BATCH (9967441) — job j_65e7374aef SUCCEEDED (tr4-secondary), integrated, disposition accepted.** `--batch FILE|DIR …` (a directory contributes its top-level video files, sorted; or an explicit list) + `--batch-out DIR`; the pipeline runs once per file sequentially; a rejected/failing file is reported and skipped, the loop continues, exit 0 only when every file succeeded. Rejects positional input / `-o` / `--previous` / `--metadata` (each names ONE file); shared `--watermark` / `--asset` apply to every file; an instruction placed after the file list parses (trailing-token disambiguation). The single-file CLI path is byte-unchanged. Suite 1254/1.
- **M12.HDR (8c2e11a) — job j_ba4899bb09: the work is good, the job record is NOT.** `ToneMapOp` (no options) in the Operation union; NLU `_TONEMAP_RX` / `_detect_tonemap` (tone-map words or hdr↔sdr / hdr↔rec-709 pairings within 60 chars), detection gated on probe metadata (PQ `smpte2084` / HLG `arib-std-b67`); planner `_TONEMAP_CHAIN = format=gbrpf32le,tonemap=tonemap=hable:desat=0,format=yuv420p`, default output 8-bit Rec.709, a 10-bit `main10` re-encode keeps the HDR; HDR10+ / Dolby Vision named-unsupported; conflicts: audio-mode ops, subtitles mode, stabilize; registry 2.8.0 entry (tonemap, min_version 4.2); 24 tests (tests/test_tonemap.py). Worker E2E-verified on i7 docker 9.0.1. **Job marked failed/invalid_result on the worker's result JSON (outcome 'success_pending_review' outside the enum; acceptance_criteria entries missing 'criterion'; scope_violations [".venv"]) — `dispatch_integrate` REFUSED (server gate: only succeeded jobs can be integrated, no override).** I reviewed the worktree diff, resolved the one 3-way conflict (audio-mode block: kept M12.WM plural `watermarks` + added `or tonemap is not None`), re-verified E2E on tr4 6.1.1 (synthesized HDR10 main10 10-bit PQ source → 8-bit SDR h264, chain + "BT.2020/PQ → Rec.709" disclosure correct; artifacts /tmp/hdr_src.mp4, /tmp/hdr_out.mp4) and merged manually — **8c2e11a IS the integration record**; the job's worktree stays retained. Suite 1278/1.
- **README job (6bca1a3) — j_0214afe7db SUCCEEDED (i7), integrated, accepted.** Worker's scope (per its objective: "DO NOT document unshipped features"): quality-comparison section (reference = FIRST input, `-i ref -i main`, `[distorted][reference]`, libvmaf 7.0+ / `FASS_VMAF_MODEL[_DIR]`, null-muxer diagnostic output, standalone-only, probe-conditional setpts), stream allowlist gained rtp, spec link → ffmpeg-assistant-rag-prd.md.
- **M12 docs close-out (this commit) — the worker was told NOT to write, so it's mine:** README "What it understands" — shape line (watermark is just an image input), Watermarks bullet (multiple; first `--watermark`, extras `--asset name=image:file`), new HDR→SDR and Batch-mode bullets; docs/DEVELOPMENT.md — CLI table (`--batch` / `--batch-out` rows, `--watermark` clarified), feature bullets (Watermarks M12.WM / HDR M12.HDR / Batch M12.BATCH), out-of-scope example `add a second watermark` → `remove the watermark`, roadmap: M11.1 → [x] (all gates PASS) + M11.2/3/4 [x] + M12.WM/BATCH/HDR [x], the "deferred (PRD §5)" line removed (all three shipped).
**Dispatcher protocol drift (recurrence, worth a profile fix):** THREE jobs now left good work behind a false "failed" status because the worker's result JSON used non-schema vocabulary (c27a0d8: outcome 'success'; j_3d4505da56 attempt 1: crash; j_ba4899bb09: 'success_pending_review' + missing criterion keys). Every one needed manual rescue-merge. Recommend adding the exact result-schema contract (outcome enum, tests LIST shape, acceptance_criteria[].criterion strings) to the cc-worker profile prompt.
**Next (user-decision items only — no work is owed):** (1) 2.7.0 merge questions: histogram 1-letter aliases (register or not), tinterlace docs-only `flags`, blockdetect/blurdetect `planes` phrasing. (2) i7 `fass-worker` container still runs pre-rebuild code — restart only when the user wants. (3) Minor optional: gaps/ note on the auto-adapter 401-no-fallback environmental issue (tr4 has no ANTHROPIC_API_KEY → `--adapter auto` → LLM → 401; use `--adapter rules` for E2E on tr4); dispatcher drift note as above. Standing: advisory evidence only — registry/builder/argv gate stays sovereign; treat evidence_status 'supported' with skepticism.

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
