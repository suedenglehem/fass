# HANDOFF — fass (natural language → validated FFmpeg commands)

## Goal
Implement plan0.md milestone 1: CLI-first pipeline (instruction → NLU → strict
EditPlan → planner → builder argv → POSIX render), 5 MVP ops (trim, resize,
grayscale, encode/convert, one watermark), 70+ deterministic tests, then a
50-request benchmark, real media-fixture outcome tests, README. No web UI yet.
Spec: ffmpeg-assistant-prd.md; guide: plan0.md.

## Current state (2026-10-23)
- Pipeline, schema, registry, planner, builder, rules+LLM adapters, CLI,
  probe, feedback: COMPLETE and smoke-verified.
- Tests: 70 passed (schema, rules, planner/builder, render-adversarial, cli).
- Delegated via dispatcher (project `fass`, team mode, worker `i7`):
  - Job A: tests/benchmark/requests.json (50 requests) + tests/test_benchmark.py
  - Job B: tests/test_media.py + conftest lavfi fixtures (depends on A)
- Next: integrate A and B into main when jobs finish, run full suite,
  write README.md, save mycelium project memory.

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

## Changed files (this milestone)
fass/{__init__,__main__,cli,pipeline,explain,feedback}.py,
fass/schema/{plan,registry?}.py (schema/), fass/registry/, fass/probe/,
fass/planner/, fass/builder/, fass/nlu/{rules,llm}.py,
tests/{conftest,test_schema,test_rules,test_planner_builder,
test_render_adversarial,test_cli}.py, pyproject.toml, .gitignore, HANDOFF.md

## Test results
`./.venv/bin/python -m pytest -q` → 70 passed (before delegation).
No failures. venv has pydantic 2.14.1 + pytest 9.1.1; fass is NOT pip-installed
(cwd import) — run tests from repo root.

## Next steps
1. Poll jobs A/B (`dispatch_job`), review diffs, merge branches to main,
   `dispatch_integrate` each with the merge commit.
2. Full suite after integration; fix any benchmark-exposed rule bugs.
3. README.md (usage, architecture, validation levels, MVP scope).
4. Mycelium memory for the fass project.
5. No commits/pushes beyond integration merges unless asked.

## Environment notes
- Python 3.12.3, `.venv` (PEP 668 blocks system pip) — use `./.venv/bin/python -m pytest`.
- ffmpeg 6.1.1 + ffprobe on PATH (main box).
- Dispatcher: fass project was `single` mode + had zero commits — switched to
  team and committed initial state (local, no push) to enable delegation.
