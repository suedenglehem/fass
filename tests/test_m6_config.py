"""M6 deployment config: load/save round-trip, defaults, validation,
and the compose preview the Config tab shows."""

from __future__ import annotations

import json

import pytest

from fass.config import (
    DEFAULT_HOST_ADDRESS,
    FassConfig,
    LLMConfig,
    GPUConfig,
    RagConfig,
    StreamingConfig,
    VolumesConfig,
    config_path,
    load_config,
    resolved_rag,
    save_config,
)
from fass import dockerctl

RAG_ENV = ("FASS_RAG_ENABLED", "FASS_RAG_URL", "FASS_RAG_TIMEOUT_MS",
           "FASS_RAG_TOP_K", "FASS_RAG_MAX_EVIDENCE_CHARS", "FASS_RAG_CORPUS",
           "FASS_RAG_AUTH_TOKEN", "FASS_RAG_SEND_INSTRUCTION")


def _clear_rag_env(monkeypatch):
    for name in RAG_ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    cfg = FassConfig()
    assert cfg.llm.provider == "anthropic"
    assert cfg.streaming.enabled is False
    assert cfg.streaming.host_address == DEFAULT_HOST_ADDRESS
    assert cfg.gpu.enabled is False
    assert cfg.gpu.device == "all"
    assert cfg.volumes.input == ""


def test_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("FASS_CONFIG", str(p))
    assert config_path() == p
    # missing file -> defaults
    assert load_config() == FassConfig()

    cfg = FassConfig(
        llm=LLMConfig(provider="openai", host="172.17.0.1", port=8080,
                      model="qwen3.8-27b"),
        gpu=GPUConfig(enabled=True, device="0"),
        streaming=StreamingConfig(enabled=True,
                                  port_mappings=[
                                      {"name": "cam", "container_port": 5000,
                                       "host_port": 5000, "proto": "udp"}]),
        volumes=VolumesConfig(input="/d/in", output="/d/out", log="/d/log"),
    )
    save_config(cfg)
    assert p.exists()
    loaded = load_config()
    assert loaded == cfg
    # the file is plain JSON (hand-editable)
    on_disk = json.loads(p.read_text())
    assert on_disk["llm"]["model"] == "qwen3.8-27b"


