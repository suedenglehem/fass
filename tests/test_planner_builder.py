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


def test_av1_default_crf():
    # AV1 maps to the CPU reference encoder libaom-av1; no gpu/qsv coder
    # exists for it, so the default CPU quality preset (-crf 32) is emitted.
    argv = argv_for(output=OutputSpec(container="mp4", video_codec="av1"))
    i = argv.index("-c:v")
    assert argv[i + 1] == "libaom-av1"
    j = argv.index("-crf")
    assert argv[j + 1] == "32"


def test_av1_svt_encoder():
    # SVT-AV1 is its own codec (av1_svt -> libsvtav1), not an alias for av1.
    argv = argv_for(output=OutputSpec(container="mp4", video_codec="av1_svt"))
    i = argv.index("-c:v")
    assert argv[i + 1] == "libsvtav1"


def test_av1_nvenc_on_gpu():
    # AV1 gained a GPU form (av1 in gpu.coders -> av1_nvenc); the generic
    # nvenc branch picks the coder for the requested codec.
    argv = argv_for(output=OutputSpec(container="mp4", video_codec="av1", gpu=True))
    i = argv.index("-c:v")
    assert argv[i + 1] == "av1_nvenc"


def test_vp8_encoder():
    argv = argv_for(output=OutputSpec(container="webm", video_codec="vp8"))
    i = argv.index("-c:v")
    assert argv[i + 1] == "libvpx"


def test_theora_encoder_ogv():
    argv = argv_for(output=OutputSpec(container="ogv", video_codec="theora"))
    i = argv.index("-c:v")
    assert argv[i + 1] == "libtheora"


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


def test_two_watermarks_chain_distinct_labels_and_inputs():
    # ep_for supports one image — build the assets dict by hand
    edit = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        operations=[WatermarkOp(asset_id="wm", position="top_left"),
                    WatermarkOp(asset_id="badge", position="bottom_right")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
              "wm": Asset(path="logo.png", kind=AssetKind.IMAGE),
              "badge": Asset(path="badge.png", kind=AssetKind.IMAGE)}
    ep = plan_execution(edit, assets, None, None)
    argv = build_argv(ep)
    fc = argv[argv.index("-filter_complex") + 1]
    # two overlay stages, one per image, chained through numbered pads
    # (no other video op: the chain starts at v0)
    assert "[1:v]format=rgba[wm1]" in fc
    assert "[2:v]format=rgba[wm2]" in fc
    assert "[0:v][wm1]overlay=" in fc
    assert "[v0][wm2]overlay=" in fc
    assert fc.endswith("[v1]")
    # main clip first, then one image input per watermark, in op order
    inputs = [argv[i + 1] for i, v in enumerate(argv) if v == "-i"]
    assert inputs == ["clip.mp4", "logo.png", "badge.png"]
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert any(m.startswith("[v1") for m in maps)


def test_two_watermarks_chain_behind_a_resize():
    # the overlays must chain through the resize's output pad, not restart
    # from [0:v]
    edit = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        operations=[ResizeOp(height=720),
                    WatermarkOp(asset_id="wm", position="top_left"),
                    WatermarkOp(asset_id="badge", position="bottom_right")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
              "wm": Asset(path="logo.png", kind=AssetKind.IMAGE),
              "badge": Asset(path="badge.png", kind=AssetKind.IMAGE)}
    ep = plan_execution(edit, assets, None, None)
    argv = build_argv(ep)
    fc = argv[argv.index("-filter_complex") + 1]
    assert "[0:v]scale=-2:720[v0]" in fc
    assert "[1:v]format=rgba[wm1]" in fc
    assert "[v0][wm1]overlay=" in fc
    assert "[2:v]format=rgba[wm2]" in fc
    assert "[v1][wm2]overlay=" in fc
    assert fc.endswith("[v2]")
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert any(m.startswith("[v2") for m in maps)


def test_two_watermarks_scale_applies_per_image():
    edit = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        operations=[WatermarkOp(asset_id="wm", position="top_left"),
                    WatermarkOp(asset_id="badge", position="bottom_right",
                                scale_percent=50)])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
              "wm": Asset(path="logo.png", kind=AssetKind.IMAGE),
              "badge": Asset(path="badge.png", kind=AssetKind.IMAGE)}
    ep = plan_execution(edit, assets, None, None)
    argv = build_argv(ep)
    fc = argv[argv.index("-filter_complex") + 1]
    assert "[1:v]format=rgba[wm1]" in fc  # the unscaled one stays plain
    assert "[2:v]format=rgba,scale=iw*50/100:ih*50/100[wm2]" in fc


