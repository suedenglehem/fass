"""M10.5 stabilization: the two-pass vidstab transform.

Layers, cheapest first:
- NLU:      trigger phrases at RulesAdapter.parse level; the unsupported
            neighbours (denoise stays out; 'stabilize and denoise' reports
            the denoise, not the stabilize), the audio/subtitles mode
            conflict (the M10.5 bugfix), and the GPU consume rule (a CUDA
            filter absorbs the GPU mention unless an encode word is also
            present)
- planner:  plan shape (the two passes, the intermediate .trf, the bare
            name), both argvs, and the semantic conflicts (probes are
            monkeypatched with fake MediaInfo — argv shape only)
- pipeline: generate() reports the steps and keeps the top-level
            arguments on the FINAL pass
- media:    the emitted two-pass sequence is executed on a real synthetic
            clip and the outputs probed (skipped when ffmpeg is absent)
"""

from __future__ import annotations

import subprocess

import pytest

from fass.builder import build_argv, build_argv_all
from fass.nlu import get_adapter
from fass.planner import PlanError, plan_execution
from fass.probe.ffprobe import ffmpeg_available
from fass.registry import REGISTRY
from fass.schema import (
    Asset,
    AssetKind,
    AudioReplaceOp,
    AudioResampleOp,
    CombinerOp,
    EditPlan,
    FilterOp,
    GrayscaleOp,
    OutputSpec,
    ParseStatus,
    ResizeOp,
    RotateOp,
    SpeedOp,
    StabilizeOp,
    TrimSpec,
    WatermarkOp,
)
from tests.conftest import make_media

ADAPTER = get_adapter("rules")


def _parse(instruction, assets=None):
    assets = assets or {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}
    return ADAPTER.parse(instruction, assets, None, None)


def _plan(operations, assets, monkeypatch, probes, *, trim=None,
          output="out.mp4", output_spec=None, bsf=None, destination=None):
    """plan_execution with fake probes (argv-shape tests, no files)."""
    monkeypatch.setattr("fass.planner.planner.probe_media",
                        lambda p, require_video=True: probes.get(p))
    edit = EditPlan(
        output=output_spec or OutputSpec(container="mp4", video_codec="h264"),
        trim=trim,
        operations=operations,
        bitstream_filters=bsf,
        output_destination=destination,
    )
    return plan_execution(edit, assets, probes.get(assets["main"].path),
                          output)


# ------------------------------------------------------------------- NLU

def test_stabilize_phrases():
    for instr in ("stabilize the shaky clip",
                  "remove the camera shake from the clip",
                  "get rid of the shaky handheld look",
                  "reduce the hand shake"):
        r = _parse(instr)
        assert r.status is ParseStatus.READY, instr
        assert any(o.type == "stabilize" for o in r.plan.operations), instr


def test_stabilize_neighbours_stay_unsupported():
    r = _parse("denoise the video")
    assert r.status is ParseStatus.UNSUPPORTED
    # the denoise claim wins over the stabilize claim — the honest report
    r = _parse("stabilize and denoise the clip")
    assert r.status is ParseStatus.UNSUPPORTED
    assert any("denois" in u.fragment.lower() for u in r.unsupported)
    r = _parse("stabilize the clip and apply sepia")
    assert r.status is ParseStatus.UNSUPPORTED


def test_stabilize_audio_mode_conflict():
    # M10.5 bugfix: the op used to be silently dropped for audio outputs
    r = _parse("stabilize the clip to mp3")
    assert r.status is ParseStatus.NEEDS_CLARIFICATION
    assert any("stabiliz" in c.question.lower() for c in r.clarifications)


def test_stabilize_subtitles_mode_conflict():
    r = _parse("stabilize the clip and extract the subtitles to a file")
    assert r.status is ParseStatus.NEEDS_CLARIFICATION


def test_gpu_consumed_by_resize_cuda_filter():
    r = _parse("resize to 1080p on the gpu")
    assert r.status is ParseStatus.READY
    assert r.plan.output.gpu is False  # the filter took the GPU
    assert any(getattr(o, "expr", "") == "scale_cuda=w=-1:h=1080"
               for o in r.plan.operations)
    assert any("not the encoder" in d for d in r.defaults)


def test_gpu_consumed_by_yadif_cuda_filter():
    r = _parse("deinterlace on the gpu")
    assert r.status is ParseStatus.READY
    assert r.plan.output.gpu is False
    assert any(getattr(o, "expr", "") == "yadif_cuda"
               for o in r.plan.operations)


def test_gpu_encoder_word_reenables_nvenc():
    r = _parse("deinterlace and encode on the gpu")
    assert r.status is ParseStatus.READY
    assert r.plan.output.gpu is True
    assert any(getattr(o, "expr", "") == "yadif_cuda"
               for o in r.plan.operations)


