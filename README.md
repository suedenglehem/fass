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

- **Trim / cut** (frame-accurate), **resize** (aspect-preserving, exact dims,
  letterbox), **grayscale**
- **Container conversion** — MP4 / MOV / MKV / WebM / MPEG-TS with H.264 /
  H.265 / VP9, AAC / Opus, codec-matrix validated
- **Target bitrate** (`cbr 10 Mbps`), **one static watermark**, **audio
  replacement** from a second bound input, **audio keep / remove** — and
  compositions of any of the above, in the order stated
- **Stream I/O** — inputs and outputs may be stream URLs (rtmp, rtsp, srt,
  http(s), udp, tcp — allowlisted); a stream destination's scheme fixes the
  container (rtmp→flv, rtsp→rtsp, srt→srt, udp/tcp→mpegts) and is disclosed
  in the response

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
