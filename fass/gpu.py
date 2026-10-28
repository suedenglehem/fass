"""GPU presence probe + GPU-involved argv detection (M14).

The install script (docker/install.sh) probes the machine and records the
answer as FASS_GPU=0/1 in docker/.env, which compose passes to the
container as FASS_GPU_PROBE. Without that override (host/python
deployments) the runtime probes nvidia-smi.

Policy: a machine without a GPU may still GENERATE GPU-involved
commands (argv/CLI — generation is machine-independent), but executing
them is refused: start_run checks gpu_involved(argv) against
gpu_present() and raises PermissionError, which the API surfaces as 403.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading

from fass.registry import REGISTRY

# install-time answer, passed through the environment: "0" forces absent,
# "1" forces present; unset = probe nvidia-smi
PROBE_ENV = "FASS_GPU_PROBE"

_lock = threading.Lock()
_present: bool | None = None


def set_gpu_present(value: bool | None) -> None:
    """Override the cached probe result (tests; None re-probes)."""
    global _present
    with _lock:
        _present = value


def _probe() -> bool:
    env = os.environ.get(PROBE_ENV, "").strip()
    if env in ("0", "1"):
        return env == "1"
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return False
    try:
        return subprocess.run([smi, "-L"], capture_output=True,
                              timeout=15).returncode == 0
    except OSError:
        return False


def gpu_present() -> bool:
    global _present
    if _present is None:
        with _lock:
            if _present is None:
                _present = _probe()
    return _present


# Registry-driven GPU names: filters marked "gpu": true plus the nvenc
# coders; the suffix rules catch the hardware names the registry does not
# enumerate (cuvid decoders, the *_npp/_opencl filter families on other
# builds). hw* are the hardware-frame helpers that only work on a GPU
# surface; the hwaccel flags take GPU device values.
_GPU_FILTERS = {
    name
    for dom in REGISTRY["filters"].values()
    if isinstance(dom, dict)
    for name, spec in dom.items()
    if isinstance(spec, dict) and spec.get("gpu")
} | {coder["encoder"] for coder in REGISTRY["gpu"]["coders"].values()}
_GPU_SUFFIXES = ("_nvenc", "_cuvid", "_cuda", "_npp", "_vulkan", "_opencl")
_HW_FILTERS = {"hwupload", "hwupload_cuda", "hwmap", "hwdownload"}
_HW_VALUE_FLAGS = {"-hwaccel", "-hwaccel_output_format", "-filter_hw_device"}
_HW_VALUES = {"cuda", "cuda_all", "nvdec", "vulkan", "opencl"}
# a filter graph token splits into graph parts; a part is name[:args]
_GRAPH_SPLIT_RX = re.compile(r"[,;\[\]\s]+")


def gpu_involved(argv: list[str]) -> list[str]:
    """The GPU-involved tokens an argv carries (empty = CPU-only)."""
    found: list[str] = []
    prev: str | None = None
    for tok in argv[1:]:
        if prev in _HW_VALUE_FLAGS and tok in _HW_VALUES:
            found.append(f"{prev} {tok}")
        prev = tok
        if tok.endswith(_GPU_SUFFIXES) and tok not in _HW_VALUE_FLAGS:
            found.append(tok)
        for part in _GRAPH_SPLIT_RX.split(tok):
            base = part.split("=", 1)[0].split(":", 1)[0]
            if base in _GPU_FILTERS or base in _HW_FILTERS \
                    or base.endswith(_GPU_SUFFIXES):
                found.append(base)
    return sorted(set(found))


def gate(argv: list[str]) -> str | None:
    """Refusal message when argv needs a GPU and this machine has none;
    None when the argv may run here. Generation never calls this —
    only the execution entry points (start_run / start_run_sequence)."""
    toks = gpu_involved(argv)
    if toks and not gpu_present():
        return (
            "no GPU on this machine — refusing to execute the GPU-involved "
            f"command ({', '.join(toks)}); the command is generated and can "
            "be run on a machine with a GPU"
        )
    return None