def test_output_onto_a_watermark_file_is_refused():
    edit = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        operations=[WatermarkOp(asset_id="wm"),
                    WatermarkOp(asset_id="badge")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
              "wm": Asset(path="logo.png", kind=AssetKind.IMAGE),
              "badge": Asset(path="badge.png", kind=AssetKind.IMAGE)}
    with pytest.raises(PlanError, match="watermark file"):
        plan_execution(edit, assets, None, "badge.png")


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


def test_gif_output_drops_audio():
    # A GIF carries no audio: the input's track is dropped with an explicit
    # -an, no audio map and no audio encoder (the §9.2 held-out 'Make it a
    # gif' request crashed the planner on the audio-codec lookup).
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(output=OutputSpec(container="gif", video_codec="gif"),
                media=media)
    assert ep.mode == "reencode"
    argv = build_argv(ep)
    assert "-an" in argv
    assert "-c:a" not in argv
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert "0:a?" not in maps
    assert any("carries no audio" in d for d in ep.defaults)


def test_gif_output_rejects_audio_operations():
    out = OutputSpec(container="gif", video_codec="gif")
    with pytest.raises(PlanError, match="carries no audio"):
        ep_for(output=out,
               operations=[AudioResampleOp(sample_rate=22050)])
    with pytest.raises(PlanError, match="carries no audio"):
        ep_for(output=out, operations=[AudioReplaceOp(asset_id="aud")],
               audio_path="song.mp3")


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


# --------- M13.1: single-variant DASH

def test_dash_output_extension_and_muxer():
    ep = ep_for(output=OutputSpec(container="dash", video_codec="h264"))
    assert ep.output_path == "clip.mpd"
    # -f dash is prepended to the output options (mirrors the hls special case)
    assert ep.output_opts[:2] == ["-f", "dash"]
    assert "-movflags" not in ep.output_opts


def test_dash_forces_mpd_extension():
    ep = ep_for(output=OutputSpec(container="dash", video_codec="h264"), out="clip")
    assert ep.output_path == "clip.mpd"


def test_file_destination_carries_conflicting_container():
    # An explicitly requested mp4 plan with a .ts destination is a
    # contradiction (a merely-defaulted container is reconciled upstream)
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    with pytest.raises(PlanError, match="carries MPEGTS"):
        plan_execution(edit, _assets(), None, "out.ts")


def test_ts_destination_reconciles_defaulted_mp4_container(gen):
    # Bug: an mp4/mov source transcoded to a .ts file — the container
    # defaulted from the input filename used to ship -movflags +faststart
    # into the mpegts output
    r = gen("convert my clip", media=make_media(), output_filename="out.ts")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "mpegts"
    assert "-movflags" not in r.arguments
    assert r.arguments[-1] == "out.ts"


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

def test_output_muxer_for_aac_plan_is_adts():
    # the .aac ADTS case: FFmpeg's muxer is named 'adts' — the 'aac' entry
    # in '-formats' is demux-only, so preflight must probe 'adts'
    edit = _audio_plan("aac", "aac")
    ep = plan_execution(edit, _assets_main(), make_media())
    assert _output_muxer(ep) == "adts"


def test_preflight_flags_missing_adts_muxer():
    # a build without the adts muxer is reported, never assumed
    ff = FfmpegInfo(version="ffmpeg version 6.1.1",
                    encoders={"aac"}, filters=set(),
                    muxers={"mp4", "matroska", "webm"})  # no "adts" muxer
    checks = preflight(make_media(), ff, ["aac"], [], ["adts"])
    mux = [c for c in checks if c.name == "muxer adts"]
    assert mux and mux[0].ok is False
    assert "NOT available" in mux[0].detail


def test_preflight_aac_plan_passes_with_adts():
    # the empirical Ubuntu 6.1.1 situation: '-formats' lists 'aac' demux-only
    # but '-muxers' carries 'adts' — a .aac plan must pass the probe
    ff = FfmpegInfo(version="ffmpeg version 6.1.1",
                    encoders={"aac"}, filters=set(),
                    muxers={"mp4", "adts"})
    checks = preflight(make_media(), ff, ["aac"], [], ["adts"])
    assert all(c.ok for c in checks)


# --------- M7 regressions: the video-mode argv honors the output's own codecs

def test_video_mode_named_audio_codec_overrides_container_default(gen):
    # "convert to mpeg2 video and mp2 audio": mpegts's default audio is aac,
    # but the named audio_codec must reach the argv (regression: the
    # kept-audio branch used to emit the container default)
    r = gen("convert to mpeg2 video and mp2 audio", media=make_media(),
            output_filename="out.ts")
    assert r.status == "ready"
    argv = r.arguments
    assert argv[argv.index("-c:a") + 1] == "mp2"
    assert argv[argv.index("-b:a") + 1] == "192k"


