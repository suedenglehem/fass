"""Planner + builder: execution decisions and argv shape (PRD FR-4, security)."""

from __future__ import annotations

import pytest

from fass.builder import build_argv
from fass.planner import PlanError, plan_execution
from fass.planner.planner import _scale_expr
from fass.schema import (
    Asset,
    AssetKind,
    AudioReplaceOp,
    EditPlan,
    GrayscaleOp,
    OutputSpec,
    ResizeOp,
    TrimSpec,
    WatermarkOp,
)
from tests.conftest import make_media


def ep_for(operations=None, trim=None, output=None, media=None, out=None,
           input_path="clip.mp4", watermark_path=None, audio_path=None,
           audio_kind=AssetKind.AUDIO):
    output = output or OutputSpec(container="mp4", video_codec="h264")
    edit = EditPlan(output=output, trim=trim, operations=operations or [])
    assets = {"main": Asset(path=input_path, kind=AssetKind.VIDEO)}
    if watermark_path:
        assets["wm"] = Asset(path=watermark_path, kind=AssetKind.IMAGE)
    if audio_path:
        assets["aud"] = Asset(path=audio_path, kind=audio_kind)
    return plan_execution(edit, assets, media, out)


def argv_for(**kw):
    return build_argv(ep_for(**kw))


def test_trim_ss_is_input_option_before_i():
    argv = argv_for(trim=TrimSpec(start_seconds=10, duration_seconds=20))
    i_ss = argv.index("-ss")
    i_i = argv.index("-i")
    assert argv[i_ss + 1] == "10"
    assert i_ss < i_i  # -ss must precede -i


def test_trim_t_is_output_option():
    argv = argv_for(trim=TrimSpec(start_seconds=10, duration_seconds=20))
    i_t = argv.index("-t")
    assert argv[i_t + 1] == "20"
    # -t comes after the input, among output options
    assert i_t > argv.index("-i")


def test_no_y_flag_ever():
    for kw in ({"trim": TrimSpec(start_seconds=0, duration_seconds=5)},
               {"operations": [GrayscaleOp()]},
               {}):
        assert "-y" not in argv_for(**kw)


def test_copy_mode_when_compatible():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264"), media=media)
    assert ep.mode == "copy"
    argv = build_argv(ep)
    assert "copy" in argv
    assert "-c" in argv
    # no video encoder in a remux
    assert "libx264" not in argv


def test_reencode_when_codec_mismatches():
    media = make_media(video_codec="h265")  # input hevc, want h264
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264"), media=media)
    assert ep.mode == "reencode"
    argv = build_argv(ep)
    assert "libx264" in argv


def test_reencode_without_metadata_noted():
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264"))
    assert ep.mode == "reencode"
    assert any("metadata" in d for d in ep.defaults)


def test_watermark_uses_filter_complex_and_maps_label():
    ep = ep_for(
        operations=[WatermarkOp(asset_id="wm", position="top_left")],
        watermark_path="logo.png",
    )
    argv = build_argv(ep)
    assert "-filter_complex" in argv
    fc = argv[argv.index("-filter_complex") + 1]
    assert "overlay=" in fc
    assert "format=rgba" in fc
    # the mapped output is a labeled pad, not a stream index
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert any(m.startswith("[v") for m in maps)
    # watermark image is the second input
    assert argv.count("-i") == 2


def test_audio_removed():
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264", audio_mode="remove"))
    argv = build_argv(ep)
    assert "-an" in argv
    assert "0:a?" not in argv
    assert "-c:a" not in argv


def test_audio_kept_maps_optional_audio():
    argv = argv_for(output=OutputSpec(container="mp4", video_codec="h264"))
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert "0:a?" in maps


def test_explicit_output_equal_input_rejected():
    with pytest.raises(PlanError):
        ep_for(output=OutputSpec(container="mp4", video_codec="h264"),
               input_path="clip.mp4", out="clip.mp4")


def test_matrix_violation_rejected():
    with pytest.raises(PlanError):
        ep_for(output=OutputSpec(container="webm", video_codec="h264"))


def test_missing_watermark_asset_rejected():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[WatermarkOp(asset_id="nope")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}
    with pytest.raises(PlanError):
        plan_execution(edit, assets, None, None)


def test_default_output_does_not_collide():
    ep = ep_for(input_path="clip.mp4")
    assert ep.output_path == "clip_out.mp4"


def test_scale_expr_height_only_even():
    assert _scale_expr(ResizeOp(height=720), []) == "scale=-2:720"


def test_scale_expr_snaps_odd_height():
    d = []
    assert _scale_expr(ResizeOp(height=721), d) == "scale=-2:722"
    assert d  # a visible note was added


