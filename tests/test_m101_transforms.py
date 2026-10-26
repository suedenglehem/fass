"""M10.1 — transform filters: NLU phrasing, the option-token mask, planner
wiring on -vf/-af, semantic-trap warnings, and the argv gate.

The registry (M10.0) already enforces names and options at every layer;
these tests cover the new NLU phrases, the disclosures the plan makes on
the probed build, and the trap warnings (§4 decision 4).
"""
from __future__ import annotations

import pytest

from fass import execute
from fass.planner.planner import _chain_filter_names
from fass.probe.ffprobe import probe_ffmpeg
from fass.registry import filter_expr_problems
from fass.schema import FilterOp, OutputSpec

from tests.test_planner_builder import argv_for, ep_for

# ------------------------------------------------------------------ NLU

AUDIO_PHRASES = [
    ("compress the audio", "acompressor"),
    ("audio compressor please", "acompressor"),
    ("compand the audio", "compand"),
    ("dynamically normalize the audio", "dynaudnorm"),
    ("dynamic range control on the audio", "adrc"),
    ("dynamic equalizer boost", "adynamicequalizer"),
    ("fix the clicks and pops", "adeclick"),
    ("remove the silences at the start", "silenceremove=start_periods=1:start_silence=0.1"),
    ("strip silences", "silenceremove=start_periods=1:start_silence=0.1"),
    ("remove the dc offset", "hdcd"),
]
VIDEO_PHRASES = [
    ("deinterlace the video", "yadif"),
    ("deinterlace it", "yadif"),
    ("decimate the video", "decimate"),
    ("drop duplicate frames", "decimate"),
    ("remove the green screen", "chromakey=color=green"),
    ("chroma key the background", "chromakey=color=green"),
    ("convert to bt709 color space", "colorspace=iall=bt709:all=bt709"),
    ("set fps to 30", "fps=fps=30"),
    ("convert to 30 fps", "fps=fps=30"),
    ("set the display aspect ratio to 16:9", "setdar=ratio=16/9"),
    ("set the sample aspect ratio to 4:3", "setsar=ratio=4/3"),
]


@pytest.mark.parametrize("instruction,expr", AUDIO_PHRASES + VIDEO_PHRASES)
def test_transform_phrases(gen, instruction, expr):
    r = gen(instruction, "clip.mp4")
    assert r.status == "ready", (instruction, r.status)
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert f and f[0]["expr"] == expr, (instruction, f)


def test_arbitrary_rotate_is_the_rotate_filter(gen):
    r = gen("rotate the video 45 degrees", "clip.mp4")
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"].startswith("rotate=angle=45*PI/180")
    assert f["domain"] == "video"


def test_quarter_rotate_stays_the_rotate_op(gen):
    r = gen("rotate the video 90 degrees", "clip.mp4")
    ops = [o["type"] for o in r.plan["operations"]]
    assert ops == ["rotate"]  # the transpose op, not the filter


# ------------------------------------------------------- mask (BSF pattern)

def test_asetrate_number_is_not_a_resample(gen):
    r = gen("asetrate=44100", "clip.mp4")
    assert r.status == "ready"
    types = [o["type"] for o in r.plan["operations"]]
    assert "audio_resample" not in types
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "asetrate=sample_rate=44100"


def test_fps_number_is_not_a_bitrate(gen):
    r = gen("fps=30", "clip.mp4")
    assert r.status == "ready"
    assert r.plan["output"]["video_bitrate_kbps"] is None
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "fps=fps=30"


def test_bsf_style_space_form(gen):
    r = gen("asetrate sample_rate=44100", "clip.mp4")
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "asetrate=sample_rate=44100"
    assert r.plan["output"]["audio_bitrate_kbps"] is None


def test_positional_expression_maps_to_named_options(gen):
    r = gen("drawbox=0:0:10:10:red", "clip.mp4")
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "drawbox=x=0:y=0:width=10:height=10:color=red"


def test_option_token_not_double_read_as_encoder_option(gen):
    # 'level' is an h264_metadata bsf key and an x264 encoder option;
    # 'mode' is a yadif option — the mask keeps them out of the codec path
    r = gen("yadif=mode=send_frame", "clip.mp4")
    assert r.status == "ready"
    assert not r.plan["output"]["video_codec_options"]


# ------------------------------------------------------------- ambiguity

def test_plain_normalization_stays_loudnorm(gen):
    r = gen("normalize the audio loudness", "clip.mp4")
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "loudnorm"


def test_dynamic_normalization_is_dynaudnorm_not_loudnorm(gen):
    r = gen("dynamically normalize the audio", "clip.mp4")
    exprs = [o["expr"] for o in r.plan["operations"] if o["type"] == "filter"]
    assert exprs == ["dynaudnorm"]  # precedence: the specific phrase wins


def test_compress_audio_is_not_a_size_question(gen):
    r = gen("compress the audio", "clip.mp4")
    assert r.status == "ready"


def test_compress_to_size_still_clarifies(gen):
    r = gen("compress to 10 mb", "clip.mp4")
    assert r.status == "needs_clarification"


def test_silence_the_audio_is_still_mute(gen):
    # 'silence the audio' (mute) is not 'remove the silences' (silenceremove)
    r = gen("silence the audio", "clip.mp4")
    f = [o for o in r.plan["operations"] if o["type"] == "filter"][0]
    assert f["expr"] == "anull"


# ---------------------------------------------------------------- planner

