"""Bitstream filters for stream-copy remuxes: registry lookups and the
planner wiring for both directions (video mp4/mov -> mpegts Annex-B restamp,
audio mpegts -> mp4/mov ADTS strip). Plans are built directly — the NLU
layer is not involved."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fass import execute
from fass.api import app
from fass.builder import build_argv
from fass.planner import PlanError, plan_execution
from fass.probe.ffprobe import ffmpeg_available, ffprobe_available, probe_media
from fass.registry import audio_bsf_for_copy, bsf_errors, bsf_for_copy, bsf_spec
from fass.schema import (
    Asset,
    AssetKind,
    AudioMode,
    BitstreamFilterSpec,
    EditPlan,
    OutputSpec,
    TrimSpec,
)
from tests.conftest import make_media

client = TestClient(app)

ALL_BSFS = {"trace_headers", "showinfo", "setts", "prores_metadata",
            "mpeg2_metadata", "h264_mp4toannexb", "hevc_mp4toannexb",
            "hevc_metadata", "h264_metadata", "filter_units",
            "av1_metadata", "aac_adtstoasc", "trim"}

REPO = Path(__file__).resolve().parent.parent
TITAN = REPO / "media" / "titan.mp4"


def ep_for(input_path="clip.mp4", output=None, media=None, out=None):
    output = output or OutputSpec(container="mp4", video_codec="h264")
    edit = EditPlan(output=output, operations=[])
    assets = {"main": Asset(path=input_path, kind=AssetKind.VIDEO)}
    return plan_execution(edit, assets, media, out)


# ------------------------------------------------------------- registry lookups

def test_audio_bsf_mpegts_to_mp4_aac():
    assert audio_bsf_for_copy("mpegts", "mp4", "aac") == "aac_adtstoasc"


def test_audio_bsf_mpegts_to_mov_aac():
    assert audio_bsf_for_copy("mpegts", "mov", "aac") == "aac_adtstoasc"


def test_audio_bsf_none_for_other_audio_codec():
    assert audio_bsf_for_copy("mpegts", "mp4", "opus") is None


def test_audio_bsf_none_for_other_source_container():
    assert audio_bsf_for_copy("mp4", "mp4", "aac") is None


def test_audio_bsf_none_for_other_target_container():
    assert audio_bsf_for_copy("mpegts", "mpegts", "aac") is None


def test_audio_bsf_none_for_unknown_source_container():
    assert audio_bsf_for_copy("", "mp4", "aac") is None


def test_video_bsf_direction_unchanged():
    # The video bsf stays exactly as before: mp4/mov -> mpegts restamps
    # Annex-B; the reverse direction needs no video filter.
    assert bsf_for_copy("mp4", "mpegts", "h264") == "h264_mp4toannexb"
    assert bsf_for_copy("mp4", "mpegts", "h265") == "hevc_mp4toannexb"
    assert bsf_for_copy("mpegts", "mp4", "h264") is None


# ------------------------------------------------------------- planner copy plans

def test_copy_ts_to_mp4_aac_gets_audio_bsf():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(input_path="in.ts",
                output=OutputSpec(container="mp4", video_codec="h264"),
                media=media)
    assert ep.mode == "copy"
    opts = ep.output_opts
    # MPEG-TS video is already Annex-B: no video bsf in this direction
    assert "-bsf:v" not in opts
    assert opts[opts.index("-bsf:a"):opts.index("-bsf:a") + 2] == ["-bsf:a", "aac_adtstoasc"]
    assert any("aac_adtstoasc" in d for d in ep.defaults)
    argv = build_argv(ep)
    assert argv[argv.index("-bsf:a") + 1] == "aac_adtstoasc"


def test_copy_ts_to_mp4_audio_removed_has_no_audio_bsf():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(input_path="in.ts",
                output=OutputSpec(container="mp4", video_codec="h264",
                                  audio_mode=AudioMode.REMOVE),
                media=media)
    assert ep.mode == "copy"
    assert "-an" in ep.output_opts
    assert "-bsf:a" not in ep.output_opts


def test_copy_ts_to_mp4_hevc_aac_still_no_video_bsf():
    media = make_media(video_codec="h265", audio_codec="aac", has_audio=True)
    ep = ep_for(input_path="in.ts",
                output=OutputSpec(container="mp4", video_codec="h265"),
                media=media)
    assert ep.mode == "copy"
    assert "-bsf:v" not in ep.output_opts
    assert ["-bsf:a", "aac_adtstoasc"] == ep.output_opts[
        ep.output_opts.index("-bsf:a"):ep.output_opts.index("-bsf:a") + 2]


def test_copy_mp4_to_mp4_needs_no_bsf_at_all():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(input_path="in.mp4",
                output=OutputSpec(container="mp4", video_codec="h264"),
                media=media)
    assert ep.mode == "copy"
    assert "-bsf:v" not in ep.output_opts
    assert "-bsf:a" not in ep.output_opts


def test_copy_mp4_to_mpegts_video_bsf_only():
    # MPEG-TS accepts ADTS-framed AAC as-is: the audio direction needs no
    # filter going into TS.
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = ep_for(input_path="in.mp4",
                output=OutputSpec(container="mpegts", video_codec="h264"),
                media=media)
    assert ep.mode == "copy"
    opts = ep.output_opts
    assert opts[opts.index("-bsf:v") + 1] == "h264_mp4toannexb"
    assert "-bsf:a" not in opts


# ------------------------------------------------------------- real-media outcome

def _probe_audio_sample_rate(path: Path) -> int:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert p.returncode == 0, p.stderr
    return int(p.stdout.strip())


def _probe_format_name(path: Path) -> str:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.mark.skipif(
    not (ffmpeg_available() and ffprobe_available() and TITAN.exists()),
    reason="ffmpeg/ffprobe or media/titan.mp4 not available — media-outcome test needs real media",
)
def test_titan_ts_to_mp4_audio_bsf_remux(tmp_path):
    # titan.mp4 -> in.ts (video restamped to Annex-B) -> out.mp4 copy remux:
    # the ADTS header must be stripped for the AAC copy to land in MP4 intact.
    ts = tmp_path / "in.ts"
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(TITAN),
         "-c", "copy", "-bsf:v", "h264_mp4toannexb", str(ts)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert p.returncode == 0, p.stderr
    src = probe_media(str(ts))
    assert src is not None and src.audio_codec == "aac"

    out = tmp_path / "out.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    assets = {"main": Asset(path=str(ts), kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, src, str(out))
    assert ep.mode == "copy"
    argv = build_argv(ep)
    assert argv[argv.index("-bsf:a") + 1] == "aac_adtstoasc"

    proc = subprocess.run(
        argv, shell=False, cwd=str(tmp_path),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, f"argv={argv}\nstderr={proc.stderr}"
    info = probe_media(str(out))
    assert info is not None, f"output {out} is not probeable"
    assert _probe_format_name(out).startswith("mov,mp4")
    # the copied AAC stream survived the ADTS strip with its rate intact
    assert _probe_audio_sample_rate(out) == 48000
    assert info.duration is not None and src.duration is not None
    assert abs(info.duration - src.duration) <= 0.5


# ------------------------------------------------------------- registry: bsf_errors

def test_bsf_spec_lookup():
    spec = bsf_spec("h264_metadata")
    assert spec is not None and spec["kind"] == "video"
    assert bsf_spec("no_such_filter") is None


def test_bsf_errors_unknown_name():
    errs = bsf_errors("no_such_filter", {})
    assert errs and "no_such_filter" in errs[0]


def test_bsf_errors_copy_cut_takes_no_options():
    assert bsf_errors("trim", {"discard": "all"})
    assert bsf_errors("trim", {}) == []


def test_bsf_errors_valid_combos_pass():
    assert bsf_errors("h264_metadata",
                      {"level": "4.1", "flip": "horizontal"}) == []
    assert bsf_errors("filter_units", {"discard": "all"}) == []
    assert bsf_errors("setts", {"time_base": "1/30000", "prescale": "1"}) == []
    assert bsf_errors("trace_headers", {}) == []
    assert bsf_errors("aac_adtstoasc", {}) == []


def test_bsf_errors_unknown_key():
    errs = bsf_errors("h264_metadata", {"bogus": "1"})
    assert errs and "bogus" in errs[0]


def test_bsf_errors_bad_values():
    assert bsf_errors("h264_metadata", {"level": "9.9"})  # outside the level table
    assert bsf_errors("h264_metadata", {"rotate": "900"})  # outside -360..360
    assert bsf_errors("setts", {"time_base": "not-a-rational"})


# ------------------------------------------------------------- schema

def test_bsf_spec_rejects_unknown_name():
    with pytest.raises(ValidationError):
        BitstreamFilterSpec(name="no_such_filter")


def test_bsf_spec_rejects_bad_option():
    with pytest.raises(ValidationError):
        BitstreamFilterSpec(name="h264_metadata", options={"level": "9.9"})


def test_editplan_bsf_max_eight():
    with pytest.raises(ValidationError):
        EditPlan(
            output=OutputSpec(container="mp4", video_codec="h264"),
            bitstream_filters=[BitstreamFilterSpec(name="trace_headers")] * 9,
        )


def test_trim_bsf_switches_trim_to_copy_and_is_dropped():
    ep = EditPlan(
        trim=TrimSpec(start_seconds=10, duration_seconds=10),
        output=OutputSpec(container="mp4", video_codec="h264"),
        bitstream_filters=[BitstreamFilterSpec(name="trim")],
    )
    assert ep.trim.stream_copy is True
    assert ep.bitstream_filters is None


def test_trim_bsf_without_trim_rejected():
    with pytest.raises(ValidationError):
        EditPlan(
            output=OutputSpec(container="mp4", video_codec="h264"),
            bitstream_filters=[BitstreamFilterSpec(name="trim")],
        )


# ------------------------------------------------------------- min_version notes

@pytest.fixture
def _fake_build():
    """Pin the planner's probed local FFmpeg version per test."""
    import fass.planner.planner as planner
    old = (planner._ffmpeg_version_cache, planner._ffmpeg_version_probed)
    yield planner
    planner._ffmpeg_version_cache, planner._ffmpeg_version_probed = old


