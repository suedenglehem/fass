#!/usr/bin/env bash
# fass docker deployment launcher — bring the compose stack up/down and
# open the web GUI in Chrome.
#
#   ./launcher.sh            # = start
#   ./launcher.sh start      # up -d (no-op if already healthy) + open the GUI
#   ./launcher.sh rebuild    # up -d --build (fresh image) + open the GUI
#   ./launcher.sh stop       # stop the containers (volumes + state kept)
#   ./launcher.sh status     # containers + healthz
#
# Env overrides:
#   FASS_GUI_URL  GUI to open (default http://127.0.0.1:8000)
#   FASS_BROWSER  browser binary (default: first of google-chrome,
#                 google-chrome-stable, chromium, chromium-browser)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "$DIR/compose.yaml")
URL="${FASS_GUI_URL:-http://127.0.0.1:8000}"

health() { curl -sf -m 3 "$URL/healthz" >/dev/null 2>&1; }

running() {
  [[ "$(docker inspect -f '{{.State.Running}}' fass 2>/dev/null || true)" == "true" ]]
}

wait_healthy() {
  local i
  for i in $(seq 1 90); do
    if health; then echo "healthy"; return 0; fi
    sleep 1
  done
  echo "not healthy after 90 s — last logs:" >&2
  docker logs --tail 30 fass >&2 2>/dev/null || true
  return 1
}

open_gui() {
  local browser="${FASS_BROWSER:-$(command -v google-chrome \
    || command -v google-chrome-stable || command -v chromium \
    || command -v chromium-browser || true)}"
  if [[ -z "$browser" ]]; then
    echo "no chrome/chromium found — open $URL manually"
  elif [[ -z "${DISPLAY:-}" ]]; then
    echo "no DISPLAY — open $URL manually"
  else
    nohup "$browser" "$URL" >/dev/null 2>&1 &
    echo "opened $URL in $(basename "$browser")"
  fi
}

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

cmd="${1:-start}"
case "$cmd" in
  start)
    if running && health; then
      echo "already up and healthy"
    else
      "${COMPOSE[@]}" up -d
      wait_healthy
    fi
    open_gui
    ;;
  rebuild)
    "${COMPOSE[@]}" up -d --build
    wait_healthy
    open_gui
    ;;
  stop)
    "${COMPOSE[@]}" stop
    echo "stopped (volumes + state kept — 'start' again any time)"
    ;;
  status)
    docker ps -a --filter "name=fass" --format '  {{.Names}}  {{.Status}}'
    if health; then echo "  healthz: ok"; else echo "  healthz: DOWN"; fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 2
    ;;
esac