def test_mxf_output_reencodes_and_uses_pcm(gen):
    # MXF is a master wrapper: even when the input codec matches, the video
    # is re-encoded and the audio must be PCM (the mxf muxer rejects aac)
    r = gen("convert to mxf", media=make_media(), output_filename="out.mxf")
    assert r.status == "ready"
    argv = r.arguments
    assert "-c copy" not in argv
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-c:a") + 1] == "pcm_s16le"
    assert argv[argv.index("-g") + 1] == "120"  # mxf index table: frame offsets ±127
    assert any("master format" in d for d in r.defaults)
    assert any("GOP capped at 120" in d for d in r.defaults)


# ------------------------------------------------- M13.3 ABR ladder (planner)

def _lvl(h, kbps, profile=None, level=None):
    d = {"height": h, "video_bitrate_kbps": kbps}
    if profile:
        d["video_profile"] = profile
    if level:
        d["video_level"] = level
    return d


def test_ladder_explicit_levels_argv_exact():
    # the probed specifier form: all the maps first (v-then-a per level, so
    # level K is v:K and a:K), the shared bare encoders, then per-level
    # options addressed by stream specifier, then '-var_stream_map', then
    # the %v playlist name
    ep = ep_for(output=OutputSpec(
        container="hls", video_codec="h264",
        variants=[_lvl(216, 300), _lvl(360, 700), _lvl(480, 1500)]),
        media=make_media())
    argv = build_argv(ep)
    assert argv[-1] == "stream_%v.m3u8"
    # maps is empty — the per-level maps live inside output_opts
    assert ep.maps == []
    assert argv[:6] == ["ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-i", "clip.mp4"]
    assert argv[6:8] == ["-f", "hls"]
    assert argv[8:20] == ["-map", "0:v:0", "-map", "0:a:0"] * 3
    # the shared encoders ride bare — they apply to every mapped stream
    assert argv[20:26] == ["-c:v", "libx264", "-c:a", "aac",
                           "-pix_fmt", "yuv420p"]
    # level 0: the 216p row (300k, main, 3.0, 56k, 384x216)
    assert argv[26:42] == [
        "-b:v:0", "300k", "-minrate:v:0", "300k", "-maxrate:v:0", "300k",
        "-bufsize:v:0", "600k", "-profile:v:0", "main", "-level:v:0", "3.0",
        "-s:v:0", "384x216", "-b:a:0", "56k"]
    # level 1: the 360p row (700k, main, 3.1, 64k, 640x360)
    assert argv[42:58] == [
        "-b:v:1", "700k", "-minrate:v:1", "700k", "-maxrate:v:1", "700k",
        "-bufsize:v:1", "1400k", "-profile:v:1", "main", "-level:v:1", "3.1",
        "-s:v:1", "640x360", "-b:a:1", "64k"]
    # level 2: the 480p row (1500k, main, 3.2, 80k, 854x480)
    assert argv[58:74] == [
        "-b:v:2", "1500k", "-minrate:v:2", "1500k", "-maxrate:v:2", "1500k",
        "-bufsize:v:2", "3000k", "-profile:v:2", "main", "-level:v:2", "3.2",
        "-s:v:2", "854x480", "-b:a:2", "80k"]
    # var_stream_map at the end, one 'v:i,a:i' per level
    v = argv.index("-var_stream_map")
    assert argv[v + 1] == "v:0,a:0 v:1,a:1 v:2,a:2"
    # no stream-copy (the ladder always re-encodes)
    assert "copy" not in argv


def test_vod_hls_defaults_list_size_zero():
    # FFmpeg 9 defaults hls_list_size to 5, which keeps only the last 5
    # segments of a finite (VOD) clip; a VOD plan must keep the whole clip.
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264"),
                media=make_media())
    argv = build_argv(ep)
    assert argv[-1].endswith(".m3u8")
    assert "-hls_list_size" in argv
    assert argv[argv.index("-hls_list_size") + 1] == "0"


def test_vod_hls_keeps_explicit_list_size():
    # an explicit playlist window is honored — not overridden by the VOD
    # keep-all default.
    argv = argv_for(output=OutputSpec(
        container="hls", video_codec="h264",
        format_options={"hls_list_size": "8"}),
        media=make_media())
    assert argv[argv.index("-hls_list_size") + 1] == "8"