def _pin(planner, ver):
    planner._ffmpeg_version_cache = ver
    planner._ffmpeg_version_probed = True


def test_version_note_below_min_warns(_fake_build):
    _pin(_fake_build, (6, 1, 1))
    note = _fake_build._version_note("bitstream filter 'showinfo'", "7.1")
    assert note and "6.1.1" in note and "predates" in note
    assert _fake_build._version_note("bitstream filter 'x'", "6.0") is None


def test_version_note_at_or_above_min_is_silent(_fake_build):
    _pin(_fake_build, (9, 0, 1))
    assert _fake_build._version_note("bitstream filter 'showinfo'", "7.1") is None
    assert _fake_build._version_note("bitstream filter 'x'", "9.0") is None
    assert _fake_build._version_note("bitstream filter 'x'", "10.0") is not None


def test_version_note_unprobed_warns_unverified(_fake_build):
    _pin(_fake_build, None)
    note = _fake_build._version_note("bitstream filter 'showinfo'", "7.1")
    assert note and "could not be probed" in note


def test_planner_showinfo_warning_depends_on_build(_fake_build):
    # 6.1.1 warns that the build predates showinfo; 9.0.1 does not —
    # the plan itself is identical (warnings never reject)
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    edit = EditPlan(
        output=OutputSpec(container="mpegts", video_codec="h264"),
        bitstream_filters=[BitstreamFilterSpec(name="showinfo")])
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    for ver, expect_warning in (((6, 1, 1), True), ((9, 0, 1), False)):
        _pin(_fake_build, ver)
        ep = plan_execution(edit, assets, media, "out.ts")
        assert ep.mode == "copy"
        ws = [w for w in ep.warnings if "showinfo" in w and "7.1" in w]
        assert bool(ws) is expect_warning, (ver, ep.warnings)


