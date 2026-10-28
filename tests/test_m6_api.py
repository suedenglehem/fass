"""M6 API surface: config endpoints, the compose preview, the execution
gate on /v1/runs, the LLM test endpoint, and /v1/env reporting."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fass.api import app

client = TestClient(app)


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("FASS_CONFIG", str(p))
    monkeypatch.delenv("FASS_ALLOW_EXECUTE", raising=False)
    monkeypatch.delenv("FASS_DOCKER_MANAGE", raising=False)
    return p


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    for name, d in (("FASS_RUN_DIR", "runs"), ("FASS_INPUT_DIR", "in"),
                    ("FASS_OUTPUT_DIR", "out")):
        (tmp_path / d).mkdir(exist_ok=True)
        monkeypatch.setenv(name, str(tmp_path / d))
    return tmp_path


# ------------------------------------------------------------- config

def test_get_config_defaults(cfg_env):
    r = client.get("/v1/config")
    assert r.status_code == 200
    d = r.json()
    assert d["config"]["llm"]["provider"] == "anthropic"
    assert d["config"]["llm"]["timeout"] == 10
    assert d["config"]["streaming"]["enabled"] is False
    assert "compose" in d
    assert d["docker"]["manage"] is False


def test_put_config_roundtrip_and_compose(cfg_env):
    body = {
        "llm": {"provider": "openai", "host": "172.17.0.1", "port": 8080,
                "model": "qwen3.8-27b"},
        "gpu": {"enabled": True, "device": "all"},
        "streaming": {"enabled": True, "host_address": "172.17.0.1",
                      "port_mappings": [
                          {"name": "cam", "container_port": 5000,
                           "host_port": 5000, "proto": "udp"}]},
        "volumes": {"input": "/d/in", "output": "/d/out", "log": "/d/log"},
    }
    r = client.put("/v1/config", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["config"]["llm"]["model"] == "qwen3.8-27b"
    assert "5000:5000/udp" in d["compose"]
    assert "driver: nvidia" in d["compose"]
    # persisted: a fresh GET sees it
    r2 = client.get("/v1/config")
    assert r2.json()["config"]["llm"]["host"] == "172.17.0.1"


def test_put_config_rejects_unknown_field(cfg_env):
    r = client.put("/v1/config",
                   json={"llm": {"provider": "anthropic", "bogus": 1}})
    assert r.status_code == 422


def test_apply_requires_worker(cfg_env):
    # no FASS_DOCKER_WORKER -> no sidecar to do the swap -> 409
    r = client.post("/v1/config/apply")
    assert r.status_code == 409
    assert "fass-worker" in r.json()["detail"]


def test_apply_worker_unreachable_502(cfg_env, monkeypatch):
    monkeypatch.setenv("FASS_DOCKER_WORKER", "127.0.0.1:1")
    r = client.post("/v1/config/apply")
    assert r.status_code == 502
    assert "not reachable" in r.json()["detail"]


def test_config_reports_worker_flag(cfg_env, monkeypatch):
    assert client.get("/v1/config").json()["docker"]["worker"] is False
    monkeypatch.setenv("FASS_DOCKER_WORKER", "fass-worker:8001")
    assert client.get("/v1/config").json()["docker"]["worker"] is True


def test_apply_result_404_without_state(cfg_env, tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_APPLY_STATE", str(tmp_path / "last-apply.json"))
    assert client.get("/v1/config/apply-result").status_code == 404


def test_apply_result_returns_state(cfg_env, tmp_path, monkeypatch):
    p = tmp_path / "last-apply.json"
    p.write_text('{"status": "applied", "new_container": "abc123"}\n')
    monkeypatch.setenv("FASS_APPLY_STATE", str(p))
    r = client.get("/v1/config/apply-result")
    assert r.status_code == 200
    assert r.json()["new_container"] == "abc123"


def test_llm_test_unconfigured(cfg_env):
    r = client.post("/v1/config/llm-test", json={
        "use_unsaved": True,
        "llm": {"provider": "openai", "host": "", "port": 8080,
                "model": "m"}})
    assert r.status_code == 422


def test_put_config_llm_timeout_survives(cfg_env):
    # the Config tab owns the timeout: PUT persists it, a fresh GET reads it back
    body = {"llm": {"provider": "openai", "host": "172.17.0.1", "port": 8080,
                    "model": "m", "timeout": 25}}
    r = client.put("/v1/config", json=body)
    assert r.status_code == 200
    assert r.json()["config"]["llm"]["timeout"] == 25
    assert client.get("/v1/config").json()["config"]["llm"]["timeout"] == 25


def test_put_config_llm_timeout_bounds(cfg_env):
    assert client.put("/v1/config", json={
        "llm": {"provider": "openai", "timeout": 0}}).status_code == 422
    assert client.put("/v1/config", json={
        "llm": {"provider": "openai", "timeout": 301}}).status_code == 422


def test_llm_test_applies_the_timeout_and_reports_a_hang_cleanly(cfg_env, monkeypatch):
    # the test button runs the request under the configured budget, and a
    # timed-out endpoint comes back as a readable 400 (no bare hang)
    import socket

    from fass.nlu import llm as llm_mod

    seen = []

    def fake(req, timeout=None):
        seen.append(timeout)
        raise socket.timeout("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake)
    r = client.post("/v1/config/llm-test", json={
        "use_unsaved": True,
        "llm": {"provider": "openai", "host": "127.0.0.1", "port": 8080,
                "model": "m", "timeout": 2}})
    assert seen == [2]
    assert r.status_code == 400
    assert "unreachable" in r.json()["detail"]


# ------------------------------------------------------------- runs

def test_runs_disabled_403(cfg_env, run_env):
    r = client.post("/v1/runs",
                    json={"arguments": ["ffmpeg", "-version"]})
    assert r.status_code == 403


def test_runs_invalid_argv_422(cfg_env, run_env, monkeypatch):
    monkeypatch.setenv("FASS_ALLOW_EXECUTE", "1")
    r = client.post("/v1/runs", json={"arguments": ["rm", "-rf", "/"]})
    assert r.status_code == 422


def test_runs_list_and_404(run_env):
    r = client.get("/v1/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}
    assert client.get("/v1/runs/nope").status_code == 404
    assert client.get("/v1/runs/nope/log").status_code == 404


def test_media_missing_404(run_env):
    assert client.get("/media/output/nope.mp4").status_code == 404


def _fake_run(run_env, run_id, output):
    import json
    rec = {"id": run_id, "argv": ["ffmpeg"], "command": "ffmpeg",
           "started": "2026-01-01T00:00:00", "status": "success",
           "exit_code": 0, "output": output, "notes": []}
    (run_env / "runs" / f"{run_id}.json").write_text(json.dumps(rec))


def test_run_output_media_serves_input_folder_file(run_env):
    # a chained processing: the run's output landed in the input folder
    p = run_env / "in" / "step1.mp4"
    p.write_bytes(b"0")
    _fake_run(run_env, "20260101-000000-aaaaaa", str(p))
    r = client.get("/media/run/20260101-000000-aaaaaa/output")
    assert r.status_code == 200
    assert r.content == b"0"


def test_run_output_media_serves_output_volume_file(run_env):
    p = run_env / "out" / "step2.mp4"
    p.write_bytes(b"1")
    _fake_run(run_env, "20260101-000000-bbbbbb", str(p))
    r = client.get("/media/run/20260101-000000-bbbbbb/output")
    assert r.status_code == 200
    assert r.content == b"1"


def test_run_output_media_guarded(run_env):
    outside = run_env / "outside.mp4"
    outside.write_bytes(b"2")
    _fake_run(run_env, "20260101-000000-cccccc", str(outside))
    assert client.get("/media/run/20260101-000000-cccccc/output").status_code == 404
    assert client.get("/media/run/nope/output").status_code == 404


# ------------------------------------------------------------- env

def test_env_reports_execution(monkeypatch):
    monkeypatch.delenv("FASS_ALLOW_EXECUTE", raising=False)
    assert client.get("/v1/env").json()["execution"] is False
    monkeypatch.setenv("FASS_ALLOW_EXECUTE", "1")
    d = client.get("/v1/env").json()
    assert d["execution"] is True
    assert "gpu" in d


def test_ui_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Config" in r.text
