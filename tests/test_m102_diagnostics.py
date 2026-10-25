"""M10.2 — diagnostic filters: NLU phrasing, the diagnostic plan mode
('-f null -', log level raised to info, no output file), the argv gate,
and the registry entries.

Diagnostic filters (registry filters.* entries with 'diagnostic': true)
report through FFmpeg's log. A plan whose operations are all diagnostic
filter ops becomes a diagnostic run: decode → filter → discard. A mixed
plan (diagnostic + anything else) stays a normal encode — the diagnostic
filter simply logs on the way.
"""
from __future__ import annotations

import pytest

from fass import execute
from fass.builder import build_argv
from fass.planner import PlanError, plan_execution
from fass.registry import diagnostic_output, filter_expr_problems, filter_names, filter_spec
from fass.schema import Asset, AssetKind, EditPlan, FilterOp, OutputSpec

from tests.test_planner_builder import argv_for, ep_for

DIAG_NAMES = ["silencedetect", "volumedetect", "astats", "ashowinfo",
              "blackdetect", "blackdetect_vulkan", "cropdetect",
              "freezedetect", "idet", "showinfo", "bbox"]


# ------------------------------------------------------------------ registry

def test_registry_marks_the_diagnostic_filters():
    for name in DIAG_NAMES:
        spec = filter_spec(name)
        assert spec is not None and spec.get("diagnostic") is True, name


def test_diagnostic_output_section():
    d = diagnostic_output()
    assert d.get("muxer") == "null" and d.get("path") == "-"


def test_noise_tolerance_accepts_the_db_spelling():
    # ffmpeg parses the dB suffix on <double> options (av_parse_number)
    assert filter_expr_problems("silencedetect=n=-30dB:d=0.5") == []
    assert filter_expr_problems("freezedetect=n=-60dB:d=10") == []
    assert filter_expr_problems("silencedetect=n=0.001:d=2") == []


# ------------------------------------------------------------------ NLU

AUDIO_PHRASES = [
    ("detect silences in the audio", "silencedetect"),
    ("find where the silences are", "silencedetect"),
    ("how much silence is in this recording", "silencedetect"),
    ("how loud is this audio", "volumedetect"),
    ("measure the volume levels", "volumedetect"),
    ("peak volume of the audio", "volumedetect"),
    ("audio statistics", "astats"),
    ("audio frame info", "ashowinfo"),
    ("info about the audio frames", "ashowinfo"),
]
VIDEO_PHRASES = [
    ("detect black frames", "blackdetect"),
    ("where are the black frames", "blackdetect"),
    ("where are the black bars", "cropdetect"),
    ("detect the letterbox bars", "cropdetect"),
    ("detect frozen frames", "freezedetect"),
    ("detect interlacing", "idet"),
    ("is this video interlaced", "idet"),
    ("detect the bounding box", "bbox"),
    ("frame info", "showinfo"),
]


@pytest.mark.parametrize("phrase,expr", AUDIO_PHRASES + VIDEO_PHRASES)
def test_diagnostic_phrases(gen, phrase, expr):
    r = gen(phrase, "clip.mp4")
    assert r.status == "ready", (phrase, r.status)
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == expr
    expected = "audio" if expr in filter_names("audio") else "video"
    assert f["domain"] == expected


