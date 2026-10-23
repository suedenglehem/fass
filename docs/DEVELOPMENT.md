# fass — developer documentation

Full documentation: API contract, security model, layout, testing, roadmap.
Short version / quick start: [README.md](../README.md).

---

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
report), `fass serve` (web API + UI), `fass feedback --useful/--incorrect
--category …` (consented feedback logging), `fass examples`.

Exit codes: `0` ready · `2` usage error · `3` needs clarification · `4`
unsupported · `5` error.

## Web API

```bash
./.venv/bin/python -m fass serve            # binds 0.0.0.0:8000
# then open <host IP>:8000 in a browser
# or: uvicorn fass.api:app --host 0.0.0.0
```

`GET /` serves a minimal self-contained UI (request form, clarification flow,
command output — PRD §9). The UI's **"+ add input"** rows bind extra audio /
video / image files as assets (ids `in1`, `in2`, …), auto-typed by extension —
the web path to multi-input requests like audio replacement. The same
endpoints the UI uses:

| Endpoint | Purpose |
|---|---|
| `POST /v1/commands` | `{instruction, assets: {id → local path or allowlisted stream URL}, output_filename?, target_shell, previous_plan?, media?, adapter?, preflight?}` → the full structured response (the CLI `--json` contract) |
| `GET /v1/support` | what the MVP understands + registry version |
| `GET /v1/env` | local FFmpeg build capability report (same as `fass env`) |
| `GET /healthz` | liveness |

Contract notes: every **domain** outcome (`ready` / `needs_clarification` /
`unsupported` / `error`) is a structured **200**; **422** is reserved for
malformed requests (missing `main` asset, non-POSIX `target_shell` — other
shells are deferred, never rendered best-effort — unknown fields, asset
URLs with a non-allowlisted scheme). The service never executes generated
commands (PRD §10) and has no media upload endpoints: assets are local paths
or allowlisted stream URLs the client binds.

## What the MVP understands

- **Trim** — `cut from 00:10 to 00:30`, `trim the first 20 seconds`,
  `keep the last 10 seconds` (needs duration or probes it). Frame-accurate:
  always re-encodes, `-ss` as input option + `-t` output duration.
- **Resize** — `resize to 720p` (aspect-preserving, `scale=-2:H`), width or
  height, `exact` dims, letterbox when both dims are given with preserve.
  Odd dimensions snap to even (yuv420p) with a visible note.
- **Container conversion** — MP4 / MOV / MKV / WebM / MPEG-TS (`.ts`/`.mts`/
  `.m2ts`), with a codec matrix (e.g. H.264 in WebM is rejected as
  *unsupported*, not guessed).
- **Video encode** — H.264 (`libx264 crf 23`), H.265 (`libhevc crf 28`),
  VP9 (`libvpx-vp9 crf 32 -b:v 0`); audio AAC 160k or Opus 128k for WebM.
- **Target bitrate (CBR-style)** — `cbr 10 Mbps`, `5 mbps`, `2000 kbps` set a
  target video bitrate: `-b:v`/`-maxrate` at the target (+ `-bufsize` 2× for
  H.264/H.265), overriding the quality preset and forcing a re-encode.
  A *file-size* target (`exactly 5 MB`) is still `needs_clarification` —
  bit rates are honored, exact byte sizes are not.
- **Grayscale** — `hue=s=0`, audio kept.
- **One static watermark** — preset positions (corners/center), margin,
  percent scale; labeled `filter_complex` + `overlay`, `eof_action=repeat` so a
  single-frame image never bounds the video.
- **Audio replacement** — `replace the audio with song.mp3`, `use this file
  as the soundtrack`: binds a bound audio file (CLI `--asset id=path`, web UI
  "+ add input"), maps its first audio stream (`-map N:a:0` + `-shortest`),
  encoded to the container's audio codec — never stream-copied, so it forces
  a re-encode. No audio file bound → `needs_clarification` ("which file should
  provide the new audio?").
- **Stream I/O** — an *input* may be a stream URL (rtmp/rtsp/srt/http/udp/
  …, allowlisted in the registry; unprobeable, so a re-encode is assumed and
  noted); raw `udp`/`rtp`/`tcp` inputs get an explicit `-f mpegts`. An
  *output* may be a stream destination — `stream to rtmp://host:1935/live`,
  or passed via `--output`/`output_filename`: the destination's scheme fixes
  the container (rtmp→flv, rtsp→rtsp, srt→srt, udp/rtp/tcp→mpegts) and the
  builder prepends the matching `-f` muxer. Stream outputs are disclosed in
  the response ("data is sent to a remote endpoint; nothing is saved
  locally"); a container named in the instruction that conflicts with the
  destination's is a structured error, never a silent pick.
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
  `Asset` validator rejects non-allowlisted schemes (`file://`, `ftp://`, …)
  and control characters. Only the registry's streaming schemes are
  accepted as URLs, and a URL is passed to FFmpeg as a single argv token —
  never through a shell.
- Output never overwrites the input: defaulted names de-collide (`_out`
  suffix), explicit `-o input.mp4` is a hard error, and `-y` is never
  emitted. Probing is local `ffprobe` only — media leaves the machine only
  when the *user's own request* names a stream output destination, and that
  destination is then disclosed in the response.
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
  api.py             FastAPI layer + minimal UI (PRD §9 contract)
  cli.py             CLI + subcommands
tests/
  test_schema.py, test_rules.py, test_planner_builder.py,
  test_render_adversarial.py (hostile filenames + injection payloads),
  test_cli.py (subprocess, exit codes, JSON mode),
  test_api.py (FastAPI TestClient: PRD contract + security invariants)
```

## Testing

```bash
./.venv/bin/python -m pytest -q
```

Tests assert on **plans** (what) and **argv shape** (how), plus adversarial
round-trips of hostile filenames and instruction-injection payloads.

## Roadmap

- [x] 50-request benchmark suite — 89 requests in `tests/benchmark/`
- [x] real media-fixture outcome tests — `tests/test_media.py` (synthetic
  lavfi) + `tests/test_titan.py` (committed real file), run the command,
  probe the result
- [x] FastAPI web layer over the same `generate()` — `fass/api.py`,
  `POST /v1/commands` + `/v1/support` + `/v1/env`, minimal UI at `GET /`,
  `fass serve` subcommand
- [x] multi local typed inputs + audio replacement — `AudioReplaceOp`,
  `--asset id=path` / UI "+ add input", `-map N:a:0` + `-shortest`
- [x] stream / remote sources and stream outputs — allowlisted stream
  URLs as inputs and destinations (scheme → container), `-f` muxer
  injection, destination disclosure; HTTP/HLS *output* deferred

Spec: `ffmpeg-assistant-prd.md` · Implementation guide: `plan0.md`