def test_bare_gpu_mention_keeps_nvenc():
    # no CUDA filter involved — the M6 behavior stands
    r = _parse("stabilize the clip on the gpu")
    assert r.status is ParseStatus.READY
    assert r.plan.output.gpu is True


# --------------------------------------------------------------- planner

def test_stabilize_plan_shape(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": make_media(path="in.mp4")}
    ep = _plan([StabilizeOp()], assets, monkeypatch, probes)
    assert ep.mode == "reencode"
    assert [p.purpose for p in ep.passes] == ["stabilize-detect",
                                              "stabilize-transform"]
    # the side-file is a bare name derived from the output stem
    assert ep.intermediates == ["out.vidstab.trf"]
    detect, transform = ep.passes
    assert detect.vf == "vidstabdetect=result=out.vidstab.trf"
    assert detect.output_path == "-"
    assert detect.output_opts == ["-f", "null"]
    assert transform.vf == "vidstabtransform=input=out.vidstab.trf"
    assert transform.output_path == "out.mp4"
    # the top-level fields mirror the FINAL pass
    assert ep.vf == "vidstabtransform=input=out.vidstab.trf"
    assert ep.output_path == "out.mp4"


def test_stabilize_trf_follows_output_stem(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": make_media(path="in.mp4")}
    ep = _plan([StabilizeOp()], assets, monkeypatch, probes,
               output="my clip.mp4")
    assert ep.intermediates == ["my clip.vidstab.trf"]


def test_stabilize_argv_for_both_passes(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": make_media(path="in.mp4")}
    ep = _plan([StabilizeOp()], assets, monkeypatch, probes)
    argvs = build_argv_all(ep)
    assert len(argvs) == 2
    # build_argv (the single-plan contract) returns the final pass
    assert argvs[1] == build_argv(ep)
    detect, transform = argvs
    assert detect[detect.index("-vf") + 1] == \
        "vidstabdetect=result=out.vidstab.trf"
    assert detect[-3:] == ["-f", "null", "-"]
    assert "vidstabdetect" not in " ".join(transform)
    assert transform[transform.index("-vf") + 1] == \
        "vidstabtransform=input=out.vidstab.trf"
    assert transform[-1] == "out.mp4"


def test_stabilize_resize_lands_in_transform_pass_only(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": make_media(path="in.mp4")}
    ep = _plan([StabilizeOp(), ResizeOp(height=720)], assets, monkeypatch,
               probes)
    detect, transform = ep.passes
    assert detect.vf == "vidstabdetect=result=out.vidstab.trf"  # analysis only
    assert transform.vf.startswith("vidstabtransform=input=out.vidstab.trf,")
    assert "scale" in transform.vf


def _expect_reject(operations, monkeypatch, assets, probes, needle, **kw):
    with pytest.raises(PlanError) as e:
        _plan(operations, assets, monkeypatch, probes, **kw)
    assert needle.lower() in str(e.value).lower()


def test_stabilize_conflicts(monkeypatch):
    main = Asset(path="in.mp4", kind=AssetKind.VIDEO)
    video = Asset(path="b.mp4", kind=AssetKind.VIDEO)
    audio = Asset(path="track.mp3", kind=AssetKind.AUDIO)
    image = Asset(path="logo.png", kind=AssetKind.IMAGE)
    probes = {"in.mp4": make_media(path="in.mp4"),
              "b.mp4": make_media(path="b.mp4", duration=30.0)}
    simple = {"main": main}

    _expect_reject([StabilizeOp()], monkeypatch, simple, probes,
                   "same frames",
                   trim=TrimSpec(start_seconds=5, duration_seconds=10))
    _expect_reject([StabilizeOp(), SpeedOp(factor=1.5)], monkeypatch,
                   simple, probes, "frame correspondence")
    _expect_reject([StabilizeOp(), FilterOp(domain="video", expr="hflip")],
                   monkeypatch, simple, probes,
                   "not compatible with stabilization")
    _expect_reject([StabilizeOp(), AudioReplaceOp(asset_id="track")],
                   monkeypatch, {**simple, "track": audio}, probes,
                   "cannot be combined with stabilization")
    _expect_reject([StabilizeOp(), WatermarkOp(asset_id="logo")],
                   monkeypatch, {**simple, "logo": image}, probes,
                   "cannot be added in the same pass")
    _expect_reject([StabilizeOp()], monkeypatch, simple, probes,
                   "compressed stream",
                   bsf=[{"name": "h264_metadata", "options": {}}])
    _expect_reject([StabilizeOp(), CombinerOp(filter="xfade",
                                              asset_ids=["b"])],
                   monkeypatch, {**simple, "b": video}, probes,
                   "cannot be combined with a multi-input filter")
    _expect_reject([StabilizeOp()], monkeypatch, simple, probes,
                   "intermediate transform file",
                   destination="udp://127.0.0.1:5004")


def test_stabilize_allowed_combos(monkeypatch):
    main = Asset(path="in.mp4", kind=AssetKind.VIDEO)
    probes = {"in.mp4": make_media(path="in.mp4")}
    simple = {"main": main}
    for ops in ([StabilizeOp(), GrayscaleOp()],
                [StabilizeOp(), ResizeOp(height=720)],
                [StabilizeOp(), RotateOp(angle=90)],
                [StabilizeOp(), FilterOp(domain="audio", expr="volume=0.5")],
                [StabilizeOp(), AudioResampleOp(sample_rate=48000)]):
        ep = _plan(ops, simple, monkeypatch, probes)
        assert [p.purpose for p in ep.passes] == ["stabilize-detect",
                                                  "stabilize-transform"], ops


# -------------------------------------------------------------- pipeline

def test_generate_reports_steps(gen):
    r = gen("stabilize the shaky clip", output_filename="titan.mp4")
    assert r.status == "ready"
    assert [o["type"] for o in r.plan["operations"]] == ["stabilize"]
    assert r.steps is not None and len(r.steps) == 2
    assert r.steps[0]["purpose"] == "stabilize-detect"
    assert r.steps[1]["purpose"] == "stabilize-transform"
    # the top-level arguments/command are the FINAL pass
    assert r.steps[1]["arguments"] == r.arguments
    assert r.steps[1]["command"] == r.command
    assert any("vidstab" in s for s in r.summary)
    assert any("two-pass" in d for d in r.defaults)


def test_generate_stabilize_conflict_is_error(gen):
    r = gen("stabilize the clip and trim the first 10 seconds")
    assert r.status == "error"
    assert r.plan is None


# --------------------------------------------- sources & user filtergraphs

def test_anullsrc_lavfi_source_spec():
    # 'lavfi:' source specs pass through bare (registry sources.input.lavfi);
    # anullsrc (silent audio) exercises the spec path with an audio source
    edit = EditPlan(output=OutputSpec(mode="audio", container="wav",
                                      audio_codec="pcm_s16le"))
    assets = {"main": Asset(path="lavfi:anullsrc=r=44100:cl=stereo",
                            kind=AssetKind.SOURCE)}
    ep = plan_execution(edit, assets, None, "out.wav")
    assert ep.inputs[0].path == "anullsrc=r=44100:cl=stereo"
    assert ep.inputs[0].pre_opts == ["-f", "lavfi"]
    argv = build_argv(ep)
    f, i = argv.index("-f"), argv.index("-i")
    assert argv[f + 1] == "lavfi" and f < i  # -f lavfi precedes -i


def test_channelsplit_in_user_filtergraph():
    # channelsplit is a multi-OUTPUT filter: it has no place in the
    # builder's single-output -vf chain, but a user filtergraph may emit
    # several labeled streams from it — validation must accept the graph
    from fass.registry import filtergraph_problems

    assert "lavfi" in REGISTRY["sources"]["input"]  # the spec path exists
    assert filtergraph_problems(
        "[0:a]channelsplit=channel_layout=5.1[L][R][C]"
    ) == []
    # an unknown filter in the graph is still a validation problem
    assert filtergraph_problems(
        "[0:a]channelsplitx=channel_layout=5.1[L][R][C]"
    )


# ------------------------------------------------------------------ media

@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_stabilize_two_pass_real_media(tmp_path):
    # a synthetic clip through the actual two-pass sequence: the detect
    # pass must write the .trf the transform pass consumes
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=3",
         "-c:v", "libx264", str(src)],
        check=True,
    )
    from fass.pipeline import generate

    r = generate(
        "stabilize the clip",
        {"main": Asset(path=str(src), kind=AssetKind.VIDEO)},
        output_filename=str(tmp_path / "out.mp4"),
        adapter_name="rules",
    )
    assert r.status == "ready", r.errors
    # both passes run in the final output's working directory
    for argv in [s["arguments"] for s in r.steps]:
        proc = subprocess.run(argv, cwd=tmp_path, capture_output=True,
                              text=True)
        assert proc.returncode == 0, proc.stderr[-800:]
    trf = tmp_path / "out.vidstab.trf"
    assert trf.exists()
    head = trf.read_text().splitlines()[0]
    # vidstab's transform-file header ("# Transform info" or "VID.STAB 1")
    assert head.startswith("#") or head.startswith("VID.STAB")
    assert (tmp_path / "out.mp4").exists()
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height",
         "-of", "csv=p=0", str(tmp_path / "out.mp4")],
        capture_output=True, text=True, check=True,
    )
    assert "h264,320,240" in out.stdout
