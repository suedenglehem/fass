"""The fass MCP server (`fass mcp`): protocol + tools, driven by a real
stdio MCP client (one server process per test — cheap enough, and each
test is isolated)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the [mcp] extra is not installed")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # force deterministic rules adapter
    env["PYTHONPATH"] = str(ROOT)
    return env


def call(calls):
    """Spawn `python -m fass mcp` and run (tool, args) calls on it.

    Returns one record per step: the tool list first, then one parsed
    JSON result per call (with _isError from the protocol)."""

    async def go():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "fass", "mcp"],
            cwd=str(ROOT), env=_env())
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=90)
            ) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                out = [{"_tools": sorted(t.name for t in tools)}]
                for name, args in calls:
                    res = await session.call_tool(name, args)
                    text = "".join(c.text for c in res.content
                                   if getattr(c, "text", None))
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        payload = {"_raw": text}  # a tool error, not JSON
                    out.append({"_tool": name, "_isError": res.isError,
                                **payload})
                return out

    return asyncio.run(go())


def test_mcp_tools_listed():
    out = call([])
    for tool in ("generate", "run_case", "probe", "env_info"):
        assert tool in out[0]["_tools"]


def test_mcp_generate_ready():
    out = call([("generate", {
        "instruction": "resize to 720p",
        "assets": {"main": "clip.mp4", "watermark": "image:logo.png"},
        "output": "small.mp4",
        "adapter": "rules",
    })])
    data = out[1]
    assert data["_isError"] is False, data
    assert data["status"] == "ready", data
    assert data["arguments"][-1] == "small.mp4"
    assert data["plan"]["operations"][0]["type"] == "resize"


def test_mcp_run_case(tmp_path):
    case = {
        "instruction": "replace the audio with the music",
        "assets": {"main": "clip.mp4", "in1": "audio:song.mp3"},
        "output": "mixed.mp4",
        "adapter": "rules",
    }
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(case), encoding="utf-8")
    out = call([("run_case", {"case_path": str(case_file)})])
    data = out[1]
    assert data["_isError"] is False, data
    assert data["status"] == "ready", data
    ops = data["plan"]["operations"]
    assert len(ops) == 1 and ops[0]["type"] == "audio_replace"
    assert ops[0]["asset_id"] == "in1"


def test_mcp_run_case_rejects_missing_main(tmp_path):
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(
        {"instruction": "resize to 720p", "assets": {"in1": "clip.mp4"}}),
        encoding="utf-8")
    out = call([("run_case", {"case_path": str(case_file)})])
    data = out[1]
    assert data["_isError"] is True
    assert "main" in str(data)


def test_mcp_env_info():
    out = call([("env_info", {})])
    data = out[1]
    assert data["_isError"] is False
    assert data["registry_version"]
    assert data["filters"]["scale"] is True
    assert data["filters"]["overlay"] is True