# ------------------------------------------------------------- NLU (rules)
# A probed media is always passed: without one the planner cannot verify
# the input codec survives the remux and conservatively re-encodes.

def _media():
    return make_media(video_codec="h264", audio_codec="aac", has_audio=True)


def test_nlu_bsf_with_options(gen):
    r = gen("Remux clip.mp4 to clip.ts and apply the h264_metadata filter "
            "level=4.1", media=_media())
    assert r.status == "ready", r.errors
    b = r.plan["bitstream_filters"][0]
    assert b["name"] == "h264_metadata"
    assert b["options"] == {"level": "4.1"}


def test_nlu_bsf_flip_option_not_double_read_as_filter(gen):
    # 'flip=horizontal' belongs to h264_metadata: it must not be re-read as
    # the hflip video filter (which would force a re-encode)
    r = gen("Remux clip.mp4 to clip.ts with h264_metadata flip=horizontal",
            media=_media())
    assert r.status == "ready", r.errors
    assert all(o["type"] != "filter" for o in r.plan["operations"])
    assert r.plan["bitstream_filters"][0]["options"] == {"flip": "horizontal"}


def test_nlu_bsf_rotate_option_not_double_read_as_rotate(gen):
    r = gen("Remux clip.mp4 to clip.ts with h264_metadata rotate=90",
            media=_media())
    assert r.status == "ready", r.errors
    assert all(o["type"] != "rotate" for o in r.plan["operations"])
    assert r.plan["bitstream_filters"][0]["options"] == {"rotate": "90"}