def test_invalid_file_falls_back_to_defaults(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{not json")
    monkeypatch.setenv("FASS_CONFIG", str(p))
    assert load_config() == FassConfig()


def test_unknown_field_rejected(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text('{"llm": {"provider": "anthropic", "bogus": 1}}')
    monkeypatch.setenv("FASS_CONFIG", str(p))
    # extra=forbid on the whole tree: a stray key is a validation error,
    # and the loader degrades to defaults (the UI is the place to fix it)
    assert load_config() == FassConfig()


def test_bad_proto_rejected():
    with pytest.raises(ValueError):
        StreamingConfig(port_mappings=[{"container_port": 1, "host_port": 2,
                                        "proto": "icmp"}])


def test_gpu_device_pattern():
    GPUConfig(device="0")
    GPUConfig(device="0,1")
    with pytest.raises(ValueError):
        GPUConfig(device="card0")


def test_compose_preview():
    cfg = FassConfig(
        gpu=GPUConfig(enabled=True),
        streaming=StreamingConfig(port_mappings=[
            {"name": "cam", "container_port": 5000, "host_port": 5000,
             "proto": "udp"}]),
        volumes=VolumesConfig(input="/d/in", output="/d/out", log="/d/log"),
    )
    txt = dockerctl.compose_preview(cfg)
    assert '"8000:8000"' in txt
    assert '"5000:5000/udp"' in txt
    assert "/d/in:/input" in txt
    assert "driver: nvidia" in txt
    # GPU off -> no reservation block
    cfg2 = FassConfig(volumes=cfg.volumes)
    assert "nvidia" not in dockerctl.compose_preview(cfg2)


def test_unmanaged_binds_kept_managed_rebuilt():
    # regression: the first Apply recreated the container without the
    # docker socket bind (recreate rebuilt binds from the config only)
    cur = ["/d/in:/input:rw", "/d/out:/output:rw", "/d/log:/log:rw",
           "/d/cfg:/config:rw", "/var/run/docker.sock:/var/run/docker.sock:rw"]
    assert dockerctl._unmanaged_binds(cur) == [
        "/var/run/docker.sock:/var/run/docker.sock:rw"]
    # nothing extra -> empty
    assert dockerctl._unmanaged_binds(cur[:4]) == []
    # a user-added bind with a non-managed destination is carried over
    assert dockerctl._unmanaged_binds(["/d/x:/data"]) == ["/d/x:/data"]


def test_config_binds_parses_mode_suffix():
    # the engine reports binds as src:dest:rw — the mode suffix must not
    # break the /config lookup (regression: Apply said "no /config bind")
    cur = {"HostConfig": {"Binds": [
        "/d/in:/input:rw", "/d/out:/output:rw", "/d/log:/log:rw",
        "/d/cfg:/config:rw", "/var/run/docker.sock:/var/run/docker.sock:rw",
    ]}}
    assert dockerctl._config_binds(cur) == "/d/cfg"
    # no mode suffix also works
    cur2 = {"HostConfig": {"Binds": ["/d/cfg:/config"]}}
    assert dockerctl._config_binds(cur2) == "/d/cfg"


# ------------------------------------------------------------ M11.2 rag

def test_rag_defaults():
    rag = FassConfig().rag
    assert rag.enabled is False          # opt-in: rules stay the default
    assert rag.url == "http://fass-rag:8100"
    assert rag.timeout_ms == 800
    assert rag.top_k == 6
    assert rag.max_evidence_chars == 4000
    assert rag.corpus == "auto"
    assert rag.auth_token is None
    assert rag.send_instruction is True


def test_rag_validation():
    with pytest.raises(ValueError):
        RagConfig(top_k=0)
    with pytest.raises(ValueError):
        RagConfig(top_k=9)                # server cap: 8 (PRD 5.4)
    with pytest.raises(ValueError):
        RagConfig(timeout_ms=0)
    with pytest.raises(ValueError):
        RagConfig(extra=1)                # extra=forbid like the siblings


def test_rag_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("FASS_CONFIG", str(p))
    _clear_rag_env(monkeypatch)
    cfg = FassConfig(rag=RagConfig(enabled=True, url="http://172.17.0.1:8100",
                                   auth_token="tok"))
    save_config(cfg)
    assert load_config() == cfg
    assert json.loads(p.read_text())["rag"]["enabled"] is True


def test_resolved_rag_env_overrides(monkeypatch):
    _clear_rag_env(monkeypatch)
    base = FassConfig(rag=RagConfig(enabled=False, url="http://cfg:8100",
                                    timeout_ms=800, top_k=6,
                                    max_evidence_chars=4000, corpus="auto",
                                    send_instruction=True))
    # no env -> the config values
    assert resolved_rag(base) == base.rag
    # each env equivalent overrides its field
    monkeypatch.setenv("FASS_RAG_ENABLED", "1")
    monkeypatch.setenv("FASS_RAG_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("FASS_RAG_TIMEOUT_MS", "500")
    monkeypatch.setenv("FASS_RAG_TOP_K", "4")
    monkeypatch.setenv("FASS_RAG_MAX_EVIDENCE_CHARS", "2000")
    monkeypatch.setenv("FASS_RAG_CORPUS", "ffmpeg-9.0.1")
    monkeypatch.setenv("FASS_RAG_AUTH_TOKEN", "s3cret")
    monkeypatch.setenv("FASS_RAG_SEND_INSTRUCTION", "false")
    got = resolved_rag(base)
    assert got.enabled is True
    assert got.url == "http://127.0.0.1:8100"
    assert got.timeout_ms == 500
    assert got.top_k == 4
    assert got.max_evidence_chars == 2000
    assert got.corpus == "ffmpeg-9.0.1"
    assert got.auth_token == "s3cret"
    assert got.send_instruction is False
    # the config model itself is untouched (env never writes back)
    assert base.rag.enabled is False
    assert resolved_rag(base).model_dump() != base.rag.model_dump()


def test_resolved_rag_env_variants(monkeypatch):
    _clear_rag_env(monkeypatch)
    base = FassConfig()
    for v in ("0", "false", "no", "off"):
        monkeypatch.setenv("FASS_RAG_ENABLED", v)
        assert resolved_rag(base).enabled is False, v
    for v in ("1", "TRUE", "yes", "on"):
        monkeypatch.setenv("FASS_RAG_ENABLED", v)
        assert resolved_rag(base).enabled is True, v
    monkeypatch.delenv("FASS_RAG_ENABLED")
    assert resolved_rag(base).enabled is False


def test_resolved_rag_bad_env_ignored(monkeypatch):
    # an unparseable numeric override must not raise (fail-open, PRD 4.4)
    _clear_rag_env(monkeypatch)
    monkeypatch.setenv("FASS_RAG_TOP_K", "lots")
    monkeypatch.setenv("FASS_RAG_TIMEOUT_MS", "800ms")
    got = resolved_rag(FassConfig())
    assert got.top_k == 6
    assert got.timeout_ms == 800


def test_resolved_rag_reads_file_when_no_cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("FASS_CONFIG", str(p))
    _clear_rag_env(monkeypatch)
    p.write_text(json.dumps({"rag": {"enabled": True, "top_k": 3}}))
    got = resolved_rag()
    assert got.enabled is True
    assert got.top_k == 3
    assert got.url == "http://fass-rag:8100"  # untouched defaults stay
