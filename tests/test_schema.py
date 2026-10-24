"""Strictness of the versioned editing-plan schema (PRD FR-2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fass.schema import (
    Asset,
    AssetKind,
    AudioResampleOp,
    CropOp,
    EditPlan,
    GrayscaleOp,
    OutputSpec,
    ResizeOp,
    RotateOp,
    SpeedOp,
    TrimSpec,
    WatermarkOp,
    guess_kind,
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
        OutputSpec(container="avi", video_codec="vp9")


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
        "http://cdn.example.com/out.mp4",
        "https://cdn.example.com/live.ts",
    ):
        assert plan(output_destination=url).output_destination == url


def test_output_destination_rejects_non_stream_urls():
    # plain filenames are the application's job, never the plan's
    with pytest.raises(ValidationError):
        plan(output_destination="out.mp4")
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


# ---------------------------------------------------------------- M4 operations


def test_crop_bounds():
    CropOp(width=16, height=16)
    CropOp(width=8192, height=8192)
    with pytest.raises(ValidationError):
        CropOp(width=15, height=16)
    with pytest.raises(ValidationError):
        CropOp(width=16, height=15)
    with pytest.raises(ValidationError):
        CropOp(width=8193, height=16)
    with pytest.raises(ValidationError):
        CropOp(width=16, height=8193)
    # crop needs *exact* dimensions — both are required (unlike resize)
    with pytest.raises(ValidationError):
        CropOp(width=640)
    with pytest.raises(ValidationError):
        CropOp(height=480)


def test_speed_bounds():
    assert SpeedOp(factor=0.25).factor == 0.25
    assert SpeedOp(factor=8).factor == 8
    assert SpeedOp(factor=1.5).factor == 1.5
    with pytest.raises(ValidationError):
        SpeedOp(factor=0.24)
    with pytest.raises(ValidationError):
        SpeedOp(factor=8.01)
    with pytest.raises(ValidationError):
        SpeedOp(factor=0)


def test_rotate_angle_literal():
    op = RotateOp()  # defaults: 90° clockwise
    assert op.angle == 90 and op.clockwise is True
    RotateOp(angle=90)
    RotateOp(angle=180)
    RotateOp(angle=270)
    with pytest.raises(ValidationError):
        RotateOp(angle=45)
    with pytest.raises(ValidationError):
        RotateOp(angle=0)
    with pytest.raises(ValidationError):
        RotateOp(angle=360)


def test_audio_resample_bounds():
    AudioResampleOp(sample_rate=8000, channels=1)
    AudioResampleOp(sample_rate=192000, channels=6)
    with pytest.raises(ValidationError):
        AudioResampleOp(sample_rate=7999)
    with pytest.raises(ValidationError):
        AudioResampleOp(sample_rate=192001)
    with pytest.raises(ValidationError):
        AudioResampleOp(channels=0)
    with pytest.raises(ValidationError):
        AudioResampleOp(channels=7)


def test_audio_resample_needs_a_target():
    # one of the two targets is enough; neither alone is not
    assert AudioResampleOp(sample_rate=22050).channels is None
    assert AudioResampleOp(channels=2).sample_rate is None
    with pytest.raises(ValidationError):
        AudioResampleOp()
    with pytest.raises(ValidationError):
        AudioResampleOp(sample_rate=None, channels=None)


def test_at_most_one_of_each_m4_op():
    factories = [
        lambda: CropOp(width=640, height=480),
        lambda: SpeedOp(factor=2),
        lambda: RotateOp(angle=90),
        lambda: AudioResampleOp(sample_rate=48000),
    ]
    for make in factories:
        plan(operations=[make()])  # one is fine
        with pytest.raises(ValidationError):
            plan(operations=[make(), make()])  # the second is not


def test_m4_ops_discriminated_union():
    # all four new ops round-trip through the plan's discriminated list
    p = plan(operations=[
        CropOp(width=640, height=480),
        SpeedOp(factor=2),
        RotateOp(angle=90, clockwise=False),
        AudioResampleOp(sample_rate=22050, channels=1),
    ])
    types = [o.type for o in p.operations]
    assert types == ["crop", "speed", "rotate", "audio_resample"]
    assert p.ops_of("speed")[0].factor == 2


# ---------------------------------------------------------------- M4 output fields


def test_output_spec_m4_literals():
    o = OutputSpec(container="mp4", video_codec="h264", pixel_format="yuv420p10le",
                   video_profile="baseline", video_preset="ultrafast")
    assert o.pixel_format == "yuv420p10le"
    assert o.video_profile == "baseline"
    assert o.video_preset == "ultrafast"
    # all three default to None (codec defaults apply)
    d = OutputSpec(container="mp4", video_codec="h264")
    assert d.pixel_format is None
    assert d.video_profile is None
    assert d.video_preset is None


def test_output_spec_rejects_bad_m4_literals():
    for kw in ({"pixel_format": "rgb24"},
               {"video_profile": "extended"},
               {"video_preset": "turbofast"}):
        with pytest.raises(ValidationError):
            OutputSpec(container="mp4", video_codec="h264", **kw)


# ---------------------------------------------------------------- M4 source assets


def test_asset_kind_source_accepts_source_specs():
    Asset(path="lavfi:testsrc=duration=5:size=640x480", kind=AssetKind.SOURCE)
    Asset(path="v4l2:/dev/video0", kind=AssetKind.SOURCE)
    Asset(path="x11grab::1.0+10,20;framerate=10", kind=AssetKind.SOURCE)
    Asset(path="alsa:hw:0", kind=AssetKind.SOURCE)


def test_asset_kind_source_rejects_plain_paths():
    # 'source' is only for allowlisted '<format>:<spec>' strings
    with pytest.raises(ValidationError):
        Asset(path="clip.mp4", kind=AssetKind.SOURCE)
    with pytest.raises(ValidationError):
        Asset(path="rtmp://host/live", kind=AssetKind.SOURCE)
    with pytest.raises(ValidationError):
        Asset(path="mp4:file.mp4", kind=AssetKind.SOURCE)  # 'mp4' not an input format


def test_guess_kind_source_specs():
    assert guess_kind("lavfi:testsrc=duration=5") is AssetKind.SOURCE
    assert guess_kind("v4l2:/dev/video0") is AssetKind.SOURCE
    assert guess_kind("x11grab::1.0+10,20;framerate=10") is AssetKind.SOURCE
    # the source-spec check wins over the extension heuristic
    assert guess_kind("lavfi:testsrc=duration=5.mp4") is AssetKind.SOURCE


# ---------------------------------------------------------------- M4 registry helpers


def test_is_source_spec():
    from fass.registry import is_source_spec

    for s in ("lavfi:testsrc=duration=5", "v4l2:/dev/video0",
              "v4l2:/dev/video0;video_size=1280x720",
              "x11grab::1.0+10,20;framerate=10", "alsa:hw:0"):
        assert is_source_spec(s), s
    for s in ("clip.mp4", "rtmp://x/live", "12:30", "mp4:file.mp4",
              "C:\\videos\\a.mp4", ""):
        assert not is_source_spec(s), s


def test_source_spec_parts():
    from fass.registry import source_spec_parts

    assert source_spec_parts("v4l2:/dev/video0;video_size=1280x720") == \
        ("v4l2", "/dev/video0", [("video_size", "1280x720")])
    assert source_spec_parts("x11grab::1.0+10,20;framerate=10") == \
        ("x11grab", ":1.0+10,20", [("framerate", "10")])
    # only the FIRST colon splits format from spec (lavfi lists carry ':')
    assert source_spec_parts("lavfi:testsrc=duration=5:size=640x480") == \
        ("lavfi", "testsrc=duration=5:size=640x480", [])
    assert source_spec_parts("clip.mp4") is None
    assert source_spec_parts("mp4:file.mp4") is None  # format not allowlisted


def test_output_device_parts_and_for():
    from fass.registry import output_device_for, output_device_parts

    assert output_device_parts("alsa:hw:0") == ("alsa", "hw:0", [])
    assert output_device_parts("pulse:default") == ("pulse", "default", [])
    # lavfi is an input format, not an output device
    assert output_device_parts("lavfi:testsrc=duration=5") is None
    assert output_device_for("pulse:default") == "pulse"
    assert output_device_for("alsa:hw:0") == "alsa"
    assert output_device_for("out.mp4") is None
    assert output_device_for("rtmp://host/live") is None


def test_pixel_format_matrix():
    from fass.registry import pixel_format_error, pixel_formats_for

    assert pixel_formats_for("h264") == ["yuv420p", "yuv420p10le"]
    assert "yuv444p" in pixel_formats_for("vp9")
    assert pixel_formats_for("nope") == []
    assert pixel_format_error("h264", "yuv420p") is None
    assert pixel_format_error("h264", "yuv420p10le") is None
    err = pixel_format_error("h264", "yuv444p")
    assert err is not None and "yuv444p" in err


def test_video_profiles_and_presets_matrix():
    from fass.registry import video_presets_for, video_profiles_for

    assert video_profiles_for("h264") == ["baseline", "main", "high"]
    assert video_profiles_for("h265") == []  # profiles are H.264-only in the MVP
    assert len(video_presets_for("h264")) == 9
    assert "ultrafast" in video_presets_for("h264")
    assert video_presets_for("h265") == []  # presets are x264-only in the MVP


def test_audio_resample_registry():
    from fass.registry import channel_layouts, sample_rates

    assert 22050 in sample_rates() and 48000 in sample_rates()
    assert sample_rates()[0] == 8000 and sample_rates()[-1] == 192000
    assert channel_layouts() == [1, 2, 6]


def test_speed_and_atempo_limits():
    from fass.registry import atempo_limits, speed_limits

    assert speed_limits() == (0.25, 8.0)
    assert atempo_limits() == (0.5, 2.0)


def test_bsf_for_copy():
    from fass.registry import bsf_for_copy

    assert bsf_for_copy("mp4", "mpegts", "h264") == "h264_mp4toannexb"
    assert bsf_for_copy("mov", "mpegts", "h264") == "h264_mp4toannexb"
    assert bsf_for_copy("mp4", "mpegts", "h265") == "hevc_mp4toannexb"
    # plain copy when no restamping is needed
    assert bsf_for_copy("mp4", "mp4", "h264") is None
    assert bsf_for_copy("mpegts", "mp4", "h264") is None
    assert bsf_for_copy("mp4", "mpegts", "vp9") is None
