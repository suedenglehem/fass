# fass — natural language → validated FFmpeg commands

fass turns a plain-English editing request into a **validated** FFmpeg command.
An LLM (or a deterministic rules parser) produces a strict, schema-bound *edit
plan*; a deterministic planner and builder turn that plan into an argument
array. **The model never produces shell** — argv arrays are the source of truth
and a POSIX shell rendering (`shlex.join`) is derived from them, never parsed
back.

```
instruction ──> NLU adapter ──> EditPlan (strict pydantic schema)
                (rules | llm)        │
                                     ▼
                              planner ──> execution decisions
                    (remux vs re-encode, presets, defaults, warnings)
                                     │
                                     ▼
                              builder ──> argv: list[str]
                                     │
                                     ▼
                              render_posix (shlex.join) — display/copy only
```

## Quick start

```bash
./.venv/bin/python -m fass "resize to 720p" clip.mp4 --json
./.venv/bin/python -m fass "cut from 00:10 to 00:30 and make it black and white" clip.mp4 -o out.mp4
./.venv/bin/python -m fass "convert to MP4 with H.265" clip.mov
./.venv/bin/python -m fass "add my logo to the bottom-right corner" clip.mp4 --watermark logo.png
```

Output (non-JSON mode) shows the command, its argv, an edit summary, defaults
applied, warnings, and the validation level. `--json` emits the full
`GenerationResponse` for scripts and the future API.

### CLI

| Flag | Meaning |
|---|---|
| `instruction` / `input` (positional) | what to do; the input video |
| `-o, --output` | output filename (must not equal the input) |
| `--watermark IMG` | bind the watermark image (asset id `watermark`) |
| `--asset id=path` | bind any extra asset (the model only ever sees ids) |
| `--adapter auto\|rules\|llm` | `rules` is offline/deterministic (default without `ANTHROPIC_API_KEY`); `llm` uses the Anthropic API with the same schema validation |
| `--previous plan.json` | revision: change only what the new instruction asks |
| `--metadata probe.json` | pre-collected ffprobe JSON (skips local probing) |
| `--preflight` | check the local FFmpeg build for the encoders/filters the plan needs |
| `--execute` | DEV: run the generated argv locally (no `-y` is ever added) |

Subcommands: `fass probe FILE` (local metadata), `fass env` (build capability
report), `fass feedback --useful/--incorrect --category …` (consented feedback
logging), `fass examples`.

Exit codes: `0` ready · `2` usage error · `3` needs clarification · `4`
unsupported · `5` error.

## What the MVP understands

- **Trim** — `cut from 00:10 to 00:30`, `trim the first 20 seconds`,
  `keep the last 10 seconds` (needs duration or probes it). Frame-accurate:
  always re-encodes, `-ss` as input option + `-t` output duration.
- **Resize** — `resize to 720p` (aspect-preserving, `scale=-2:H`), width or
  height, `exact` dims, letterbox when both dims are given with preserve.
  Odd dimensions snap to even (yuv420p) with a visible note.
- **Container conversion** — MP4 / MOV / MKV / WebM, with a codec matrix
  (e.g. H.264 in WebM is rejected as *unsupported*, not guessed).
- **Video encode** — H.264 (`libx264 crf 23`), H.265 (`libhevc crf 28`),
  VP9 (`libvpx-vp9 crf 32 -b:v 0`); audio AAC 160k or Opus 128k for WebM.
- **Grayscale** — `hue=s=0`, audio kept.
- **One static watermark** — preset positions (corners/center), margin,
  percent scale; labeled `filter_complex` + `overlay`, `eof_action=repeat` so a
  single-frame image never bounds the video.
- **Audio keep/remove** and **compositions** of any of the above, in the
  order the user stated them.

Ambiguity is a first-class result: `needs_clarification` with specific
questions (`make it smaller`, `make it exactly 5 MB`, `re-encode lossless`,
`keep the last 10 seconds` without a known duration). Out-of-scope requests
(`2x faster`, `rotate`, `extract to mp3`) return `unsupported` with the
reason and the support summary — **never a silent partial plan**.

## Validation levels

- `static` — schema-valid plan, registry matrix check, builder invariants
  (no `-y`, output ≠ input, quoted argv round-trips through `shlex.split`).
- `preflight` — additionally checked against the **local FFmpeg build**
  (encoders/filters present via `ffprobe`/`ffmpeg -version` probing).

The two levels are reported separately in the response; "statically
validated" never claims "tested against this build".

## Security model

- argv arrays first; POSIX rendering is cosmetic (`shlex.join`).
- Instruction text can never become arguments: the LLM adapter's output is
  validated by the same pydantic envelope as the rules adapter, and the
  builder only ever emits flags from its fixed vocabulary.
- Asset paths are bound *outside* the model (the model sees ids only); the
  `Asset` validator rejects URLs/remote schemes and control characters.
- Output never overwrites the input: defaulted names de-collide (`_out`
  suffix), explicit `-o input.mp4` is a hard error, and `-y` is never
  emitted. No media ever leaves the machine — probing is local `ffprobe` only.
- Feedback stores the instruction text **only** with a separate `--consent`
  flag.

## Layout

```
fass/
  schema/plan.py     strict EditPlan / ops / OutputSpec / Asset (extra=forbid)
  registry/          codec presets + container/codec matrix (versioned JSON)
  nlu/rules.py       deterministic offline adapter (default)
  nlu/llm.py         Anthropic adapter, same schema envelope
  planner/           remux-vs-reencode, scale/overlay math, defaults
  builder/           argv construction + POSIX renderer
  probe/ffprobe.py   local ffprobe/ffmpeg capability probing
  pipeline.py        generate(): the whole flow + GenerationResponse
  cli.py             CLI + subcommands
tests/
  test_schema.py, test_rules.py, test_planner_builder.py,
  test_render_adversarial.py (hostile filenames + injection payloads),
  test_cli.py (subprocess, exit codes, JSON mode)
```

## Testing

```bash
./.venv/bin/python -m pytest -q
```

Tests assert on **plans** (what) and **argv shape** (how), plus adversarial
round-trips of hostile filenames and instruction-injection payloads.

## Roadmap

- [ ] 50-request benchmark suite (in progress via dispatcher jobs)
- [ ] real media-fixture outcome tests (run the command, probe the result)
- [ ] FastAPI web layer over the same `generate()` (plan0: after CLI works)

Spec: `ffmpeg-assistant-prd.md` · Implementation guide: `plan0.md`
