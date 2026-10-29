"""Rule-based NLU behavior — the PRD §13 acceptance scenarios and paraphrases.

Assertions check the *plan* (what), not the command wording: status,
operation multiset, trim interval, container/codec/audio mode.
"""

from __future__ import annotations

import pytest

from fass.schema import (
    Asset,
    AssetKind,
    AudioReplaceOp,
    EditPlan,
    OutputSpec,
    WatermarkOp,
    guess_kind,
)
from tests.conftest import make_media


def ops_by_type(plan: dict | None) -> dict[str, list]:
    out: dict[str, list] = {}
    if not plan:
        return out
    for o in plan.get("operations", []):
        out.setdefault(o["type"], []).append(o)
    return out


def test_resize_720p_preserves_aspect(gen):
    r = gen("resize to 720p", "clip.mp4")
    assert r.status == "ready"
    rs = ops_by_type(r.plan)["resize"]
    assert len(rs) == 1
    assert rs[0]["height"] == 720
    assert rs[0]["aspect_ratio"] == "preserve"


def test_cut_range_becomes_trim(gen):
    r = gen("cut from 00:10 to 00:30", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["trim"]["start_seconds"] == 10
    assert r.plan["trim"]["duration_seconds"] == 20


def test_h265_mp4_preset(gen):
    r = gen("convert to MP4 with H.265", "clip.mov")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec"] == "h265"
    assert r.plan["output"]["container"] == "mp4"


def test_grayscale_keeps_audio(gen):
    r = gen("make it black and white", "clip.mp4")
    assert r.status == "ready"
    assert "grayscale" in ops_by_type(r.plan)
    assert r.plan["output"]["audio_mode"] == "encode"


def test_watermark_position(gen):
    r = gen("add my logo to the top-left corner", "clip.mp4", watermark="logo.png")
    assert r.status == "ready"
    wms = ops_by_type(r.plan)["watermark"]
    assert len(wms) == 1
    assert wms[0]["position"] == "top_left"
    assert wms[0]["asset_id"] == "watermark"


def test_two_watermarks_named_in_text_get_text_order_positions(gen):
    r = gen("put logo.png top-left and badge.png bottom-right", "clip.mp4",
            watermark="logo.png",
            extra={"badge": Asset(path="badge.png", kind=AssetKind.IMAGE)})
    assert r.status == "ready", (r.status, r.clarifications)
    wms = ops_by_type(r.plan)["watermark"]
    # binding order = asset-map order; positions follow the text order
    assert [w["asset_id"] for w in wms] == ["watermark", "badge"]
    assert [w["position"] for w in wms] == ["top_left", "bottom_right"]


def test_multiple_unmentioned_images_all_bound(gen):
    r = gen("add my logos", "clip.mp4", watermark="logo.png",
            extra={"badge": Asset(path="badge.png", kind=AssetKind.IMAGE)})
    assert r.status == "ready", (r.status, r.clarifications)
    wms = ops_by_type(r.plan)["watermark"]
    assert [w["asset_id"] for w in wms] == ["watermark", "badge"]
    assert all(w["position"] == "bottom_right" for w in wms)
    assert any("watermarks: logo.png, badge.png" in d for d in r.defaults)


def test_duplicate_position_phrases_not_deduped(gen):
    r = gen("put one logo top-left and another logo top-left", "clip.mp4",
            watermark="logo.png",
            extra={"badge": Asset(path="badge.png", kind=AssetKind.IMAGE)})
    assert r.status == "ready", (r.status, r.clarifications)
    wms = ops_by_type(r.plan)["watermark"]
    assert len(wms) == 2
    assert all(w["position"] == "top_left" for w in wms)


def test_revision_updates_multiple_watermarks(gen):
    prev = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        operations=[WatermarkOp(asset_id="watermark", position="top_left"),
                    WatermarkOp(asset_id="badge", position="bottom_right")])
    r = gen("make the logos half size", "clip.mp4", watermark="logo.png",
            extra={"badge": Asset(path="badge.png", kind=AssetKind.IMAGE)},
            previous_plan=prev)
    assert r.status == "ready", (r.status, r.clarifications)
    wms = ops_by_type(r.plan)["watermark"]
    assert [w["asset_id"] for w in wms] == ["watermark", "badge"]
    assert all(w["scale_percent"] == 50 for w in wms)
    # the follow-up names no corner — the multi-path default applies
    assert all(w["position"] == "bottom_right" for w in wms)


def test_make_it_smaller_asks(gen):
    r = gen("make it smaller", "clip.mp4")
    assert r.status == "needs_clarification"
    assert r.clarifications


def test_exact_file_size_asks(gen):
    r = gen("make the file exactly 5 MB", "clip.mp4")
    assert r.status == "needs_clarification"


