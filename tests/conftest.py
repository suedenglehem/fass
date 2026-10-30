"""Shared fixtures and helpers for the fass test suite."""

from __future__ import annotations

import os

import pytest

from fass.pipeline import generate
from fass.probe.ffprobe import MediaInfo
from fass.schema import Asset, EditPlan, OutputSpec, guess_kind

# LLM provider credentials the suite must never reach: the 'auto' adapter
# resolution and the 'unsupported -> second LLM try' retry would otherwise
# hit a real provider from a developer's environment. LLM-specific tests
# inject their own fakes (monkeypatched urlopen / fake adapters).
_LLM_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "FASS_LLM_API_KEY",
    "FASS_LLM_HOST",
    "FASS_LLM_PORT",
    "FASS_LLM_MODEL",
    "FASS_LLM_PROVIDER",
)


@pytest.fixture(autouse=True, scope="session")
def _no_real_llm_provider(tmp_path_factory):
    """Hermetic test run: no LLM provider from env or config file."""
    saved = {k: os.environ.pop(k, None) for k in _LLM_ENV_VARS}
    saved_cfg = os.environ.get("FASS_CONFIG")
    empty = tmp_path_factory.mktemp("fass-no-llm") / "config.json"
    empty.write_text("{}", encoding="utf-8")
    os.environ["FASS_CONFIG"] = str(empty)
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
    if saved_cfg is not None:
        os.environ["FASS_CONFIG"] = saved_cfg
    else:
        os.environ.pop("FASS_CONFIG", None)


def make_media(**kw) -> MediaInfo:
    base = dict(
        path="/tmp/clip.mp4",
        duration=120.0,
        video_codec="h264",
        width=1920,
        height=1080,
        pix_fmt="yuv420p",
        fps=30.0,
        has_audio=True,
        audio_codec="aac",
        video_streams=1,
        audio_streams=1,
    )
    base.update(kw)
    return MediaInfo(**base)


def assets_for(input_path: str = "clip.mp4", watermark_path: str | None = None,
               audio_path: str | None = None, extra: dict | None = None) -> dict:
    out = {"main": Asset(path=input_path, kind=guess_kind(input_path))}
    if watermark_path:
        from fass.schema import AssetKind

        out["watermark"] = Asset(path=watermark_path, kind=AssetKind.IMAGE)
    if audio_path:
        out["audio"] = Asset(path=audio_path, kind=guess_kind(audio_path))
    if extra:
        out.update(extra)
    return out


@pytest.fixture
def gen():
    """generate() pinned to the deterministic rules adapter."""

    def _gen(instruction, input_path="clip.mp4", watermark=None, audio=None, media=None,
             previous_plan=None, output_filename=None, extra=None, **kw):
        return generate(
            instruction,
            assets_for(input_path, watermark, audio, extra),
            output_filename=output_filename,
            previous_plan=previous_plan,
            media=media,
            adapter_name="rules",
            **kw,
        )

    return _gen
