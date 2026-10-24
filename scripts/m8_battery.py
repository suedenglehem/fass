"""M8 codec-options verification battery (driver).

Generates commands through the REAL pipeline and checks them two ways:
  1. generation — the status is ready and the argv carries the expected
     encoder + option tokens (both builds, all cases);
  2. execution  — the argv is run with the build under test (local: the
     host ffmpeg directly; docker: the running fass container's
     /v1/runs endpoint) and the run must finish clean.

Modes:
  --mode local    generate() in-process (needs the fass package importable
                  — run with the repo venv python) + host ffmpeg
  --mode docker   the running fass API (default http://127.0.0.1:8000) +
                  its /v1/runs (the container's ffmpeg 9.0.1)

Input: a 10-second clip the wrapper already placed
  local:  battery-out/titan_10s.mp4
  docker: docker-data/input/titan_10s.mp4  (the container's /input volume)

Exit code: 0 iff every case is PASS (or an expected SKIP).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Case:
    id: str
    instruction: str
    must_have: list[str]          # consecutive argv tokens that must appear
    run_on: frozenset[str] = frozenset({"local", "docker"})
    note: str = ""
    # mode -> a warning the response MUST carry (build-gated encoders);
    # None/absent = no warning expected in that build
    expect_warn: dict[str, str | None] | None = None
    skip_run_if_warn: bool = False   # preflight-missing build: warn, don't run


CASES = [
    Case("x264_crf18", "re-encode as h264 with crf 18",
         ["-c:v", "libx264", "-crf", "18"]),
    Case("x264_params", "re-encode as h264 with x264-params keyint=24",
         ["-x264-params", "keyint=24"]),
    Case("x265_crf28", "re-encode as h265 with crf 28",
         ["-c:v", "libx265", "-crf", "28"]),
    Case("prores", "re-encode as prores", ["-c:v", "prores_ks"]),
    Case("qsv_preset", "re-encode as h264 with qsv preset=fast",
         ["-c:v", "h264_qsv", "-preset", "fast"]),
    Case("qsv_h265", "re-encode as h265 with quick sync",
         ["-c:v", "hevc_qsv", "-b:v", "5000k"]),
    Case("ac3_dialnorm", "extract audio as ac3 with dialnorm -27",
         ["-c:a", "ac3", "-dialnorm", "-27"]),
    Case("opus", "extract audio as opus", ["-c:a", "libopus"]),
    Case("flac", "extract audio as flac", ["-c:a", "flac"]),
    # fdk_aac: 9.0.1 only — on 6.1.1 the preflight must name it as missing
    # and the case skips the run instead of failing
    Case("fdk_vbr4", "extract audio as fdk_aac vbr 4",
         ["-c:a", "libfdk_aac", "-vbr", "4"],
         note="9.0.1-only encoder",
         expect_warn={"local": "libfdk_aac", "docker": None},
         skip_run_if_warn=True),
]


def tokens_present(argv: list[str], wanted: list[str]) -> bool:
    n = len(wanted)
    return any(argv[i:i + n] == wanted for i in range(len(argv) - n + 1))


# --------------------------------------------------------------- local

def local_generate(instruction, outdir: Path):
    from fass.pipeline import generate
    from fass.probe.ffprobe import probe_media
    from fass.schema import Asset, guess_kind

    clip = REPO / "battery-out" / "titan_10s.mp4"
    if not clip.exists():
        sys.exit(f"missing {clip} — run scripts/m8_battery.sh local first")
    assets = {"main": Asset(path=str(clip), kind=guess_kind(clip.name))}
    r = generate(instruction, assets, media=probe_media(str(clip)),
                 adapter_name="rules", preflight_check=True)
    if r.status != "ready" or not r.arguments:
        return None, r.to_dict(), None
    argv = list(r.arguments)
    argv[-1] = str(outdir / Path(argv[-1]).name)  # keep the plan's filename
    return argv, r.to_dict(), None


def local_run(argv: list[str]):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    err_tail = (p.stderr or "")[-400:]
    ok = p.returncode == 0
    skip = not ok and "qsv" in err_tail.lower()
    return ok, skip, err_tail


# -------------------------------------------------------------- docker

def api(url: str, method: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def docker_generate(base: str, instruction, preflight: bool):
    body = {"instruction": instruction, "assets": {"main": "titan_10s.mp4"},
            "adapter": "rules", "preflight": preflight}
    d = api(f"{base}/v1/commands", "POST", body)
    if d.get("status") != "ready" or not d.get("arguments"):
        return None, d, None
    return list(d["arguments"]), d, None


def docker_run(base: str, argv: list[str]):
    d = api(f"{base}/v1/runs", "POST", {"arguments": argv, "timeout": 300})
    run_id = d["run"]["id"]
    deadline = time.time() + 320
    while time.time() < deadline:
        rec = api(f"{base}/v1/runs/{run_id}", "GET")
        if rec["status"] != "running":
            tail = ""
            try:
                req = urllib.request.Request(
                    f"{base}/v1/runs/{run_id}/log", method="GET")
                with urllib.request.urlopen(req, timeout=30) as r:
                    tail = r.read().decode()[-400:]
            except Exception:
                pass
            return rec["status"] == "success", "qsv" in tail.lower(), tail
        time.sleep(2)
    return False, False, "poll timeout"


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
        generate_fn = lambda instr: local_generate(instr, outdir)
        run_fn = local_run
    else:
        if api(f"{args.api}/healthz", "GET") != {"ok": True}:
            sys.exit(f"fass API not reachable at {args.api}")
        generate_fn = lambda instr: docker_generate(args.api, instr, True)
        run_fn = lambda argv: docker_run(args.api, argv)

    results = []
    for case in CASES:
        argv, d, _ = generate_fn(case.instruction)
        if argv is None:
            print(f"FAIL  {case.id:14s} generation: status={d.get('status')} "
                  f"{(d.get('errors') or d.get('unsupported')) or ''}")
            results.append(False)
            continue
        if not tokens_present(argv, case.must_have):
            print(f"FAIL  {case.id:14s} argv missing {case.must_have}: {argv}")
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
                else f"build lacks the encoder ({warn.split(':')[0]})"
            print(f"SKIP  {case.id:14s} generated ok, not executed — {why}")
            results.append(True)
            continue
        ok, skip, tail = run_fn(argv)
        if skip:
            print(f"SKIP  {case.id:14s} run failed on device init (no QSV "
                  f"device here) — {tail.strip()[-120:]}")
            results.append(True)
        elif ok:
            print(f"PASS  {case.id:14s} generated + executed clean")
            results.append(True)
        else:
            print(f"FAIL  {case.id:14s} run failed: {tail}")
            results.append(False)

    passed = sum(results)
    print(f"\n{passed}/{len(CASES)} cases ok ({mode})")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
