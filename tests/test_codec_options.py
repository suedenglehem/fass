"""M8 codec options: the per-encoder private options end to end —
registry tables, loader validation, plan-model conflicts, planner
emission (including copy-mode forcing), NLU routing, argv validation
against the builder's vocabulary, and the /v1/support + /v1/env
exposure. Mirrors the M6 GPU test structure."""

from __future__ import annotations

import re
import subprocess

import pytest
from fastapi.testclient import TestClient

from fass import execute
from fass.api import app
from fass.pipeline import generate
from fass.registry import REGISTRY
from fass.registry.loader import (
    audio_codec_option_keys,
    codec_option_errors,
    codec_option_specs,
    video_codec_option_keys,
)
from fass.schema import OutputSpec
from tests.conftest import assets_for, make_media

client = TestClient(app)


def gen(instruction, **kw):
    return generate(instruction, assets_for(), media=make_media(),
                    adapter_name="rules", **kw)


# ---------------------------------------------------------------- registry

EXPECTED_TABLES = {
    "h264_nvenc": 50, "hevc_nvenc": 54,
    "h264_qsv": 45, "hevc_qsv": 44,
    "libx264": 48, "libx265": 11,
    "prores_ks": 6, "png": 3, "dvbsub": 1, "libvvenc": 10,
    "ac3": 20, "eac3": 18, "libfdk_aac": 13,
    "flac": 11, "libopus": 8, "opus": 2,
}


def test_registry_has_sixteen_encoder_tables():
    tables = {k: v for k, v in REGISTRY["codec_options"].items()
              if k != "description" and isinstance(v, dict)}
    assert {k: len(v) for k, v in tables.items()} == EXPECTED_TABLES
    assert sum(len(v) for v in tables.values()) == 344


