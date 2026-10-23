# fass — natural language → validated FFmpeg commands

`fass` turns a plain-English editing request into a **validated** FFmpeg
command — and stops there: **it never executes it**. A parser (deterministic
rules by default, optional LLM) produces a strict, schema-bound *edit plan*;
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
- **Container conversion** — MP4 / MOV / MKV / WebM / MPEG-TS with H.264 /
  H.265 / VP9, AAC / Opus, codec-matrix validated. When the probes show the
  input streams already match, it **remuxes** (`-c copy`) instead of
  re-encoding — with bitstream filters in **both directions**: Annex-B
  restamp (`h264_mp4toannexb` / `hevc_mp4toannexb`) for mp4/mov → MPEG-TS,
  ADTS strip (`aac_adtstoasc`) for MPEG-TS → mp4/mov
- **Encoding options** — target bitrate (`cbr 10 Mbps`), pixel format
  (8-bit / 10-bit / 4:4:4 for VP9), H.264 profile & preset, audio
  resampling (sample rate / channels)
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
- **Revisions** — pass the previous plan back (`--previous plan.json` on the
  CLI, `previous_plan` in the API) and only what the new instruction changes
  changes

CLI flags, the web API (`POST /v1/commands` + minimal UI), the security
model, validation levels, tests, and the roadmap live in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Safety

- argv arrays only — model output is schema-validated, the builder emits
  flags from a fixed vocabulary; no shell, no eval
- the service never executes the commands it generates — a human reviews and
  runs them
- asset URLs are limited to an allowlist of streaming schemes (`file://`,
  `ftp://`, … are rejected)
- output never overwrites the input; `-y` is never emitted

Spec: `ffmpeg-assistant-prd.md` · Implementation guide: `plan0.md`
