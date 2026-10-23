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


def test_asset_accepts_allowlisted_stream_urls():
    # M3b: remote sources are allowed for the streaming schemes only
    Asset(path="https://example.com/clip.mp4")
    Asset(path="http://cam.local/stream.ts")
    Asset(path="rtmp://host:1935/live")
    Asset(path="rtmps://host/live")
    Asset(path="rtsp://10.0.0.5:554/live")
    Asset(path="rtsps://host/live")
    Asset(path="srt://10.0.0.5:9000/live")
    Asset(path="udp://239.0.0.1:5004")
    Asset(path="rtp://239.0.0.1:5004")
    Asset(path="tcp://host:8554/live")
    Asset(path="tls://host:5001/live")


def test_asset_rejects_non_stream_urls():
    with pytest.raises(ValidationError):
        Asset(path="ftp://example.com/clip.mp4")
    with pytest.raises(ValidationError):
        Asset(path="file:///tmp/clip.mp4")
    with pytest.raises(ValidationError):
        Asset(path="gopher://example.com")
    # protocol-relative is not an allowlisted scheme
    with pytest.raises(ValidationError):
        Asset(path="//evil/clip.mp4")
    # control characters are rejected even inside a URL
    with pytest.raises(ValidationError):
        Asset(path="http://example.com/clip\n.mp4")


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
    assert guess_kind("song.mp3") is AssetKind.AUDIO
    assert guess_kind("tune.ogg") is AssetKind.AUDIO
    assert guess_kind("track.flac") is AssetKind.AUDIO
    assert guess_kind("voice.wav") is AssetKind.AUDIO
    # M3b: URLs — query/fragment suffixes are dropped before the extension check
    assert guess_kind("http://x/live.mp4?token=abc") is AssetKind.VIDEO
    assert guess_kind("https://x/stream.m3u8") is AssetKind.VIDEO
    assert guess_kind("http://x/logo.png") is AssetKind.IMAGE
    assert guess_kind("http://radio.example.com/tune.mp3") is AssetKind.AUDIO
    # an extensionless URL is a video (the default)
    assert guess_kind("udp://239.0.0.1:5004") is AssetKind.VIDEO


def test_audio_replace_op():
    from fass.schema import AudioReplaceOp

    op = AudioReplaceOp(asset_id="music")
    assert op.type == "audio_replace"
    assert op.asset_id == "music"
    with pytest.raises(ValidationError):
        AudioReplaceOp(asset_id="")
    with pytest.raises(ValidationError):
        AudioReplaceOp(asset_id="x", bogus=1)


def test_at_most_one_audio_replace():
    from fass.schema import AudioReplaceOp

    plan(operations=[AudioReplaceOp(asset_id="music")])  # one is fine
    with pytest.raises(ValidationError):
        plan(operations=[AudioReplaceOp(asset_id="a"), AudioReplaceOp(asset_id="b")])


# ---------------------------------------------------------------- M3b streams


def test_output_destination_accepts_stream_urls():
    for url in (
        "rtmp://host:1935/live",
        "rtmps://host/live",
        "rtsp://10.0.0.5:554/live",
        "rtsps://host/live",
        "srt://10.0.0.5:9000/live",
        "udp://127.0.0.1:5004",
        "rtp://239.0.0.1:5004",
        "tcp://host:8554/live",
    ):
        assert plan(output_destination=url).output_destination == url


def test_output_destination_rejects_non_stream_urls():
    # plain filenames are the application's job, never the plan's
    with pytest.raises(ValidationError):
        plan(output_destination="out.mp4")
    with pytest.raises(ValidationError):
        plan(output_destination="http://cdn.example.com/out.mp4")
    with pytest.raises(ValidationError):
        plan(output_destination="ftp://example.com/out")
    # a path that merely contains a colon is not a scheme
    with pytest.raises(ValidationError):
        plan(output_destination="C:\\videos\\out.mp4")
    with pytest.raises(ValidationError):
        plan(output_destination="12:30")
    # control characters inside the URL are rejected (a stripped URL is fine)
    with pytest.raises(ValidationError):
        plan(output_destination="rtmp://host:1935\n/live")
    # a trailing newline is stripped, not an error
    assert plan(output_destination="rtmp://host:1935/live\n").output_destination \
        == "rtmp://host:1935/live"


def test_destination_defaults_to_none():
    assert plan().output_destination is None


def test_flv_rtsp_srt_matrix():
    from fass.registry import matrix_error

    assert matrix_error("flv", "h264") is None
    assert matrix_error("flv", "h265") is not None   # RTMP endpoints carry H.264
    assert matrix_error("flv", "vp9") is not None
    assert matrix_error("rtsp", "h264") is None
    assert matrix_error("rtsp", "h265") is None
    assert matrix_error("rtsp", "vp9") is not None
    assert matrix_error("srt", "h264") is None
    assert matrix_error("srt", "h265") is None
    # plan-level acceptance
    plan(output=OutputSpec(container="flv", video_codec="h264"))
    plan(output=OutputSpec(container="rtsp", video_codec="h264"),
         output_destination="rtsp://10.0.0.5:554/live")
    plan(output=OutputSpec(container="srt", video_codec="h265"),
         output_destination="srt://10.0.0.5:9000/live")
    # (the flv+h265 *combination* is rejected at the registry level above —
    # OutputSpec alone only validates the literals)