def test_registry_option_kinds_and_ranges():
    co = REGISTRY["codec_options"]
    assert co["libx264"]["crf"]["kind"] == "float"
    assert co["libx264"]["crf"]["range"] == [0, 51]
    assert co["libx264"]["crf_max"]["range"] == [0, 51]
    assert co["libx264"]["qp"]["range"] == [-1, 51]
    assert co["libx265"]["crf"]["range"] == [0, 69]
    assert co["ac3"]["dialnorm"] == {"kind": "int", "range": [-31, -1],
                                     "description": "Dialogue Level (dB)"}
    assert co["libfdk_aac"]["vbr"] == {"kind": "int", "range": [0, 5],
                                       "description": "VBR mode (1-5)",
                                       "builds": ["9.0.1"]}
    assert co["h264_qsv"]["preset"]["kind"] == "choice"
    assert co["h264_qsv"]["preset"]["values"] == [
        "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
    # dvbsub is a dump-format encoder: one option only, and it is not part
    # of the video codec-option vocabulary the NLU routes on
    assert set(co["dvbsub"]) == {"min_bpp"}
    assert co["dvbsub"]["min_bpp"]["range"] == [2, 8]
    assert "min_bpp" not in video_codec_option_keys()


def test_every_option_has_a_description():
    for enc, opts in EXPECTED_TABLES.items():
        for name, spec in REGISTRY["codec_options"][enc].items():
            assert spec.get("description"), f"{enc}.{name} has no description"


# ----------------------------------------------------------------- loader

def test_codec_option_specs_unknown_encoder():
    assert codec_option_specs("bogus") == {}


def test_codec_option_errors_unknown_key():
    (err,) = codec_option_errors("ac3", {"vbr": "4"})
    assert err.startswith("codec option 'vbr' is not a valid option for ac3 (valid:")
    assert "dialnorm" in err  # the valid list is the encoder's own options


def test_codec_option_errors_range_and_type():
    assert codec_option_errors("libx264", {"crf": "60"}) == [
        "crf: 60.0 is above the maximum 51"]
    assert codec_option_errors("ac3", {"dialnorm": "loud"}) == [
        "dialnorm: 'loud' is not an integer"]


def test_codec_option_errors_choice():
    (err,) = codec_option_errors("h264_qsv", {"preset": "9"})
    assert err == ("preset: '9' is not one of veryfast, faster, fast, medium, "
                   "slow, slower, veryslow or a number in its range")
    # the numeric form of the same option validates through the range
    assert codec_option_errors("h264_qsv", {"preset": "4"}) == []


def test_codec_option_key_families():
    assert "crf" in video_codec_option_keys()
    assert "preset" in video_codec_option_keys()
    assert "dialnorm" in audio_codec_option_keys()
    assert "vbr" in audio_codec_option_keys()
    # the families are disjoint for the shared-key-free cases
    assert "vbr" not in video_codec_option_keys()


# -------------------------------------------------------------- plan model

def _expect_conflict(kw, fragment):
    with pytest.raises(ValueError) as e:
        OutputSpec(**kw)
    assert fragment in str(e.value)


def test_gpu_qsv_mutually_exclusive():
    _expect_conflict(
        dict(container="mp4", video_codec="h264", gpu=True, qsv=True),
        "gpu and qsv are mutually exclusive — pick one hardware path")


def test_gpu_rc_cbr_needs_bitrate():
    _expect_conflict(
        dict(container="mp4", video_codec="h264", gpu=True,
             video_codec_options={"rc": "cbr"}),
        "rc=cbr* on the GPU needs a target video bitrate (video_bitrate_kbps)")
    # with the bitrate the same plan is legal
    OutputSpec(container="mp4", video_codec="h264", gpu=True,
               video_codec_options={"rc": "cbr"}, video_bitrate_kbps=8000)


def test_bitrate_conflicts_with_constant_quality():
    _expect_conflict(
        dict(container="mp4", video_codec="h264", gpu=True,
             video_codec_options={"cq": "24"}, video_bitrate_kbps=8000),
        "a target video bitrate and a constant-quality codec option")
    # ...but cbr/vbr modes take the bitrate as their rate — legal
    OutputSpec(container="mp4", video_codec="h264",
               video_codec_options={"crf": "18"}, video_bitrate_kbps=None)


def test_video_preset_conflicts_with_preset_option():
    _expect_conflict(
        dict(container="mp4", video_codec="h264", video_preset="veryfast",
             video_codec_options={"preset": "fast"}),
        "video_preset and the 'preset' codec option are both set")


def test_video_profile_conflicts_with_profile_option():
    _expect_conflict(
        dict(container="mp4", video_codec="h264", video_profile="main",
             video_codec_options={"profile": "main10"}),
        "video_profile and the 'profile' codec option are both set")


def test_audio_option_validated_against_container_default_codec():
    # video plan, no audio_codec named: the container default (aac in mp4)
    # must accept the audio option — native aac has no option table, so
    # the option is rejected against the default codec
    _expect_conflict(
        dict(container="mp4", video_codec="h264",
             audio_codec_options={"dialnorm": "-27"}),
        "aac has no private codec options in the registry")
    _expect_conflict(
        dict(mode="audio", container="mp4", audio_codec="ac3",
             audio_codec_options={"vbr": "4"}),
        "is not a valid option for ac3")
    OutputSpec(mode="audio", container="mp4", audio_codec="ac3",
               audio_codec_options={"dialnorm": "-27"})
    OutputSpec(mode="audio", container="m4a", audio_codec="fdk_aac",
               audio_codec_options={"vbr": "4"})


def test_audio_option_range_checked():
    _expect_conflict(
        dict(mode="audio", container="m4a", audio_codec="fdk_aac",
             audio_codec_options={"vbr": "9"}),
        "vbr: 9 is above the maximum 5")


# ---------------------------------------------------------------- planner

def test_crf_forces_reencode_and_emits_option():
    r = gen("re-encode as h264 with crf 18")
    assert r.status == "ready"
    cmd = r.command
    assert "-c:v libx264" in cmd and "-crf 18" in cmd
    assert "-c copy" not in cmd  # a stream copy could not honor -crf


def test_qsv_preset_option_emitted():
    r = gen("re-encode as h264 with qsv preset=fast")
    assert r.status == "ready"
    assert r.plan["output"]["qsv"] is True
    assert r.plan["output"]["video_codec_options"] == {"preset": "fast"}
    cmd = r.command
    assert "-c:v h264_qsv -preset fast" in cmd
    # QSV has no CRF: the CBR-style default bitrate applies
    assert "-b:v 5000k" in cmd and "-maxrate 5000k" in cmd


def test_qsv_h265_default_bitrate():
    r = gen("re-encode as h265 with quick sync")
    assert r.status == "ready"
    assert "-c:v hevc_qsv -b:v 5000k -maxrate 5000k" in r.command
    assert any("QSV default target bitrate 5000k" in d for d in r.defaults)


def test_qsv_copy_mode_warns():
    # bare QSV request, metadata-compatible h264 mp4, no options: the
    # honest outcome is the M6 copy-mode treatment — a warning, not a
    # silent re-encode
    r = gen("re-encode as h264 with quick sync")
    assert r.status == "ready"
    assert "-c copy" in r.command
    assert any("Quick Sync (QSV)" in w and "stream copy" in w
               for w in r.warnings)


def test_profile_option_emitted():
    r = gen("re-encode as h264 with profile main10")
    assert r.status == "ready"
    assert "-c:v libx264" in r.command and "-profile main10" in r.command


def test_x264_params_emitted():
    r = gen("re-encode as h264 with x264-params keyint=24")
    assert r.status == "ready"
    assert "-x264-params keyint=24" in r.command


def test_audio_dialnorm_emitted():
    r = gen("extract audio as ac3 with dialnorm -27")
    assert r.status == "ready"
    assert "-c:a ac3 -dialnorm -27" in r.command


def test_fdk_vbr_emitted():
    r = gen("extract audio as fdk_aac vbr 4")
    assert r.status == "ready"
    assert "-c:a libfdk_aac -vbr 4" in r.command


def test_audio_copy_stays_copy_without_options():
    r = gen("extract audio as aac")
    assert r.status == "ready"
    assert "-c copy" in r.command  # aac -> aac, no encoder params: remux


# -------------------------------------------------------------------- NLU

def test_fdk_aac_phrasings():
    for phrase in ("fdk_aac", "libfdk-aac"):
        r = gen(f"extract audio as {phrase}")
        assert r.status == "ready", phrase
        assert r.plan["output"]["audio_codec"] == "fdk_aac"
    # the space form routes to the codec too (the container word 'aac'
    # may attach — the codec is what matters here)
    r = gen("extract audio as fdk aac")
    assert r.status == "ready"
    assert r.plan["output"]["audio_codec"] == "fdk_aac"


def test_plain_aac_stays_native():
    r = gen("extract audio as aac")
    assert r.plan["output"]["audio_codec"] == "aac"


def test_params_space_and_key_value_forms():
    r = gen("re-encode as h264 with x264-params keyint=24")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec_options"] == {"x264-params": "keyint=24"}
    r = gen("re-encode as h264 with x264-params=keyint=24:min-keyint=24")
    assert r.plan["output"]["video_codec_options"] == {
        "x264-params": "keyint=24:min-keyint=24"}


def test_params_family_no_false_positive():
    # ordinary words ('tune the colors', 'set a profile') must never be
    # mistaken for *-params values
    r = gen("re-encode as h264 and tune the colors")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec_options"] == {}


def test_crf_phrasing():
    r = gen("re-encode as h264 with crf 18")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec_options"] == {"crf": "18"}


def test_crf_non_numeric_rejected():
    r = gen("re-encode as h264 with crf=abc")
    assert r.status == "unsupported"
    assert any("crf: 'abc' is not a number" in u["reason"]
               for u in r.unsupported)


def test_vbr_option_invalid_for_ac3():
    r = gen("extract audio as ac3 vbr 4")
    assert r.status == "unsupported"
    assert any("codec option 'vbr' is not a valid option for ac3" in u["reason"]
               for u in r.unsupported)


def test_gpu_and_qsv_exclusive_in_nlu():
    r = gen("re-encode as h264 with gpu and qsv")
    assert r.status == "unsupported"
    assert any("gpu and qsv are mutually exclusive" in u["reason"]
               for u in r.unsupported)


def test_qsv_alone_is_not_an_edit():
    r = gen("use quick sync")
    assert r.status == "needs_clarification"


def test_qsv_without_crf_knob_explains():
    r = gen("re-encode as h264 qsv crf 18")
    assert r.status == "ready"
    assert r.plan["output"]["video_codec_options"] == {}
    assert any("has no CRF-style knob" in d for d in r.defaults)


# ------------------------------------------------------------- argv checks

def _argv(*tail):
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "clip.mp4",
            "-map", "0:v:0", "-map", "0:a?", *tail, "out.mp4"]


