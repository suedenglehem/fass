#!/usr/bin/env bash
# fass container entrypoint: seed a deployment-sensible config on first
# start, make sure the volumes exist, then hand over to the CLI.
set -euo pipefail

mkdir -p /input /output /log/runs /config

# First boot only: the Config tab (or this seed) is the single place that
# defines the deployment. Defaults target the standard compose setup:
#   - LLM: OpenAI-compatible strata server on the host, reached through
#     the docker bridge gateway (172.17.0.1:8080)
#   - GPU: enabled (the compose file reserves the nvidia devices)
#   - streaming: host rewrite enabled (udp/rtp listen on 0.0.0.0,
#     pull/push to the bridge gateway)
#   - volumes: empty — fill them in from the Config tab (host folders)
if [[ ! -f /config/config.json ]]; then
  cat > /config/config.json <<'EOF'
{
  "llm": {
    "provider": "openai",
    "host": "172.17.0.1",
    "port": 8080,
    "api_key": "",
    "model": "qwen3.8-27b"
  },
  "gpu": { "enabled": true, "device": "all" },
  "streaming": {
    "enabled": true,
    "host_address": "172.17.0.1",
    "port_mappings": []
  },
  "volumes": { "input": "", "output": "", "log": "" }
}
EOF
  echo "fass: seeded /config/config.json — tune it from the Config tab"
fi

exec python -m fass "$@"
