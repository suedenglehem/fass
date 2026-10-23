"""Rule-based NLU behavior — the PRD §13 acceptance scenarios and paraphrases.

Assertions check the *plan* (what), not the command wording: status,
operation multiset, trim interval, container/codec/audio mode.
"""

from __future__ import annotations

from fass.schema import Asset, AudioReplaceOp, EditPlan, OutputSpec, guess_kind
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


def test_make_it_smaller_asks(gen):
    r = gen("make it smaller", "clip.mp4")
    assert r.status == "needs_clarification"
    assert r.clarifications


def test_exact_file_size_asks(gen):
    r = gen("make the file exactly 5 MB", "clip.mp4")
    assert r.status == "needs_clarification"


def test_lossless_asks(gen):
    r = gen("re-encode lossless", "clip.mp4")
    assert r.status == "needs_clarification"


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


def test_rotate_non_quarter_turn_is_unsupported(gen):
    r = gen("rotate the video 45 degrees", "clip.mp4")
    assert r.status == "unsupported"
    assert r.plan is None


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


def test_flip_still_unsupported(gen):
    r = gen("flip the video horizontally", "clip.mp4")
    assert r.status == "unsupported"


def test_extract_audio_is_unsupported(gen):
    r = gen("extract the audio to mp3", "clip.mp4")
    assert r.status == "unsupported"


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


def test_h264_in_webm_is_unsupported(gen):
    r = gen("convert to WebM with H.264", "clip.mp4")
    assert r.status == "unsupported"


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