def _ok(*tail):
    assert execute.validate_argv(_argv(*tail)) == [], " ".join(tail)


def _err(*tail, fragment=None, count=None):
    p = execute.validate_argv(_argv(*tail))
    assert p, " ".join(tail)
    if fragment:
        assert any(fragment in x for x in p), (fragment, p)
    if count is not None:
        assert len(p) == count, (tail, p)


def test_codec_options_accepted_for_their_encoder():
    _ok("-c:v", "libx264", "-crf", "18")
    _ok("-c:v", "h264_qsv", "-preset", "4")
    _ok("-c:v", "h264_nvenc", "-rc", "vbr_minqp", "-cq", "24")
    _ok("-c:v", "libx264", "-x264-params", "keyint=24")
    _ok("-c:a", "ac3", "-dialnorm", "-27")
    _ok("-c:a", "libfdk_aac", "-vbr", "4")
    _ok("-c:v", "libx265", "-crf", "69")
    _ok("-c:v", "h264_qsv", "-preset", "veryslow")
    _ok("-c:v", "libx264", "-preset", "ultrafast")


def test_codec_options_rejected_off_range():
    _err("-c:v", "libx264", "-crf", "60",
         fragment="crf: 60.0 is above the maximum 51")
    _err("-c:v", "libx265", "-crf", "70",
         fragment="crf: 70.0 is above the maximum 69")
    _err("-c:a", "libfdk_aac", "-vbr", "9",
         fragment="vbr: 9 is above the maximum 5")
    _err("-c:a", "ac3", "-dialnorm", "loud",
         fragment="dialnorm: 'loud' is not an integer")
    _err("-c:v", "h264_qsv", "-preset", "9", fragment="not one of veryfast")


