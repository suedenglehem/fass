#!/usr/bin/env bash
# M10.6 per-category E2E battery — wrapper.
#
#   scripts/m10_battery.sh local   # prepare battery-out/ clips, run the
#                                  # battery in-process with the host ffmpeg
#   scripts/m10_battery.sh docker  # battery against a RUNNING fass container
#                                  # (its /v1/commands + /v1/runs); the four
#                                  # battery-out/ files must already be in
#                                  # the container's /input volume
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:?usage: m10_battery.sh local|docker}"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

if [ "$MODE" = local ]; then
  mkdir -p battery-out
  ff() { ffmpeg -hide_banner -loglevel error -y "$@"; }
  [ -f battery-out/titan_10s.mp4 ] || \
    ff -i media/titan.mp4 -t 10 -c copy battery-out/titan_10s.mp4
  [ -f battery-out/titan_b.mp4 ] || \
    ff -ss 10 -i media/titan.mp4 -t 10 -c copy battery-out/titan_b.mp4
  [ -f battery-out/titan_ref.mp4 ] || \
    ff -i battery-out/titan_10s.mp4 -c:v libx264 -crf 28 \
       -c:a aac -b:a 96k battery-out/titan_ref.mp4
  [ -f battery-out/wm.png ] || \
    ff -f lavfi -i color=red@0.6:s=120x80 -frames:v 1 battery-out/wm.png
fi

exec "$PY" scripts/m10_battery.py --mode "$MODE"
