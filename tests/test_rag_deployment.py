"""Deployment tests for the rag service (joint PRD 5.6 + 6.7; M13 default).

Three surfaces:

- docker/compose.yaml — checked as TEXT (pyyaml is not installed in the
  venv, so the assertions are literal-string checks on the compose file,
  which is also what the PRD block specifies: exact lines, exact shape).
  M13: fass-rag is a DEFAULT service (no profile gate, no /data volume —
  the index is baked into the image) and the fass service enables the RAG
  client via FASS_RAG_ENABLED=1 + FASS_RAG_URL. The old opt-in overlay
  docker/compose.rag.yaml is retired.
- docker/Dockerfile.rag — the corpus bake: pinned source copied to /data
  and ingested at image-build time.
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


# --- compose default service (PRD 6.7 block, verbatim, M13 default) --------

def _compose_text() -> str:
    return COMPOSE.read_text()


def _rag_block() -> str:
    """The fass-rag service block: from its key to end of file (it is last)."""
    text = _compose_text()
    assert "  fass-rag:" in text, "no fass-rag service in compose.yaml"
    return text[text.index("  fass-rag:"):]


def test_compose_rag_overlay_retired():
    """M13: the opt-in overlay is gone — rag ships in the default compose."""
    assert not OVERLAY.exists()


def test_compose_yaml_has_rag_service_by_default():
    text = _compose_text()
    assert "fass-rag:" in text
    assert "build: { context: .., dockerfile: docker/Dockerfile.rag }" in text
    assert "image: fass-rag:latest" in text
    assert "- FASS_RAG_DATA=/data" in text
    assert "- FASS_RAG_BIND=0.0.0.0:8100" in text
    assert 'healthcheck: { test: ["CMD", "python", "-m", "rag.health"], interval: 30s }' in text
    assert "restart: unless-stopped" in text


def test_compose_yaml_rag_not_profile_gated():
    """The rag service is default: no profile gate anywhere in the file."""
    assert "profiles" not in _compose_text()


def test_compose_yaml_rag_has_no_data_volume():
    """The index is baked into the image — no host volume mounts /data."""
    assert ":/data" not in _compose_text()


def test_compose_yaml_healthcheck_names_health_module():
    health_lines = [ln for ln in _rag_block().splitlines() if "healthcheck" in ln]
    assert health_lines, "no healthcheck line in the fass-rag service"
    assert all("rag.health" in ln for ln in health_lines)


@pytest.mark.parametrize("forbidden", [
    "privileged", "nvidia", "gpu", "GPU", "device_ids", "docker.sock", "deploy",
])
def test_rag_block_has_no_gpu_socket_privileged(forbidden):
    assert forbidden not in _rag_block()


def test_compose_yaml_fass_env_enables_rag():
    """The fass service is configured to USE the rag service by default."""
    text = _compose_text()
    assert "- FASS_RAG_ENABLED=1" in text
    assert "- FASS_RAG_URL=http://fass-rag:8100" in text


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
    assert "COPY rag/*.py /work/rag/" in text
    assert "COPY fass/" not in text


def test_dockerfile_rag_bakes_corpus_into_image():
    """M13: the pinned corpus is copied to /data and ingested at build time,
    so the image serves with NO volume and NO first-boot build."""
    text = (REPO_ROOT / "docker" / "Dockerfile.rag").read_text()
    for corpus in ("ffmpeg-9.0.1", "ffmpeg-6.1.1"):
        assert f"COPY rag/corpus/{corpus}/manifest.json /data/{corpus}/" in text
        assert f"COPY rag/corpus/{corpus}/docs /data/{corpus}/docs" in text
        assert f"COPY rag/corpus/{corpus}/build-facts /data/{corpus}/build-facts" in text
        assert f"python -m rag.ingest --corpus-dir /data/{corpus}" in text
    # The ingest runs as the non-root rag user, after USER rag.
    assert text.index("USER rag") < text.index("python -m rag.ingest")
    # The gitignored scratch and host-built generations never enter the image:
    # every COPY line names source files only (no bare "COPY rag/", no _build,
    # no chunks).
    copy_lines = [ln for ln in text.splitlines() if ln.strip().startswith("COPY")]
    assert copy_lines
    for ln in copy_lines:
        assert "_build" not in ln
        assert "chunks" not in ln
        assert ln.strip() != "COPY rag/ /work/rag/"


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