def test_lossless_maps_to_prores(gen):
    # "lossless" is a video-quality request: it maps to the ProRes codec,
    # which lands in its natural container (mov), not a clarification.
    r = gen("re-encode lossless", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec"] == "prores"
    assert r.plan["output"]["container"] == "mov"


def test_end_before_start_is_rejected():
    # The schema itself rejects end<start; the adapter must not emit it.
    from fass.schema import TrimSpec
    from pydantic import ValidationError

    import pytest

    with pytest.raises(ValidationError):
        TrimSpec(start_seconds=30, end_seconds=5)


def test_missing_audio_no_metadata_reencodes_with_note(gen):
    r = gen("convert to mp4", "clip.mp4")  # no metadata
    assert r.status == "ready"
    assert any("re-encod" in d.lower() or "remux" in d.lower() for d in r.defaults)


def test_speed_change_is_supported(gen):
    r = gen("make it 2x faster", "clip.mp4")
    assert r.status == "ready"
    ops = [o["type"] for o in r.plan["operations"]]
    assert ops == ["speed"]


def test_speed_factor_out_of_range_is_unsupported_not_partial(gen):
    r = gen("make it 20x faster", "clip.mp4")
    assert r.status == "unsupported"
    assert r.unsupported
    assert r.plan is None  # never a partial plan


def test_speed_times_slower_inverts(gen):
    r = gen("make it 4 times slower", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "speed"][0]
    assert op["factor"] == 0.25


def test_speed_twice_as_fast(gen):
    r = gen("make it twice as fast", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "speed"][0]
    assert op["factor"] == 2.0


def test_speed_without_factor_clarifies(gen):
    r = gen("make it faster", "clip.mp4")
    assert r.status == "needs_clarification"
    assert any("factor" in c["question"].lower() for c in r.clarifications)


def test_speed_size_word_is_not_speed(gen):
    r = gen("make it 2x bigger", "clip.mp4")
    assert all(o["type"] != "speed" for o in (r.plan or {}).get("operations", []))


def test_rotate_is_supported(gen):
    r = gen("rotate the video 90 degrees", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "rotate"][0]
    assert op["angle"] == 90 and op["clockwise"] is True


def test_rotate_ccw_defaults_angle_with_note(gen):
    r = gen("rotate it counterclockwise", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "rotate"][0]
    assert op["angle"] == 90 and op["clockwise"] is False
    assert any("defaulted to 90" in n for n in r.defaults)


def test_rotate_non_quarter_turn_is_the_rotate_filter(gen):
    # M10.1: an arbitrary angle is the real rotate filter (padded frame),
    # not the quarter-turn transpose the RotateOp maps to
    r = gen("rotate the video 45 degrees", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["expr"].startswith("rotate=angle=45*PI/180")
    assert f[0]["domain"] == "video"


def test_crop_to_exact_dims(gen):
    r = gen("crop to 640x480", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "crop"][0]
    assert op["width"] == 640 and op["height"] == 480
    # the crop's dimensions are not also treated as a resize target
    assert all(o["type"] != "resize" for o in r.plan["operations"])


def test_crop_and_resize_take_different_dims(gen):
    r = gen("crop to 1280x720 and resize to 640x360", "clip.mp4")
    assert r.status == "ready"
    ops = {o["type"]: o for o in r.plan["operations"]}
    assert (ops["crop"]["width"], ops["crop"]["height"]) == (1280, 720)
    assert (ops["resize"]["width"], ops["resize"]["height"]) == (640, 360)


def test_crop_aspect_ratio_clarifies(gen):
    r = gen("crop to 16:9", "clip.mp4")
    assert r.status == "needs_clarification"


def test_crop_without_dims_clarifies(gen):
    r = gen("crop the video", "clip.mp4")
    assert r.status == "needs_clarification"


def test_resample_rate_and_channels(gen):
    r = gen("resample audio to 44.1k mono", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "audio_resample"][0]
    assert op["sample_rate"] == 44100 and op["channels"] == 1


def test_resample_hertz_form(gen):
    r = gen("resample the audio to 22050 hz", "clip.mp4")
    assert r.status == "ready"
    op = [o for o in r.plan["operations"] if o["type"] == "audio_resample"][0]
    assert op["sample_rate"] == 22050


def test_resample_bad_rate_is_unsupported(gen):
    r = gen("resample audio to 12345 hz", "clip.mp4")
    assert r.status == "unsupported"
    assert r.plan is None


def test_resample_without_target_clarifies(gen):
    r = gen("resample the audio", "clip.mp4")
    assert r.status == "needs_clarification"


def test_pixel_format_10bit(gen):
    r = gen("encode in 10-bit", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["pixel_format"] == "yuv420p10le"


def test_profile_detection(gen):
    r = gen("use the baseline profile for compatibility", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_profile"] == "baseline"


def test_preset_detection(gen):
    r = gen("ultrafast preset", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_preset"] == "ultrafast"


def test_encode_faster_is_a_preset_not_speed(gen):
    r = gen("encode faster with x264", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_preset"] == "faster"
    assert all(o["type"] != "speed" for o in r.plan["operations"])


def test_m4_ops_combo_order(gen):
    r = gen("crop to 640x480, make it 2x faster and rotate 90 degrees", "clip.mp4")
    assert r.status == "ready"
    ops = [o["type"] for o in r.plan["operations"]]
    assert ops == ["crop", "rotate", "speed"]


def test_flip_horizontally_is_a_video_filter(gen):
    # H-flip is a plain video filter (hflip), not an unsupported op.
    r = gen("flip the video horizontally", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f, "expected a filter op for the flip"
    assert "hflip" in f[0]["expr"]
    assert f[0]["domain"] == "video"


def test_extract_audio_to_mp3_is_audio_mode(gen):
    # Audio-only extraction: audio output mode, mp3 container, no video codec.
    r = gen("extract the audio to mp3", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "audio"
    assert out["container"] == "mp3"
    assert out["video_codec"] is None


def test_avc_synonym_for_h264(gen):
    r = gen("encode as AVC", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec"] == "h264"


def test_hevc_synonym_for_h265(gen):
    r = gen("encode as HEVC", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec"] == "h265"


def test_vp9_webm(gen):
    r = gen("convert to WebM with VP9", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "webm"
    assert r.plan["output"]["video_codec"] == "vp9"


def test_av1_detection(gen):
    r = gen("encode as AV1", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["video_codec"] == "av1"


def test_av1_webm(gen):
    r = gen("convert to WebM with AV1", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["container"] == "webm"
    assert r.plan["output"]["video_codec"] == "av1"


def test_av1_svt_detection(gen):
    # "SVT-AV1" is its own codec (libsvtav1), distinct from the plain AV1
    # (libaom) — the "SVT" qualifier must win the match over bare "AV1".
    r = gen("encode as SVT-AV1", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["video_codec"] == "av1_svt"


def test_vp8_webm(gen):
    r = gen("convert to WebM with VP8", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["container"] == "webm"
    assert r.plan["output"]["video_codec"] == "vp8"


def test_theora_lands_in_ogv(gen):
    r = gen("re-encode with theora", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["video_codec"] == "theora"
    assert r.plan["output"]["container"] == "ogv"


def test_vvc_detection(gen):
    r = gen("encode as VVC", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["video_codec"] == "vvc"


def test_h266_detection(gen):
    r = gen("encode as H.266", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["video_codec"] == "vvc"


def test_h264_in_webm_is_unsupported(gen):
    r = gen("convert to WebM with H.264", "clip.mp4")
    assert r.status == "unsupported"


def test_gif_conversion(gen):
    # The §9.2 held-out paraphrase ('Make it a gif for the docs page'): a
    # no-audio container is a ready plan — the planner drops the audio.
    r = gen("Make it a gif for the docs page", "clip.mp4")
    assert r.status == "ready", r.errors
    assert r.plan["output"]["container"] == "gif"
    assert r.plan["output"]["video_codec"] == "gif"


def test_remove_audio(gen):
    r = gen("remove the audio", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["audio_mode"] == "remove"


def test_first_n_seconds_trim(gen):
    r = gen("trim the first 20 seconds", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["trim"]["start_seconds"] == 0
    assert r.plan["trim"]["duration_seconds"] == 20


def test_last_n_seconds_needs_duration(gen):
    r = gen("keep the last 10 seconds", "clip.mp4")  # no duration
    assert r.status == "needs_clarification"


def test_last_n_seconds_with_duration(gen):
    r = gen("keep the last 10 seconds", "clip.mp4", media=make_media(duration=120))
    assert r.status == "ready"
    assert r.plan["trim"]["start_seconds"] == 110
    assert r.plan["trim"]["duration_seconds"] == 10


def test_composition_of_ops(gen):
    r = gen("cut from 00:10 to 00:30, resize to 720p, make it black and white, "
            "and remove the audio", "clip.mp4")
    assert r.status == "ready"
    t = ops_by_type(r.plan)
    assert r.plan["trim"]["start_seconds"] == 10
    assert r.plan["trim"]["duration_seconds"] == 20
    assert t["resize"][0]["height"] == 720
    assert "grayscale" in t
    assert r.plan["output"]["audio_mode"] == "remove"


def test_empty_instruction_asks(gen):
    r = gen("", "clip.mp4")
    assert r.status == "needs_clarification"


def test_nothing_supported_asks(gen):
    r = gen("hello there", "clip.mp4")
    assert r.status == "needs_clarification"


def test_revision_changes_only_what_is_asked(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h265"))
    r = gen("use H.264 instead", "clip.mp4", previous_plan=prev)
    assert r.status == "ready"
    assert r.plan["output"]["video_codec"] == "h264"
    assert r.plan["output"]["container"] == "mp4"  # preserved


def test_revision_unrelated_asks(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    r = gen("hello there", "clip.mp4", previous_plan=prev)
    assert r.status == "needs_clarification"


# ------------------------------------------------------- bitrate + mpegts

def test_cbr_bitrate_and_mpegts(gen):
    # "Mbps" is a bitrate, not a file size — must not clarify.
    r = gen("cut from 20 to 30 secs and transcode to cbr 10Mbps mpeg ts", "titan.mp4")
    assert r.status == "ready"
    assert r.plan["trim"]["start_seconds"] == 20
    assert r.plan["trim"]["duration_seconds"] == 10
    assert r.plan["output"]["container"] == "mpegts"
    assert r.plan["output"]["video_codec"] == "h264"
    assert r.plan["output"]["video_bitrate_kbps"] == 10000


def test_mbps_and_kbps_units(gen):
    r = gen("export at 5 mbps", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_bitrate_kbps"] == 5000
    r = gen("target 2000 kbps", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_bitrate_kbps"] == 2000


def test_exact_file_size_still_clarifies(gen):
    # Byte units remain a file-size target: the MVP cannot guarantee it.
    for instr in ("make it exactly 5 MB", "compress to 10 mb", "target a 2 GB file"):
        r = gen(instr, "clip.mp4")
        assert r.status == "needs_clarification", instr


def test_mpegts_container_variants(gen):
    for instr in ("convert to mpeg ts", "save as .ts", "make it a transport stream",
                  "convert to mts", "to m2ts please"):
        r = gen(instr, "clip.mp4")
        assert r.status == "ready", instr
        assert r.plan["output"]["container"] == "mpegts", instr


def test_mpegts_rejects_vp9_via_matrix(gen):
    r = gen("convert to mpeg ts with vp9", "clip.mp4")
    assert r.status == "unsupported"
    assert r.plan is None


def test_revision_sets_bitrate(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    r = gen("actually make it 12 mbps", "clip.mp4", previous_plan=prev)
    assert r.status == "ready"
    assert r.plan["output"]["video_bitrate_kbps"] == 12000


# ------------------------------------------------------------ audio replace

def test_audio_replace_named_asset(gen):
    r = gen("replace the audio with song.mp3", "clip.mp4", audio="song.mp3")
    assert r.status == "ready"
    ar = ops_by_type(r.plan)["audio_replace"]
    assert len(ar) == 1
    assert ar[0]["asset_id"] == "audio"
    assert r.plan["output"]["audio_mode"] == "encode"


def test_audio_replace_fallback_sole_audio_asset(gen):
    # asset id not mentioned in the instruction (nor a filename) → fallback
    # binds the sole audio asset and says so.
    tune = Asset(path="song.mp3", kind=guess_kind("song.mp3"))
    r = gen("replace the audio with the music file", "clip.mp4", extra={"tune": tune})
    assert r.status == "ready"
    ar = ops_by_type(r.plan)["audio_replace"]
    assert len(ar) == 1 and ar[0]["asset_id"] == "tune"
    assert any("only audio asset" in n for n in r.defaults)


def test_audio_replace_paraphrases(gen):
    for instr in (
        "swap the soundtrack to song.mp3",
        "change the music to song.mp3",
        "put song.mp3 as the audio",
        "use song.mp3 for the audio",
    ):
        r = gen(instr, "clip.mp4", audio="song.mp3")
        assert r.status == "ready", instr
        assert ops_by_type(r.plan)["audio_replace"][0]["asset_id"] == "audio", instr


def test_audio_replace_no_audio_asset_clarifies(gen):
    r = gen("replace the audio with song.mp3", "clip.mp4")
    assert r.status == "needs_clarification"
    assert any("audio" in c["question"].lower() for c in r.clarifications)


def test_audio_replace_wins_over_remove(gen):
    r = gen("remove the audio and replace it with song.mp3", "clip.mp4", audio="song.mp3")
    assert r.status == "ready"
    assert r.plan["output"]["audio_mode"] == "encode"
    assert len(ops_by_type(r.plan)["audio_replace"]) == 1


def test_audio_asset_alone_does_not_replace(gen):
    # merely binding an audio file must not add a replacement operation
    r = gen("resize to 720p", "clip.mp4", audio="song.mp3")
    assert r.status == "ready"
    assert "audio_replace" not in ops_by_type(r.plan)


def test_revision_remove_audio_clears_replacement(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[AudioReplaceOp(asset_id="audio")])
    r = gen("remove the audio", "clip.mp4", previous_plan=prev)
    assert r.status == "ready"
    assert "audio_replace" not in ops_by_type(r.plan)
    assert r.plan["output"]["audio_mode"] == "remove"


def test_revision_swap_replacement_asset(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[AudioReplaceOp(asset_id="audio")])
    jingle = Asset(path="jingle.wav", kind=guess_kind("jingle.wav"))
    r = gen("replace it with jingle.wav", "clip.mp4",
            extra={"jingle": jingle}, previous_plan=prev)
    assert r.status == "ready"
    ar = ops_by_type(r.plan)["audio_replace"]
    assert len(ar) == 1 and ar[0]["asset_id"] == "jingle"


# ------------------------------------------------------------- stream I/O (M3b)

def test_stream_to_rtmp_is_ready_with_flv(gen):
    # Naming a destination is itself an edit — it bypasses the no-edit gate.
    r = gen("stream this to rtmp://host:1935/live", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output_destination"] == "rtmp://host:1935/live"
    assert r.plan["output"]["container"] == "flv"
    assert r.plan["output"]["video_codec"] == "h264"
    assert r.plan["trim"] is None
    assert r.plan["operations"] == []


def test_publish_to_rtsp(gen):
    r = gen("publish the video to rtsp://10.0.0.5:554/live", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output_destination"] == "rtsp://10.0.0.5:554/live"
    assert r.plan["output"]["container"] == "rtsp"


def test_stream_destination_with_trim(gen):
    r = gen("cut the first 5 seconds and send it to udp://127.0.0.1:5004", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["trim"]["start_seconds"] == 0
    assert r.plan["trim"]["duration_seconds"] == 5
    assert r.plan["output_destination"] == "udp://127.0.0.1:5004"
    assert r.plan["output"]["container"] == "mpegts"


def test_h265_into_rtmp_is_unsupported(gen):
    # FLV carries h264 + aac only — h265 is rejected by the matrix.
    r = gen("stream this to rtmp://host:1935/live with h265", "clip.mp4")
    assert r.status == "unsupported"
    assert r.plan is None


def test_container_conflict_with_destination_clarifies(gen):
    r = gen("convert to webm and stream to rtmp://host:1935/live", "clip.mp4")
    assert r.status == "needs_clarification"
    assert r.plan is None
    q = r.clarifications[0]["question"].lower()
    assert "webm" in q and "flv" in q


def test_two_destinations_clarify(gen):
    r = gen("stream to rtmp://host:1935/live and also to srt://10.0.0.9:9000/show",
            "clip.mp4")
    assert r.status == "needs_clarification"
    assert len(r.clarifications) == 1
    assert r.clarifications[0]["question"].count("//") >= 2


def test_url_equal_to_bound_asset_is_not_a_destination(gen):
    # The only URL in the instruction is the bound input stream — it is not
    # an output destination.
    r = gen("resize the stream to 720p", "rtmp://host:1935/live")
    assert r.status == "ready"
    assert r.plan["input_id"] == "main"
    assert r.plan["output_destination"] is None


def test_stream_input_is_not_reported_missing(gen):
    # A stream URL never "exists" locally — that is its nature, not a problem.
    r = gen("resize to 720p", "rtmp://host:1935/live")
    assert r.status == "ready"
    assert "rtmp://host:1935/live" in r.command
    assert not any("not found on this machine" in w for w in r.warnings)


def test_trailing_punctuation_stripped_from_destination(gen):
    r = gen("cut the first 10 seconds and stream it to rtmp://host:1935/live.", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output_destination"] == "rtmp://host:1935/live"


def test_remote_audio_replace_by_basename(gen):
    radio = Asset(path="http://radio.example.com/tune.mp3",
                  kind=guess_kind("http://radio.example.com/tune.mp3"))
    r = gen("replace the audio with tune.mp3", "clip.mp4", extra={"radio": radio})
    assert r.status == "ready"
    ar = ops_by_type(r.plan)["audio_replace"]
    assert len(ar) == 1 and ar[0]["asset_id"] == "radio"


def test_revision_preserves_stream_destination(gen):
    prev = EditPlan(output=OutputSpec(container="flv", video_codec="h264"),
                    output_destination="rtmp://host:1935/live")
    r = gen("make it black and white", "clip.mp4", previous_plan=prev)
    assert r.status == "ready"
    assert r.plan["output_destination"] == "rtmp://host:1935/live"
    assert r.plan["output"]["container"] == "flv"
    assert any(op["type"] == "grayscale" for op in r.plan["operations"])


def test_revision_new_destination_updates_container(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    r = gen("stream to srt://10.0.0.9:9000/show instead", "clip.mp4",
            previous_plan=prev)
    assert r.status == "ready"
    assert r.plan["output_destination"] == "srt://10.0.0.9:9000/show"
    assert r.plan["output"]["container"] == "srt"


# ---------------------------------------------------------------- M7 (2026-10)
# Output modes, broad containers/codecs, filters + filtergraph, format
# options, stream I/O.


def test_extract_audio_to_mp2_128k(gen):
    """The user-reported driving case, typo included."""
    r = gen("exctract audio track and transcode it to mp2 audio 128kbps", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "audio"
    assert out["container"] == "mp2"
    assert out["audio_codec"] == "mp2"
    assert out["audio_bitrate_kbps"] == 128
    assert out["video_codec"] is None


def test_extract_audio_to_m4a(gen):
    r = gen("extract the audio to m4a", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "audio"
    assert out["container"] == "m4a"
    assert out["audio_codec"] == "aac"


def test_extract_audio_lossless_is_flac(gen):
    r = gen("extract the audio lossless", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "audio"
    assert out["container"] == "flac"
    assert out["audio_codec"] == "flac"
    assert out["audio_bitrate_kbps"] is None


def test_extract_audio_to_wav_is_pcm(gen):
    r = gen("extract the audio to wav", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "audio"
    assert out["container"] == "wav"
    assert out["audio_codec"] == "pcm_s16le"


def test_prores_lands_in_mov(gen):
    r = gen("re-encode with prores", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "video"
    assert out["video_codec"] == "prores"
    assert out["container"] == "mov"


def test_avi_defaults_to_mpeg4(gen):
    r = gen("convert to avi", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "avi"
    assert out["video_codec"] == "mpeg4"


def test_mxf_container(gen):
    r = gen("convert to mxf", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "mxf"
    assert out["video_codec"] == "h264"


def test_hls_container(gen):
    r = gen("convert to hls", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "hls"


def test_dash_container(gen):
    r = gen("convert to dash", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "dash"


def test_dash_word_phrasings(gen):
    r = gen("make a dash", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "dash"


def test_mpd_extension_phrasing_is_dash(gen):
    r = gen("convert to a .mpd", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "dash"


# ---------------- M13.2: live HLS (rolling event playlist)

def test_live_hls_event_playlist(gen):
    r = gen("live hls", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert out["format_options"] == {
        "hls_playlist_type": "event",
        "hls_flags": "independent_segments"}


def test_live_hls_window(gen):
    r = gen("live hls with a 10 segment window", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["format_options"] == {
        "hls_playlist_type": "event",
        "hls_flags": "independent_segments",
        "hls_list_size": "10"}
    a = r.arguments
    assert a[a.index("-f") + 1] == "hls"
    assert a[a.index("-hls_playlist_type") + 1] == "event"
    assert a[a.index("-hls_list_size") + 1] == "10"
    assert a[a.index("-hls_flags") + 1] == "independent_segments"


def test_live_hls_delete_segments_opt_in(gen):
    # delete_segments is OPT-IN (deleting rolled-off segments breaks
    # DVR/seek) — only the explicit phrasing adds it
    r = gen("live hls with a 5 segment window and delete old segments",
            "clip.mp4")
    assert r.status == "ready"
    fo = r.plan["output"]["format_options"]
    assert fo["hls_list_size"] == "5"
    # '+'-joined FFmpeg flag syntax (the flagset validator's form)
    assert set(fo["hls_flags"].split("+")) == {
        "independent_segments", "delete_segments"}


def test_hls_without_live_stays_vod(gen):
    r = gen("convert to hls", "clip.mp4")
    assert r.status == "ready"
    fo = r.plan["output"].get("format_options") or {}
    assert "hls_playlist_type" not in fo
    assert "delete_segments" not in (fo.get("hls_flags") or "")


def test_live_hls_finite_input_warns(gen):
    # an event playlist over a finite file ends with #EXT-X-ENDLIST
    # (probed) — the run is allowed, the warning is owed
    r = gen("live hls", "clip.mp4")
    assert r.status == "ready"
    assert any("ENDLIST" in w for w in r.warnings)


def test_live_hls_stream_input_no_endlist_warning(gen):
    r = gen("live hls", "rtsp://cam.local/stream")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "hls"
    assert not any("ENDLIST" in w for w in r.warnings)


# ---------------- M13b: -re pseudo-live (input pacing at native frame rate)

def test_pseudo_live_hls_reference_case(gen):
    # plan reference case 6: the live HLS tail plus -re in front of -i
    r = gen("pseudo-live hls with a 10 segment window", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["realtime_input"] is True
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert out["format_options"] == {
        "hls_playlist_type": "event",
        "hls_flags": "independent_segments",
        "hls_list_size": "10"}
    a = r.arguments
    assert a[a.index("-i") - 1] == "-re"
    assert a[a.index("-hls_playlist_type") + 1] == "event"
    assert a[a.index("-hls_list_size") + 1] == "10"
    assert a[a.index("-hls_flags") + 1] == "independent_segments"


def test_pseudo_live_plain_output(gen):
    r = gen("convert to mp4 pseudo-live", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["realtime_input"] is True
    assert r.arguments[r.arguments.index("-i") - 1] == "-re"


def test_read_at_real_time_phrasings(gen):
    for phrasing in ("read at real time and convert to mp4",
                     "read the input at real time and convert to mp4",
                     "read the input at real time, hls"):
        r = gen(phrasing, "clip.mp4")
        assert r.status == "ready"
        assert r.plan["realtime_input"] is True


def test_pseudo_live_word_variants(gen):
    for phrasing in ("make it pseudo live hls", "pseudolive hls"):
        r = gen(phrasing, "clip.mp4")
        assert r.status == "ready"
        assert r.plan["realtime_input"] is True


def test_live_hls_without_pseudo_stays_fast(gen):
    # M13.2's line is untouched: 'live hls' carries no -re
    r = gen("live hls", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["realtime_input"] is False
    assert "-re" not in r.arguments


def test_no_pacing_negation(gen):
    r = gen("pseudo-live hls but no pacing", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["realtime_input"] is False
    assert "-re" not in r.arguments


def test_pseudo_live_hls_finite_still_warns_endlist(gen):
    # pacing does not remove the finite-input ENDLIST honesty (M13.2)
    r = gen("pseudo-live hls", "clip.mp4")
    assert r.status == "ready"
    assert any("ENDLIST" in w for w in r.warnings)


def test_pseudo_live_stream_input_warns_no_effect(gen):
    r = gen("pseudo-live hls", "rtsp://cam.local/stream")
    assert r.status == "ready"
    assert any("no effect" in w for w in r.warnings)
    assert r.arguments[r.arguments.index("-i") - 1] == "-re"


def test_pseudo_live_paces_main_input_only(gen):
    # the watermark is an auxiliary input — it is never paced
    r = gen("pseudo-live with the watermark", "clip.mp4",
            watermark="logo.png")
    assert r.status == "ready"
    a = r.arguments
    i = a.index("-i")
    assert a[i - 1] == "-re"
    j = a.index("-i", i + 1)
    assert a[j - 1] != "-re"


def test_revision_turns_pacing_on(gen):
    prev = EditPlan(
        output=OutputSpec(container="hls", video_codec="h264",
                          format_options={"hls_playlist_type": "event",
                                          "hls_flags": "independent_segments"}))
    r = gen("now read the input at real time", "clip.mp4", previous_plan=prev)
    assert r.status == "ready"
    assert r.plan["realtime_input"] is True
    assert r.arguments[r.arguments.index("-i") - 1] == "-re"


def test_revision_turns_pacing_off(gen):
    prev = EditPlan(
        output=OutputSpec(container="hls", video_codec="h264",
                          format_options={"hls_playlist_type": "event",
                                          "hls_flags": "independent_segments"}),
        realtime_input=True)
    r = gen("no pacing", "clip.mp4", previous_plan=prev)
    assert r.status == "ready"
    assert r.plan["realtime_input"] is False
    assert "-re" not in r.arguments


# ---------------- M13.5: tee multi-output (hls + dash from one encode)

def test_tee_pair_selects_tee(gen):
    r = gen("convert to hls and dash at the same time", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "tee"


def test_tee_pair_reverse_order(gen):
    r = gen("make dash and hls", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "tee"


def test_tee_word_phrasing(gen):
    r = gen("tee to hls and dash", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "tee"


def test_tee_single_container_is_not_tee(gen):
    r = gen("convert to hls", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "hls"


def test_tee_argv_bracket_template(gen):
    r = gen("tee to hls and dash", "clip.mp4")
    assert r.status == "ready"
    a = r.arguments
    assert a[a.index("-f") + 1] == "tee"
    assert a[-1] == "[f=hls]clip.m3u8|[f=dash]clip.mpd"


def test_tee_live_window_routes_into_hls_branch(gen):
    r = gen("live hls and dash with a 10 segment window", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["container"] == "tee"
    a = r.arguments
    hls_branch, dash_branch = a[-1].split("|")
    assert hls_branch.startswith("[f=hls:")
    assert hls_branch.endswith("clip.m3u8")
    assert "hls_list_size=10" in hls_branch
    assert "hls_playlist_type=event" in hls_branch
    assert "hls_flags=independent_segments" in hls_branch
    assert dash_branch == "[f=dash]clip.mpd"
    # the tee plan carries its options INSIDE the brackets — no separate flags
    assert "-hls_list_size" not in a


def test_tee_finite_input_warns(gen):
    # the hls branch's event playlist over a finite file ends with
    # #EXT-X-ENDLIST — same honesty warning as plain live HLS (M13.2)
    r = gen("live hls and dash", "clip.mp4")
    assert r.status == "ready"
    assert any("ENDLIST" in w for w in r.warnings)


# ---------------- M13.3: ABR HLS ladder

def test_ladder_explicit_levels(gen):
    r = gen("abr hls with levels 216p 300k, 360p 700k, 480p 1500k",
            "clip.mp4", media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert [(v["height"], v["video_bitrate_kbps"]) for v in out["variants"]] == [
        (216, 300), (360, 700), (480, 1500)]
    # the per-level '216p' tokens must not double-read as a top-level
    # resize or a top-level bitrate
    assert not any(op["type"] == "resize" for op in r.plan["operations"])
    assert out["video_bitrate_kbps"] is None


def test_ladder_default_table_request(gen):
    r = gen("abr hls", "clip.mp4", media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert out["variants"] == []


def test_ladder_count_only(gen):
    r = gen("make an hls ladder with 3 quality levels", "clip.mp4",
            media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert out["variants"] == []


def test_dash_ladder(gen):
    r = gen("dash ladder", "clip.mp4", media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "dash"
    assert out["variants"] == []


def test_dash_ladder_explicit_levels(gen):
    r = gen("dash ladder with levels 216p 300k, 480p 1500k", "clip.mp4",
            media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "dash"
    assert len(out["variants"]) == 2
    v0, v1 = out["variants"]
    assert (v0["height"], v0["video_bitrate_kbps"]) == (216, 300)
    assert (v1["height"], v1["video_bitrate_kbps"]) == (480, 1500)


def test_abr_dash(gen):
    r = gen("abr dash", "clip.mp4", media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "dash"
    assert out["variants"] == []


def test_dash_word_then_ladder(gen):
    r = gen("convert to dash and make an abr ladder", "clip.mp4",
            media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "dash"
    assert out["variants"] == []


def test_ladder_default_container_is_hls(gen):
    # a ladder without a container word: segmented output, HLS is the home
    r = gen("make me a ladder with 3 quality levels", "clip.mp4",
            media=make_media())
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert out["variants"] == []
    assert any("ladder" in n.lower() for n in r.defaults)


def test_ladder_conflicts_with_resize(gen):
    r = gen("abr hls and resize to 480p", "clip.mp4", media=make_media())
    assert r.status == "needs_clarification"


def test_ladder_conflicts_with_grayscale(gen):
    r = gen("abr hls with levels 360p 700k and make it grayscale",
            "clip.mp4", media=make_media())
    assert r.status == "needs_clarification"


def test_ladder_conflicts_with_audio_remove(gen):
    r = gen("abr hls with no audio", "clip.mp4", media=make_media())
    assert r.status == "needs_clarification"


def test_ladder_trim_allowed(gen):
    r = gen("abr hls and cut the first 10 seconds", "clip.mp4",
            media=make_media())
    assert r.status == "ready"
    assert r.plan["trim"] is not None
    assert r.plan["output"]["variants"] == []


def test_live_hls_is_not_a_ladder(gen):
    r = gen("live hls with a 10 segment window", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"].get("variants") is None


def test_mpeg2_for_broadcast_is_mpegts(gen):
    r = gen("convert to mpeg2 for broadcast mpegts", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "mpegts"
    assert out["video_codec"] == "mpeg2"


def test_mpeg2_video_with_mp2_audio(gen):
    r = gen("convert to mpeg2 video and mp2 audio", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "mpegts"
    assert out["video_codec"] == "mpeg2"
    assert out["audio_codec"] == "mp2"


def test_video_only_removes_audio(gen):
    for instr in ("make it video only, no audio", "remove the audio"):
        r = gen(instr, "clip.mp4")
        assert r.status == "ready", instr
        assert r.plan["output"]["audio_mode"] == "remove"


@pytest.mark.parametrize("instr,container", [
    ("extract the subtitles to a file", "srt_file"),
    ("dump the subtitles as vtt", "vtt_file"),
    ("extract the subtitles to ass", "ass_file"),
])
def test_subtitle_dump_formats(gen, instr, container):
    subs = Asset(path="subs.srt", kind=AssetKind.SUBTITLES)
    r = gen(instr, "clip.mp4", extra={"subs": subs})
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "subtitles"
    assert out["container"] == container


def test_flip_vertically_is_a_video_filter(gen):
    r = gen("flip vertically", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and "vflip" in f[0]["expr"]
    assert f[0]["domain"] == "video"


def test_volume_up_is_an_audio_filter(gen):
    r = gen("make the audio louder", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["expr"].startswith("volume=")
    assert f[0]["domain"] == "audio"


def test_loudnormalization_is_an_audio_filter(gen):
    r = gen("normalize the audio loudness", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["expr"] == "loudnorm"
    assert f[0]["domain"] == "audio"


def test_raw_video_filter_expression(gen):
    r = gen("apply the video filter 'eq=brightness=0.2'", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["expr"] == "eq=brightness=0.2"
    assert f[0]["domain"] == "video"


def test_pasted_filtergraph_quoted(gen):
    r = gen("use this filtergraph: 'split[a][b]'", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["filtergraph"] == "split[a][b]"
    assert not [o for o in r.plan["operations"] if o["type"] == "filter"]


def test_pasted_filtergraph_unquoted(gen):
    r = gen("use this filtergraph: split=2[a][b]", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["filtergraph"] == "split=2[a][b]"


def test_pasted_filtergraph_complex(gen):
    graph = "[0:v]split[a][b];[a]eq=contrast=1.2[v0];[b]eq=saturation=0[v1]"
    r = gen(f'apply the filtergraph "{graph}"', "clip.mp4")
    assert r.status == "ready"
    assert r.plan["filtergraph"] == graph


def test_filtergraph_conflicts_with_video_edits(gen):
    r = gen("use this filtergraph: 'split[a][b]' and resize to 720p", "clip.mp4")
    assert r.status == "needs_clarification"
    assert any("filtergraph" in c["question"] for c in r.clarifications)


def test_fade_out_computed_from_duration(gen):
    r = gen("fade out the video at the end", "clip.mp4", media=make_media())
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["expr"] == "fade=t=out:st=118.5:d=1.5"


def test_faststart_format_option(gen):
    r = gen("convert to mp4 with faststart", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["format_options"] == {"movflags": "faststart"}


def test_hls_segment_duration(gen):
    r = gen("make an hls with 6 second segments", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["container"] == "hls"
    assert out["format_options"] == {"hls_time": "6"}


def test_http_output_destination(gen):
    r = gen("send the output to http://192.168.0.50/stream.ts", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output_destination"] == "http://192.168.0.50/stream.ts"
    assert r.plan["output"]["container"] == "mpegts"


def test_srt_stream_input_is_accepted(gen):
    r = gen("resize to 720p", "srt://192.168.0.50:5000")
    assert r.status == "ready"
    assert not r.warnings
    assert "resize" in ops_by_type(r.plan)


def test_h264_with_aac_audio(gen):
    r = gen("convert to h264 with aac audio", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "video"
    assert out["video_codec"] == "h264"
    assert out["audio_codec"] == "aac"


def test_replace_audio_by_dot_filename(gen):
    song = Asset(path="song.mp3", kind=AssetKind.AUDIO)
    r = gen("replace the audio with song.mp3", "clip.mp4", extra={"song": song})
    assert r.status == "ready"
    ar = ops_by_type(r.plan)["audio_replace"]
    assert len(ar) == 1 and ar[0]["asset_id"] == "song"


def test_audio_mode_keeps_audio_only_filters(gen):
    r = gen("extract the audio, make it louder, to mp3", "clip.mp4")
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "audio"
    assert out["container"] == "mp3"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["domain"] == "audio"


# ------------------------------------------------------------
# Source-relative resize: the instruction references the SOURCE's
# own dimensions, resolved from the probe instead of asking.


def _resize_target(plan):
    rs = ops_by_type(plan).get("resize")
    if not rs:
        return None
    r = rs[0]
    return (r.get("width"), r.get("height"), r.get("aspect_ratio"))


def test_resize_aspect_keep_source_height(gen):
    m = make_media(width=1280, height=720)
    r = gen("resize video to 4:3, keep the same height as in source file",
            "clip.mp4", media=m)
    assert r.status == "ready"
    assert _resize_target(r.plan) == (960, 720, "exact")
    assert any("height kept from the source" in d for d in r.defaults)


def test_resize_aspect_uses_source_height_by_default(gen):
    # 'resize to 16:9' with no explicit height keeps the probed height.
    m = make_media(width=320, height=240)
    r = gen("resize to 16:9", "clip.mp4", media=m)
    assert r.status == "ready"
    assert _resize_target(r.plan) == (428, 240, "exact")
    assert any("keeps the source height 240" in d for d in r.defaults)


def test_resize_keep_source_width(gen):
    m = make_media(width=1280, height=720)
    r = gen("keep the same width as the source file and make it 4:3",
            "clip.mp4", media=m)
    assert r.status == "ready"
    assert _resize_target(r.plan) == (1280, 960, "exact")


def test_resize_to_same_resolution_as_source(gen):
    m = make_media(width=1280, height=720)
    r = gen("resize to the same resolution as the source file",
            "clip.mp4", media=m)
    assert r.status == "ready"
    assert _resize_target(r.plan) == (1280, 720, "exact")
    assert any("matches the source's own size" in d for d in r.defaults)


def test_resize_aspect_without_probe_clarifies(gen):
    # No probe → the source height is unknown → ask, don't guess.
    r = gen("resize to 16:9", "clip.mp4")
    assert r.status == "needs_clarification"


def test_resize_source_ref_without_probe_clarifies(gen):
    r = gen("keep the same height as the source file", "clip.mp4")
    assert r.status == "needs_clarification"


def test_timecode_not_treated_as_resize(gen):
    # 'trim 0:30 to 1:00' has digits that must NOT read as WxH.
    m = make_media(width=1280, height=720)
    r = gen("trim 0:30 to 1:00", "clip.mp4", media=m)
    assert r.status == "ready"
    assert "resize" not in ops_by_type(r.plan)


def test_resize_after_cut_keeps_source_dims(gen):
    m = make_media(width=640, height=360)
    r = gen("cut from 00:10 to 00:30 and resize to 16:9", "clip.mp4", media=m)
    assert r.status == "ready"
    assert _resize_target(r.plan) == (640, 360, "exact")
    assert any("matches the source's own size" in d for d in r.defaults)


# ------------------------------------------------------------
# Same-value warnings: a conversion to the source's OWN value warns
# (the op stays in the plan — warn-only, never dropped).


def _warns(r, frag):
    return any(frag in d for d in r.defaults)


def _full_media():
    return make_media(width=1280, height=720, fps=25.0, pix_fmt="yuv420p",
                      video_bitrate=2_386_000, audio_bitrate=127_000,
                      audio_sample_rate=48000)


def test_warn_video_bitrate_same(gen):
    r = gen("cbr 2.4 Mbps", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "video bitrate 2400k matches")


def test_no_warn_video_bitrate_different(gen):
    r = gen("cbr 1.5 Mbps", "clip.mp4", media=_full_media())
    assert not _warns(r, "video bitrate 1500k matches")


def test_warn_audio_bitrate_same(gen):
    r = gen("re-encode audio to aac 128 kbps", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "audio bitrate 128k matches")


def test_no_warn_audio_bitrate_different(gen):
    r = gen("re-encode audio to aac 96 kbps", "clip.mp4", media=_full_media())
    assert not _warns(r, "audio bitrate 96k matches")


def test_warn_sample_rate_same(gen):
    r = gen("resample audio to 48k", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "48000 Hz is the source's own")


def test_no_warn_sample_rate_different(gen):
    r = gen("resample audio to 44.1k", "clip.mp4", media=_full_media())
    assert not _warns(r, "44100 Hz is the source's own")


def test_warn_pixel_format_same(gen):
    r = gen("convert to yuv420p", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "pixel format yuv420p is the source's own")


def test_warn_fps_same_keeps_filter(gen):
    r = gen("set it to 25 fps", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "25 fps matches the source's own")
    assert any(o["type"] == "filter" and o["expr"].startswith("fps=fps=")
               for o in r.plan["operations"])


def test_no_warn_fps_different(gen):
    r = gen("set it to 24 fps", "clip.mp4", media=_full_media())
    assert not _warns(r, "24 fps matches the source's own")


def test_warn_geometry_same_keeps_resize(gen):
    r = gen("resize to 1280x720", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "matches the source's own size")
    assert "resize" in ops_by_type(r.plan)  # warn-only: op retained


def test_warn_geometry_720p_same_keeps_resize(gen):
    r = gen("resize to 720p", "clip.mp4", media=_full_media())
    assert r.status == "ready"
    assert _warns(r, "matches the source's own size")
    assert "resize" in ops_by_type(r.plan)
