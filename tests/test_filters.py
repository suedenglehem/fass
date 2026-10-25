"""M10.0 — filter library foundation: registry option schemas, per-option
validation, presence probe surface, and the argv gate for -vf/-af/-filter_complex.

The 50 new entries were transcribed from `ffmpeg -h filter=NAME` on the tested
6.1.1 build (blackdetect_vulkan/libvmaf from docs — absent there; acompressor
was misspelled 'acompresor' at M10.0 and is corrected in M10.1).
"""
import pytest

from fass import execute
from fass.probe.ffprobe import probe_ffmpeg
from fass.registry import (
    REGISTRY,
    all_filter_names,
    filter_expr_problems,
    filter_options_problems,
    filter_spec,
    filter_specs,
    filtergraph_problems,
)

NEW_AUDIO = [
    "acompressor", "acrossfade", "adeclick", "adrc", "adynamicequalizer",
    "amerge", "amix", "asetrate", "ashowinfo", "astats", "channelsplit",
    "compand", "dynaudnorm", "equalizer", "hdcd", "join", "silencedetect",
    "silenceremove", "volumedetect", "anullsrc",
]
NEW_VIDEO = [
    "bbox", "blackdetect", "blackdetect_vulkan", "chromakey", "colormatrix",
    "colorspace", "cropdetect", "decimate", "drawbox", "fps", "framerate",
    "freezedetect", "idet", "libvmaf", "loop", "psnr", "rotate", "setdar",
    "setsar", "setparams", "showinfo", "ssim", "trim", "vidstabdetect",
    "vidstabtransform", "vstack", "xfade", "yadif", "scale_cuda", "yadif_cuda",
]
DIAGNOSTIC = [
    "bbox", "blackdetect", "cropdetect", "freezedetect", "idet", "showinfo",
    "ashowinfo", "astats", "silencedetect", "volumedetect",
]
KINDS = {"choice", "number", "int", "rational", "bool", "flagset", "string"}


# ---------------------------------------------------------------- registry

def test_registry_has_the_50_new_entries():
    video = REGISTRY["filters"]["video"]
    audio = REGISTRY["filters"]["audio"]
    for name in NEW_VIDEO:
        assert name in video, name
    for name in NEW_AUDIO:
        assert name in audio, name
    assert len(video) == 48 and len(audio) == 36
    assert int(REGISTRY["registry_version"].split(".")[1]) >= 2


def test_option_schemas_are_wellformed():
    for name, spec in filter_specs().items():
        opts = spec.get("options")
        if opts is None:
            continue  # name-only entry (the pre-M10 34)
        assert isinstance(opts, dict), name
        for key, opt in opts.items():
            assert opt["kind"] in KINDS, (name, key, opt["kind"])
            if opt["kind"] in ("choice", "flagset"):
                assert opt.get("values"), (name, key)
            if "range" in opt:
                lo, hi = opt["range"]
                assert lo <= hi, (name, key)


def test_category_flags():
    for name in DIAGNOSTIC:
        assert filter_spec(name)["diagnostic"] is True, name
    assert filter_spec("amix")["multi_input"] == 0
    assert filter_spec("vstack")["multi_input"] == 0
    assert filter_spec("acrossfade")["multi_input"] == 2
    assert filter_spec("xfade")["multi_input"] == 2
    for name in ("psnr", "ssim", "libvmaf"):
        assert filter_spec(name)["needs_reference"] is True, name
    for name in ("scale_cuda", "yadif_cuda", "blackdetect_vulkan"):
        assert filter_spec(name)["gpu"] is True, name
    assert filter_spec("blackdetect_vulkan")["min_version"] == "7.1"


# ------------------------------------------------------------- option checks

def test_options_valid_examples():
    ok = [
        ("acompressor", {"level": "3", "ratio": "8"}),
        ("xfade", {"transition": "fade", "duration": "1", "offset": "5"}),
        ("amix", {"inputs": "2", "duration": "longest", "normalize": "0"}),
        ("yadif", {"mode": "send_frame", "parity": "auto", "deint": "all"}),
        ("astats", {"metadata": "1", "reset": "1"}),
        ("compand", {"attacks": "0", "points": "-70/-70", "soft-knee": "0.01"}),
        ("acrossfade", {"d": "1", "c1": "tri", "c2": "tri"}),
        ("anullsrc", {"channel_layout": "stereo", "sample_rate": "44100"}),
        ("silencedetect", {"n": "0.001", "d": "2"}),
        ("drawbox", {"x": "0", "y": "0", "w": "10", "h": "10", "color": "red"}),
        ("fps", {"fps": "30"}),
        ("rotate", {"angle": "90"}),
        ("trim", {"start": "5", "end": "10"}),
        ("vstack", {"inputs": "2"}),
        ("join", {"inputs": "2", "channel_layout": "stereo"}),
        ("setdar", {"dar": "16:9"}),
        ("acompressor", {"enable": "lte(t,5)"}),  # universal timeline option
    ]
    for name, opts in ok:
        assert filter_options_problems(name, opts) == [], (name, opts)


