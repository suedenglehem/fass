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
| `POST /v1/runs` | `{arguments}` → start a generated command in a process group (only when execution is enabled — see Security model); `409` otherwise |
| `GET /v1/runs` · `GET /v1/runs/{id}` | run list / one run: status, exit code, log tail, output file (playable from `/output`) |
| `POST /v1/runs/{id}/stop` | SIGTERM the run's exact process group (SIGKILL after 5 s) |
| `GET` / `PUT /v1/config` | deployment config (volumes, streaming ports, GPU, LLM) as a raw `FassConfig`; `PUT` persists and the next request uses it — no restart |
| `POST /v1/config/llm-test` | one-word probe through the real LLM request path |
| `POST /v1/config/apply` | forward to the `fass-worker` sidecar's `/swap` — recreates the fass container from the saved config |
| `GET /v1/config/apply-result` | the sidecar's `last-apply.json` (applied / failed + what changed) |

Contract notes: every **domain** outcome (`ready` / `needs_clarification` /
`unsupported` / `error`) is a structured **200**; **422** is reserved for
malformed requests (missing `main` asset, non-POSIX `target_shell` — other
shells are deferred, never rendered best-effort — unknown fields, asset
URLs with a non-allowlisted scheme). The service never executes generated
commands by default (PRD §10); the docker image is the one place that opts
in, and only validated argv runs there. There are no media upload endpoints:
assets are local paths or allowlisted stream URLs the client binds.

## What the MVP understands

- **Trim** — `cut from 00:10 to 00:30`, `trim the first 20 seconds`,
  `keep the last 10 seconds` (needs duration or probes it). Frame-accurate:
  always re-encodes, `-ss` as input option + `-t` output duration.
- **Resize** — `resize to 720p` (aspect-preserving, `scale=-2:H`), width or
  height, `exact` dims, letterbox when both dims are given with preserve.
  Odd dimensions snap to even (yuv420p) with a visible note.
- **Crop** — `crop to 800x600`: exact center crop (`crop=w:h`, FFmpeg's
  crop default); dimensions snap to even, and a crop larger than the probed
  source is rejected.
- **Container conversion** — MP4 / MOV / MKV / WebM / MPEG-TS (`.ts`/`.mts`/
  `.m2ts`), with a codec matrix (e.g. H.264 in WebM is rejected as
  *unsupported*, not guessed).
- **Video encode** — H.264 (`libx264 crf 23`), H.265 (`libhevc crf 28`),
  VP9 (`libvpx-vp9 crf 32 -b:v 0`); audio AAC 160k or Opus 128k for WebM.
- **Pixel format** — `-pix_fmt` `yuv420p` (8-bit, the codec default),
  `yuv420p10le` (10-bit), `yuv444p` (full chroma — VP9 only).
- **H.264 profile & preset** — `-profile:v` `baseline` / `main` / `high`
  and `-preset` `ultrafast`…`veryslow` (H.264 only; other codecs keep
  their registry presets).
- **Target bitrate (CBR-style)** — `cbr 10 Mbps`, `5 mbps`, `2000 kbps` set a
  target video bitrate: `-b:v`/`-maxrate` at the target (+ `-bufsize` 2× for
  H.264/H.265), overriding the quality preset and forcing a re-encode.
  A *file-size* target (`exactly 5 MB`) is still `needs_clarification` —
  bit rates are honored, exact byte sizes are not.
- **Speed** — `2x faster`, `half speed`: playback factor 0.25–8, video
  timestamps (`setpts=PTS/factor`) and audio tempo (`atempo` chain) move
  together; a trim's source duration is divided by the factor.
- **Rotate** — quarter turns: 90° / 180° / 270°, clockwise (default) or
  counterclockwise (`transpose`; 180° is `hflip+vflip`); 90°/270° swap the
  output dimensions.
- **Audio resample** — `resample to 44.1 kHz`, `downmix to mono`: `-ar`
  sample rate and/or `-ac` channels (1 / 2 / 6).
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
- **Subtitles** — bind an srt / ass / ssa / vtt file (CLI `--asset subs=subs.srt`,
  web UI "+ add input"): **burn** (the default) hard-codes the text into the
  video via libass (`subtitles=` for srt/vtt, `ass=` for ass/ssa) and forces
  a re-encode — the filter opens the file itself, so it needs no extra
  input; **soft** (say "soft" or "selectable") muxes a selectable track
  (`mov_text` for MP4/MOV, `srt` for MKV) and stays compatible with
  stream-copy (`-c:s` after `-c copy`). WebM / MPEG-TS have no soft codec —
  burn there instead. At most one subtitle file per plan; colons in the
  filename are rejected (FFmpeg parses them as stream protocols).
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
- **Input sources** — an asset whose path is `'<format>:<spec>'` is a
  capture source, not a file: `lavfi:` (synthetic, headless-testable),
  `v4l2:` (camera), `alsa:`, `dshow:`, `avfoundation:`, `x11grab:` — the
  builder emits `-f <format>` plus `;key=value` option tokens. Output
  devices (`alsa`, `pulse`) carry audio only; a video plan cannot target
  one.
- **Stream-copy remux + bitstream filters** — when metadata proves the
  input streams already match the target codecs, the planner remuxes
  (`-c copy`) instead of re-encoding. Crossing mp4/mov → mpegts restamps
  H.264/HEVC with the video bitstream filters `h264_mp4toannexb` /
  `hevc_mp4toannexb`; remuxing AAC out of mpegts into mp4/mov strips the
  ADTS header with `aac_adtstoasc` (added only when audio is kept — the
  codec is known from the probe, so copy mode always has it).
- **Audio keep/remove** and **compositions** of any of the above, in the
  order the user stated them.

