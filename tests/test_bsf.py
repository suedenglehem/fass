"""Bitstream filters for stream-copy remuxes: registry lookups and the
planner wiring for both directions (video mp4/mov -> mpegts Annex-B restamp,
audio mpegts -> mp4/mov ADTS strip). Plans are built directly — the NLU
layer is not involved."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fass.builder import build_argv
from fass.planner import plan_execution
from fass.probe.ffprobe import ffmpeg_available, ffprobe_available, probe_media
from fass.registry import audio_bsf_for_copy, bsf_for_copy
from fass.schema import Asset, AssetKind, AudioMode, EditPlan, OutputSpec
from tests.conftest import make_media

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
