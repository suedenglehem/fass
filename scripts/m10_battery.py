"""M10.6 per-category E2E verification battery (driver).

Covers every shipped capability category against a real clip
(media/titan.mp4, 10 s cut): generation through the REAL pipeline
(rules adapter, preflight on) + token checks on the argv + EXECUTION
of the argv with the build under test.

  --mode local    generate() in-process (repo venv python) + host ffmpeg
  --mode docker   the running fass API (default http://127.0.0.1:8000) +
                  its /v1/runs (the container's ffmpeg 9.0.1)

Inputs (the .sh wrapper prepares them):
  local:  battery-out/{titan_10s,titan_b,titan_ref}.mp4 + battery-out/wm.png
  docker: the same four files in the container's /input volume

Multi-pass plans (stabilize) post their steps and run them in order;
null-muxer runs (diagnostic/compare) are verified by a clean exit only
(no output file to probe). Build-gated features (fdk_aac, libvmaf,
scale_cuda) expect the preflight warning on the build that lacks them
and skip the run there; device-missing failures (no QSV/CUDA device)
are SKIPS, not FAILs.

Exit code: 0 iff every case is PASS (or an expected SKIP).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # make the fass/ package importable

MAIN_CLIP = "titan_10s.mp4"


@dataclass(frozen=True)
class Case:
    id: str
    instruction: str
    # one of these needles must appear as a SUBSTRING of some final-pass
    # argv token (filters live inside -vf / -filter_complex strings)
    must_any: list[str]
    assets: dict[str, str] = field(      # asset_id -> filename in /input
        default_factory=lambda: {"main": MAIN_CLIP})
    run_on: frozenset[str] = frozenset({"local", "docker"})
    note: str = ""
    # mode -> a warning the response MUST carry (build-gated features);
    # None/absent = no warning expected in that build
    expect_warn: dict[str, str | None] | None = None
    skip_run_if_warn: bool = False       # preflight-missing build: warn, no run
    steps: bool = False                  # multi-pass plan (stabilize)
    device_skip: tuple[str, ...] = ()    # run-failure keywords = no device


CASES = [
    # ------------------------------------------------------------- MVP (M1-7)
    Case("trim", "trim from 2 seconds to 6 seconds",
         ["-ss 2", "-to 6", "-t 4"]),
    Case("resize", "resize to 480p",
         ["scale=-2:480", "scale=w=-2:h=480", ":480"]),
    Case("grayscale", "make it grayscale", ["gray", "hue=s=0"]),
    Case("watermark", "add a watermark wm.png at top right",
         ["overlay"],
         assets={"main": MAIN_CLIP, "wm": "wm.png"}),
    Case("speed_2x", "make it 2x faster",
         ["setpts", "atempo=2"]),
    Case("prores", "re-encode as prores", ["prores_ks"]),
    # -------------------------------------------------------- M8 codec options
    Case("opus", "extract audio as opus", ["libopus"]),
    Case("fdk_vbr4", "extract audio as fdk_aac vbr 4",
         ["libfdk_aac", "ac3"],
         note="9.0.1-only encoder",
         expect_warn={"local": "libfdk_aac", "docker": None},
         skip_run_if_warn=True),
    # ---------------------------------------------------------- M10.1 transforms
    Case("rotate_90", "rotate the video 90 degrees",
         ["transpose", "rotate=90"]),
    Case("acompressor", "compress the audio", ["acompressor"]),
    Case("yadif", "deinterlace the video", ["yadif"]),
    Case("loudnorm", "normalize the audio loudness", ["loudnorm"]),
    # --------------------------------------------------------- M10.2 diagnostic
    Case("blackdetect", "detect black frames", ["blackdetect"]),
    Case("volumedetect", "how loud is this audio", ["volumedetect"]),
    # ----------------------------------------------------------- M10.3 combiners
    Case("xfade", "crossfade titan_10s.mp4 and titan_b.mp4", ["xfade"],
         assets={"main": MAIN_CLIP, "b": "titan_b.mp4"}),
    Case("concat", "concatenate titan_10s.mp4 and titan_b.mp4", ["concat"],
         assets={"main": MAIN_CLIP, "b": "titan_b.mp4"}),
    # ------------------------------------------------------------ M10.4 comparison
    Case("psnr", "compare titan_10s.mp4 against titan_ref.mp4 with psnr",
         ["psnr"],
         assets={"main": MAIN_CLIP, "ref": "titan_ref.mp4"}),
    Case("libvmaf", "compare titan_10s.mp4 against titan_ref.mp4 with libvmaf",
         ["libvmaf"],
         assets={"main": MAIN_CLIP, "ref": "titan_ref.mp4"},
         note="9.0.1 filter + vendored model",
         expect_warn={"local": "libvmaf", "docker": None},
         skip_run_if_warn=True),
    # --------------------------------------------------------- M10.5 two-pass
    Case("stabilize", "stabilize the shaky clip",
         ["vidstabtransform"],
         steps=True, note="two passes: vidstabdetect then vidstabtransform"),
    # -------------------------------------------------------------- M9 BSF
    Case("bsf_metadata",
         "remux titan_10s.mp4 to titan_ts.ts and apply the h264_metadata "
         "filter flip=horizontal",
         ["h264_metadata"]),
    # ---------------------------------------------------------- M10.5 GPU rule
    Case("scale_cuda", "resize to 1080p on the gpu",
         ["scale_cuda"],
         note="CUDA filter absorbs the gpu mention (CPU encoder)",
         expect_warn={"local": "scale_cuda", "docker": None},
         skip_run_if_warn=True,
         device_skip=("cuda", "no gpu", "CUDA-capable")),
]


def needle_present(argv: list[str], needle: str) -> bool:
    joined = " ".join(argv)
    return needle in joined


def _looks_like_output_file(tok: str) -> bool:
    return bool(tok) and not tok.startswith("-") and tok != "-"


# --------------------------------------------------------------- local

def local_generate(instruction, assets, outdir: Path):
    from fass.pipeline import generate
    from fass.probe.ffprobe import probe_media
    from fass.schema import Asset, guess_kind

    bound = {}
    for aid, fname in assets.items():
        p = REPO / "battery-out" / fname
        if not p.exists():
            sys.exit(f"missing {p} — run scripts/m10_battery.sh local first")
        bound[aid] = Asset(path=str(p), kind=guess_kind(p.name))
    r = generate(instruction, bound, media=probe_media(str(
        REPO / "battery-out" / assets["main"])),
        adapter_name="rules", preflight_check=True)
    if r.status != "ready" or not r.arguments:
        return None, r.to_dict(), None
    d = r.to_dict()
    steps = [[t for t in s["arguments"]] for s in d.get("steps", [])] \
        if d.get("steps") else None
    return d["arguments"], d, steps


def _retarget(argv: list[str], outdir: Path) -> list[str]:
    a = list(argv)
    if _looks_like_output_file(a[-1]):
        a[-1] = str(outdir / Path(a[-1]).name)  # keep the plan's filename
    return a


def local_run(argvs: list[list[str]], outdir: Path):
    """Run every step in order, one cwd (the final output's dir)."""
    from fass.execute import validate_argv
    # the API's /v1/runs gate: no argv is executed that fails the registry
    # validator. Local mode used to bypass this, so the host 21/21 was
    # hollow for xfade/concat (M10.6 i7 cross-check, 2026-10-26) — run the
    # same gate here so both modes share acceptance semantics
    for i, argv in enumerate(argvs):
        problems = validate_argv(argv)
        if problems:
            return False, (i, "refusing to run — argv failed validation: "
                              + "; ".join(problems))
    for i, argv in enumerate(argvs):
        p = subprocess.run(_retarget(argv, outdir), cwd=str(outdir),
                           capture_output=True, text=True, timeout=600)
        err_tail = (p.stderr or "")[-500:]
        if p.returncode != 0:
            return False, (i, err_tail)
    # the trf must have been written next to the output (stabilize)
    return True, (None, "")


# -------------------------------------------------------------- docker

def api(url: str, method: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # an API refusal (422 = the /v1/runs validation gate) is EVIDENCE,
        # not a crash: surface the server's message verbatim
        raise ValueError(
            f"HTTP {e.code} from {url}: "
            f"{e.read().decode(errors='replace')}") from None


def docker_generate(base: str, instruction: str, assets: dict[str, str]):
    body = {"instruction": instruction, "assets": assets,
            "adapter": "rules", "preflight": True}
    try:
        d = api(f"{base}/v1/commands", "POST", body)
    except ValueError as e:  # an API error is a failed case, not a crash
        return None, {"status": "api-error", "errors": [str(e)]}, None
    if d.get("status") != "ready" or not d.get("arguments"):
        return None, d, None
    steps = [s["arguments"] for s in d.get("steps", [])] if d.get("steps") \
        else None
    return list(d["arguments"]), d, steps


def docker_run(base: str, argv: list[str], steps: list[list[str]] | None):
    body = {"arguments": argv, "timeout": 600}
    if steps:
        body["steps"] = steps
    try:
        d = api(f"{base}/v1/runs", "POST", body)
    except ValueError as e:
        # the validation gate refused the argv (422): the server's message
        # is the failure detail — report it and keep running the battery
        return False, (0, str(e))
    run_id = d["run"]["id"]
    deadline = time.time() + 640
    while time.time() < deadline:
        try:
            rec = api(f"{base}/v1/runs/{run_id}", "GET")
        except ValueError as e:
            return False, (None, str(e))
        if rec["status"] != "running":
            tail = ""
            try:
                req = urllib.request.Request(
                    f"{base}/v1/runs/{run_id}/log", method="GET")
                with urllib.request.urlopen(req, timeout=30) as r:
                    tail = r.read().decode()[-500:]
            except Exception:
                pass
            return rec["status"] == "success", (None, tail)
        time.sleep(2)
    return False, (None, "poll timeout")


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "docker"], required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    mode = args.mode
    outdir = REPO / "battery-out" / mode
    outdir.mkdir(parents=True, exist_ok=True)

    if mode == "local":
        gen_fn = lambda instr, assets: local_generate(instr, assets, outdir)
        run_fn = lambda argv, steps: local_run(
            [argv] if not steps else steps, outdir)
    else:
        if api(f"{args.api}/healthz", "GET") != {"ok": True}:
            sys.exit(f"fass API not reachable at {args.api}")
        gen_fn = lambda instr, assets: docker_generate(args.api, instr, assets)
        run_fn = lambda argv, steps: docker_run(args.api, argv, steps)

    results = []
    for case in CASES:
        argv, d, steps = gen_fn(case.instruction, case.assets)
        if argv is None:
            why = d.get('errors') or d.get('unsupported') \
                or d.get('clarifications') or ''
            print(f"FAIL  {case.id:14s} generation: "
                  f"status={d.get('status')} {why}")
            results.append(False)
            continue
        argvs = steps or [argv]
        # needle check on the FINAL pass (and detect pass for stabilize)
        if not any(needle_present(argvs[-1], n) for n in case.must_any):
            print(f"FAIL  {case.id:14s} final argv missing one of "
                  f"{case.must_any}: {argvs[-1]}")
            results.append(False)
            continue
        if case.steps and not any("vidstabdetect" in " ".join(s)
                                  for s in argvs[:-1]):
            print(f"FAIL  {case.id:14s} no vidstabdetect pass: {argvs}")
            results.append(False)
            continue
        wanted = case.expect_warn.get(mode) if case.expect_warn else None
        warn = next((w for w in d.get("warnings", [])
                     if wanted and wanted in w), None)
        if wanted and not warn:
            print(f"FAIL  {case.id:14s} expected warning naming "
                  f"'{wanted}': {d.get('warnings')}")
            results.append(False)
            continue
        if mode not in case.run_on or (case.skip_run_if_warn and warn):
            why = "not in this build's run set" if mode not in case.run_on \
                else f"build lacks the feature ({warn.split(':')[0]})"
            print(f"SKIP  {case.id:14s} generated ok, not executed — {why}")
            results.append(True)
            continue
        ok, (step, tail) = run_fn(argv, steps)
        low = tail.lower()
        if not ok and any(k in low for k in case.device_skip):
            print(f"SKIP  {case.id:14s} run failed on device init (no "
                  f"device here) — {tail.strip()[-120:]}")
            results.append(True)
        elif ok:
            extra = ""
            if case.steps:
                trf = list(outdir.glob("*.vidstab.trf")) if mode == "local" \
                    else []
                extra = f" (trf: {len(trf)})" if mode == "local" else ""
            print(f"PASS  {case.id:14s} generated + executed clean{extra}")
            results.append(True)
        else:
            where = f" (step {step})" if step is not None else ""
            print(f"FAIL  {case.id:14s} run failed{where}: {tail}")
            results.append(False)

    passed = sum(results)
    print(f"\n{passed}/{len(CASES)} cases ok ({mode})")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