def test_event_hls_is_not_forced_to_keep_all():
    # a live (event) playlist is a deliberate rolling window: no keep-all
    # default is injected, and an explicit window is left in place.
    argv = argv_for(output=OutputSpec(
        container="hls", video_codec="h264",
        format_options={"hls_playlist_type": "event"}),
        media=make_media())
    assert "-hls_list_size" not in argv
    argv2 = argv_for(output=OutputSpec(
        container="hls", video_codec="h264",
        format_options={"hls_playlist_type": "event",
                        "hls_list_size": "10"}),
        media=make_media())
    assert argv2[argv2.index("-hls_list_size") + 1] == "10"


def test_ladder_default_table_picks_top3_fit_input():
    # 1080p input, no explicit levels -> the three tallest default rows that
    # fit: 480p / 720p / 1080p (never upscales)
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[]),
                media=make_media(width=1920, height=1080))
    argv = build_argv(ep)
    # collect all per-level sizes in order
    s = [argv[i + 1] for i in range(len(argv))
         if argv[i].startswith("-s:v:")]
    assert s == ["854x480", "1280x720", "1920x1080"]
    b = [argv[i + 1] for i in range(len(argv))
         if argv[i].startswith("-b:v:")]
    assert b == ["1500k", "2500k", "4500k"]
    assert argv[-1] == "stream_%v.m3u8"


def test_ladder_never_upscales_explicit_level():
    # explicit 1080p level on a 720p input -> capped to the input height
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(1080, 4500)]),
                media=make_media(width=1280, height=720))
    argv = build_argv(ep)
    s = [argv[i + 1] for i in range(len(argv))
         if argv[i].startswith("-s:v:")]
    assert s == ["1280x720"]  # 1080p capped to the 720p input
    assert argv[-1] == "stream_%v.m3u8"


def test_ladder_single_variant_keeps_var_stream_map():
    # even a one-level ladder is a ladder: var_stream_map + %v are present
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(360, 700)]),
                media=make_media())
    argv = build_argv(ep)
    v = argv.index("-var_stream_map")
    assert argv[v + 1] == "v:0,a:0"
    assert argv[-1] == "stream_%v.m3u8"


def test_ladder_dash_uses_mpd_no_v():
    ep = ep_for(output=OutputSpec(container="dash", video_codec="h264",
                                  variants=[_lvl(360, 700), _lvl(480, 1500)]),
                media=make_media())
    argv = build_argv(ep)
    assert ep.output_path == "clip.mpd"
    assert argv[-1] == "clip.mpd"
    assert argv[6:8] == ["-f", "dash"]
    assert "%v" not in argv[-1]
    # still a ladder: var_stream_map is emitted (the DASH ladder, M13.4)
    assert argv.index("-var_stream_map") != -1


def test_ladder_dash_argv_exact():
    # the DASH ladder (M13.4): identical specifier form to the HLS ladder,
    # '-f dash' in front, a single .mpd as the destination (auto adaptation
    # sets — the planner never emits explicit -adaptation_sets)
    ep = ep_for(output=OutputSpec(
        container="dash", video_codec="h264",
        variants=[_lvl(216, 300), _lvl(360, 700), _lvl(480, 1500)]),
        media=make_media())
    argv = build_argv(ep)
    assert argv[-1] == "clip.mpd"
    assert argv[6:8] == ["-f", "dash"]
    assert argv[8:20] == ["-map", "0:v:0", "-map", "0:a:0"] * 3
    assert argv[20:26] == ["-c:v", "libx264", "-c:a", "aac",
                           "-pix_fmt", "yuv420p"]
    assert argv[26:42] == [
        "-b:v:0", "300k", "-minrate:v:0", "300k", "-maxrate:v:0", "300k",
        "-bufsize:v:0", "600k", "-profile:v:0", "main", "-level:v:0", "3.0",
        "-s:v:0", "384x216", "-b:a:0", "56k"]
    v = argv.index("-var_stream_map")
    assert argv[v + 1] == "v:0,a:0 v:1,a:1 v:2,a:2"
    # auto adaptation sets only — no explicit -adaptation_sets anywhere
    assert "-adaptation_sets" not in argv


def test_ladder_dash_seg_duration_emitted():
    # container format_options ride the ladder path too (M13.4): seg_duration
    # is emitted as -seg_duration <value> before the output path
    ep = ep_for(output=OutputSpec(
        container="dash", video_codec="h264",
        variants=[_lvl(360, 700), _lvl(480, 1500)],
        format_options={"seg_duration": "6"}),
        media=make_media())
    argv = build_argv(ep)
    i = argv.index("-seg_duration")
    assert argv[i + 1] == "6"
    assert i < argv.index(ep.output_path)


