"""M6 fass-worker sidecar: the /swap endpoint that performs the container
recreate on behalf of the fass container (self-swap is impossible — the
process answering Apply dies when its own container stops). The worker
acks immediately, swaps in a background thread, and records the outcome
in the shared state file the fass UI polls."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from fass import dockerctl

VALID_CONFIG = {
    "llm": {"provider": "anthropic"},
    "gpu": {"enabled": False},
    "streaming": {"enabled": False, "host_address": "172.17.0.1",
                  "port_mappings": []},
    "volumes": {"input": "/d/in", "output": "/d/out", "log": "/d/log"},
}


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    (tmp_path / "sock").touch()
    monkeypatch.setenv("FASS_DOCKER_MANAGE", "1")
    monkeypatch.setenv("FASS_DOCKER_SOCKET", str(tmp_path / "sock"))
    monkeypatch.setenv("FASS_APPLY_STATE", str(tmp_path / "last-apply.json"))
    return tmp_path / "last-apply.json"


@pytest.fixture
def fake_recreate(monkeypatch):
    calls = []

    def fake(cfg):
        calls.append(cfg)
        return {"applied": True, "new_container": "new123",
                "old_container": "old123",
                "ports": {"8000/tcp": "8000"},
                "volumes": ["/d/in:/input"], "gpu": False}

    monkeypatch.setattr(dockerctl, "recreate", fake)
    return calls


def _poll_state(path, deadline_s=5.0):
    deadline = time.time() + deadline_s
    state = None
    while time.time() < deadline:
        try:
            state = json.loads(path.read_text())
            if state.get("status") in ("applied", "failed"):
                return state
        except (OSError, json.JSONDecodeError):
            state = None
        time.sleep(0.05)
    return state


def test_swap_recreates_and_records_state(worker_env, fake_recreate):
    client = TestClient(dockerctl.worker_app())
    r = client.post("/swap", json={"config": VALID_CONFIG})
    # the ack goes out BEFORE the swap starts, so fass (and the UI)
    # can return it before its own container is stopped
    assert r.status_code == 200
    assert r.json() == {"status": "recreating"}

    state = _poll_state(worker_env)
    assert state is not None, "the swap thread never recorded a result"
    assert state["status"] == "applied"
    assert state["new_container"] == "new123"
    assert state["started"]  # the pre-swap timestamp survives the merge
    assert len(fake_recreate) == 1  # recreate ran exactly once


def test_swap_rejects_invalid_config(worker_env, fake_recreate):
    client = TestClient(dockerctl.worker_app())
    r = client.post("/swap", json={"config": {"bogus": 1}})
    assert r.status_code == 422
    assert not fake_recreate


def test_swap_without_docker_manage_409(tmp_path, monkeypatch, fake_recreate):
    (tmp_path / "sock").touch()
    monkeypatch.setenv("FASS_DOCKER_SOCKET", str(tmp_path / "sock"))
    monkeypatch.delenv("FASS_DOCKER_MANAGE", raising=False)
    monkeypatch.setenv("FASS_APPLY_STATE", str(tmp_path / "last-apply.json"))
    client = TestClient(dockerctl.worker_app())
    r = client.post("/swap", json={"config": VALID_CONFIG})
    assert r.status_code == 409
    assert "no docker control" in r.json()["detail"]
    assert not fake_recreate


def test_swap_records_failure(worker_env, monkeypatch):
    def boom(cfg):
        raise dockerctl.DockerError("engine exploded")

    monkeypatch.setattr(dockerctl, "recreate", boom)
    client = TestClient(dockerctl.worker_app())
    assert client.post("/swap", json={"config": VALID_CONFIG}).status_code == 200
    state = _poll_state(worker_env)
    assert state["status"] == "failed"
    assert "engine exploded" in state["error"]


def test_worker_healthz(worker_env):
    client = TestClient(dockerctl.worker_app())
    assert client.get("/healthz").json() == {"ok": True}
