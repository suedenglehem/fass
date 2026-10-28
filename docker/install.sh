#!/usr/bin/env bash
# fass docker install — one-time setup of a machine (the host folders, the
# LLM endpoint and its API key are the install-time questions; the model
# itself is tuned in the web GUI).
#
#   ./install.sh                                  # interactive prompts
#   ./install.sh --yes                            # accept the defaults
#   ./install.sh --yes --input /srv/in --output /srv/out --log /srv/log
#   ./install.sh --config /srv/config             # move the config folder
#   ./install.sh --yes --llm http://192.168.0.9:8080 --api-key sk-...
#
# Asks for the host folders mounted as /input, /output, /log (sources,
# results, run logs) and the config folder (/config); the LLM base URL
# (OpenAI-compatible endpoint) and the API key (empty = not required).
# The model name is NOT asked — set it in the Config tab afterwards.
#
# GPU: probes nvidia-smi. Without a GPU the deployment is CPU-only:
# the compose GPU device reservation is dropped (docker/compose.local.yaml
# override) and the container gets FASS_GPU_PROBE=0, so GPU-involved
# ffmpeg commands are GENERATED (argv/CLI) but REFUSED at execution
# (start_run raises 403) — see fass/gpu.py.
#
# The folders are recorded in two places so they stay in sync:
#   docker/.env            — compose reads it at every `up` (git-ignored,
#                            machine-local; the compose file interpolates
#                            ${FASS_INPUT_DIR} etc. with repo-relative
#                            defaults)
#   <config>/config.json   — the volumes section, which the Config tab
#                            shows and the Apply button re-creates the
#                            container from
# After install the Config tab OWNS the paths: Apply re-creates from
# config.json, and launcher.sh re-syncs docker/.env from config.json
# before every compose up — so paths changed in the UI survive rebuilds.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$DIR")"

INPUT="${FASS_INPUT_DIR:-$REPO/media/input}"
OUTPUT="${FASS_OUTPUT_DIR:-$REPO/media/output}"
LOG="${FASS_LOG_DIR:-$REPO/media/log}"
CONFIG="${FASS_CONFIG_DIR:-$REPO/docker-data/config}"
LLM_URL=""
API_KEY=""
ASSUME=0
BUILD=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)  INPUT="$2";  shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --log)    LOG="$2";    shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --llm)    LLM_URL="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    -y|--yes) ASSUME=1; shift ;;
    --no-build) BUILD=0; shift ;;
    -h|--help) sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# absolute paths, no need to exist yet (Docker requires absolute binds)
canon() { realpath -m -- "$1"; }
INPUT="$(canon "$INPUT")"; OUTPUT="$(canon "$OUTPUT")"
LOG="$(canon "$LOG")";     CONFIG="$(canon "$CONFIG")"

# GPU probe: nvidia-smi listing GPUs (host driver + toolkit present)
GPU=1
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; then
  GPU=0
fi

CFGJSON="$CONFIG/config.json"
# defaults for the prompts: existing config wins, else the deployment
# defaults the container entrypoint seeds (strata on the docker bridge)
LLM_HOST="172.17.0.1"; LLM_PORT="8080"; CFG_API_KEY=""; CFG_GPU="true"
if [[ -f "$CFGJSON" ]]; then
  read -r LLM_HOST LLM_PORT CFG_API_KEY CFG_GPU < <(python3 - "$CFGJSON" <<'PYEOF'
import json, sys
cfg = json.load(open(sys.argv[1]))
llm = cfg.get("llm") or {}
print(llm.get("host", "172.17.0.1"), llm.get("port", 8080),
      llm.get("api_key", ""), str(bool((cfg.get("gpu") or {}).get("enabled", True))).lower())
PYEOF
)
fi
[[ -n "$LLM_URL" ]] || LLM_URL="http://$LLM_HOST:$LLM_PORT"

if [[ $ASSUME -eq 0 ]]; then
  ask() { # ask() <label> <default> — Enter accepts the default
    local ans
    read -e -p "$1 [$2]: " ans || true
    printf '%s' "${ans:-$2}"
  }
  INPUT="$(ask  "input host folder"  "$INPUT")"
  OUTPUT="$(ask "output host folder" "$OUTPUT")"
  LOG="$(ask    "log host folder"    "$LOG")"
  CONFIG="$(ask "config host folder" "$CONFIG")"
  LLM_URL="$(ask "LLM base URL (OpenAI-compatible)" "$LLM_URL")"
  API_KEY="$(ask "LLM API key (empty = not required)" "$CFG_API_KEY")"
