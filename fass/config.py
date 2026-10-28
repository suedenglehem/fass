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
    timeout: int = Field(
        default=10, ge=1, le=300,
        description="seconds allowed for one LLM request (connect + read); a "
                    "hung endpoint fails fast instead of blocking the request",
    )


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
    http_base_url: str = Field(
        default="http://localhost:8080", max_length=2048,
        description="base URL of the fass-http sidecar serving the "
                    "output volume's http/ stream tree (M13.6) — "
                    "GET /v1/streams builds its URLs as "
                    "<base>/http/<path>. docker/install.sh seeds it "
                    "with the host address and the sidecar's port "
                    "(FASS_HTTP_PORT, default 8080); the sidecar "
                    "itself is brought up by compose, not by Apply",
    )


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


class RagConfig(BaseModel):
    """M11.2 §6.6 — retrieval-augmented generation for the LLM path.

    Off by default (rules stay the default adapter). The env equivalents
    (FASS_RAG_*) override the file values at request time — see
    resolved_rag() — so deployments can enable RAG without editing
    config.json. The model itself is pure data for the config tab."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="opt-in: send a /v1/search to the fass-rag service and "
                    "augment the LLM prompt with retrieved documentation "
                    "evidence (and derive citations in the response)",
    )
    url: str = Field(
        default="http://fass-rag:8100", max_length=2048,
        description="base URL of the fass-rag service (compose service name "
                    "in the deployment; 127.0.0.1 for host-side testing)",
    )
    timeout_ms: int = Field(
        default=800, ge=1, le=30000,
        description="per-request budget for the search; RAG is fail-open — "
                    "a timeout degrades to a RAG-less generation",
    )
    top_k: int = Field(
        default=6, ge=1, le=8,
        description="chunks requested (server cap: 8, PRD 5.4)",
    )
    max_evidence_chars: int = Field(
        default=4000, ge=1,
        description="evidence budget appended to the prompt (PRD 6.3: "
                    "4000 chars)",
    )
    corpus: str = Field(
        default="auto", max_length=64,
        description="'auto' = corpus selected from the probed input version "
                    "(exact -> nearest lower -> nightly, PRD 6.2)",
    )
    auth_token: str | None = Field(
        default=None, max_length=1024,
        description="optional bearer token for the fass-rag service",
    )
    send_instruction: bool = Field(
        default=True,
        description="include the raw user instruction in the search request "
                    "(the query builder always adds component names on top)",
    )


class FassConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    gpu: GPUConfig = Field(default_factory=GPUConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    volumes: VolumesConfig = Field(default_factory=VolumesConfig)
    rag: RagConfig = Field(default_factory=RagConfig)


def config_path() -> Path:
    env = os.environ.get(CONFIG_ENV, "").strip()
    if env:
        return Path(env)
    # The container convention: /config is a mounted volume.
    if Path("/config").is_dir():
        return Path("/config/config.json")
    return Path.cwd() / ".fass-config.json"


# FASS_RAG_* env equivalents of the rag block (PRD 6.6). An env var that is
# set (and, for numbers, parseable) overrides the config.json value; the
# override is resolved per request and never written back to the file.
_RAG_BOOL_TRUE = {"1", "true", "yes", "on"}
_RAG_BOOL_FALSE = {"0", "false", "no", "off"}


def _env_bool(name: str) -> bool | None:
    v = os.environ.get(name, "").strip().lower()
    if v in _RAG_BOOL_TRUE:
        return True
    if v in _RAG_BOOL_FALSE:
        return False
    return None  # unset or unparseable -> no override


def _env_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None  # unparseable -> no override (config value wins)


def _env_str(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def resolved_rag(cfg: FassConfig | None = None) -> RagConfig:
    """The rag block with FASS_RAG_* env overrides applied (PRD 6.6).

    The config model stays pure data (the UI's config tab reads/writes it);
    this is the only place the env equivalents are consulted. Unparseable
    numeric overrides are ignored rather than raising — a bad env var must
    not break generation (RAG is fail-open, PRD 4.4)."""
    base = (cfg if cfg is not None else load_config()).rag
    out = base.model_copy()
    if (v := _env_bool("FASS_RAG_ENABLED")) is not None:
        out.enabled = v
    if (v := _env_str("FASS_RAG_URL")) is not None:
        out.url = v
    if (v := _env_int("FASS_RAG_TIMEOUT_MS")) is not None:
        out.timeout_ms = v
    if (v := _env_int("FASS_RAG_TOP_K")) is not None:
        out.top_k = v
    if (v := _env_int("FASS_RAG_MAX_EVIDENCE_CHARS")) is not None:
        out.max_evidence_chars = v
    if (v := _env_str("FASS_RAG_CORPUS")) is not None:
        out.corpus = v
    if (v := _env_str("FASS_RAG_AUTH_TOKEN")) is not None:
        out.auth_token = v
    if (v := _env_bool("FASS_RAG_SEND_INSTRUCTION")) is not None:
        out.send_instruction = v
    return out


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