def test_nlu_bsf_codec_mismatch_is_error(gen):
    r = gen("Remux clip.mp4 to clip.ts with av1_metadata", media=_media())
    assert r.status == "error"
    assert any("av1" in e for e in r.errors)


def test_nlu_copy_trim(gen):
    r = gen("Copy trim clip.mp4 from 00:10 to 00:20 to clip.ts", media=_media())
    assert r.status == "ready", r.errors
    assert r.plan["trim"]["stream_copy"] is True
    assert r.plan["trim"]["start_seconds"] == 10


# ------------------------------------------------------------- planner: user bsf argv

def test_copy_mp4_to_ts_user_bsf_chained_after_auto():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    edit = EditPlan(
        output=OutputSpec(container="mpegts", video_codec="h264"),
        bitstream_filters=[BitstreamFilterSpec(
            name="h264_metadata", options={"flip": "horizontal", "level": "4.1"})])
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, media, "out.ts")
    assert ep.mode == "copy"
    argv = build_argv(ep)
    # one -bsf:v flag per filter: the auto Annex-B restamp first, the user
    # filter second, its options sorted and colon-joined in the value
    assert argv.count("-bsf:v") == 2
    i_auto = argv.index("h264_mp4toannexb")
    i_user = argv.index("h264_metadata=flip=horizontal:level=4.1")
    assert i_auto < i_user


def test_copy_ts_to_mp4_user_bsf_deduped_with_auto():
    # requesting the same audio bsf the remux direction already applies
    # must not duplicate the flag
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    edit = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h264"),
        bitstream_filters=[BitstreamFilterSpec(name="aac_adtstoasc")])
    assets = {"main": Asset(path="in.ts", kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, media, "out.mp4")
    argv = build_argv(ep)
    assert argv.count("-bsf:a") == 1
    assert argv[argv.index("-bsf:a") + 1] == "aac_adtstoasc"


def test_reencode_plus_video_bsf_is_plan_error():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    edit = EditPlan(
        output=OutputSpec(container="mp4", video_codec="h265"),
        bitstream_filters=[BitstreamFilterSpec(name="h264_metadata")])
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    with pytest.raises(PlanError):
        plan_execution(edit, assets, media, "out.mp4")


def test_video_bsf_codec_mismatch_is_plan_error():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    edit = EditPlan(
        output=OutputSpec(container="mpegts", video_codec="h264"),
        bitstream_filters=[BitstreamFilterSpec(name="av1_metadata")])
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    with pytest.raises(PlanError):
        plan_execution(edit, assets, media, "out.mp4")


# ------------------------------------------------------------- execute: argv gate

def _copy_argv(*extra):
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "in.mp4",
            *extra, "-c", "copy", "out.mp4"]


def test_execute_bsf_valid_combo_passes():
    assert execute.validate_argv(_copy_argv("-bsf:v", "h264_metadata=level=4.1")) == []
    assert execute.validate_argv(_copy_argv("-bsf:a", "aac_adtstoasc")) == []


def test_execute_bsf_unknown_name_rejected():
    errs = execute.validate_argv(_copy_argv("-bsf:v", "no_such_filter"))
    assert errs and "no_such_filter" in errs[0]


def test_execute_bsf_trim_rejected():
    # 'trim' is not an FFmpeg bitstream filter — the gate must say so
    errs = execute.validate_argv(_copy_argv("-bsf:v", "trim"))
    assert errs and "trim" in errs[0] and "copy" in errs[0]


def test_execute_bsf_malformed_rejected():
    assert execute.validate_argv(_copy_argv("-bsf:v", "h264_metadata=level"))
    assert execute.validate_argv(_copy_argv("-bsf:v", "=level=4.1"))


def test_execute_bsf_bad_option_rejected():
    assert execute.validate_argv(_copy_argv("-bsf:v", "h264_metadata=bogus=1"))
    assert execute.validate_argv(_copy_argv("-bsf:v", "h264_metadata=level=9.9"))


def test_execute_c_copy_accepted():
    # the planner emits '-c copy' for every stream-copy plan — the gate
    # must accept it, or no remux could ever run
    assert execute.validate_argv(_copy_argv()) == []


def test_execute_c_unknown_codec_rejected():
    errs = execute.validate_argv(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "in.mp4",
         "-c", "no_such_codec", "out.mp4"])
    assert errs and "no_such_codec" in errs[0]


# -------------------------------------------------------------------- API

