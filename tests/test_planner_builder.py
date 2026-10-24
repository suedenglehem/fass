"""Planner + builder: execution decisions and argv shape (PRD FR-4, security)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fass.builder import build_argv
from fass.explain import summarize
from fass.pipeline import _output_muxer
from fass.planner import PlanError, plan_execution
from fass.planner.planner import _scale_expr
from fass.probe.ffprobe import FfmpegInfo, preflight
from fass.schema import (
    Asset,
    AssetKind,
    AudioReplaceOp,
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
    # codec/container pairs are validated at the schema level (registry matrix)
    with pytest.raises(ValidationError):
        OutputSpec(container="webm", video_codec="h264")


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


# ------------------------------------------------------------- stream I/O (M3b)

def _stream_edit(dest, container="flv"):
    return EditPlan(output=OutputSpec(container=container, video_codec="h264"),
                    output_destination=dest)


def _assets(input_path="clip.mp4"):
    return {"main": Asset(path=input_path, kind=AssetKind.VIDEO)}


def test_rtmp_destination_flv_argv():
    ep = plan_execution(_stream_edit("rtmp://host:1935/live", "flv"),
                        _assets(), None, None)
    argv = build_argv(ep)
    assert argv[-1] == "rtmp://host:1935/live"
    f = argv.index("-f")
    assert argv[f + 1] == "flv"
    assert "-movflags" not in argv  # no mp4 file flags on a flv stream
    assert any("remote endpoint" in w for w in ep.warnings)


def test_udp_destination_mpegts_muxer():
    ep = plan_execution(_stream_edit("udp://127.0.0.1:5004", "mpegts"),
                        _assets(), None, None)
    argv = build_argv(ep)
    assert argv[-1] == "udp://127.0.0.1:5004"
    f = argv.index("-f")
    assert argv[f + 1] == "mpegts"


def test_stream_destination_skips_file_collision_checks():
    # A destination is not a path: same URL as the input must not trip the
    # overwrite refusal.
    url = "rtmp://host:1935/live"
    ep = plan_execution(_stream_edit(url, "flv"),
                        {"main": Asset(path=url, kind=AssetKind.VIDEO)}, None, None)
    assert build_argv(ep)


def test_rtsp_srt_containers_require_stream_destination():
    # rtsp/srt have no file form: the planner uses the literal container
    # value, and a file output is refused.
    for c in ("rtsp", "srt"):
        edit = EditPlan(output=OutputSpec(container=c, video_codec="h264"))
        with pytest.raises(PlanError, match="no file form"):
            plan_execution(edit, _assets(), None, None)


def test_flv_file_output_is_allowed():
    # flv *files* exist — the stream-only restriction is on rtsp/srt only.
    ep = plan_execution(EditPlan(output=OutputSpec(container="flv", video_codec="h264")),
                        _assets(), None, "out.flv")
    argv = build_argv(ep)
    assert argv[-1] == "out.flv"
    assert "-f" not in argv


def test_plan_container_conflicts_with_destination():
    # A plan whose container disagrees with its destination's scheme is a
    # contradiction the planner reports (safety net for adapter output).
    with pytest.raises(PlanError, match="carries FLV"):
        plan_execution(_stream_edit("rtmp://host:1935/live", "mp4"),
                       _assets(), None, None)


def test_output_filename_stream_url_needs_matching_plan_container():
    # The pipeline (not the planner) aligns a defaulted container with the
    # -o destination; the planner alone reports the conflict.
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    with pytest.raises(PlanError, match="carries FLV"):
        plan_execution(edit, _assets(), None, "rtmp://host:1935/live")


def test_http_stream_output_container_conflict():
    # http output is supported; a .ts destination carries MPEG-TS, so an MP4
    # plan conflicts with it
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    with pytest.raises(PlanError, match="carries MPEGTS"):
        plan_execution(edit, _assets(), None, "http://cdn.example.com/out.ts")


def test_non_stream_url_output_is_rejected():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    with pytest.raises(PlanError, match="not a supported stream"):
        plan_execution(edit, _assets(), None, "ftp://cdn.example.com/out.ts")


def test_udp_stream_input_gets_mpegts_pre_opt():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    trim=TrimSpec(start_seconds=0, duration_seconds=5))
    assets = {"main": Asset(path="udp://239.0.0.1:5004", kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, None, None)
    argv = build_argv(ep)
    i = argv.index("-i")
    assert argv[i + 1] == "udp://239.0.0.1:5004"
    f = argv.index("-f")
    assert argv[f + 1] == "mpegts" and f < i  # explicit muxer before -i


def test_remote_stream_input_notes_reencode_default():
    # Remote sources can't be probed: always re-encode, and say so.
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    assets = {"main": Asset(path="https://cdn.example.com/clip.mp4",
                            kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, None, None)
    assert ep.mode == "reencode"
    assert any("remote stream" in d for d in ep.defaults)


# ------------------------------------------------------------------ M4: speed

def test_speed_setpts_and_atempo():
    ep = ep_for(operations=[SpeedOp(factor=2)])
    assert ep.vf == "setpts=PTS/2"
    assert ep.af == "atempo=2"  # audio kept -> tempo chain on -af


def test_speed_atempo_chains_beyond_one_instance():
    # atempo accepts 0.5-2.0 per instance; wider factors chain instances
    assert ep_for(operations=[SpeedOp(factor=4)]).af == "atempo=2,atempo=2"
    assert ep_for(operations=[SpeedOp(factor=8)]).af == "atempo=2,atempo=2,atempo=2"
    ep = ep_for(operations=[SpeedOp(factor=0.25)])
    assert ep.vf == "setpts=PTS/0.25"
    assert ep.af == "atempo=0.5,atempo=0.5"


def test_speed_fractional_factor_format():
    ep = ep_for(operations=[SpeedOp(factor=1.5)])
    assert ep.vf == "setpts=PTS/1.5"
    assert ep.af == "atempo=1.5"


def test_speed_remove_audio_drops_atempo():
    # -af is inert without an audio stream: the planner omits it entirely
    ep = ep_for(operations=[SpeedOp(factor=2)],
                output=OutputSpec(container="mp4", video_codec="h264", audio_mode="remove"))
    assert ep.vf == "setpts=PTS/2"
    assert ep.af is None
    argv = build_argv(ep)
    assert "-af" not in argv


def test_speed_argv_carries_af_and_vf():
    argv = argv_for(operations=[SpeedOp(factor=2)])
    assert argv[argv.index("-vf") + 1] == "setpts=PTS/2"
    assert argv[argv.index("-af") + 1] == "atempo=2"


# ------------------------------------------------------------------- M4: crop

def test_crop_even_snap_note():
    ep = ep_for(operations=[CropOp(width=641, height=480)])
    assert ep.vf == "crop=642:480"
    assert any("snapped to 642x480" in d for d in ep.defaults)


def test_crop_even_dims_no_snap_note():
    ep = ep_for(operations=[CropOp(width=640, height=480)])
    assert ep.vf == "crop=640:480"
    assert not any("snapped" in d for d in ep.defaults)


def test_crop_larger_than_source_rejected():
    # the planner checks the crop against the probed geometry
    with pytest.raises(PlanError, match="larger than the video"):
        ep_for(operations=[CropOp(width=2000, height=2000)],
               media=make_media(width=1280, height=720))


def test_crop_after_rotate_checked_against_rotated_geometry():
    # 90° rotation swaps the working dimensions: 1280x720 -> 720x1280,
    # so a 700x1200 crop fits *after* the rotate but not before it
    ep_for(operations=[RotateOp(angle=90), CropOp(width=700, height=1200)],
           media=make_media(width=1280, height=720))
    with pytest.raises(PlanError, match="larger than the video"):
        ep_for(operations=[CropOp(width=700, height=1200), RotateOp(angle=90)],
               media=make_media(width=1280, height=720))


# ----------------------------------------------------------------- M4: rotate

@pytest.mark.parametrize(
    ("angle", "clockwise", "vf"),
    [
        (90, True, "transpose=1"),
        (270, True, "transpose=2"),
        (90, False, "transpose=2"),
        (270, False, "transpose=1"),
        (180, True, "hflip,vflip"),
        (180, False, "hflip,vflip"),
    ],
)
def test_rotate_transpose_mapping(angle, clockwise, vf):
    ep = ep_for(operations=[RotateOp(angle=angle, clockwise=clockwise)])
    assert ep.vf == vf


# ------------------------------------------------------------- M4: resample

def test_resample_emits_ar_ac():
    ep = ep_for(operations=[AudioResampleOp(sample_rate=48000, channels=1)])
    opts = ep.output_opts
    assert opts[opts.index("-ar") + 1] == "48000"
    assert opts[opts.index("-ac") + 1] == "1"
    assert any("sample rate 48000" in d for d in ep.defaults)
    assert any("channels 1" in d for d in ep.defaults)


def test_resample_single_target():
    ep = ep_for(operations=[AudioResampleOp(sample_rate=22050)])
    assert "-ar" in ep.output_opts and "-ac" not in ep.output_opts
    ep = ep_for(operations=[AudioResampleOp(channels=2)])
    assert "-ac" in ep.output_opts and "-ar" not in ep.output_opts


def test_resample_with_removed_audio_rejected():
    with pytest.raises(PlanError, match="cannot be resampled"):
        ep_for(operations=[AudioResampleOp(sample_rate=48000)],
               output=OutputSpec(container="mp4", video_codec="h264", audio_mode="remove"))


# ------------------------------------------------- M4: profile/preset/pix_fmt

def test_profile_preset_pix_fmt_opts():
    ep = ep_for(output=OutputSpec(
        container="mp4", video_codec="h264",
        pixel_format="yuv420p10le", video_profile="baseline", video_preset="ultrafast",
    ))
    opts = ep.output_opts
    assert opts[opts.index("-profile:v") + 1] == "baseline"
    assert opts[opts.index("-preset") + 1] == "ultrafast"
    assert opts[opts.index("-pix_fmt") + 1] == "yuv420p10le"
    assert any("profile baseline" in d for d in ep.defaults)
    assert any("preset ultrafast" in d for d in ep.defaults)
    assert any("yuv420p10le" in d for d in ep.defaults)


def test_default_pix_fmt_is_codec_default():
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264"))
    opts = ep.output_opts
    assert opts[opts.index("-pix_fmt") + 1] == "yuv420p"
    assert "-profile:v" not in opts and "-preset" not in opts


def test_profile_rejected_for_h265():
    # per-codec profiles are validated at the schema level (OutputSpec._capability)
    with pytest.raises(ValidationError):
        OutputSpec(container="mkv", video_codec="h265", video_profile="baseline")


def test_preset_rejected_for_non_h264():
    with pytest.raises(PlanError, match="only supported for H.264"):
        ep_for(output=OutputSpec(container="mkv", video_codec="h265", video_preset="ultrafast"))
    with pytest.raises(PlanError, match="only supported for H.264"):
        ep_for(output=OutputSpec(container="webm", video_codec="vp9", video_preset="fast"))


def test_pixel_format_outside_matrix_rejected():
    # per-codec pixel formats are validated at the schema level (registry matrix)
    with pytest.raises(ValidationError, match="not supported by H264"):
        OutputSpec(container="mp4", video_codec="h264", pixel_format="yuv444p")


# ------------------------------------------------------------- M4: trim+speed

def test_trim_plus_speed_scales_output_duration():
    ep = ep_for(trim=TrimSpec(start_seconds=5, duration_seconds=10),
                operations=[SpeedOp(factor=2)])
    # -ss stays an input option at the source position
    assert ep.inputs[0].pre_opts == ["-ss", "5"]
    # -t bounds the *output* timeline: 10 source seconds at 2x = 5 output seconds
    opts = ep.output_opts
    assert opts[opts.index("-t") + 1] == "5"
    assert any("output duration 5s" in d and "10s of source at 2x speed" in d
               for d in ep.defaults)


def test_trim_plus_slow_speed_scales_output_duration():
    ep = ep_for(trim=TrimSpec(start_seconds=0, duration_seconds=10),
                operations=[SpeedOp(factor=0.5)])
    opts = ep.output_opts
    assert opts[opts.index("-t") + 1] == "20"


# ------------------------------------------------------------- M4: copy + bsf

def test_copy_mpegts_h264_gets_annexb_bsf():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="mpegts", video_codec="h264"), media=media)
    assert ep.mode == "copy"
    opts = ep.output_opts
    assert opts[:4] == ["-c", "copy", "-bsf:v", "h264_mp4toannexb"]
    assert any("Annex-B" in d for d in ep.defaults)
    argv = build_argv(ep)
    assert argv[argv.index("-bsf:v") + 1] == "h264_mp4toannexb"


def test_copy_mpegts_h265_gets_hevc_bsf():
    media = make_media(video_codec="h265", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="mpegts", video_codec="h265"), media=media)
    assert ep.mode == "copy"
    assert ep.output_opts[:4] == ["-c", "copy", "-bsf:v", "hevc_mp4toannexb"]


def test_copy_mp4_to_mp4_needs_no_bsf():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="mp4", video_codec="h264"), media=media)
    assert ep.mode == "copy"
    assert "-bsf:v" not in ep.output_opts


def test_ffprobe_style_hevc_metadata_reencodes_to_h265():
    # ffprobe reports 'hevc' where the plan says 'h265': the codec strings
    # don't match, so the planner re-encodes (and says why) instead of
    # attempting a copy remux.
    media = make_media(video_codec="hevc", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="mpegts", video_codec="h265"), media=media)
    assert ep.mode == "reencode"
    assert any("hevc" in w for w in ep.warnings)


# ------------------------------------------------------------- M4: source inputs

def test_lavfi_source_input_pre_opts():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    trim=TrimSpec(start_seconds=0, duration_seconds=2))
    assets = {"main": Asset(path="lavfi:testsrc=duration=5:size=640x480",
                            kind=AssetKind.SOURCE)}
    ep = plan_execution(edit, assets, None, "out.mp4")
    assert ep.inputs[0].path == "testsrc=duration=5:size=640x480"
    assert ep.inputs[0].pre_opts[:2] == ["-f", "lavfi"]
    argv = build_argv(ep)
    i = argv.index("-i")
    assert argv[i + 1] == "testsrc=duration=5:size=640x480"
    f = argv.index("-f")
    assert argv[f + 1] == "lavfi" and f < i  # -f lavfi precedes -i


def test_v4l2_source_input_carries_device_options():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    trim=TrimSpec(start_seconds=0, duration_seconds=2))
    assets = {"main": Asset(path="v4l2:/dev/video0;video_size=1280x720",
                            kind=AssetKind.SOURCE)}
    ep = plan_execution(edit, assets, None, "out.mp4")
    assert ep.inputs[0].path == "/dev/video0"
    assert ep.inputs[0].pre_opts[:4] == ["-f", "v4l2", "-video_size", "1280x720"]


def test_source_asset_accepted_as_main_input():
    # a SOURCE asset is a valid main input for the planner
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    assets = {"main": Asset(path="lavfi:testsrc=duration=5", kind=AssetKind.SOURCE)}
    ep = plan_execution(edit, assets, None, "out.mp4")
    assert ep.mode == "reencode"


# ------------------------------------------------------------- M4: device output

def test_device_output_plan_shape():
    ep = ep_for(out="alsa:hw:0")
    assert ep.mode == "device"
    assert ep.maps == ["0:a?"]
    assert ep.output_opts[-4:] == ["-acodec", "pcm_s16le", "-f", "alsa"]
    assert ep.output_path == "hw:0"
    assert any(w.startswith("output goes to the local audio device alsa:hw:0")
               and "nothing is saved to a file" in w for w in ep.warnings)


def test_device_output_with_audio_replace():
    ep = ep_for(operations=[AudioReplaceOp(asset_id="aud")],
                audio_path="song.mp3", out="alsa:hw:0")
    assert ep.mode == "device"
    assert ep.maps == ["1:a:0"]
    assert len(ep.inputs) == 2
    assert any("replaced" in d for d in ep.defaults)


def test_device_output_with_resample_and_trim():
    ep = ep_for(operations=[AudioResampleOp(sample_rate=22050, channels=1)],
                trim=TrimSpec(start_seconds=1, duration_seconds=4), out="alsa:hw:0")
    assert ep.inputs[0].pre_opts == ["-ss", "1"]
    opts = ep.output_opts
    assert opts[opts.index("-t") + 1] == "4"
    assert opts[opts.index("-ar") + 1] == "22050"
    assert opts[opts.index("-ac") + 1] == "1"
    assert opts[-4:] == ["-acodec", "pcm_s16le", "-f", "alsa"]


def test_device_output_rejects_video_edits():
    with pytest.raises(PlanError, match="carries audio only"):
        ep_for(operations=[CropOp(width=640, height=480)], out="alsa:hw:0")


def test_device_output_rejects_removed_audio():
    with pytest.raises(PlanError, match="needs audio, but the plan removes"):
        ep_for(output=OutputSpec(container="mp4", video_codec="h264", audio_mode="remove"),
               out="alsa:hw:0")


def test_device_output_rejects_device_options():
    with pytest.raises(PlanError, match="must not carry options"):
        ep_for(out="alsa:hw:0;freq=44100")


# ------------------------------------------------------- M7 modes: explain
#
# The old explain crash: audio/subtitle plans carry video_codec=None and
# the video branch used to index REGISTRY["video_codecs"][None]. These
# plans must summarize sensibly (mode-aware) and never crash.

def _assets_main():
    return {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}


def _audio_plan(container: str, codec: str) -> EditPlan:
    return EditPlan(output=OutputSpec(mode="audio", container=container,
                                      audio_codec=codec))


def test_explain_audio_mp3_names_encoder_and_bitrate():
    edit = _audio_plan("mp3", "mp3")
    ep = plan_execution(edit, _assets_main(), make_media())
    assert ep.mode == "reencode"  # source audio is aac -> mp3 needs encoding
    text = " ".join(summarize(edit, ep, make_media()))
    assert "libmp3lame" in text and "128k" in text and "MP3" in text


def test_explain_audio_mp2_names_encoder_and_bitrate():
    edit = _audio_plan("mp2", "mp2")
    ep = plan_execution(edit, _assets_main(), make_media())
    text = " ".join(summarize(edit, ep, make_media()))
    assert "mp2" in text and "192k" in text and "MP2" in text


def test_explain_audio_pcm_says_no_bitrate_is_applied():
    edit = _audio_plan("wav", "pcm_s16le")
    ep = plan_execution(edit, _assets_main(), make_media())
    lines = summarize(edit, ep, make_media())
    text = " ".join(lines)
    assert "pcm_s16le" in text and "no bitrate" in text
    assert "-b:a" not in ep.output_opts  # PCM carries no bitrate flag


def test_explain_audio_copy_mode_says_remux():
    # source audio aac -> m4a (aac codec): the planner copies the stream
    edit = _audio_plan("m4a", "aac")
    ep = plan_execution(edit, _assets_main(), make_media())
    assert ep.mode == "copy"
    text = " ".join(summarize(edit, ep, make_media()))
    assert "stream copy" in text.lower()


def test_explain_subtitle_dump_plan():
    edit = EditPlan(output=OutputSpec(mode="subtitles", container="srt_file"))
    media = make_media(subtitle_streams=1)
    ep = plan_execution(edit, _assets_main(), media)
    assert ep.mode == "copy"  # the text muxer encodes the stream itself
    lines = summarize(edit, ep, media)
    text = " ".join(lines)
    assert "Subtitle dump" in text and "srt" in text
    assert any("No re-encoding" in l for l in lines)


# ------------------------------------------------- M7 preflight: muxer probe

def test_output_muxer_for_aac_plan_is_aac():
    # the .aac ADTS case: the plan's output muxer is probed against the
    # build, so _output_muxer must report "aac" for a .aac audio plan
    edit = _audio_plan("aac", "aac")
    ep = plan_execution(edit, _assets_main(), make_media())
    assert _output_muxer(ep) == "aac"


def test_preflight_flags_missing_aac_muxer():
    # registry note: the Ubuntu 6.1.1 build lacks the standalone AAC/ADTS
    # muxer — preflight reports it instead of assuming availability
    ff = FfmpegInfo(version="ffmpeg version 6.1.1",
                    encoders={"aac"}, filters=set(),
                    muxers={"mp4", "matroska", "webm"})  # no "aac" muxer
    checks = preflight(make_media(), ff, ["aac"], [], ["aac"])
    mux = [c for c in checks if c.name == "muxer aac"]
    assert mux and mux[0].ok is False
    assert "NOT available" in mux[0].detail
