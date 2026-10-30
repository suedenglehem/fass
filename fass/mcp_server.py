"""The fass MCP server — fass as a tool for MCP clients.

`fass mcp` speaks the Model Context Protocol (stdio by default;
--transport streamable-http for network clients) so any MCP client
(Claude Code, an agent, a test harness) can drive fass directly:

  - generate: instruction + assets -> the validated ffmpeg response
  - run_case: a test case described entirely by a JSON file (the same
    schema as `fass --case` — instruction, assets, output, adapter)
  - probe:    media metadata (ffprobe)
  - env_info: the local FFmpeg build fass validates against

The tools return the same JSON the CLI's --json mode prints. Bare
filenames resolve against $FASS_INPUT_DIR / $FASS_OUTPUT_DIR exactly
like the rest of fass, so a harness that stages a case's media into its
input dir can reference the files by their original names.

The MCP SDK is optional: pip install 'mcp<2' (the [mcp] extra).
"""

from __future__ import annotations

import dataclasses
import json
import os
from fass import __version__
from fass.cli import _build_case_assets, _execute_steps
from fass.pipeline import generate as generate_fn
from fass.probe.ffprobe import ffmpeg_available, probe_ffmpeg, probe_media
from fass.registry import REGISTRY

def _case_from_file(case_path: str) -> dict:
    with open(case_path, encoding="utf-8") as f:
        case = json.load(f)
    if not isinstance(case, dict) or not case.get("instruction"):
        raise ValueError("case file needs an 'instruction' (the NL request)")
    assets_in = case.get("assets")
    if not isinstance(assets_in, dict) or "main" not in assets_in:
        raise ValueError("case file needs 'assets': {\"main\": file, ...}")
    return case


def build_server():
    """The configured FastMCP server. Built lazily so importing this
    module never requires the mcp package (only serving does)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(f"fass {__version__}")

    @mcp.tool()
    def generate(
        instruction: str,
        assets: dict,
        output: str | None = None,
        adapter: str = "auto",
    ) -> dict:
        """Generate a validated ffmpeg command from a natural-language
        instruction. assets maps asset ids to file paths (the 'main' id
        is required; use 'watermark' for a logo image, free ids like
        'in1'/'in2' for extra inputs). A value may be 'kind:path'
        (kind: video|audio|image|source) to pin the kind. Returns the
        full response: status (ready|needs_clarification|unsupported|
        error), command, arguments, steps, plan, summary, warnings."""
        assets_in = _build_case_assets({"assets": assets}, base_dir=".")
        resp = generate_fn(
            instruction, assets_in, output_filename=output,
            adapter_name=adapter if adapter in ("auto", "rules", "llm") else "auto",
        )
        return resp.to_dict()

    @mcp.tool()
    def run_case(case_path: str) -> dict:
        """Run a test case described entirely by a JSON file:
        {"instruction", "assets": {"main": file, ...}, "assets_dir"?,
        "output"?, "adapter"?, "execute"?}. Asset paths are relative to
        assets_dir (default: the case file's directory). Returns the
        full response JSON (status, command, arguments, steps, plan,
        warnings, errors)."""
        case = _case_from_file(case_path)
        base_dir = case.get("assets_dir") or os.path.dirname(os.path.abspath(case_path))
        assets = _build_case_assets(case, base_dir)
        resp = generate_fn(
            case["instruction"], assets,
            output_filename=case.get("output"),
            adapter_name=case.get("adapter", "auto"),
        )
        out = resp.to_dict()
        if resp.status == "ready" and case.get("execute"):
            # DEV: actually run the generated command(s); the rc is
            # recorded alongside the response
            out["execute_rc"] = _execute_steps(resp)
        return out

    @mcp.tool()
    def probe(file: str) -> dict:
        """Media metadata for a file (ffprobe): streams, codecs,
        duration, resolution. Returns null when the file cannot be
        probed."""
        info = probe_media(file)
        if info is None:
            return {"error": "cannot probe file (missing ffprobe or unreadable file)"}
        return dataclasses.asdict(info)

    @mcp.tool()
    def env_info() -> dict:
        """The local FFmpeg build fass validates against: version,
        registry version, and the presence of every encoder/filter the
        registry references."""
        if not ffmpeg_available():
            return {"error": "ffmpeg not found on PATH"}
        ff = probe_ffmpeg()
        return {
            "fass_version": __version__,
            "ffmpeg": ff.version,
            "registry_version": REGISTRY["registry_version"],
            "video_codecs": {
                name: {"encoder": spec["encoder"],
                       "ok": spec["encoder"] in ff.encoders}
                for name, spec in REGISTRY["video_codecs"].items()
            },
            "audio_codecs": {
                name: {"encoder": spec["encoder"],
                       "ok": spec["encoder"] in ff.encoders}
                for name, spec in REGISTRY["audio_codecs"].items()
            },
            "filters": {
                name: name in ff.filters
                for name in ("scale", "pad", "hue", "overlay", "format",
                             "crop", "transpose", "fps", "setpts", "atempo",
                             "amix", "anull", "null", "subtitles", "drawtext")
            },
        }

    return mcp


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the server (blocking). stdio = for an MCP client that spawns
    `fass mcp`; streamable-http = a network endpoint on host:port."""
    mcp = build_server()
    if transport != "stdio":
        mcp.settings.host = host
        mcp.settings.port = port
    mcp.run(transport=transport)