def test_bare_diagnostic_names_are_claimed(gen):
    r = gen("run silencedetect and volumedetect", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["silencedetect", "volumedetect"]


def test_db_suffix_keeps_the_canonical_case(gen):
    # the instruction is lowercased, ffmpeg parses 'dB' case-sensitively
    r = gen("silencedetect=n=-30dB:d=0.5", "clip.mp4")
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "silencedetect=n=-30dB:d=0.5"


# ------------------------------------------------------- verb collisions

def test_detect_silences_is_not_silenceremove(gen):
    r = gen("detect silences", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["silencedetect"]


def test_remove_silences_stays_silenceremove(gen):
    r = gen("remove the silences", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs[0].startswith("silenceremove")


def test_detect_interlacing_is_not_yadif(gen):
    r = gen("detect interlacing", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["idet"]


def test_deinterlace_stays_yadif(gen):
    r = gen("deinterlace the video", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["yadif"]


def test_black_frames_and_black_bars_are_different_filters(gen):
    r = gen("detect black frames", "clip.mp4")
    assert [o["expr"] for o in r.plan["operations"]
            if o["type"] == "filter"] == ["blackdetect"]
    r = gen("detect the black bars", "clip.mp4")
    assert [o["expr"] for o in r.plan["operations"]
            if o["type"] == "filter"] == ["cropdetect"]


def test_audio_frame_info_is_not_showinfo(gen):
    r = gen("audio frame info", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["ashowinfo"]


def test_how_loud_is_not_a_volume_edit(gen):
    # 'how loud' measures; 'louder' edits — the volume gain needs a number
    r = gen("how loud is this audio", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["volumedetect"]


# ---------------------------------------------------------------- planner

def _plan(instr, media=None, out=None):
    from fass.nlu.rules import RulesAdapter
    ad = RulesAdapter()
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}
    r = ad.parse(instr, assets, media=media)
    assert r.status == "ready", (instr, r.status)
    return ep_for(operations=list(r.plan.operations), trim=r.plan.trim,
                  output=r.plan.output, media=media, out=out)


def test_diagnostic_plan_shape():
    ep = _plan("detect silences")
    assert ep.mode == "diagnostic"
    assert ep.loglevel == "info"
    assert ep.output_path == "-"
    assert ep.output_opts == ["-f", "null"]
    assert ep.maps == []
    assert ep.af == "silencedetect"
    assert ep.vf is None


def test_diagnostic_argv():
    ep = _plan("detect silences")
    argv = build_argv(ep)
    assert argv[:4] == ["ffmpeg", "-hide_banner", "-loglevel", "info"]
    assert argv[-3:] == ["-f", "null", "-"]
    assert "-c" not in argv and "-c:v" not in argv and "-map" not in argv
    assert execute.validate_argv(argv) == []


def test_diagnostic_chains_multiple_filters():
    ep = _plan("detect silences and measure the volume levels")
    assert ep.af == "silencedetect,volumedetect"
    assert ep.mode == "diagnostic"


def test_diagnostic_with_trim():
    ep = _plan("trim 10 to 20 seconds and detect silences")
    argv = build_argv(ep)
    assert argv[argv.index("-ss") + 1] == "10"
    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-t") + 1] == "10"
    assert argv[-3:] == ["-f", "null", "-"]


def test_mixed_plan_is_not_diagnostic():
    ep = _plan("detect silences and normalize the audio")
    assert ep.mode != "diagnostic"
    assert ep.output_path != "-"
    assert ep.loglevel == "error"


def test_diagnostic_accepts_an_audio_asset():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[FilterOp(domain="audio", expr="silencedetect")])
    assets = {"main": Asset(path="voice.mp3", kind=AssetKind.AUDIO)}
    ep = plan_execution(edit, assets, None, None)
    assert ep.mode == "diagnostic"


def test_diagnostic_rejects_a_stream_destination():
    with pytest.raises(PlanError, match="writes nothing"):
        _plan("detect silences", out="udp://239.0.0.1:1234")


def test_diagnostic_rejects_an_audio_device():
    with pytest.raises(PlanError, match="writes nothing"):
        _plan("detect silences", out="alsa:hw:0")


def test_diagnostic_ignores_an_output_filename(gen):
    r = gen("detect silences", "clip.mp4", output_filename="out.mp4")
    assert r.status == "ready"
    assert any("ignored" in d for d in (r.defaults or []))
    assert r.arguments[-3:] == ["-f", "null", "-"]


# ------------------------------------------------------------------ gate

def test_gate_accepts_the_null_muxer_destination():
    assert execute.validate_argv(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", "in.mp4",
         "-af", "silencedetect", "-f", "null", "-"]) == []


def test_gate_rejects_an_unknown_muxer():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "in.mp4", "-f", "bogus", "-"]) != []


def test_gate_accepts_diagnostic_chains():
    for expr in ("silencedetect=n=-30dB:d=0.5", "volumedetect", "astats",
                 "blackdetect=d=0.1:pic_th=0.95", "cropdetect",
                 "freezedetect=n=-60dB:d=10", "idet", "showinfo", "bbox",
                 "ashowinfo", "blackdetect_vulkan"):
        assert filter_expr_problems(expr) == [], expr
        domain = "af" if expr.split("=")[0] in filter_names("audio") else "vf"
        assert execute.validate_argv(
            ["ffmpeg", "-i", "in.mp4", f"-{domain}", expr, "-f", "null", "-"]) == [], expr


# ------------------------------------------------------------- explain

def test_summary_is_diagnostic(gen):
    r = gen("detect silences", "clip.mp4")
    joined = " ".join(r.summary)
    assert "Diagnostic analysis" in joined
    assert "Encode:" not in joined
    assert "Audio: kept" not in joined


def test_summary_shows_the_analyzed_range(gen):
    r = gen("trim 10 to 20 seconds and detect silences", "clip.mp4")
    assert any("Analyzed range: 10s to 20s" in s for s in r.summary)


def test_diagnostic_warnings(gen):
    r = gen("detect silences", "clip.mp4")
    joined = " ".join(r.warnings)
    assert "nothing is written to a file" in joined
    assert "silencedetect writes silence intervals" in joined
