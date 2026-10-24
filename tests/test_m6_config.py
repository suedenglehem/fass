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
    StreamingConfig,
    VolumesConfig,
    config_path,
    load_config,
    save_config,
)
from fass import dockerctl


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
