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
./.venv/bin/python -m pip install -e ".[api]"     # add ,dev for tests,
                                                  # ,storage for remote
                                                  # volume mapping (FTP/
                                                  # SFTP/S3), ,mcp for
                                                  # `fass mcp`

./.venv/bin/python -m fass "cut from 00:10 to 00:30 and resize to 720p" clip.mp4
./.venv/bin/python -m fass "replace the audio with song.mp3" clip.mp4 --asset song=song.mp3
./.venv/bin/python -m fass serve                  # web UI + JSON API on :8000
```

Every request returns one of four structured outcomes — `ready`,
`needs_clarification`, `unsupported`, `error`. Ambiguity is a first-class
answer, never a silent partial plan.

## Web UI

`fass serve` (or the docker image) puts a single-page web UI at the
service's root — seven tabs, plus a footer (ffmpeg build, fass version,
[GitHub issues](https://github.com/suedenglehem/fass/issues), and an
"if you're still clueless" video). The page is one self-contained HTML
file with no CDN dependencies (shaka-player is bundled locally for the
Player tab), so it works on a networked box as well as an isolated one.

### FFmpeg tab — generate

The workhorse: a natural-language request in, a validated command out.

- **Instruction** — the free-text request. When it reads like a quality
  comparison ("compare … with psnr/ssim/vmaf", "how much quality loss
  against …"), the other fields switch meaning: **Input** becomes the
  *reference*, **Output** the *distorted* clip — nothing is written, the
  score lands in the run log ([Quality comparison](#quality-comparison-vmaf--psnr--ssim)).
- **Input video** — a file from the input folder, or a stream URL. The
  field accepts several `-i file` tokens typed straight in: the first is
  the main input, the rest become extra inputs (auto-typed from their
  probes).
- **Output** — optional; a filename, or a stream URL to send to
  (`rtmp://`, `rtsp://`, `srt://`, `udp://`, …). The `?` tip: naming the
  output `/input/<file>` writes into the input folder for chained
  processings — the next command picks it up by bare name.
- **Watermark image** — a logo file; further logos go in as extra image
  inputs.
- **Adapter** — `auto` (rules first, LLM fallback), `rules`, or `llm`.
- **Additional inputs** — one row per extra input: a file (or stream
  URL), a kind selector (`auto` / `video` / `audio` / `image` /
  `source`), and a remove button; **"+ add input"** appends the next
  `in1`, `in2`, … row.