fi
for p in "$INPUT" "$OUTPUT" "$LOG" "$CONFIG"; do
  [[ "$p" == /* ]] || { echo "not an absolute path: $p" >&2; exit 2; }
done

# host:port from the URL (scheme/path stripped, missing port = 8080)
LLM_HOST="$(printf '%s' "$LLM_URL" | sed -e 's|^[a-zA-Z]*://||' -e 's|/.*||' -e 's|:.*||')"
LLM_PORT="$(printf '%s' "$LLM_URL" | sed -e 's|^[a-zA-Z]*://||' -e 's|/.*||')"
case "$LLM_PORT" in *:*) LLM_PORT="${LLM_PORT#*:}";; *) LLM_PORT=8080;; esac
[[ "$LLM_HOST" == /* ]] && { echo "LLM URL has no host: $LLM_URL" >&2; exit 2; }

echo "input  -> $INPUT"
echo "output -> $OUTPUT"
echo "log    -> $LOG"
echo "config -> $CONFIG"
echo "llm    -> http://$LLM_HOST:$LLM_PORT$([[ -n "$API_KEY" ]] && echo ' (API key set)' || echo ' (no API key)')"
echo "gpu    -> $([[ $GPU -eq 1 ]] && echo present || echo 'NOT FOUND (CPU-only: GPU commands generate, execution refused)')"
mkdir -p "$INPUT" "$OUTPUT" "$LOG" "$CONFIG"

# compose reads .env from the project dir (docker/) at every `up`
cat > "$DIR/.env" <<EOF
FASS_INPUT_DIR=$INPUT
FASS_OUTPUT_DIR=$OUTPUT
FASS_LOG_DIR=$LOG
FASS_CONFIG_DIR=$CONFIG
FASS_GPU=$GPU
EOF
echo "wrote $DIR/.env"

# GPU-less machine: drop the compose device reservation (it fails at
# `up` without the nvidia runtime); GPU machine: remove a stale override
if [[ $GPU -eq 1 ]]; then
  rm -f "$DIR/compose.local.yaml"
else
  cat > "$DIR/compose.local.yaml" <<'EOF'
# written by docker/install.sh — no GPU on this machine: the fass
# service asks for no device reservation (launcher.sh/install.sh add
# this file to every compose command when present)
services:
  fass:
    deploy:
      resources:
        reservations:
          devices: []
EOF
  echo "wrote $DIR/compose.local.yaml (no GPU: device reservation dropped)"
fi

# config.json: seed the deployment defaults (same shape as the container
# entrypoint seeds on first boot) with the answers filled; on an existing
# file keep everything and update llm, gpu.enabled and the volumes
python3 - "$CFGJSON" "$INPUT" "$OUTPUT" "$LOG" "$LLM_HOST" "$LLM_PORT" "$API_KEY" "$GPU" <<'PYEOF'
import json, os, sys
path, inp, outp, logp, host, port, key, gpu = sys.argv[1:9]
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        cfg = json.load(f)
seed = {
    "llm": {"provider": "openai", "host": host, "port": int(port),
            "api_key": key, "model": "qwen3.8-27b"},
    "gpu": {"enabled": gpu == "1", "device": "all"},
    "streaming": {"enabled": True, "host_address": "172.17.0.1",
                  "port_mappings": []},
}
for k, v in seed.items():
    cfg.setdefault(k, v)
cfg["llm"].update({"host": host, "port": int(port)})
if key:
    cfg["llm"]["api_key"] = key
cfg["gpu"]["enabled"] = gpu == "1"
vols = cfg.setdefault("volumes", {})
vols.update({"input": inp, "output": outp, "log": logp})
tmp = path + ".new"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
try:
    os.replace(tmp, path)
    print(f"updated {path}")
except PermissionError:
    print(f"cannot replace {path} (owned by root?) — run:\n"
          f"  sudo cp {tmp} {path} && rm {tmp}", file=sys.stderr)
    sys.exit(1)
PYEOF

if [[ $BUILD -eq 1 ]]; then
  COMPOSE=(docker compose -f "$DIR/compose.yaml")
  [[ -f "$DIR/compose.local.yaml" ]] && COMPOSE+=(-f "$DIR/compose.local.yaml")
  "${COMPOSE[@]}" up -d --build
  for i in $(seq 1 90); do
    if curl -sf -m 3 "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
      echo "healthy — open http://127.0.0.1:8000 (Config tab: model, GPU, ports)"
      exit 0
    fi
    sleep 1
  done
  echo "not healthy after 90 s — last logs:" >&2
  docker logs --tail 30 fass >&2 || true
  exit 1
fi
echo "installed (skipped build/start: --no-build)"
