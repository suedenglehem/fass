"""Deployment configuration (M6).

A single JSON document (config.json) holds everything the docker
deployment needs that cannot be a CLI flag: host-folder mappings for the
/input, /output and /log volumes, stream port mappings, GPU settings and
the LLM provider. It lives at $FASS_CONFIG (set to /config/config.json
inside the container; ./.fass-config.json in a development checkout).

The document is plain data, written by the web UI's config tab (PUT
/v1/config) or by hand. fass reloads it on every request — no restart is
needed for LLM or stream-address changes; volume/ports/GPU changes take
effect when the container is recreated (POST /v1/config/apply).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CONFIG_ENV = "FASS_CONFIG"

# The docker bridge gateway: how a container reaches services on the host.
DEFAULT_HOST_ADDRESS = "172.17.0.1"


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(
        default="anthropic", pattern="^(anthropic|openai)$",
        description="'anthropic' = api.anthropic.com; 'openai' = any "
                    "OpenAI-compatible /v1/chat/completions endpoint "
                    "(strata, vLLM, llama.cpp, …)",
    )
    host: str = Field(
        default="", max_length=253,
        description="openai provider: endpoint host (e.g. the docker bridge "
                    "gateway 172.17.0.1 to reach a host-side server)",
    )
    port: int | None = Field(
        default=None, ge=1, le=65535,
        description="openai provider: endpoint port (0/empty = not configured)",
    )
    api_key: str = Field(default="", max_length=1024)
    model: str = Field(default="", max_length=256)


class StreamPortMapping(BaseModel):
    """One container<->host port for streams (the config-tab port list).

    A mapping is what lets a client on the LAN push/pull a stream to/from
    the host port; inside the container the stream address uses the
    container port (see FassConfig.streaming.host_address)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=64)
    container_port: int = Field(ge=1, le=65535)
    host_port: int = Field(ge=1, le=65535)
    proto: str = Field(default="tcp", pattern="^(tcp|udp)$")


class StreamingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="when true, the stream host placeholder 'host' in "
                    "input/output URLs is rewritten: pull/push addresses "
                    "become host_address (the docker bridge gateway), and "
                    "udp/rtp listen inputs bind to 0.0.0.0",
    )
    host_address: str = Field(
        default=DEFAULT_HOST_ADDRESS, max_length=253,
        description="IP of the docker host as seen from the container "
                    "(default: bridge gateway)",
    )
    port_mappings: list[StreamPortMapping] = Field(default_factory=list)


class GPUConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="the deployment exposes an NVIDIA GPU (compose "
                    "device reservation / --gpus all); nvenc encoders "
                    "become the GPU path for h264/h265",
    )
    device: str = Field(
        default="all", pattern="^(all|\\d+(,\\d+)*)$",
        description="'all' = every GPU; otherwise a comma list of indices",
    )


class VolumesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(default="", max_length=1024)
    output: str = Field(default="", max_length=1024)
    log: str = Field(default="", max_length=1024)


class FassConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    gpu: GPUConfig = Field(default_factory=GPUConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    volumes: VolumesConfig = Field(default_factory=VolumesConfig)


def config_path() -> Path:
    env = os.environ.get(CONFIG_ENV, "").strip()
    if env:
        return Path(env)
    # The container convention: /config is a mounted volume.
    if Path("/config").is_dir():
        return Path("/config/config.json")
    return Path.cwd() / ".fass-config.json"


def load_config() -> FassConfig:
    """Read config.json; missing or invalid files fall back to defaults
    (the UI's config tab is the place to fix them)."""
    p = config_path()
    if p.exists():
        try:
            return FassConfig.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            pass
    return FassConfig()


def save_config(cfg: FassConfig) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(cfg.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p