def test_support_exposes_bitstream_filters():
    d = client.get("/v1/support").json()
    bf = d["bitstream_filters"]
    assert set(bf) == ALL_BSFS
    assert bf["showinfo"]["min_version"] == "7.1"
    assert bf["showinfo"]["diagnostic"] is True
    assert bf["trace_headers"]["diagnostic"] is True
    assert bf["trim"]["copy_cut"] is True
    assert bf["aac_adtstoasc"]["kind"] == "audio"
    assert "level" in bf["h264_metadata"]["options"]
    assert "flip" in bf["h264_metadata"]["options"]
    assert bf["h264_mp4toannexb"]["codecs"] == ["h264"]


def test_env_reports_bsf_presence():
    d = client.get("/v1/env").json()
    if not d.get("available"):
        pytest.skip("ffmpeg not available")
    assert set(d["bsfs"]) == ALL_BSFS
    for name, row in d["bsfs"].items():
        assert set(row) == {"present"} and isinstance(row["present"], bool)
    # present on both probed builds (6.1.1 and 9.0.1); showinfo is NOT
    # asserted here — it exists only from FFmpeg 7.1
    assert d["bsfs"]["aac_adtstoasc"]["present"] is True
    assert d["bsfs"]["h264_metadata"]["present"] is True


# ------------------------------------------------------------- real media E2E

def _wait_run(run_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = execute.get_run(run_id)
        if st["status"] != "running":
            return st
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} still running after {timeout}s")


@pytest.mark.skipif(
    not (ffmpeg_available() and ffprobe_available() and TITAN.exists()),
    reason="ffmpeg/ffprobe or media/titan.mp4 not available — E2E needs real media",
)
def test_titan_bsf_chain_level_actually_applied(tmp_path):
    # mp4 -> mpegts with a user h264_metadata on top of the auto
    # h264_mp4toannexb: proves the real ffmpeg accepts the repeated -bsf:v
    # flag, and that the SPS level was actually rewritten to 4.1.
    media = probe_media(str(TITAN))
    edit = EditPlan(
        output=OutputSpec(container="mpegts", video_codec="h264"),
        bitstream_filters=[BitstreamFilterSpec(
            name="h264_metadata", options={"level": "4.1"})])
    assets = {"main": Asset(path=str(TITAN), kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, media, "out.ts")
    assert ep.mode == "copy"
    argv = build_argv(ep)
    assert argv.count("-bsf:v") == 2
    p = subprocess.run(argv, shell=False, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=300, check=False)
    assert p.returncode == 0, f"argv={argv}\nstderr={p.stderr}"
    q = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,level",
         "-of", "default=noprint_wrappers=1:nokey=1", str(tmp_path / "out.ts")],
        capture_output=True, text=True, timeout=60, check=False)
    assert q.returncode == 0, q.stderr
    # ffprobe lists program demuxer entries per program (TS prints each
    # stream twice) — dedupe before comparing
    lines = [l for l in q.stdout.splitlines() if l.strip()]
    assert set(lines) == {"h264", "41"}


@pytest.mark.skipif(
    not (ffmpeg_available() and ffprobe_available() and TITAN.exists()),
    reason="ffmpeg/ffprobe or media/titan.mp4 not available — E2E needs real media",
)
def test_titan_copy_trim_through_execution_layer(tmp_path, monkeypatch):
    # full pipeline for a stream-copy cut: planner -> build_argv ->
    # validate_argv (the '-c copy' gate) -> Popen -> probe the result
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    monkeypatch.setenv(execute.INPUT_DIR_ENV, str(tmp_path / "in"))
    monkeypatch.setenv(execute.OUTPUT_DIR_ENV, str(tmp_path / "out"))
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(tmp_path / "runs"))
    for d in ("in", "out", "runs"):
        (tmp_path / d).mkdir(exist_ok=True)
    shutil.copy(TITAN, tmp_path / "in" / "titan.mp4")

    media = probe_media(str(tmp_path / "in" / "titan.mp4"))
    edit = EditPlan(
        trim=TrimSpec(start_seconds=5, duration_seconds=5, stream_copy=True),
        output=OutputSpec(container="mp4", video_codec="h264"))
    assets = {"main": Asset(path="titan.mp4", kind=AssetKind.VIDEO)}
    ep = plan_execution(edit, assets, media, "out.mp4")
    assert ep.mode == "copy"
    argv = build_argv(ep)
    assert "-c" in argv and argv[argv.index("-c") + 1] == "copy"

    run = execute.start_run(argv)
    st = _wait_run(run.id)
    assert st["status"] == "success", st
    out = tmp_path / "out" / "out.mp4"
    info = probe_media(str(out))
    assert info is not None, f"output {out} is not probeable"
    # keyframe-accurate start (titan's GOP is ~2s), so allow a slack window
    assert 4.0 <= info.duration <= 6.5
