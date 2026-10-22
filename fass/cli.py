"""Command-line interface (plan0.md: CLI first, web after)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from fass import __version__, feedback as fb
from fass.nlu.rules import SUPPORTED_SUMMARY
from fass.pipeline import GenerationResponse, generate
from fass.probe.ffprobe import ffmpeg_available, probe_ffmpeg, probe_media
from fass.registry import REGISTRY
from fass.schema import Asset, AssetKind, EditPlan, guess_kind

EXIT_CODES = {"ready": 0, "needs_clarification": 3, "unsupported": 4, "error": 5}

EXAMPLES = [
    'fass "resize to 720p" clip.mp4',
    'fass "cut from 00:10 to 00:30 and make it black and white" clip.mp4 -o out.mp4',
    'fass "convert to MP4 with H.265" clip.mov',
    'fass "add my logo to the bottom-right corner" clip.mp4 --watermark logo.png',
    'fass "trim the first 20 seconds, resize to 720p, and remove audio" clip.mp4 --json',
    'fass "use H.264 instead" clip.mp4 --previous plan.json   # revision',
    "fass probe clip.mp4          # local media metadata",
    "fass env                     # local FFmpeg build capabilities",
]


def _build_assets(args) -> dict:
    assets: dict = {}
    if args.input:
        assets["main"] = Asset(path=args.input, kind=guess_kind(args.input))
    if args.watermark:
        assets["watermark"] = Asset(path=args.watermark, kind=AssetKind.IMAGE)
    for spec in args.asset or []:
        aid, _, path = spec.partition("=")
        if not aid or not path:
            raise SystemExit(f"--asset expects id=path, got {spec!r}")
        assets[aid] = Asset(path=path, kind=guess_kind(path))
    return assets


def _print_response(r: GenerationResponse) -> None:
    if r.status == "ready":
        print("Command:")
        print(f"  {r.command}")
        print(f"  # argument array (shell-safe): {json.dumps(r.arguments)}")
        print("\nEdit summary:")
        for s in r.summary:
            print(f"  - {s}")
        if r.defaults:
            print("\nDefaults applied:")
            for d in r.defaults:
                print(f"  - {d}")
        if r.warnings:
            print("\nWarnings:")
            for w in r.warnings:
                print(f"  - {w}")
        level = {"static": "statically validated", "preflight": "validated against the local FFmpeg build"}[r.validation_level]
        print(f"\nValidation: {level} (parser: {r.adapter})")
        for c in r.checks:
            mark = "ok  " if c["ok"] else "FAIL"
            print(f"  [{mark}] {c['name']}: {c['detail']}")
    elif r.status == "needs_clarification":
        print("A quick question before I can build this:")
        for c in r.clarifications:
            print(f"  ? {c['question']}")
            if c.get("hint"):
                print(f"    {c['hint']}")
    elif r.status == "unsupported":
        print("Not in the MVP support set:")
        for u in r.unsupported:
            print(f"  - {u['fragment']}: {u['reason']}")
        print(f"\n{SUPPORTED_SUMMARY}")
    else:
        print("Error:", file=sys.stderr)
        for e in r.errors:
            print(f"  - {e}", file=sys.stderr)


def _generate_cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="fass", description="Natural language -> validated ffmpeg commands")
    p.add_argument("instruction", nargs="?", help="what you want done to the video")
    p.add_argument("input", nargs="?", help="input video file (name only is fine)")
    p.add_argument("-o", "--output", help="output filename")
    p.add_argument("--watermark", help="watermark image file")
    p.add_argument("--asset", action="append", help="extra asset: id=path")
    p.add_argument("--adapter", choices=["auto", "rules", "llm"], default="auto")
    p.add_argument("--previous", help="previous plan JSON file (revision)")
    p.add_argument("--metadata", help="pre-collected ffprobe JSON for the input")
    p.add_argument("--preflight", action="store_true", help="check the local FFmpeg build")
    p.add_argument("--json", action="store_true", help="print the full JSON response")
    p.add_argument("--execute", action="store_true", help="DEV: run the generated command locally")
    p.add_argument("--version", action="version", version=f"fass {__version__}")
    args = p.parse_args(argv)

    if not args.instruction:
        p.print_help()
        print("\nExamples:")
        for e in EXAMPLES:
            print(f"  {e}")
        return 0

    assets = _build_assets(args)
    if "main" not in assets:
        print("error: an input video is required (positional argument)", file=sys.stderr)
        return 2

    previous = None
    if args.previous:
        previous = EditPlan.model_validate(json.loads(open(args.previous).read()))

    media = None
    if args.metadata:
        from fass.probe.ffprobe import parse_ffprobe_json

        media = parse_ffprobe_json(json.loads(open(args.metadata).read()))

    resp = generate(
        args.instruction,
        assets,
        output_filename=args.output,
        previous_plan=previous,
        media=media,
        adapter_name=args.adapter,
        preflight_check=args.preflight,
    )

    if args.json:
        print(json.dumps(resp.to_dict(), indent=2))
    else:
        _print_response(resp)

    if resp.status == "ready" and args.execute:
        print("\nExecuting:", file=sys.stderr)
        print("  " + resp.command, file=sys.stderr)
        rc = subprocess.run(resp.arguments, shell=False).returncode
        return rc if rc != 0 else 0
    return EXIT_CODES.get(resp.status, 1)


def _subcommand(argv: list[str]) -> int:
    cmd, rest = argv[0], argv[1:]
    if cmd in ("--help", "-h"):
        print(__doc__)
        print("\nSubcommands: generate (default), probe FILE, env, serve, feedback, examples")
        return 0
    if cmd == "--version":
        print(f"fass {__version__}")
        return 0
    if cmd == "examples":
        print(SUPPORTED_SUMMARY)
        print("\nExamples:")
        for e in EXAMPLES:
            print(f"  {e}")
        return 0
    if cmd == "probe":
        if not rest:
            print("usage: fass probe FILE", file=sys.stderr)
            return 2
        info = probe_media(rest[0])
        if info is None:
            print("error: cannot probe file (missing ffprobe or unreadable file)", file=sys.stderr)
            return 1
        print(json.dumps(dataclasses_asdict(info), indent=2))
        return 0
    if cmd == "env":
        if not ffmpeg_available():
            print("ffmpeg not found on PATH", file=sys.stderr)
            return 1
        ff = probe_ffmpeg()
        print(f"build: {ff.version}")
        print(f"registry version: {REGISTRY['registry_version']}")
        print("\nencoders used by fass:")
        for name, spec in REGISTRY["video_codecs"].items():
            print(f"  {spec['encoder']:<14} {'ok' if spec['encoder'] in ff.encoders else 'MISSING'}   ({name})")
        for name, spec in REGISTRY["audio_codecs"].items():
            print(f"  {spec['encoder']:<14} {'ok' if spec['encoder'] in ff.encoders else 'MISSING'}   ({name})")
        print("\nfilters used by fass:")
        for name in ("scale", "pad", "hue", "overlay", "format"):
            print(f"  {name:<14} {'ok' if name in ff.filters else 'MISSING'}")
        return 0
    if cmd == "serve":
        p = argparse.ArgumentParser(prog="fass serve")
        p.add_argument("--host", default="127.0.0.1",
                       help="bind address (default 127.0.0.1 — local-first)")
        p.add_argument("--port", type=int, default=8000)
        a = p.parse_args(rest)
        try:
            import uvicorn
        except ImportError:
            print("error: uvicorn is required to serve (pip install uvicorn)", file=sys.stderr)
            return 1
        from fass.api import app
        print(f"fass API on http://{a.host}:{a.port} (open / for the web UI; "
              "the service never executes generated commands)")
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
        return 0
    if cmd == "feedback":
        p = argparse.ArgumentParser(prog="fass feedback")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--useful", action="store_true")
        g.add_argument("--incorrect", action="store_true")
        p.add_argument("--category", choices=fb.CATEGORIES)
        p.add_argument("--note")
        p.add_argument("--error", help="an ffmpeg error message you saw")
        p.add_argument("--instruction", help="the original request (stored ONLY with --consent)")
        p.add_argument("--consent", action="store_true",
                       help="separate consent to use the request text for evaluation/training")
        a = p.parse_args(rest)
        useful = True if a.useful else (False if a.incorrect else None)
        path = fb.log_feedback(
            useful=useful,
            category=a.category,
            note=a.note,
            ffmpeg_error=a.error,
            instruction=a.instruction,
            consent=a.consent,
        )
        print(f"recorded in {path}")
        return 0
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


def dataclasses_asdict(obj) -> dict:
    import dataclasses

    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(obj)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("probe", "env", "serve", "feedback", "examples", "--help", "-h", "--version"):
        return _subcommand(argv)
    return _generate_cli(argv)