Ambiguity is a first-class result: `needs_clarification` with specific
questions (`make it smaller`, `make it exactly 5 MB`, `re-encode lossless`,
`keep the last 10 seconds` without a known duration). Out-of-scope requests
(`extract to mp3`, `extract the subtitles`, `concatenate two videos`) return
`unsupported` with the reason and the support summary — **never a silent
partial plan**.

## FFmpeg component coverage

FFmpeg's core components ([ffmpeg.org/documentation](https://ffmpeg.org/documentation.html))
and where fass covers each. `fass/registry/registry.json` is the source of
truth; availability on the installed build is checked by `fass env` /
`--preflight`.

| FFmpeg component | fass coverage | Registry key / module |
|---|---|---|
| Video scaling & pixel format | `scale` / `pad` filters (resize, letterbox), `-pix_fmt` (8-bit / 10-bit / 4:4:4) | `pixel_formats`; `planner/` scale math |
| Audio resampler | `-ar` sample rate, `-ac` channels (1 / 2 / 6) | `audio_resample`; `AudioResampleOp` |
| Encoders & decoders | Registry-driven codec selection (H.264 / H.265 / VP9, AAC / Opus) validated by the codec/container matrix | `video_codecs`, `audio_codecs`, `matrix` |
| Bitstream filters | Auto: `h264_mp4toannexb` / `hevc_mp4toannexb` for stream-copies mp4/mov → mpegts (Annex-B restamp), `aac_adtstoasc` for mpegts → mp4/mov (ADTS strip). User-named: 12 registry-validated bsfs (`trace_headers`, `showinfo` 7.1+, `setts`, `prores_metadata`, `mpeg2_metadata`, `hevc_mp4toannexb`, `hevc_metadata`, `h264_metadata`, `filter_units`, `av1_metadata`, `aac_adtstoasc`) plus the `trim` pseudo-entry (stream-copy cut) — any of them in any stream-copy remux, chained after the auto ones, with per-build presence in `fass env` | `bitstream_filters`; `planner/` copy plan; `execute.validate_argv` |
| Muxers & demuxers | Container matrix (mp4 / mov / mkv / webm / mpegts / flv; rtsp / srt stream-only) + explicit `-f` for stream destinations | `containers`, `matrix`, `streaming.output` |
| Protocols | Allowlisted URL schemes in and out (http(s), rtmp(s), rtsp(s), srt, udp, rtp, tcp, tls) | `streaming.input_schemes`, `streaming.output` |
| Input/output devices | Input sources `lavfi` / `v4l2` / `alsa` / `dshow` / `avfoundation` / `x11grab`; audio-only output devices (`alsa`, `pulse`) | `sources` |
| Filters | `crop`, `scale`, `pad`, `transpose`, `hue`, `setpts` + `atempo`, `overlay`, `format`, `subtitles` / `ass` (burn-in) | `operations`, `subtitles`; `builder/` filter graphs |

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
- Execution is **off unless `FASS_ALLOW_EXECUTE` is set** (the docker
  deployment image sets it — an explicit per-deployment choice). Even there,
  a run request must be an argv array (`shell=False`), and every token is
  validated against the builder's own flag vocabulary — flags from the
  registry only, codec/format/pix-fmt/preset values from the registry,
  pattern-checked numbers and filter graphs — before any process spawns.
  Runs are process groups; Stop kills the exact group. Records + logs live
  in the run directory and a run left `running` by a crash is marked
  `interrupted` on next read.

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
  api.py             FastAPI layer (PRD §9 contract) + runs/config endpoints
  ui.py              the web UI (form, runs tab, Config tab)
  config.py          FassConfig (volumes/streaming/GPU/LLM) load/save
  dockerctl.py       Docker Engine control + fass-worker sidecar app
  execute.py         argv-validated run records (the FASS_ALLOW_EXECUTE path)
  cli.py             CLI + subcommands (probe, env, serve, worker, feedback)
docker/
  Dockerfile, entrypoint.sh, compose.yaml, README.md
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
- [x] crop / speed / rotate / audio resample — `CropOp`, `SpeedOp`,
  `RotateOp`, `AudioResampleOp` (registry `crop`, `speed`, `operations`)
- [x] pixel format, H.264 profile & preset — `pixel_format` /
  `video_profile` / `video_preset` output options (registry
  `pixel_formats`, `video_profiles`, `video_presets`)
- [x] input sources + audio-only output devices — `lavfi:` / `v4l2:` /
  `alsa:` / `dshow:` / `avfoundation:` / `x11grab:` asset specs with
  `-f` + option tokens (registry `sources`)
- [x] remux bitstream filters, both directions — mp4/mov → mpegts
  Annex-B restamp + mpegts → mp4/mov ADTS strip (registry
  `bitstream_filters`)
- [x] user-named bitstream filters — 12 validated bsfs (metadata writers,
  `setts`, `filter_units`, diagnostics) + `trim` copy-cut pseudo-entry in
  stream-copy plans; codec/option-validated in NLU, schema, planner and the
  argv gate; per-build presence (`showinfo` 7.1+) probed in `fass env`
  (registry `bitstream_filters`)
- [x] subtitles (burn + soft track) — `SubtitleOp`, libass `subtitles=` /
  `ass=` burn-in, `-c:s mov_text` / `srt` soft mux with stream-copy support
  (registry `subtitles`)
- [x] docker deployment — FFmpeg 9.0 + nvenc image, Config tab (volumes,
  stream ports, GPU, LLM), in-container execution (argv-validated,
  `FASS_ALLOW_EXECUTE`-gated) with run records, and the `fass-worker`
  sidecar that recreates the container on Apply
- [ ] deferred (PRD §5): concatenation, transitions, audio
  mixing, HDR, batch processing

Spec: `ffmpeg-assistant-prd.md`