- **Options** — *preflight against the local FFmpeg build* (check
  encoders/filters before planning), *verbose log* (`-loglevel verbose`;
  the log lands in the output folder's `log/`), and *serve over http*
  (write the output into the http sidecar tree so the **Player** tab can
  play it — HLS / DASH, m3u8 / mpd).
- **Generate** — runs the pipeline; the result pane renders per status:
  - **ready** (green) — an editable **Command** textarea with a copy
    button, the **argument array** (shell-safe JSON), **What it does**,
    **RAG evidence** (evidence-status and corpus badges, chunk links,
    warnings), **Defaults applied**, **Warnings**, and **Validation**
    (level + each check ok/FAIL). When execution is enabled on this
    deployment (`FASS_ALLOW_EXECUTE`), a **▶ Run now** button starts the
    command server-side — the textarea's *current* text is shlex-split
    and re-validated, so a hand edit is checked, not trusted — and jumps
    to the **Runs** tab. Otherwise the button is replaced by a note:
    copy the command and run it yourself.
  - **needs_clarification** (amber) — a "Please clarify" list; adjust
    the instruction and generate again.
  - **unsupported** (red) — "Not supported in the MVP", an optional
    **Second try (direct LLM)** section, and a "Supported instead" hint.
  - **error** (red) — the error list.
- **Save** — freezes the last *ready* generation as a preset (re-openable
  from Info / Log → Presets).
- **Enter** — expert path, skips the NLU: prefills an editable
  `ffmpeg -i … -o …` skeleton from the input/output fields; **Generate**
  then validates it as a raw command.
- **Mediainfo** — ffprobe + mediainfo for the input (needs an input and
  an *empty* instruction); the result opens in Info / Log.
- **Clear** — resets the whole pane.

### Files tab — the volumes

Input and output panes side by side (name / size / date, newest first),
each with the file count and free / total disk. **Right-click a row**
for the context menu:

- **as input** (input pane) — copies the filename into the FFmpeg tab's
  Input field;
- **to input** (output pane) — copies the file into the input volume
  (the output original is kept) so it can become an input;
- **ffprobe** / **mediainfo** — opens the metadata in Info / Log;
- **tsanalyze** — TS containers only (`.ts` / `.mpg` / `.mpeg` / `.ps`);
  jumps to Info / Log and runs;
- **1st frame** — decodes the first frame to a JPEG (with dimensions) in
  a modal (Escape closes).

### Runs tab — executed commands

The executed-command table (id, started, status, exit, command, output,
actions) with **↻ refresh**:

- status badges — **success** (green), **running** (amber; the table
  polls every 2.5 s while a run is in flight), **failed** (red);
- **output** — a link: http-served runs go to the **Player** tab,
  ordinary runs to the raw file;
- **log** — the run's log inline; **stop** — kills a running command.

### Player tab — play http-served output

Runs with *serve over http* land here as HLS / DASH playlists:

- **streams** table — playlist, kind, state (**live** / **vod**
  badges), size, modified, and a **play** row action; **↻ refresh**
  re-lists, and the list auto-polls while the tab is open, so a live
  stream keeps appearing as it is written;
- playback uses the bundled shaka-player with a status line ("playing
  HLS — …", or the fatal error);
- **Purge stream tree** (danger, confirmed) — deletes everything under
  the output volume's `http/` tree.

### Info / Log tab — four views

A radio switch picks the view:

- **Media info** — Look in (input / output) + a bare filename:
  **Show media info** (ffprobe JSON), **TSAnalyze** (TS containers), and
  **Bitrate** (the vviz bitrate chart in a sandboxed iframe; non-mp4
  inputs are remuxed to mp4 first).
- **Run log** — pick a log from the server's list and **Show log**;
  logs longer than 10 000 lines show their last 1 000.
- **Prompts** — the last 1 000 accepted instructions, latest first;
  click one to load it back into the FFmpeg tab's Instruction field.
- **Presets** — saved generations: one JSON file each, named by the md5
  of instruction + inputs + ffmpeg command (re-saving the same
  generation overwrites its file). Pick one, preview it, and **Load into
  the FFmpeg tab** restores the whole form plus the stored command.

### RAG tab — ask the documentation service

A direct query box for the retrieval service that backs the **RAG
evidence** on ready results. **Query** returns the matched chunks:
corpus, corpus-match and evidence-status badges, retrieval time,
warnings, and one card per chunk — title (linking into the pinned
manual), component type·name, option-name badges, score, and the chunk
text. The Config tab shows the service's status; a down service degrades
generation (fail-open), it never breaks it.

### Config tab — deployment settings

Sections, top to bottom:

- **Appearance** — theme (auto / light / dark "night" / kitty "pink" /
  autodesk "gray") and content width (default 860 px / wider 1 260 px);
  remembered per browser.
- **Volumes** — the host folders behind `/input`, `/output`, `/log`,
  each with an optional **remote mapping** (FTP / SFTP / S3 bucket —
  `ftplib`, paramiko, boto3; install the `[storage]` extra):
  - **input** — a file missing locally is *downloaded from the remote
    before use*;
  - **output** — the result file, the verbose log and the run's `http/`
    subtree are *uploaded after a successful run*;
  - **log** — the run log + run record are *uploaded after every run*.

  Per mapping: type, host (S3: blank = real AWS), port (blank = default
  21/22/443), user (S3: access key id), password / secret, path or
  prefix, bucket, region, an *https* checkbox (http for an on-host
  MinIO / LocalStack), a *uuid naming* checkbox (S3 objects keyed by
  UUID, the real basename in object metadata), and a **test (unsaved
  values)** button. Under docker, on-host services are reached as
  `host.docker.internal`; sftp host keys are not pinned (dev-tool
  convention).
- **Streaming — port mappings** — the host-address rewrite for stream
  URLs: *the host in a URL → the docker host* (udp/rtp inputs bind
  0.0.0.0 inside the container), the host address (docker bridge
  gateway, default 172.17.0.1), and per-port rows (label, container
  port, host port, proto) with **"+ add port mapping"**.
- **GPU (NVIDIA)** — enable nvenc (H.264 / H.265) and pick the device
  (`all`, or e.g. `0`).
- **LLM provider** — `anthropic` or any OpenAI-compatible endpoint
  (strata, vLLM, …): host, port, api key (optional for the openai
  provider), model, timeout (1–300 s, default 90), and **thinking mode**
  (openai provider — Qwen3 / strata / vLLM; **off by default** — no
  reasoning trace, faster JSON; endpoints that reject the chat-template
  kwarg fall back to a plain request automatically). **Test LLM (unsaved
  values)** reports "ok — model X answered in Ns: '…'".
- **RAG — documentation retrieval** — the service status: url, timeout,
  top_k, max evidence chars, the configured corpus, whether the
  instruction is sent into the query, and which corpora are loaded — or
  "RAG disabled" / "service unreachable (fail-open)".
- **Deploy** (docker deployments) — the docker-control + fass-worker
  sidecar status; **Save** (LLM, stream addresses and the compose
  preview are live immediately; volumes / ports / GPU take effect after
  Apply); **Apply to container** (danger — asks the sidecar to recreate
  the container from the saved config; the connection drop is expected,
  the UI polls the apply result, and a swap that fails partway
  re-creates the old container); and the **generated compose** (what
  Apply will create).

## CLI

Subcommands (the bare form is `generate`):

```bash
fass "INSTRUCTION" [input] [options]        # generate (the default)
fass probe FILE                             # ffprobe JSON for one file
fass env                                    # this FFmpeg build vs the registry
fass serve [--host 0.0.0.0] [--port 8000]   # web UI + JSON API
fass worker [--host 0.0.0.0] [--port 8001]  # fass-worker sidecar (Apply)
fass mcp [--transport stdio|streamable-http] [--host 127.0.0.1] [--port 8765]
fass feedback --useful | --incorrect [--category C] [--note S]
         [--error "ffmpeg error you saw"] [--instruction S --consent]
fass examples                               # supported-feature summary + examples
```

`generate` flags:

- **instruction**, **input** (positional) — the request and the main
  input (a file from the input folder, or a stream URL)
- **-o / --output** — output filename, or a stream URL to send to
  (`rtmp://`, `rtmps://`, `rtsp://`, `rtsps://`, `srt://`, `udp://`,
  `rtp://`, `tcp://`)
- **--watermark IMAGE** — a logo to burn in
- **--asset id=path** or **--asset id=kind:path** (repeatable; kind
  `video` | `audio` | `image` | `source`) — extra typed inputs
- **--adapter {auto,rules,llm}** — default `auto` (rules first, LLM
  fallback)
- **--previous PLAN.json** — revision: only what the new instruction
  changes changes
- **--metadata FILE** — pre-collected ffprobe JSON for the input (skip
  the probe)
- **--preflight** — check the local FFmpeg build before planning
- **--json** — print the full JSON response (always on with `--case`)
- **--execute** — DEV: run the generated command locally
- **--batch FILE|DIR …** — run the *same* instruction over a directory's
  top-level video files (or an explicit file list), one at a time; a
  failing file is reported and skipped — **--batch-out DIR** sends every
  result into one folder
- **--case FILE** — a whole test case in one JSON file:
  `{"instruction", "assets": {"main": file, …}, "assets_dir"?,
  "output"?, "adapter"?, "execute"?}` — asset paths are relative to
  `assets_dir` (default: the case file's directory); implies `--json`
- **--version**

## MCP — fass as a tool for agents

`fass mcp` speaks the Model Context Protocol, so any MCP client (Claude
Code, an agent, a test harness) can drive fass directly. stdio by
default (the client spawns the process); `--transport streamable-http`
exposes a network endpoint on host:port. Install the extra:
`pip install -e ".[mcp]"` (mcp v1 — 2.x renamed FastMCP).

Four tools, all returning the same JSON the CLI's `--json` prints:

- **generate**(instruction, assets, output?, adapter?) — `assets` maps
  ids to file paths (`main` required; `watermark` for a logo image; free
  ids like `in1` / `in2` for extras); a value may be `kind:path`
  (video / audio / image / source) to pin the kind
- **run_case**(case_path) — a case described entirely by a JSON file
  (the `--case` schema); with `"execute": true` the ready command(s)
  also run and the return code is recorded as `execute_rc`
- **probe**(file) — ffprobe metadata: streams, codecs, duration,
  resolution
- **env_info**() — the local FFmpeg build fass validates against:
  version, registry version, and the presence of every encoder/filter
  the registry references

Bare filenames resolve against `$FASS_INPUT_DIR` / `$FASS_OUTPUT_DIR`
exactly like the rest of fass, so a harness that stages a case's media
into its input dir can reference the files by their original names.

## What it understands

**Shape**: N typed inputs — video, audio, image, local capture sources
(the web UI has a "+ add input" row for each — or several `-i file` tokens
typed straight into the main input field) → one output that is a file
**or** a stream destination. Multiple inputs is what makes "replace the
audio with song.mp3" and "watermark it with a and b" work.

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
- **Combiners** — two typed inputs through a labeled filter graph:
  `crossfade a.mp4 and b.mp4` (xfade; offset computed from the first
  input's duration, a "2-second crossfade" sets the transition duration;
  "crossfade the audio" → acrossfade), `stack them side by side /
  vertically` (hstack / vstack), `concatenate a.mp4 and b.mp4` (concat;
  "without the audio" drops it), `mix the audio` (amix), `merge the
  audio tracks` (amerge), `join the audio to 5.1` (join), "join / merge
  the two videos" = end-to-end
- **Quality comparison** — `compare a.mp4 against b.mp4 with psnr`
  (also ssim; libvmaf on builds that ship the filter + model): the
  reference clip is the **first** input (`-i ref -i main`), the metric
  runs frame by frame, the summary lands in the run log (null muxer —
  nothing is written) — full workflow:
  [Quality comparison](#quality-comparison-vmaf--psnr--ssim)
- **Two-pass stabilization** — `stabilize the shaky clip`: a
  vidstabdetect pass measures the shake into a `.trf` next to the
  output, the final pass applies vidstabtransform — the response
  carries the plan's `steps` and the execution layer runs them in order
- **Container conversion** — a broad, codec-matrix-validated set: video in
  MP4 / MOV / MKV / WebM / MPEG-TS / MXF / AVI / FLV / GIF / WebP / OGV with
  H.264 / H.265 / AV1 (libaom, the default) / SVT-AV1 / VP9 / VP8 / Theora /
  VVC / MPEG-2 / ProRes / MJPEG, audio in MP3 / AAC / FLAC / Opus /
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
  **GPU encoding** (nvenc) when a deployment exposes a GPU, and the
  **GPU filter-consume rule**: `resize to 1080p on the gpu` picks the
  CUDA filter (`scale_cuda`) and keeps the encoder CPU — the GPU mention
  is absorbed by the filter; an explicit encoder word (`nvenc`)
  re-enables GPU encoding
- **Muxer / format options** — `+faststart`, HLS segment settings
  (`hls_time`, segment type), MPEG-TS flags, MP3 ID3 tags, …, validated
  against the plan's actual output container
- **Watermarks** — one or several static images (preset positions, margin,
  percent scale): the first binds with `--watermark`, each extra one with
  `--asset name=image:file`; they overlay in the order the plan lists them;
  **audio replacement** from a second bound input, **audio keep / remove** —
  and compositions of any of the above, in the order stated
- **HDR → SDR tone-mapping** — `tone map this HDR clip to SDR`: detected
  from probe metadata (PQ / HLG transfer), built as
  `format=gbrpf32le,tonemap=tonemap=hable:desat=0,format=yuv420p` — the
  default output is 8-bit Rec.709, and a 10-bit `main10` re-encode keeps
  the HDR. HDR10+ and Dolby Vision are rejected explicitly
- **Batch mode** — `fass "resize to 720p" --batch clips/` (or an explicit
  file list) runs the same instruction over every top-level video file in a
  directory, one at a time; a failing file is reported and skipped, and
  `--batch-out DIR` sends every result into one folder
- **Subtitles** — bind an srt / ass / ssa / vtt file; **burned** into the
  video by default (libass, forces a re-encode) or muxed as a **selectable
  soft track** (MP4 / MOV / MKV — compatible with stream-copy)
- **Capture sources** — `lavfi:` synthetic sources, `v4l2:` cameras, and
  alsa / dshow / avfoundation / x11grab bind as typed inputs
- **Stream I/O** — inputs and outputs may be stream URLs (rtmp, rtsp, srt,
  http(s), udp, rtp, tcp — allowlisted); a stream destination's scheme fixes the
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

The full web API (every endpoint — `POST /v1/commands`, `POST /v1/runs`,
the files / media / config endpoints), the security model, the validation
levels, the test suite, and the roadmap live in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Quality comparison (VMAF / PSNR / SSIM)

Measure how far a clip drifted from its reference — the Netflix vmaf docs'
command order, as a standalone analysis:

```bash
./.venv/bin/python -m fass "compare main.mp4 against ref.mp4 with psnr" main.mp4 --asset ref=video:ref.mp4
./.venv/bin/python -m fass "give me the vmaf against ref.mp4" main.mp4 --asset ref=video:ref.mp4
./.venv/bin/python -m fass "measure ssim between the two clips" main.mp4 --asset ref=video:ref.mp4
```

- **Reference binding** — the reference clip is bound with `--asset`
  (`--asset ref=video:ref.mp4`) and named in the request; a metric-less
  "compare against ref.mp4" / "how much quality loss" asks which metric
  (PSNR or SSIM — libvmaf is offered only when you name it)
- **Input order** — the reference is input 0 (the first `-i`), the main
  (distorted) input 1; the metric's pads take `[distorted][reference]`.
  When the sizes differ the main input is scaled to the reference's
  (the reference is the ground truth); fps / pixel-format mismatches are
  noted, not converted (resampling would distort the measurement)
- **Metrics** — `psnr` and `ssim` on every build; `libvmaf` needs a build
  that ships the filter (FFmpeg 7.0+ — present in the docker image's 9.0,
  absent from 6.1.1; the plan discloses it as a warning). The VMAF model
  comes from `FASS_VMAF_MODEL` (a full path) or `FASS_VMAF_MODEL_DIR`
  (`vmaf_v0.6.1.pkl` / `vmaf_v0.6.1.json`); with neither set, the
  filter's built-in model applies
- **Diagnostic output** — the scores go to the ffmpeg log (the run log /
  UI log tab, log level raised to info); the plan runs against the null
  muxer (`-f null -`) — **no output file is produced**
- **Standalone only** — a comparison cannot share one request with trims,
  watermarks, subtitles, audio replacement, combiners or filter edits;
  the NLU asks for a follow-up (apply the edit first, then compare the
  result against the reference)
- **Timeline alignment** — `setpts=PTS-STARTPTS` re-bases each input's
  pad only when its probe shows the stream carries packet pts; a raw
  bitstream (a bare Annex-B `.hevc`) demuxes without any, so the filter
  is skipped for that pad and the run notes it (frames compare in
  arrival order)

## Docker deployment

fass packaged with FFmpeg 9.0 + NVIDIA hardware acceleration, in a single
container (`jrottenberg/ffmpeg:9.0-nvidia2404` base):

```bash
docker compose -f docker/compose.yaml up -d --build   # → http://<host>:8000
```

The web UI gets a **Config tab**: host-folder mappings for `/input`,
`/output`, `/log` (each optionally mapped to a remote FTP / SFTP / S3
store), stream port mappings (in: `udp://host:port` binds to `0.0.0.0`
inside; out: `srt/rtmp://host:port` rewrites to the docker gateway),
GPU on/off (H.264/H.265 → nvenc), and the LLM provider (Anthropic, or
any OpenAI-compatible endpoint). Execution is enabled in the
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

Spec: `ffmpeg-assistant-rag-prd.md`
