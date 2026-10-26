"""M6 GPU encoding: NLU phrasings, planner nvenc selection, the
VP9/preset/profile rejections, the copy-mode warning, and the
explanation line."""

from __future__ import annotations

import pytest

from fass.explain import summarize
from fass.pipeline import generate
from fass.planner import PlanError, plan_execution
from fass.schema import Asset, EditPlan, OutputSpec, guess_kind
from tests.conftest import make_media


def assets(input_path: str = "clip.mp4") -> dict:
    return {"main": Asset(path=input_path, kind=guess_kind(input_path))}


# ------------------------------------------------------------------- NLU

def test_gpu_phrasing_sets_flag():
    # 'resize … on the gpu' is the M10.5 case: the GPU mention is consumed
    # by scale_cuda and the encoder stays CPU (see test_stabilize.py).
    # This test pins the bare-flag rule: an encode word + gpu mention.
    r = generate("re-encode as h264 on the gpu", assets(),
                 media=make_media(), adapter_name="rules")
    assert r.status == "ready"
    assert r.plan["output"]["gpu"] is True
    assert any("GPU" in d for d in r.defaults)


def test_nvenc_phrasing():
    r = generate("re-encode as h265 with nvenc", assets(), media=make_media(),
                 adapter_name="rules")
    assert r.status == "ready"
    assert r.plan["output"]["gpu"] is True
    assert r.plan["output"]["video_codec"] == "h265"


def test_no_gpu_phrasing_negates():
    r = generate("resize to 480p, no gpu", assets(), media=make_media(),
                 adapter_name="rules")
    assert r.status == "ready"
    assert r.plan["output"]["gpu"] is False


def test_gpu_alone_is_not_an_edit():
    r = generate("use the gpu", assets(), media=make_media(), adapter_name="rules")
    assert r.status == "needs_clarification"


def test_revision_toggles_gpu():
    # the base edit is a grayscale — 'resize … on gpu' is the M10.5
    # scale_cuda case (the gpu mention is consumed by the filter), so it
    # cannot anchor the toggle
    prev = generate("make it grayscale and re-encode on gpu", assets(),
                    media=make_media(), adapter_name="rules")
    assert prev.status == "ready"
    on = EditPlan.model_validate(prev.plan)
    off = generate("no gpu", assets(), media=make_media(), previous_plan=on,
                   adapter_name="rules")
    assert off.status == "ready"
    assert off.plan["output"]["gpu"] is False
    assert off.plan["operations"][0]["type"] == "grayscale"  # preserved
    back = generate("use gpu", assets(), media=make_media(),
                    previous_plan=EditPlan.model_validate(off.plan),
                    adapter_name="rules")
    assert back.status == "ready"
    assert back.plan["output"]["gpu"] is True


# ---------------------------------------------------------------- planner

def _plan(gpu: bool, codec: str = "h264", container: str = "mp4", **out_kw):
    return EditPlan(
        input_id="main",
        output=OutputSpec(container=container, video_codec=codec,
                          gpu=gpu, **out_kw),
        operations=[{"type": "resize", "height": 480, "aspect_ratio": "preserve"}],
    )


def test_gpu_h264_selects_nvenc():
    ep = plan_execution(_plan(gpu=True), assets(), make_media())
    argv_opts = " ".join(ep.output_opts)
    assert "h264_nvenc" in argv_opts
    # constant-quality nvenc mode instead of x264 CRF
    assert "-cq" in ep.output_opts


def test_gpu_h265_selects_hevc_nvenc():
    ep = plan_execution(_plan(gpu=True, codec="h265"), assets(), make_media())
    assert "hevc_nvenc" in " ".join(ep.output_opts)


def test_gpu_cpu_codec_unchanged():
    ep = plan_execution(_plan(gpu=False), assets(), make_media())
    assert "libx264" in " ".join(ep.output_opts)
    assert "-c:v" in ep.output_opts


def test_gpu_vp9_rejected():
    with pytest.raises(PlanError) as e:
        plan_execution(_plan(gpu=True, codec="vp9",
                             container="webm"), assets(), make_media())
    assert any("CPU-only" in x for x in e.value.errors)


def test_gpu_with_x264_preset_rejected():
    with pytest.raises(PlanError) as e:
        plan_execution(_plan(gpu=True, video_preset="veryfast"),
                       assets(), make_media())
    assert any("preset" in x.lower() for x in e.value.errors)


def test_gpu_with_baseline_profile_rejected():
    with pytest.raises(PlanError) as e:
        plan_execution(_plan(gpu=True, video_profile="baseline"),
                       assets(), make_media())
    assert any("profile" in x.lower() for x in e.value.errors)


def test_gpu_main_profile_passes():
    ep = plan_execution(_plan(gpu=True, video_profile="main"),
                        assets(), make_media())
    assert "-profile:v" in ep.output_opts


def test_gpu_with_bitrate_keeps_cbr_style():
    ep = plan_execution(_plan(gpu=True, video_bitrate_kbps=8000),
                        assets(), make_media())
    opts = ep.output_opts
    assert "-b:v" in opts and "8000k" in opts
    assert "-cq" not in opts


def test_gpu_copy_mode_warns():
    # metadata-compatible h264 mp4 with no forced ops -> remux; GPU is
    # honored as a request but cannot be used on a copy
    r = generate("use the gpu and save as mp4", assets(), media=make_media(),
                 adapter_name="rules")
    assert r.status == "ready"
    assert any("stream copy" in w and "GPU" in w for w in r.warnings)


# ---------------------------------------------------------------- explain

def test_explain_mentions_gpu():
    ep = plan_execution(_plan(gpu=True), assets(), make_media())
    edit = _plan(gpu=True)
    lines = summarize(edit, ep, make_media())
    assert any("GPU" in l for l in lines)