def test_scale_expr_letterbox_when_preserve_both():
    expr = _scale_expr(ResizeOp(width=1280, height=720), [])
    assert "force_original_aspect_ratio=decrease" in expr
    assert "pad=" in expr


def test_scale_expr_exact():
    assert _scale_expr(ResizeOp(width=640, height=360, aspect_ratio="exact"), []) == "scale=640:360"


# ------------------------------------------------------- bitrate + mpegts

def test_bitrate_overrides_crf():
    # An explicit target bitrate replaces the quality preset (no -crf).
    argv = argv_for(output=OutputSpec(container="mp4", video_codec="h264", video_bitrate_kbps=10000))
    assert "-crf" not in argv
    assert argv[argv.index("-b:v") + 1] == "10000k"
    assert argv[argv.index("-maxrate") + 1] == "10000k"
    assert argv[argv.index("-bufsize") + 1] == "20000k"  # 2x, for H.264


def test_bitrate_bufsize_only_for_h264_h265():
    # libvpx-vp9 takes -b:v/-maxrate but not -bufsize.
    argv = argv_for(output=OutputSpec(container="webm", video_codec="vp9", video_bitrate_kbps=3000))
    assert argv[argv.index("-b:v") + 1] == "3000k"
    assert "-maxrate" in argv
    assert "-bufsize" not in argv


def test_bitrate_forces_reencode_even_when_copy_would_fit():
    # Matching codecs would normally remux, but a bitrate target must be
    # encoded — never stream-copied.
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264", video_bitrate_kbps=5000),
                media=media)
    assert ep.mode == "reencode"
    assert "copy" not in ep.output_opts
    assert any("bitrate" in d.lower() for d in ep.defaults)


def test_mpegts_output_extension():
    ep = ep_for(output=OutputSpec(container="mpegts", video_codec="h264", video_bitrate_kbps=10000))
    assert ep.output_path.endswith(".ts")
    # mpegts has no -movflags
    assert "-movflags" not in ep.output_opts
    assert "libx264" in ep.output_opts


def test_cbr_trim_argv_full_shape():
    # The user's exact request: trim 20-30s, CBR 10 Mbps, mpeg ts.
    argv = argv_for(
        trim=TrimSpec(start_seconds=20, duration_seconds=10),
        output=OutputSpec(container="mpegts", video_codec="h264", video_bitrate_kbps=10000),
    )
    # -ss before -i, -t after
    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-ss") + 1] == "20"
    assert argv[argv.index("-t") + 1] == "10"
    # CBR video at the target, AAC audio at its preset
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-b:v") + 1] == "10000k"
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert argv[argv.index("-b:a") + 1] == "160k"
    assert argv[-1].endswith(".ts")


# ------------------------------------------------------------ audio replace

def test_audio_replace_maps_replacement_stream():
    argv = argv_for(operations=[AudioReplaceOp(asset_id="aud")], audio_path="song.mp3")
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert maps == ["0:v:0", "1:a:0"]
    assert "0:a?" not in maps
    assert "-shortest" in argv
    assert argv[argv.index("-c:a") + 1] == "aac"  # encoded, not copied


def test_audio_replace_with_watermark_audio_is_last_input():
    argv = argv_for(
        operations=[WatermarkOp(asset_id="wm", position="top_right"),
                    AudioReplaceOp(asset_id="aud")],
        watermark_path="logo.png",
        audio_path="song.mp3",
    )
    assert argv.count("-i") == 3  # main, watermark, audio
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert any(m.startswith("[v") for m in maps)
    assert "2:a:0" in maps  # audio is the third input
    fc = argv[argv.index("-filter_complex") + 1]
    assert "[1:v]" in fc  # watermark stays the second input
    assert "-shortest" in argv


def test_audio_replace_forces_reencode_even_when_copy_would_fit():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(operations=[AudioReplaceOp(asset_id="aud")],
                audio_path="song.mp3", media=media)
    assert ep.mode == "reencode"
    argv = build_argv(ep)
    assert "copy" not in argv
    assert "-shortest" in argv


def test_audio_replace_wrong_kind_rejected():
    with pytest.raises(PlanError, match="not an audio file"):
        ep_for(operations=[AudioReplaceOp(asset_id="aud")],
               audio_path="other.mp4", audio_kind=AssetKind.VIDEO)


def test_audio_replace_missing_asset_rejected():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[AudioReplaceOp(asset_id="ghost")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}
    with pytest.raises(PlanError, match="not in the asset map"):
        plan_execution(edit, assets, None, None)


def test_audio_replace_output_collision_rejected():
    with pytest.raises(PlanError, match="replacement-audio"):
        ep_for(operations=[AudioReplaceOp(asset_id="aud")],
               audio_path="song.mp3", out="song.mp3")