def test_options_invalid_examples():
    bad = [
        ("acompressor", {"level": "999"}, "above the maximum"),
        ("acompressor", {"level": "-1"}, "below the minimum"),
        ("xfade", {"transition": "bogus"}, "is not one of"),
        ("amix", {"inputs": "0"}, "below the minimum"),
        ("astats", {"metadata": "2"}, "expected 0 or 1"),
        ("acompressor", {"bogus": "1"}, "is not valid"),
        ("vstack", {"inputs": "x"}, "not an integer"),
        ("acrossfade", {"d": "abc"}, "not a number"),
    ]
    for name, opts, expect in bad:
        problems = filter_options_problems(name, opts)
        assert problems, (name, opts)
        assert any(expect in p for p in problems), (name, opts, problems)


def test_name_only_filters_pass_through():
    # the pre-M10 34 entries keep their description-only shape
    assert filter_options_problems("loudnorm", {"I": "-16"}) == []
    assert filter_options_problems("scale", {"-2": "480"}) == []
    assert filter_expr_problems("loudnorm=I=-16:LRA=11:TP=-1.5") == []
    assert filter_expr_problems("scale=-2:480") == []
    assert filter_expr_problems("crop=100:50") == []


# ------------------------------------------------------- expression wiring

def test_expr_instances_get_option_validation():
    assert filter_expr_problems("acompressor=level=3") == []
    probs = filter_expr_problems("acompressor=level=999")
    assert probs and "above the maximum" in probs[0]
    probs = filter_expr_problems("acompressor=level=1:level=2")
    assert probs and "twice" in probs[0]
    probs = filter_expr_problems("astats=1:2:3:4:5:6:7")
    assert probs and "too many positional" in probs[0]
    probs = filter_expr_problems("acompressor=level=1::ratio=2")
    assert probs and "empty argument" in probs[0]


def test_positional_and_named_mix():
    # positional fills the schema order; named after it is fine
    assert filter_expr_problems("amix=2:duration=longest") == []
    assert filter_expr_problems("drawbox=0:0:10:10:red:2") == []
    assert filter_expr_problems("setdar=dar=16:9") == []


def test_labeled_graph_validation():
    assert filtergraph_problems(
        "[0:v][1:v]xfade=transition=fade:duration=1:offset=5[out]"
    ) == []
    probs = filtergraph_problems("[0:v][1:v]xfade=transition=bogus[out]")
    assert probs and "is not one of" in probs[0]
    assert filtergraph_problems("[0:a][1:a]amix=inputs=2:duration=first[aout]") == []


# ---------------------------------------------------------------- argv gate

def _vf_argv(expr: str) -> list[str]:
    return ["ffmpeg", "-i", "in.mp4", "-vf", expr, "out.mp4"]


def test_argv_gate_accepts_valid_filter_options():
    assert execute.validate_argv(_vf_argv("acompressor=level=3")) == []
    assert execute.validate_argv(_vf_argv("yadif=mode=send_frame")) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-i", "b.mp4",
         "-filter_complex", "[0:v][1:v]xfade=transition=fade:duration=1:offset=5",
         "out.mp4"]
    ) == []


def test_argv_gate_rejects_invalid_filter_options():
    errs = execute.validate_argv(_vf_argv("acompressor=level=999"))
    assert errs and "above the maximum" in errs[0]
    errs = execute.validate_argv(_vf_argv("xfade=transition=bogus"))
    assert errs and "is not one of" in errs[0]
    errs = execute.validate_argv(_vf_argv("acompressor=bogus=1"))
    assert errs and "is not valid" in errs[0]


# ------------------------------------------------------------- presence probe

def test_presence_probe_covers_new_filters():
    ff = probe_ffmpeg()
    assert ff is not None
    # present on the tested 6.1.1 build (acompressor: the M10.0 entry was
    # misspelled 'acompresor', so the probe reported it absent — fixed in M10.1)
    for name in ("acrossfade", "astats", "silencedetect", "xfade", "yadif",
                 "drawbox", "anullsrc", "volumedetect", "acompressor"):
        assert name in ff.filters, name
    # absent on 6.1.1 (the probe reports the build, never assumes)
    for name in ("blackdetect_vulkan", "libvmaf"):
        assert name not in ff.filters, name


def test_probe_parser_excludes_legend_words():
    ff = probe_ffmpeg()
    # the old substring bug captured description words — the fixed parser
    # must not (555 real filters on 6.1.1, not 21 bogus)
    assert len(ff.filters) > 400
    for bogus in ("T.C", "V->V", "A->A", "Show", "textual"):
        assert bogus not in ff.filters
