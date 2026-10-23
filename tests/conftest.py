"""Shared fixtures and helpers for the fass test suite."""

from __future__ import annotations

import pytest

from fass.pipeline import generate
from fass.probe.ffprobe import MediaInfo
from fass.schema import Asset, EditPlan, OutputSpec, guess_kind


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
