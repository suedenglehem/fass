"""Strictness of the versioned editing-plan schema (PRD FR-2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fass.schema import (
    Asset,
    AssetKind,
    EditPlan,
    GrayscaleOp,
    OutputSpec,
    ResizeOp,
    TrimSpec,
    WatermarkOp,
)


def plan(**over) -> EditPlan:
    base = dict(output=OutputSpec(container="mp4", video_codec="h264"), operations=[])
    base.update(over)
    return EditPlan(**base)


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        plan(output=OutputSpec(container="mp4", video_codec="h264"),
             operations=[], raw_flags="-y -ss 5")


def test_trim_end_before_start_rejected():
    with pytest.raises(ValidationError):
        TrimSpec(start_seconds=30, end_seconds=10)


def test_trim_end_equal_start_rejected():
    with pytest.raises(ValidationError):
        TrimSpec(start_seconds=10, end_seconds=10)


def test_trim_end_and_duration_mutually_exclusive():
    with pytest.raises(ValidationError):
        TrimSpec(start_seconds=10, duration_seconds=5, end_seconds=15)


def test_trim_end_normalizes_to_duration():
    t = TrimSpec(start_seconds=10, end_seconds=30)
    assert t.duration_seconds == 20
    assert t.end_seconds is None
    assert t.is_bounded


def test_unbounded_trim():
    t = TrimSpec(start_seconds=10)
    assert not t.is_bounded
    assert t.duration_seconds is None


def test_resize_bounds():
    assert ResizeOp(height=16).height == 16
    with pytest.raises(ValidationError):
        ResizeOp(height=15)
    with pytest.raises(ValidationError):
        ResizeOp(width=8193)


def test_resize_needs_a_dimension():
    with pytest.raises(ValidationError):
        ResizeOp()
    with pytest.raises(ValidationError):
        ResizeOp(width=None, height=None)


def test_resize_defaults_to_preserve():
    op = ResizeOp(height=720)
    assert op.aspect_ratio == "preserve"


def test_at_most_one_watermark():
    with pytest.raises(ValidationError):
        plan(operations=[
            WatermarkOp(asset_id="logo", position="top_left"),
            WatermarkOp(asset_id="logo2", position="bottom_right"),
        ])


def test_operation_count_cap():
    ops = [GrayscaleOp() for _ in range(8)]
    plan(operations=ops)  # 8 is allowed
    with pytest.raises(ValidationError):
        plan(operations=ops + [GrayscaleOp()])  # 9 is not


def test_matrix_is_enforced_at_plan_level_via_registry():
    # h264 in webm is not a supported combination; the registry rejects it.
    from fass.registry import matrix_error

    assert matrix_error("webm", "h264") is not None
    assert matrix_error("mp4", "h264") is None
    assert matrix_error("mpegts", "h264") is None
    assert matrix_error("mpegts", "vp9") is not None


def test_video_bitrate_field():
    o = OutputSpec(container="mp4", video_codec="h264", video_bitrate_kbps=10000)
    assert o.video_bitrate_kbps == 10000
    # defaults to None (quality preset)
    assert OutputSpec(container="mp4", video_codec="h264").video_bitrate_kbps is None
    # bounds
    with pytest.raises(ValidationError):
        OutputSpec(container="mp4", video_codec="h264", video_bitrate_kbps=50)
    with pytest.raises(ValidationError):
        OutputSpec(container="mp4", video_codec="h264", video_bitrate_kbps=200_001)
    with pytest.raises(ValidationError):
        OutputSpec(container="mp4", video_codec="h264", video_bitrate_kbps="10mb")


def test_mpegts_container_accepted():
    plan(output=OutputSpec(container="mpegts", video_codec="h264"))
    plan(output=OutputSpec(container="mpegts", video_codec="h265"))
    with pytest.raises(ValidationError):
        OutputSpec(container="avi", video_codec="h264")


def test_asset_rejects_urls():
    with pytest.raises(ValidationError):
        Asset(path="https://example.com/clip.mp4")
    with pytest.raises(ValidationError):
        Asset(path="//evil/clip.mp4")


def test_asset_rejects_control_chars():
    with pytest.raises(ValidationError):
        Asset(path="clip\n.mp4")
    with pytest.raises(ValidationError):
        Asset(path="clip\x00.mp4")


def test_guess_kind():
    from fass.schema import guess_kind

    assert guess_kind("a.mp4") is AssetKind.VIDEO
    assert guess_kind("logo.PNG") is AssetKind.IMAGE
    assert guess_kind("photo.jpg") is AssetKind.IMAGE
    assert guess_kind("clip.mkv") is AssetKind.VIDEO