def test_ladder_rejects_resizing_operation():
    with pytest.raises(PlanError, match="re-encodes every level"):
        ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                 variants=[_lvl(360, 700)]),
               operations=[ResizeOp(height=480)])


def test_ladder_rejects_audio_remove():
    with pytest.raises(PlanError, match="cannot remove the audio"):
        ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                 variants=[_lvl(360, 700)],
                                 audio_mode="remove"))


def test_ladder_rejects_duplicate_heights():
    with pytest.raises(PlanError, match="duplicate ladder level 360p"):
        ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                 variants=[_lvl(360, 700), _lvl(360, 900)]),
               media=make_media())


def test_ladder_rejects_below_smallest_row_without_bitrate():
    # 180p with no bitrate: below the smallest default row -> the planner
    # cannot invent a bitrate
    with pytest.raises(PlanError, match="below the smallest default-table row"):
        ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                 variants=[{"height": 180}]),
               media=make_media())


def test_ladder_rejects_h265_baseline():
    with pytest.raises(PlanError, match="no 'baseline' profile"):
        ep_for(output=OutputSpec(container="hls", video_codec="h265",
                                 variants=[_lvl(360, 700, profile="baseline")]),
               media=make_media())


def test_ladder_rejects_input_without_audio():
    with pytest.raises(PlanError, match="input has no audio stream"):
        ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                 variants=[_lvl(360, 700)]),
               media=make_media(has_audio=False))


def test_ladder_rejects_input_shorter_than_smallest_level():
    with pytest.raises(PlanError, match="shorter than the ladder's smallest"):
        ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                 variants=[]),
               media=make_media(width=320, height=180))


def test_ladder_explicit_name_gets_v():
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(360, 700)]), out="myladder",
                media=make_media())
    assert ep.output_path == "myladder_%v.m3u8"
    assert build_argv(ep)[-1] == "myladder_%v.m3u8"


def test_ladder_explicit_m3u8_name_still_gets_v():
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(360, 700)]),
                out="myladder.m3u8", media=make_media())
    assert ep.output_path == "myladder_%v.m3u8"


def test_ladder_trim_places_ss_input_and_t_output():
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(360, 700)]),
                trim=TrimSpec(start_seconds=10, duration_seconds=20),
                media=make_media())
    argv = build_argv(ep)
    i_i = argv.index("-i")
    i_ss = argv.index("-ss")
    assert i_ss < i_i  # -ss is an input option, before -i
    assert argv[i_ss + 1] == "10"
    i_t = argv.index("-t")
    assert i_t > i_i  # -t is an output option, after the input
    assert argv[i_t + 1] == "20"
    # the ladder groups are still intact
    assert argv.index("-var_stream_map") != -1


def test_ladder_widths_follow_input_aspect_even():
    # a 4:3 720x540 input: level widths derive from the input aspect, even
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(360, 700)]),
                media=make_media(width=720, height=540))
    argv = build_argv(ep)
    s = [argv[i + 1] for i in range(len(argv))
         if argv[i].startswith("-s:v:")]
    # 360p at 4:3 -> 480x360
    assert s == ["480x360"]


def test_ladder_explicit_profile_and_level_pass_through():
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(480, 1500,
                                                 profile="high", level="4.1")]),
                media=make_media())
    argv = build_argv(ep)
    p = argv.index("-profile:v:0")
    assert argv[p + 1] == "high"
    l = argv.index("-level:v:0")
    assert argv[l + 1] == "4.1"


def test_ladder_bufsize_is_double_bitrate():
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264",
                                  variants=[_lvl(360, 700)]),
                media=make_media())
    argv = build_argv(ep)
    b = argv.index("-b:v:0")
    bs = argv.index("-bufsize:v:0")
    assert argv[b + 1] == "700k"
    assert argv[bs + 1] == "1400k"


def test_single_variant_hls_argv_regression_guard():
    # A h264/aac input to hls still stream-copies, with the maps from the
    # builder's map loop. A VOD (non-event) playlist keeps the whole clip:
    # the VOD keep-all default appends -hls_list_size 0 (FFmpeg 9's default
    # of 5 would drop all but the last 5 segments).
    ep = ep_for(output=OutputSpec(container="hls", video_codec="h264"),
                media=make_media())
    assert build_argv(ep) == [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "clip.mp4",
        "-map", "0:v:0", "-map", "0:a?", "-f", "hls", "-c", "copy",
        "-hls_list_size", "0",
        "clip.m3u8"]
