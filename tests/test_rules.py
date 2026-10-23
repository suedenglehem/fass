"""Rule-based NLU behavior — the PRD §13 acceptance scenarios and paraphrases.

Assertions check the *plan* (what), not the command wording: status,
operation multiset, trim interval, container/codec/audio mode.
"""

from __future__ import annotations

from fass.schema import EditPlan, OutputSpec
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


def test_speed_change_is_unsupported_not_partial(gen):
    r = gen("make it 2x faster", "clip.mp4")
    assert r.status == "unsupported"
    assert r.unsupported
    assert r.plan is None  # never a partial plan


def test_rotate_is_unsupported(gen):
    r = gen("rotate the video 90 degrees", "clip.mp4")
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
