"""Deployment tests for the rag service (joint PRD 5.6 + 6.7).

Three surfaces:

- docker/compose.rag.yaml — checked as TEXT (pyyaml is not installed in the
  venv, so the assertions are literal-string checks on the overlay, which is
  also what the PRD block specifies: exact lines, exact shape).
- rag/server.py env surface — FASS_RAG_DATA / FASS_RAG_BIND, with
  FASS_RAG_DATA_DIR / FASS_RAG_PORT kept as backward-compatible fallbacks.
- rag/health.py — the compose healthcheck, exercised as a subprocess
  (``python -m rag.health``) exactly the way the container runs it.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OVERLAY = REPO_ROOT / "docker" / "compose.rag.yaml"
COMPOSE = REPO_ROOT / "docker" / "compose.yaml"


# --- compose overlay (PRD 6.7 block, verbatim) ------------------------------

def _overlay_text() -> str:
    return OVERLAY.read_text()


def test_overlay_exists_and_is_a_service_overlay():
    text = _overlay_text()
    assert text.startswith("#")
    assert "\nservices:" in text
    assert "fass-rag:" in text


@pytest.mark.parametrize("line", [
    'profiles: ["rag"]',
    "build: { context: .., dockerfile: docker/Dockerfile.rag }",
    "image: fass-rag:latest",
    "- /dd2/andrei/fass/docker-data/rag:/data",
    "- FASS_RAG_DATA=/data",
    "- FASS_RAG_BIND=0.0.0.0:8100",
    'healthcheck: { test: ["CMD", "python", "-m", "rag.health"], interval: 30s }',
    "restart: unless-stopped",
])
def test_overlay_contains_contract_line(line):
    assert line in _overlay_text()


def test_overlay_healthcheck_names_health_module():
    text = _overlay_text()
    health_lines = [ln for ln in text.splitlines() if "healthcheck" in ln]
    assert health_lines, "no healthcheck line in overlay"
    assert all("rag.health" in ln for ln in health_lines)


@pytest.mark.parametrize("forbidden", [
    "privileged", "nvidia", "gpu", "GPU", "docker.sock", "device_ids",
])
def test_overlay_has_no_gpu_socket_privileged(forbidden):
    assert forbidden not in _overlay_text()


def test_compose_yaml_untouched():
    """The default compose file must not have gained the rag service."""
    text = COMPOSE.read_text()
    assert "fass-rag" not in text
    assert "Dockerfile.rag" not in text


def test_dockerfile_rag_is_cpu_only_non_root_server():
    text = (REPO_ROOT / "docker" / "Dockerfile.rag").read_text()
    assert "FROM python:3.12-slim" in text
    assert "useradd -r -u 10001 rag" in text
    assert "USER rag" in text
    assert 'CMD ["python", "-m", "rag.server"]' in text
    assert "EXPOSE 8100" in text
    for forbidden in ("nvidia", "device_ids", "docker.sock", "--privileged",
                      "--gpus", "runtime"):
        assert forbidden not in text
    # The server needs rag/ + fastapi/uvicorn only — not the product code.
    assert "COPY rag/ /work/rag/" in text
    assert "COPY fass/" not in text


# --- server env surface (PRD 5.6) ------------------------------------------

def test_parse_bind_valid_specs():
    from rag.server import parse_bind
    assert parse_bind("0.0.0.0:8100") == ("0.0.0.0", 8100)
    assert parse_bind("127.0.0.1:8100") == ("127.0.0.1", 8100)
    assert parse_bind("fass-rag:8100") == ("fass-rag", 8100)
    assert parse_bind("[::1]:8100") == ("[::1]", 8100)


@pytest.mark.parametrize("spec", ["8100", "a:b:c", "", ":8100", "127.0.0.1:",
                                  "127.0.0.1:abc", "127.0.0.1:99999"])
def test_parse_bind_rejects_malformed(spec):
    from rag.server import parse_bind
    with pytest.raises(ValueError):
        parse_bind(spec)


def test_data_dir_prefers_fass_rag_data(monkeypatch):
    monkeypatch.setenv("FASS_RAG_DATA", "/data")
    monkeypatch.setenv("FASS_RAG_DATA_DIR", "/legacy")
    import rag.server
    importlib.reload(rag.server)
    assert str(rag.server.DATA_DIR) == "/data"


def test_data_dir_falls_back_to_legacy_dir(monkeypatch):
    monkeypatch.delenv("FASS_RAG_DATA", raising=False)
    monkeypatch.setenv("FASS_RAG_DATA_DIR", "/legacy")
    import rag.server
    importlib.reload(rag.server)
    assert str(rag.server.DATA_DIR) == "/legacy"


def test_data_dir_default(monkeypatch):
    monkeypatch.delenv("FASS_RAG_DATA", raising=False)
    monkeypatch.delenv("FASS_RAG_DATA_DIR", raising=False)
    import rag.server
    importlib.reload(rag.server)
    assert str(rag.server.DATA_DIR) == "rag/corpus"


def test_bind_defaults_and_port_override():
    from rag.server import DEFAULT_BIND, parse_bind
    assert parse_bind(DEFAULT_BIND) == ("127.0.0.1", 8100)
    # FASS_RAG_PORT overrides only the port part of the bind spec.
    host, port = parse_bind("0.0.0.0:8100")
    assert (host, int(os.environ.get("FASS_RAG_PORT", str(port)))) == ("0.0.0.0", 8100)


# --- health module, as the container healthcheck runs it -------------------

def _run_health(env_extra: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("FASS_RAG_DATA", None)
    env.pop("FASS_RAG_DATA_DIR", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "rag.health"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)


def test_health_ok_on_repo_corpus():
    proc = _run_health({"FASS_RAG_DATA": "rag/corpus"})
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.strip().splitlines()) == 1
    assert "rag-data OK" in proc.stdout
    assert "2 corpora" in proc.stdout
    assert proc.stderr == ""


def test_health_default_data_dir():
    proc = _run_health({})
    assert proc.returncode == 0, proc.stderr
    assert "rag-data OK" in proc.stdout


def test_health_empty_dir(tmp_path):
    proc = _run_health({"FASS_RAG_DATA": str(tmp_path)})
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr.strip()


def test_health_missing_dir(tmp_path):
    proc = _run_health({"FASS_RAG_DATA": str(tmp_path / "nope")})
    assert proc.returncode == 1
    assert "MISSING" in proc.stderr


def test_health_never_imports_server():
    src = (REPO_ROOT / "rag" / "health.py").read_text()
    assert "import rag.server" not in src
    assert "from rag.server" not in src
    for forbidden in ("uvicorn", "fastapi", "requests", "socket"):
        assert f"import {forbidden}" not in src
