"""M14 GPU policy: GPU-involved argv detection, the presence probe, and
execution refusal on a GPU-less machine. Generation is never affected —
the gate lives in start_run/start_run_sequence, not in validate_argv."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import subprocess

import pytest
from fastapi.testclient import TestClient

from fass import execute, gpu
from fass.api import app

FFMPEG = shutil.which("ffmpeg")
client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv(execute.INPUT_DIR_ENV, str(tmp_path / "in"))
    monkeypatch.setenv(execute.OUTPUT_DIR_ENV, str(tmp_path / "out"))
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(tmp_path / "runs"))
    for d in ("in", "out", "runs"):
        (tmp_path / d).mkdir(exist_ok=True)
    monkeypatch.delenv(execute.ALLOW_EXECUTE_ENV, raising=False)
    monkeypatch.delenv(gpu.PROBE_ENV, raising=False)
    gpu.set_gpu_present(None)  # re-probe per test
    yield
    gpu.set_gpu_present(None)


def _nvenc_argv() -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "clip.mp4",
            "-c:v", "h264_nvenc", "-b:v", "2000k", "-c:a", "aac", "out.mp4"]


def _cpu_argv() -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "clip.mp4",
            "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "out.mp4"]


# ------------------------------------------------------------ detection

@pytest.mark.parametrize("codec", ["h264_nvenc", "hevc_nvenc"])
def test_nvenc_encoder_detected(codec):
    argv = ["ffmpeg", "-i", "a.mp4", "-c:v", codec, "o.mp4"]
    assert gpu.gpu_involved(argv) == [codec]


def test_cuvid_decoder_detected():
    argv = ["ffmpeg", "-c:v", "h264_cuvid", "-i", "a.mp4", "o.mp4"]
    assert "h264_cuvid" in gpu.gpu_involved(argv)


def test_hwaccel_value_detected():
    argv = ["ffmpeg", "-hwaccel", "cuda", "-i", "a.mp4", "o.mp4"]
    assert "-hwaccel cuda" in gpu.gpu_involved(argv)


def test_hwaccel_output_format_detected():
    argv = ["ffmpeg", "-hwaccel_output_format", "cuda", "-i", "a.mp4", "o.mp4"]
    assert "-hwaccel_output_format cuda" in gpu.gpu_involved(argv)


def test_cpu_hwaccel_value_not_detected():
    argv = ["ffmpeg", "-hwaccel", "v4l2m2m", "-i", "a.mp4", "o.mp4"]
    assert gpu.gpu_involved(argv) == []


@pytest.mark.parametrize("graph,expected", [
    ("scale_cuda=w=1280:h=720:format=nv12", "scale_cuda"),
    ("yadif_cuda", "yadif_cuda"),
    ("hwupload,overlay_cuda=x=0:y=0", "overlay_cuda"),
    ("[0:v]hwupload[gv]", "hwupload"),
    ("hwmap=derive_cpu=1", "hwmap"),
    ("hwdownload", "hwdownload"),
])
def test_filter_graph_gpu_tokens_detected(graph, expected):
    argv = ["ffmpeg", "-i", "a.mp4", "-vf", graph, "o.mp4"]
    assert expected in gpu.gpu_involved(argv)


def test_registry_marks_the_gpu_filters():
    # the detection set is registry-driven: every "gpu": true filter and
    # the nvenc coders are in it
    marked = {n for dom in gpu.REGISTRY["filters"].values()
              if isinstance(dom, dict) for n, s in dom.items()
              if isinstance(s, dict) and s.get("gpu")}
    assert {"blackdetect_vulkan", "scale_cuda", "yadif_cuda"} <= marked
    argv = ["ffmpeg", "-i", "a.mp4", "-vf", "blackdetect_vulkan", "o.mp4"]
    assert "blackdetect_vulkan" in gpu.gpu_involved(argv)


def test_cpu_argv_not_gpu_involved():
    assert gpu.gpu_involved(_cpu_argv()) == []


def test_filename_lookalike_not_detected():
    # an output NAMED like a GPU filter must not trip the detector
    argv = ["ffmpeg", "-i", "a.mp4", "scale_cuda.mp4"]
    assert gpu.gpu_involved(argv) == []


# ------------------------------------------------------------ probe

def test_probe_env_override_forces_absent(monkeypatch):
    monkeypatch.setenv(gpu.PROBE_ENV, "0")
    gpu.set_gpu_present(None)
    assert gpu.gpu_present() is False


def test_probe_env_override_forces_present(monkeypatch):
    monkeypatch.setenv(gpu.PROBE_ENV, "1")
    gpu.set_gpu_present(None)
    assert gpu.gpu_present() is True


# ------------------------------------------------------------ gate

def test_gate_refuses_gpu_argv_without_gpu():
    gpu.set_gpu_present(False)
    msg = gpu.gate(_nvenc_argv())
    assert msg is not None and "no GPU" in msg and "h264_nvenc" in msg


def test_gate_allows_gpu_argv_with_gpu():
    gpu.set_gpu_present(True)
    assert gpu.gate(_nvenc_argv()) is None


def test_gate_allows_cpu_argv_without_gpu():
    gpu.set_gpu_present(False)
    assert gpu.gate(_cpu_argv()) is None


# ------------------------------------------------------------ execution

def test_start_run_refuses_gpu_argv_without_gpu(monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    gpu.set_gpu_present(False)
    with pytest.raises(PermissionError, match="no GPU"):
        execute.start_run(_nvenc_argv())


def test_start_run_allows_gpu_argv_with_gpu(monkeypatch):
    # gate passes; ffmpeg itself is not on PATH in CI-less runs, so only
    # assert the PermissionError is gone (ValueError/execution is later)
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    gpu.set_gpu_present(True)
    try:
        execute.start_run(_nvenc_argv())
    except PermissionError as e:  # must not happen
        pytest.fail(f"gate refused with GPU present: {e}")
    except (ValueError, FileNotFoundError):
        pass  # input file missing / ffmpeg absent — past the gate


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_start_run_cpu_run_unaffected(monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    gpu.set_gpu_present(False)  # CPU runs never care about the probe
    out = Path(os.environ[execute.OUTPUT_DIR_ENV])
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
            "-f", "mp4", str(out / "m14.mp4")]
    run = execute.start_run(argv)
    assert run.status == "running"


def test_start_run_sequence_refuses_gpu_step(monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    gpu.set_gpu_present(False)
    detect = ["ffmpeg", "-i", "clip.mp4", "-vf", "vidstabdetect", "-f", "null", "-"]
    with pytest.raises(PermissionError, match="step 2"):
        execute.start_run_sequence([detect, _nvenc_argv()])


# ------------------------------------------------------------ generation

def test_generation_unaffected_by_gpu_absence(monkeypatch):
    # validate_argv (the generation gate) accepts the nvenc argv even
    # with no GPU — only execution refuses
    gpu.set_gpu_present(False)
    assert execute.validate_argv(_nvenc_argv()) == []


def test_api_run_403_without_gpu(monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    gpu.set_gpu_present(False)
    r = client.post("/v1/runs", json={"arguments": _nvenc_argv()})
    assert r.status_code == 403
    assert "no GPU" in r.json()["detail"]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_api_commands_generation_ok_without_gpu(monkeypatch):
    # generation is machine-independent: the nvenc argv is produced even
    # with no GPU (the refusal is at execution only)
    gpu.set_gpu_present(False)
    clip = Path(os.environ[execute.INPUT_DIR_ENV]) / "clip.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                   check=True)
    r = client.post("/v1/commands", json={
        "instruction": "scale clip.mp4 to 720p using the gpu",
        "assets": {"main": "clip.mp4"}, "adapter": "rules"})
    assert r.status_code == 200
    # "using the gpu" routes to the scale_cuda filter (registry alias) —
    # a GPU-involved argv, generated fine with no GPU on this machine
    assert "scale_cuda" in " ".join(r.json()["arguments"])
