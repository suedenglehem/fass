# fass — natural language → validated FFmpeg commands

`fass` turns a plain-English editing request into a **validated** FFmpeg
command — and, by default, stops there: it never executes. A parser
(deterministic rules by default, optional LLM) produces a strict, schema-bound
*edit plan*;
a deterministic planner and builder turn the plan into an argv array. The
model never produces shell — argv is the source of truth, and a POSIX
rendering is derived from it (`shlex.join`), never parsed back.

```
instruction ──> NLU (rules | llm) ──> EditPlan (strict schema)
                                      │
                    planner (remux vs re-encode, presets, warnings)
                                      │
                            builder ──> argv: list[str]
                                      │
                render_posix (shlex.join) — display/copy only
```

## Quick start

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[api]"     # add ,dev for tests

./.venv/bin/python -m fass "cut from 00:10 to 00:30 and resize to 720p" clip.mp4
./.venv/bin/python -m fass "replace the audio with song.mp3" clip.mp4 --asset song=song.mp3
./.venv/bin/python -m fass serve                  # web UI + JSON API on :8000
```

Every request returns one of four structured outcomes — `ready`,
`needs_clarification`, `unsupported`, `error`. Ambiguity is a first-class
answer, never a silent partial plan.

## What it understands

**Shape**: N typed inputs — video, audio, image, local capture sources,
plus one optional watermark image (the web UI has a "+ add input" row for
each) → one output that is a file **or** a stream destination. Multiple
inputs is what makes "replace the audio with song.mp3" work.

- **Trim / cut** (frame-accurate), **resize** (aspect-preserving, exact
  dims, letterbox), **grayscale**, **crop** (center), **rotate** (quarter
  turns), **speed** (0.25–8×, video and audio together)
- **Filter effects** — brightness / contrast / saturation (`eq`), fade in /
  out (video and audio), blur & sharpen, **burned-in text or a live
  timecode** (`drawtext`), volume / loudness normalization, low- / high-pass,
  bass & treble, echo, limiter, delay, silence padding
- **Transform filters** — audio dynamics (`acompressor`, `compand`,
  `dynaudnorm`, `adrc`, `adynamicequalizer`, `adeclick`, `silenceremove`,
  `hdcd`), video cleanup (`yadif` deinterlace, `decimate` duplicate-frame
  drop, `chromakey`, `drawbox`, arbitrary-angle `rotate`), frame-rate
  (`fps`/`framerate`), color space (`colorspace`/`colormatrix`), aspect
  ratio (`setdar`/`setsar`) — every chain validated against the filter
  registry, with semantic-trap warnings (asetrate changes pitch **and**
  speed; decimate drops frames; colorspace is metadata-only, …)
- **Diagnostic analysis** — "detect silences", "how loud is this", "detect
  black frames", "where are the black bars", "detect interlacing", "detect
  frozen frames", "audio statistics", "frame info", "bounding box"
  (`silencedetect`, `volumedetect`, `astats`, `ashowinfo`, `blackdetect`,
  `cropdetect`, `freezedetect`, `idet`, `showinfo`, `bbox`) — an
  all-diagnostic plan decodes through the filters and discards the stream
  (`-f null -`, no output file), raising the log level to info so the
  results land in the run log
- **Container conversion** — a broad, codec-matrix-validated set: video in
  MP4 / MOV / MKV / WebM / MPEG-TS / MXF / AVI / FLV / GIF / WebP with H.264 /
  H.265 / VP9 / MPEG-2 / ProRes / MJPEG, audio in MP3 / AAC / FLAC / Opus /
  ALAC / AC-3 / E-AC-3 / PCM, HLS, and more (see
  [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)). When the probes show the input
  streams already match, it **remuxes** (`-c copy`) instead of re-encoding —
  with bitstream filters in **both directions**: Annex-B restamp
  (`h264_mp4toannexb` / `hevc_mp4toannexb`) for mp4/mov → MPEG-TS, ADTS strip
  (`aac_adtstoasc`) for MPEG-TS → mp4/mov. Remuxes also accept **named
  bitstream filters** with validated options (`h264_metadata level=4.1`,
  `setts`, `filter_units`, `trace_headers`, `showinfo` (FFmpeg 7.1+), …) —
  any of them on top of the automatic ones; "trim" in a copy context is a
  stream-copy cut (`-ss`/`-t` with `-c copy`)
- **Encoding options** — target bitrate (`cbr 10 Mbps`), pixel format
  (8-bit / 10-bit / 4:4:4), H.264 profile & preset, audio resampling (sample
  rate / channels), and **per-encoder options**: NVENC / QSV rate control,
  ProRes profile, x264 / x265 / VVC tuning, encoder-specific flags — plus
  **GPU encoding** (nvenc) when a deployment exposes a GPU
- **Muxer / format options** — `+faststart`, HLS segment settings
  (`hls_time`, segment type), MPEG-TS flags, MP3 ID3 tags, …, validated
  against the plan's actual output container
- **One static watermark** (preset positions, margin, percent scale),
  **audio replacement** from a second bound input, **audio keep / remove** —
  and compositions of any of the above, in the order stated
- **Subtitles** — bind an srt / ass / ssa / vtt file; **burned** into the
  video by default (libass, forces a re-encode) or muxed as a **selectable
  soft track** (MP4 / MOV / MKV — compatible with stream-copy)
- **Capture sources** — `lavfi:` synthetic sources, `v4l2:` cameras, and
  alsa / dshow / avfoundation / x11grab bind as typed inputs
- **Stream I/O** — inputs and outputs may be stream URLs (rtmp, rtsp, srt,
  http(s), udp, tcp — allowlisted); a stream destination's scheme fixes the
  container (rtmp→flv, rtsp→rtsp, srt→srt, udp/tcp→mpegts) and is disclosed
  in the response
- **Chained processings** — name the output `/input/<file>` (e.g.
  `/input/step1.mp4`) and the result is written into the input folder
  instead of the output folder, so the next command picks it up by bare
  name: `scale to 480p` → `/input/step1.mp4`, then `scale to 240p` on
  `step1.mp4`
- **Revisions** — pass the previous plan back (`--previous plan.json` on the
  CLI, `previous_plan` in the API) and only what the new instruction changes
  changes

CLI flags, the web API (`POST /v1/commands`, plus `POST /v1/runs` and the
media endpoints for the execution layer) and the web UI (Command / Runs /
Config tabs), the security model, validation levels, tests, and the roadmap
live in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Docker deployment

fass packaged with FFmpeg 9.0 + NVIDIA hardware acceleration, in a single
container (`jrottenberg/ffmpeg:9.0-nvidia2404` base):

```bash
docker compose -f docker/compose.yaml up -d --build   # → http://<host>:8000
```

The web UI gets a **Config tab**: host-folder mappings for `/input`,
`/output`, `/log`, stream port mappings (in: `udp://host:port` binds to
`0.0.0.0` inside; out: `srt/rtmp://host:port` rewrites to the docker
gateway), GPU on/off (H.264/H.265 → nvenc), and the LLM provider
(Anthropic, or any OpenAI-compatible endpoint). Execution is enabled in the
image: the UI's **Runs** tab starts the generated command inside the
container, follows its log live, stops it, and links the result file; an
output named `/input/<file>` lands in the input volume so a later run can
pick it up by bare name (chained processings). The Config tab's
**Apply** button recreates the container from the saved config via a
`fass-worker` sidecar (a container cannot recreate itself). Full details:
[docker/README.md](docker/README.md).

## Safety

- argv arrays only — model output is schema-validated, the builder emits
  flags from a fixed vocabulary; no shell, no eval
- **no execution by default** — the service only generates, and a human
  reviews and runs the command. Execution is an opt-in per deployment
  (`FASS_ALLOW_EXECUTE=1`; the docker image sets it). Even there, only argv
  from the builder's own fixed vocabulary can run — every flag, codec,
  format, filter and value is checked against the registry before the
  process starts, and paths resolve into the deployment's input / output
  volumes (see [docker/README.md](docker/README.md#execution))
- run output is served back only when the file lives inside the input or
  output volume — never a path outside them
- asset URLs are limited to an allowlist of streaming schemes (`file://`,
  `ftp://`, … are rejected)
- output never overwrites the input; `-y` is never emitted

Spec: `ffmpeg-assistant-prd.md`
