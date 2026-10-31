# Ruleset vs direct LLM call — analysis note (2026-10-31)

Written from: the 100-test MCP campaign (`tests/prompts/RECAP.md`, per-case
`report.txt`) and the two-LLM qualification (`tests/prompts/LLM-QUAL.md`,
per-case `llm.txt`). Both records are on disk under `tests/prompts/`
(uncommitted by user instruction).

## Question 1 — why did cases fail when the direct second try is fass's default behavior?

**The second try was on, and it fired.** The direct LLM second try is default
at three call sites — `pipeline.py:572` (first pass produced no valid plan),
`pipeline.py:594` (first pass said "unsupported"), `pipeline.py:656` (plan
rejected by the planner). In the campaign it **fired in 58 of 59** non-ready
cases. The one exception is case 60 (`needs_clarification`) — by design fass
stops and asks the user; a clarification is a conversation, not a failure, so
no retry.

**The model never failed to answer.** In all 58 cases the LLM produced a
well-formed, extractable `ffmpeg …` command. Zero extraction failures.

**Every one died at fass's own gate.** All 58 ended with
`second LLM try failed: the command failed validation: …` from `validate_argv`.
Rejection breakdown across the 59 report files:

| rejected because | cases |
|---|---|
| valid ffmpeg filter not in fass's filter registry (`pan` ×13, `asetpts` ×6, blend/geq/select/alphamerge/afftdn/tonemap/yadif-expr/asetrate-expr, bad stream spec ×3) | ~34 |
| standard CLI flag not in the builder's vocabulary (`-r` ×4, `-y` ×3, `-stream_loop` ×3, `-loop` ×2, `-metadata:s:*` ×3, `-fflags`, `-color_primaries`, `-channel_layout`, `-mpegts_pmt_start_pid`) | ~18 |
| positional `-map` specifiers (`0`, `0:0`, `1:0`) | 5 |
| codec/format/value not in its tables (prores, rawvideo, `-profile:v 4444`, `-b:v` shape) | ~4 |

The gate's own error strings say it: *"unknown flag '-r' (not in the builder's
vocabulary)"*. The gate was built to accept what fass's **planner** can emit —
a schema-shaped subset of ffmpeg — because its job is to validate expert
hand-edits of planner-generated commands. So the second try's effective
solution space was **model knowledge ∩ gate vocabulary**, and the gate was the
binding constraint. The qualification bypassed the gate (plain ffmpeg) and
**48/59 ran clean** (rc=0), 46/59 with the expected output present.

Secondary factors, smaller but real:

- **one attempt only** — the campaign showed run-to-run variance (case 10's
  LLM A: rc=234 in one run, rc=0 in the next);
- **one small model only** — no escalation; the better model (deepseek-flash[1m])
  converted +11 extra cases over the best single model (50/37/35 vs 48/34/34);
- **no hints** — both LLMs kept proposing `-c copy` through `-filter_complex`
  (rc=234: "Filtering and streamcopy cannot be used together") because nothing
  in the query warns against it.

Bonus finding: fass's "ready" means *passed the static gate*, not *executed
and verified*. The campaign only generated commands, never ran them — some of
the 41 "ready" may be commands that would fail at runtime. The qualification
(which actually ran the commands) exposed this trust gap.

## Question 2 — why keep the ruleset at all when the direct call seems to win?

Because the two are not the same job. Honest score from the data:

1. **The ruleset's real job is not command generation — it's the semantics
   layer.** What a bare command string lacks and fass has: applied **defaults**
   (recorded, visible), **clarification questions** (case 60 — fass asked
   instead of guessing), **RAG evidence** (names the closest supported
   component), **stream-host rewrite** (M6), and an **audit trail** of what the
   plan decided and why. 1728 tests pin that behavior down. A model reply has
   none of that structure.
2. **Determinism.** Same instruction → same command, every time, offline, in
   microseconds, with zero API dependency. That is what "validated" is supposed
   to mean for the *common* case. The qualification's variance (rc=234 → rc=0
   for the identical case) is exactly why the common path should not ride on a
   model's mood. The LLM's 81% is *best-of-one-shot on a hard subset* — it
   covers the long tail, it does not beat the ruleset "every time".
3. **The LLM provably can't do the hardest part.** The hard-11 (TS PID
   targeting, `-stream_loop`/`-loop`, timestamp rebasing — cases 4, 11, 15,
   18, 25, 36, 45, 48, 65, 79, 95) failed **both** models with a gate-free raw
   prompt, including the most capable model available. Those tasks need *media
   facts* (PID tables, stream layouts, timestamp origins) — exactly what the
   ruleset already computes via ffprobe and RAG. A direct call *fed those facts*
   is a stronger prompt than the qualification's bare prompt; the bare prompt
   is a lower bound.
4. **What the ruleset is *not* good at, proven by the data:** when the task is
   "produce the command from scratch", the structured EditPlan schema is a
   *worse* interface for the model than raw ffmpeg. 38 unsupported + 20 error =
   58/100 where the answer could not be expressed inside the schema, yet 48 of
   those 58 the model *could* express fine as a raw command. There, the schema
   — not the knowledge — was the failure mode.

## Conclusion — layered, not either/or

- **Rules/structured path** = deterministic core + semantics (defaults,
  clarifications, evidence, validation) — keep it.
- **Direct LLM call** = the long tail — already wired as the second try
  (validates the direction); currently **muzzled by the gate**.
- **The real fix is the seam:** the second try needs a wider acceptance
  surface (whitelist of valid-ffmpeg flags/filters/map forms, or a "trusted
  direct" tier) + two-model escalation (strata xxs → deepseek-flash[1m] on
  failure) + the copy-vs-filtergraph hint.
- **Trust comes from execution, not the schema:** fass has the actual input
  files, so a "ready (validated by running on the input)" tier — exactly what
  the qualification harness did — is a *stronger* guarantee than the static
  gate and on-brand for a "validated FFmpeg" product.

The hard-11 are where the ruleset work earns its keep — a clean, known list:
TS PID targeting (11, 36), `-stream_loop`/`-loop` (25, 45, 65, 79),
asetpts/timestamp rebase (4, 15), plus 18, 36, 95.

## Open decisions (user's call, not yet made)

1. Widen `validate_argv` for the second-try path **vs** add a separate
   "validated-by-execution" tier.
2. Wire two-model escalation now **vs** do the ruleset pass on the hard-11
   first.