def _plan(instr, media=None):
    from fass.nlu.rules import RulesAdapter
    from fass.schema import Asset, AssetKind
    media = media  # caller-probed media info, may be None
    ad = RulesAdapter()
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}
    r = ad.parse(instr, assets, media=media)
    assert r.status == "ready", (instr, r.status)
    return ep_for(operations=list(r.plan.operations),
                  output=r.plan.output, media=media)


MEDIA = None


def test_transform_filter_lands_in_af(gen):
    ep = _plan("compress the audio")
    assert ep.af == "acompressor"


def test_transform_filter_lands_in_vf(gen):
    ep = _plan("deinterlace the video")
    assert ep.vf == "yadif"


def test_chain_order_preserved(gen):
    ep = _plan("deinterlace the video and compress the audio")
    assert ep.vf == "yadif"
    assert ep.af == "acompressor"


def test_argv_carries_the_filter(gen):
    argv = argv_for(operations=[FilterOp(domain="audio", expr="acompressor=level_in=3")])
    assert "-af" in argv and argv[argv.index("-af") + 1] == "acompressor=level_in=3"
    assert execute.validate_argv(argv) == []


def test_argv_gate_accepts_new_transform_chains():
    for expr in ("yadif=mode=send_frame", "fps=fps=30",
                 "asetrate=sample_rate=44100",
                 "drawbox=x=0:y=0:width=10:height=10:color=red",
                 "silenceremove=start_periods=1:start_silence=0.1",
                 "rotate=angle=45*PI/180:fillcolor=black:out_w=rotw(45*PI/180):out_h=roth(45*PI/180)"):
        assert execute.validate_argv(
            ["ffmpeg", "-i", "in.mp4", "-vf", expr, "out.mp4"]) == [], expr
        assert filter_expr_problems(expr) == [], expr


# ------------------------------------------------------ semantic warnings

TRAPS = [
    ("asetrate=sample_rate=44100", "pitch AND playback speed"),
    ("decimate", "fewer frames"),
    ("chromakey=color=green", "alpha"),
    ("colorspace=space=bt709", "metadata tags"),
    ("colormatrix=src=bt601:dst=bt709", "metadata tags"),
    ("fps=fps=30", "playback speed is unchanged"),
    ("setdar=ratio=16/9", "not rescaled"),
    ("setsar=ratio=4/3", "not rescaled"),
    ("silenceremove=start_periods=1", "timestamps"),
    ("loop=loop=2:size=100", "longer than the input"),
]


@pytest.mark.parametrize("expr,needle", TRAPS)
def test_semantic_trap_warning(expr, needle):
    ep = ep_for(operations=[FilterOp(domain="video" if expr not in
                                     ("asetrate=sample_rate=44100",
                                      "silenceremove=start_periods=1")
                                     else "audio", expr=expr)])
    assert any(needle in w for w in ep.warnings), ep.warnings


def test_no_trap_warning_for_plain_filters():
    ep = ep_for(operations=[FilterOp(domain="audio", expr="loudnorm")])
    assert not any("pitch" in w for w in ep.warnings)


def test_gpu_filter_warning():
    ep = ep_for(operations=[FilterOp(domain="video", expr="scale_cuda=w=1280")])
    assert any("GPU" in w for w in ep.warnings), ep.warnings


def test_min_version_warning_on_old_build():
    # blackdetect_vulkan is 7.1-gated; the local 6.1.1 build predates it
    ep = ep_for(operations=[FilterOp(domain="video", expr="blackdetect_vulkan")])
    notes = [w for w in ep.warnings if "7.1" in w]
    ff = probe_ffmpeg()
    if ff is not None and (ff.version or "").find("6.1") >= 0:
        assert notes, ep.warnings


def test_presence_warning_for_absent_filter():
    # blackdetect_vulkan is the build-optional example: the plain (vulkan)
    # diagnostic set exists in every build, the _vulkan variant only where
    # the build links vulkan (absent from the 6.1.1 host). libvmaf cannot
    # stand in — M10.4 made it a needs_reference comparison metric, and a
    # bare -vf chain has no reference pad to feed it.
    ff = probe_ffmpeg()
    if ff is None or "blackdetect_vulkan" in ff.filters:
        pytest.skip("blackdetect_vulkan present in the local build")
    ep = ep_for(operations=[FilterOp(domain="video",
                                     expr="blackdetect_vulkan")])
    assert any("not available in the local FFmpeg build" in w
               for w in ep.warnings), ep.warnings


def test_warnings_dedup_per_filter():
    ep = ep_for(operations=[
        FilterOp(domain="video", expr="decimate"),
        FilterOp(domain="video", expr="decimate=ppsrc=1"),
    ])
    drops = [w for w in ep.warnings if "fewer frames" in w]
    assert len(drops) == 1


# ---------------------------------------------------------------- misc

def test_chain_filter_names():
    assert _chain_filter_names("yadif,decimate") == ["yadif", "decimate"]
    assert _chain_filter_names("drawtext=text='a,b':x=10") == ["drawtext"]
    assert _chain_filter_names("asetrate=sample_rate=44100") == ["asetrate"]


def test_acompressor_spelling_is_the_ffmpeg_name():
    # M10.0 shipped 'acompresor' — the probe reported it absent; the real
    # filter name is 'acompressor' (present in both tested builds)
    assert filter_expr_problems("acompressor=level_in=3") == []
    ff = probe_ffmpeg()
    assert ff is not None and "acompressor" in ff.filters