def test_codec_options_rejected_for_the_wrong_encoder():
    # -dialnorm is an ac3/eac3/fdk option, not a libx264 one
    _err("-c:v", "libx264", "-dialnorm", "-27",
         fragment="unknown flag '-dialnorm'")
    _err("-c:v", "libx264", "-bogus", "1",
         fragment="unknown flag '-bogus'")


# -------------------------------------------------------------------- API

def test_support_exposes_codec_options():
    d = client.get("/v1/support").json()
    co = d["codec_options"]
    assert set(co) == set(EXPECTED_TABLES)
    assert len(co["libx264"]) == 48
    assert "cq" in co["h264_nvenc"] and "rc" in co["h264_nvenc"]
    assert "dialnorm" in co["ac3"]
    assert "vbr" in co["libfdk_aac"]
    assert set(d["qsv"]["coders"].values()) == {"h264_qsv", "hevc_qsv"}


def test_env_reports_qsv_coders():
    d = client.get("/v1/env").json()
    if not d["available"]:
        pytest.skip("ffmpeg not on PATH")
    enc = d["encoders"]
    assert enc["video:h264_qsv"]["encoder"] == "h264_qsv"
    assert enc["video:h265_qsv"]["encoder"] == "hevc_qsv"
    # presence is build-level registration, not hardware availability: the
    # dev-host build registers QSV (unusable on tr4 — AMD CPU, no Intel
    # iGPU) while the 9.0.1 nvidia image is built without QSV — the
    # endpoint must mirror the build, never assume
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True
    ).stdout
    raw_enc = {
        p[1]
        for line in raw.splitlines()
        for p in [line.split()]
        if len(p) >= 2 and p[1] != "="
        and re.match(r"^[A-Za-z0-9_]+$", p[1])
    }
    assert enc["video:h264_qsv"]["present"] is ("h264_qsv" in raw_enc)
    assert enc["video:h265_qsv"]["present"] is ("hevc_qsv" in raw_enc)
