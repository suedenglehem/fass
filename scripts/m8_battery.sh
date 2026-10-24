#!/usr/bin/env bash
# M8 codec-options battery: prepare the 10s clip, then run the driver.
#
#   scripts/m8_battery.sh local    in-process generate() + host ffmpeg
#                                  (bootstraps .venv if missing)
#   scripts/m8_battery.sh docker   against a running fass container
#   FASS_API=...                   docker API base (default http://127.0.0.1:8000)
#   FASS_BATTERY_SRC=...           titan.mp4 source (default: media/titan.mp4,
#                                  then /dd2/andrei/fass/media/titan.mp4)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
MODE="${1:?usage: m8_battery.sh [local|docker]}"

# ---- the 10-second clip (titan_10s.mp4) ---------------------------------

if [ -z "${FASS_BATTERY_SRC:-}" ]; then
    for cand in media/titan.mp4 /dd2/andrei/fass/media/titan.mp4; do
        [ -f "$cand" ] && FASS_BATTERY_SRC="$cand" && break
    done
fi
[ -n "${FASS_BATTERY_SRC:-}" ] || {
    echo "titan.mp4 not found (set FASS_BATTERY_SRC)" >&2; exit 2; }

make_clip() {  # $1 = destination
    local dst="$1"
    [ -f "$dst" ] && return 0
    mkdir -p "$(dirname "$dst")"
    echo "trimming 10s clip -> $dst"
    ffmpeg -hide_banner -loglevel error -y -i "$FASS_BATTERY_SRC" \
        -t 10 -c copy "$dst"
}

if [ "$MODE" = local ]; then
    make_clip battery-out/titan_10s.mp4

    if [ ! -x .venv/bin/python ]; then
        echo "bootstrapping .venv"
        python3 -m venv .venv
        .venv/bin/pip install -q -e .
    fi
    # the venv has no editable install: put the repo root on the path
    PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}" \
        exec ./.venv/bin/python scripts/m8_battery.py --mode local
fi

if [ "$MODE" = docker ]; then
    make_clip docker-data/input/titan_10s.mp4
    PY=python3
    [ -x .venv/bin/python ] && PY=./.venv/bin/python
    exec "$PY" scripts/m8_battery.py --mode docker \
        --api "${FASS_API:-http://127.0.0.1:8000}"
fi

echo "unknown mode: $MODE" >&2
exit 2
