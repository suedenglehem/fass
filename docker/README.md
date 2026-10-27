# fass — Docker deployment (M6)

fass packaged together with FFmpeg 9.0 + NVIDIA hardware acceleration, in a
single container. The web UI gets a **Config tab** that defines the whole
deployment: host-folder mappings for `/input`, `/output`, `/log`, stream port
mappings, GPU, and the LLM provider. The container can **execute** the
generated FFmpeg commands (gated and validated — see [Execution](#execution)).

```
host folder ──(volume)──▶ /input    source media
host folder ──(volume)──▶ /output   run results (playable from the UI)
host folder ──(volume)──▶ /log      run logs + run records
host folder ──(volume)──▶ /config   config.json (the Config tab reads/writes it)
```

## Host requirements

- Docker (with the `nvidia` runtime: NVIDIA driver + `nvidia-container-toolkit`)
- an NVIDIA GPU (for GPU encoding; the image is `nvidia2404`-flavoured anyway)
- the base image `jrottenberg/ffmpeg:9.0-nvidia2404` (pulled automatically
  on first build)

The image is a **deployment** build: `FASS_ALLOW_EXECUTE=1` is baked in, so
only run it where you want fass to execute FFmpeg. A development checkout
(`python -m fass serve` from the repo) never executes anything — the gate is
environment-based, see [Execution](#execution).

## Quick start

From the repo root:

```sh
# 1. host folders (adjust to taste)
mkdir -p docker-data/{input,output,log,config}

# 2. build + start (compose file is docker/compose.yaml)
docker compose -f docker/compose.yaml up -d --build

# 3. open the UI
#    http://<host>:8000
```

### Launcher (start / stop + GUI)

`docker/launcher.sh` wraps the compose lifecycle and opens the web GUI:

- `./launcher.sh start` — no-op if the stack is already healthy, else
  `up -d` + wait for healthz, then fires Chrome at `http://127.0.0.1:8000`
  (override with `FASS_GUI_URL` / `FASS_BROWSER`); over ssh (no graphical
  session — neither `DISPLAY` nor `WAYLAND_DISPLAY` set) it prints the URL
  instead.
- `./launcher.sh rebuild` — `up -d --build` (fresh image) + GUI.
- `./launcher.sh stop` — `compose stop`; volumes and state are kept.
- `./launcher.sh status` — containers + healthz.

The entrypoint seeds `/config/config.json` on first start with deployment
sensible defaults (OpenAI-compatible LLM at the docker bridge gateway
`172.17.0.1:8080`, model `qwen3.8-27b` — the strata server on the host; GPU
enabled; streaming enabled; empty volume mappings). Tune everything from the
Config tab afterwards.

> `docker/compose.yaml` hard-codes `docker-data/` under the repo path on this
> machine — edit the four volume paths to your host layout before deploying
> elsewhere. The Config tab's volume fields show the *intended* host folders
> and drive the generated compose preview; the actual mounts are whatever the
> compose file (or `docker run`) says.

## The Config tab

`GET/PUT /v1/config` behind the UI. Four sections:

### Volumes

Host folders for `/input`, `/output`, `/log`. This is documentation + the
input to the **compose preview** shown under the tab — fass cannot remount
volumes in a running container, so changing them here means: update the
compose file, then **Apply** (or `docker compose up -d` again).

The runtime already resolves bare filenames for you: a command generated for
`clip.mp4` runs against `/input/clip.mp4` and writes to `/output/…`
automatically (the UI paths stay simple).

### Streaming

- **enabled** — the stream host placeholder `host` in generated commands is
  rewritten: *pull/push* addresses become `host_address` (the docker host as
  seen from the container, default bridge gateway `172.17.0.1`), and
  *udp/rtp listen* inputs (camera-in) bind to `0.0.0.0` so packets arriving
  on the mapped port reach the container.
- **host address** — override `172.17.0.1` (e.g. a custom bridge subnet).
- **port mappings** — one row per stream port: name, container port, host
  port, proto (tcp/udp). These must be mirrored in the compose file's
  `ports:` list (see the commented examples in `docker/compose.yaml`); the
  compose preview under the tab shows the exact lines to paste.

**Receiving a stream** (camera → fass): map e.g. `5000:5000/udp`; the camera
pushes to `<host-ip>:5000`; the instruction can say "from the stream on
`udp://host:5000`" and fass binds `udp://0.0.0.0:5000` inside the container.

**Sending a stream** (fass → elsewhere): map e.g. `9000:9000/tcp` (srt) or
`1935:1935/tcp` (rtmp); "stream it to `srt://host:9000/stream`" becomes
`srt://172.17.0.1:9000/stream` inside the container — but an *external*
client reaches it on `<host-ip>:9000`.

### GPU

- **enabled** — the deployment exposes an NVIDIA GPU (compose reserves the
  `nvidia` devices; for a partial list, set `device` to indices like `0,1`).
- When on, asking for the GPU in an instruction ("use the gpu", "nvenc",
  "on the nvidia card") selects `h264_nvenc` / `hevc_nvenc` for H.264/H.265.
  VP9 stays CPU-only; x264 speed presets and the `baseline` profile are
  rejected for GPU (the planner says why). Quality maps to nvenc
  `-rc vbr -cq <crf>`; an explicit bitrate target switches to CBR-style
  `-b:v`.

### LLM

The NLU provider (the **llm** adapter; the deterministic **rules** adapter is
always available as fallback and needs nothing):

- **provider** `anthropic` — `api.anthropic.com`, needs an API key.
- **provider** `openai` — any OpenAI-compatible `/v1/chat/completions`
  endpoint (strata, vLLM, llama.cpp, …): **host, port, api key, model id**.
  From inside the container, a host-side server is reached through the
  bridge gateway: host `172.17.0.1`.

  **strata on the host (the test setup):** provider `openai`, host
  `172.17.0.1`, port `8080`, api key *empty*, model `qwen3.8-27b`. Thinking models are handled: if the model exhausts
  `max_tokens` in its reasoning trace (empty answer), fass retries once with
  8× the token budget.

**Test LLM** button: sends a one-word probe through the real request path
(`POST /v1/config/llm-test`) and shows the reply + latency, using the values
in the form (no need to save first).

### Apply

Recreates the fass container from the saved config so volume/port/GPU
changes take effect, via the **fass-worker sidecar** (see Self-management
below): the UI's POST is forwarded to `fass-worker:8001/swap`, which stops
the old container, creates and starts the replacement, and records the
outcome in `last-apply.json` in the config volume. The UI expects the
connection to drop for a few seconds (two containers cannot publish the
same host port, so the old one must stop before the new one starts) and
polls `/healthz` + `/v1/config/apply-result` until the swap is recorded.
If the replacement cannot start, the old container is re-created from the
spec captured before the swap (best effort) and the state file says why.
Without the sidecar the button is disabled and the endpoint answers 409 —
the compose preview on the tab is then applied by hand
(`docker compose up -d`). LLM and stream-address changes need **no
restart** — the config is reloaded on every request.

## Execution

The Runs tab starts generated commands inside the container and shows status,
exit code, log tail, and the resulting file (playable/downloadable from
`/output`). The security model:

- **off unless `FASS_ALLOW_EXECUTE` is set** — the image sets it; a dev
  checkout never runs anything.
- **argv array only**, `shell=False` — a rendered shell string is never
  parsed; shell metacharacters are inert.
- **argv validated against the builder's flag vocabulary** before any process
  is spawned: every flag must be one the deterministic builder itself emits;
  codec/format/profile/pix-fmt/preset values must come from the registry;
  map/filter/number values are pattern-checked. Anything else is rejected.
- bare filenames resolve against the volumes (`/input`, `/output`); stream
  URLs and absolute paths pass through untouched.
- runs are process groups (`start_new_session`); **Stop** kills the exact
  group (SIGTERM → SIGKILL after 5 s). Records + logs persist in `/log/runs`
  across restarts; a run left `running` by a crash is marked `interrupted`
  on next read.

## Self-management (optional)

The Config tab's **Apply** button recreates the fass container — and a
container **cannot recreate itself**: the process answering the request
runs inside the container being stopped, and nothing inside a container
survives its own stop (the engine SIGKILLs the graceful-shutdown deadlock
before the create step can run). So the swap is orchestrated by the
**fass-worker sidecar** — the same image with `command: ["worker",
"--port", "8001"]`, declared next to fass in `docker/compose.yaml`.

Enable it by uncommenting in `docker/compose.yaml`: the docker socket
mount on **both** services, `FASS_DOCKER_MANAGE=1` on both, and
`FASS_DOCKER_WORKER=fass-worker:8001` on fass (already wired). Both then
talk to the Docker Engine API directly (no docker CLI in the image):

- fass forwards the UI's Apply to the worker's `POST /swap` and relays the
  ack; the worker performs the stop → delete → create → start in a
  background thread so the ack reaches the UI first;
- the worker writes the outcome to `last-apply.json` in the shared
  `/config` volume; the UI reads it via `GET /v1/config/apply-result`
  after fass is back;
- the replacement keeps the old container's network, env, labels, and any
  unmanaged mounts (the socket itself); the four managed volumes, port
  mappings, restart policy and GPU reservation come from the saved config.

The socket grant means each of these containers can manage *any* container
on the host — only enable it where you accept that.

## RAG (default service, M13)

`fass-rag` — the lexical retriever over the pinned FFmpeg corpora — is part
of the **default** deployment: `docker compose -f docker/compose.yaml up -d`
brings it up (no profile, no overlay; the old opt-in `docker/compose.rag.yaml`
is retired) and the fass service is wired to it via `FASS_RAG_ENABLED=1` +
`FASS_RAG_URL=http://fass-rag:8100` in the compose environment.

- **The corpus index is baked into the image.** `docker/Dockerfile.rag`
  copies the pinned corpus source (`rag/corpus/ffmpeg-9.0.1` +
  `ffmpeg-6.1.1`) into `/data` and runs `python -m rag.ingest` as a
  build-time step, so the finished `/data/<corpus_id>/` tree (manifest +
  chunks) lives in an image layer. The service starts serving with **no
  volume mount** and no first-boot build.
- **Build prerequisite:** the corpus source (`docs/*.html`,
  `build-facts/*.txt`) is generated data, gitignored by design — run
  `rag/tools/build_corpus.sh` (recipe: `rag/corpus/BUILD-RECIPE.md`) in the
  build context first. A corpus-less context fails the image build loudly.
- **Fail-open (PRD 4.4):** the fass-side client never blocks generation.
  With fass-rag down, `/v1/commands` still succeeds and the evidence block
  records status `unavailable`; `GET /v1/rag` shows the degraded state.
- **Non-dev default:** the code default in `fass/config.py` stays OFF —
  development checkouts don't need the service; docker enables it via env.
- **i7 live rule (PRD 6.7):** i7's live compose carries local opt-ins
  (socket mount, `FASS_DOCKER_MANAGE=1`, GPU pin). Merge the `fass-rag`
  service block and the two `FASS_RAG_*` env lines into it **by hand** —
  never overwrite the file from the repo.

## Development vs deployment

| | dev checkout | docker image |
|---|---|---|
| execution | off (env gate) | on (`FASS_ALLOW_EXECUTE=1`) |
| config file | `./.fass-config.json` | `/config/config.json` (seeded) |
| volumes | cwd / local paths | `/input` `/output` `/log` mounts |
| FFmpeg | whatever is on PATH | 9.0 + nvenc, in the image |
| RAG | off (code default) | on (`FASS_RAG_ENABLED=1`, baked index) |

Both run the same code; the difference is environment, not code paths.
