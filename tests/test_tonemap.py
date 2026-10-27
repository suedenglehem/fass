"""M12.HDR: HDR→SDR tone-mapping and 10-bit HDR passthrough.

Layers, cheapest first:
- registry: the tonemap entry (options, min_version, doc fields) and the
            chain's filtergraph validation
- schema:   ToneMapOp (strict, no options — the curve stays the filter's
            built-in default, like StabilizeOp)
- NLU:      tone-map phrasings at RulesAdapter.parse level; the audio /
            subtitles mode conflicts; the premium extensions (HDR10+,
            Dolby Vision) staying UNSUPPORTED; the bare-main10 passthrough
            phrasing routing to the 'profile' codec option
- planner:  the fixed chain in -vf and in complex mode, the semantic
            conflicts (10-bit pixel format, bitstream filters, audio
            mode), the probe-based disclosures (HDR input / SDR input /
            no metadata) and the passthrough notes (copy / 10-bit /
            SDR-convert warning); probes are monkeypatched (argv shape)
- pipeline: generate() end-to-end on the rules adapter
- media:    a synthetic HDR10 clip through the emitted argv on the real
            ffmpeg (skipped when ffmpeg is absent)
"""

from __future__ import annotations

import subprocess

import pytest

from fass.builder import build_argv
from fass.execute import validate_argv
from fass.nlu import get_adapter
from fass.planner import PlanError, plan_execution
from fass.probe.ffprobe import ffmpeg_available
from fass.registry import filter_expr_problems, filter_spec, filtergraph_problems
from fass.schema import (
    Asset,
    AssetKind,
    AudioResampleOp,
    BitstreamFilterSpec,
    EditPlan,
    FilterOp,
    OutputSpec,
    ParseStatus,
    ResizeOp,
    ToneMapOp,
    WatermarkOp,
)
from tests.conftest import make_media

ADAPTER = get_adapter("rules")

TONEMAP_CHAIN = "format=gbrpf32le,tonemap=tonemap=hable:desat=0,format=yuv420p"


def _parse(instruction, assets=None):
    assets = assets or {"main": Asset(path="hdr.mp4", kind=AssetKind.VIDEO)}
    return ADAPTER.parse(instruction, assets, None, None)


def _plan(operations, assets, monkeypatch, probes, *, output="out.mp4",
          output_spec=None, bsf=None):
    """plan_execution with fake probes (argv-shape tests, no files)."""
    monkeypatch.setattr("fass.planner.planner.probe_media",
                        lambda p, require_video=True: probes.get(p))
    edit = EditPlan(
        output=output_spec or OutputSpec(container="mp4", video_codec="h264"),
        operations=operations,
        bitstream_filters=bsf,
    )
    return plan_execution(edit, assets, probes.get(assets["main"].path),
                          output)


def _hdr(**kw) -> "MediaInfo":
    base = dict(pix_fmt="yuv420p10le", hdr=True)
    base.update(kw)
    return make_media(**base)


# ---------------------------------------------------------------- registry

def test_registry_tonemap_entry():
    spec = filter_spec("tonemap")
    assert spec is not None
    assert spec["min_version"] == "4.2"
    assert spec["doc_ref"]["anchor"] == "tonemap"
    assert "tone map" in spec["aliases"]
    assert spec["provenance"]["verified_builds"] == ["6.1.1", "9.0.1"]
    # the four AVOptions both builds expose (verified on 6.1.1 and 9.0.1)
    assert set(spec["options"]) == {"tonemap", "param", "desat", "peak"}
    assert "hable" in spec["options"]["tonemap"]["values"]


def test_tonemap_chain_passes_graph_validation():
    assert filtergraph_problems(f"[0:v]{TONEMAP_CHAIN}[v]") == []
    # the chain's tonemap instance validates against the option schema…
    assert filter_expr_problems("tonemap=tonemap=hable:desat=0") == []
    # …and a bogus curve name is caught there (the enum is enforced)
    assert filter_expr_problems("tonemap=tonemap=boguscurve")


# ------------------------------------------------------------------ schema

def test_tonemap_op_is_strict_and_optionless():
    assert ToneMapOp().type == "tonemap"
    with pytest.raises(Exception):
        ToneMapOp(curve="hable")  # curve parameters are not exposed (M12)
    plan = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[ToneMapOp()])
    assert plan.operations[0].type == "tonemap"


# -------------------------------------------------------------------- NLU

TONEMAP_PHRASES = (
    "tone map this HDR video to SDR",
    "convert HDR to SDR",
    "tonemap to Rec.709",
    "make this HDR10 video SDR",
    "convert this HDR10 video to Rec.709",
    "tone-map the video",
)


def test_tonemap_phrases_ready_and_validated():
    for instr in TONEMAP_PHRASES:
        r = _parse(instr)
        assert r.status is ParseStatus.READY, instr
        assert [o.type for o in r.plan.operations] == ["tonemap"], instr
        ep = plan_execution(r.plan, {"main": Asset(path="hdr.mp4")},
                            None, "out.mp4")
        argv = build_argv(ep)
        assert validate_argv(argv) == [], instr
        assert argv[argv.index("-vf") + 1] == TONEMAP_CHAIN, instr


def test_tonemap_never_a_bare_filter_op():
    # 'tonemap' is a registry filter name; routing it to ToneMapOp builds
    # the full chain — a bare FilterOp('tonemap') could not run (no float
    # RGB input, no 8-bit landing format)
    r = _parse("tonemap the video")
    assert r.status is ParseStatus.READY
    assert all(o.type != "filter" for o in r.plan.operations)
    assert any(o.type == "tonemap" for o in r.plan.operations)


def test_tonemap_audio_mode_conflict():
    r = _parse("tone map the video to mp3")
    assert r.status is ParseStatus.NEEDS_CLARIFICATION
    assert any("tonemap" in c.question.lower() or "tone-map" in c.question.lower()
               for c in r.clarifications)


def test_tonemap_subtitles_mode_conflict():
    r = _parse("tone map the video and extract the subtitles to a file")
    assert r.status is ParseStatus.NEEDS_CLARIFICATION


def test_hdr_premium_extensions_stay_unsupported():
    for instr in ("convert this to dolby vision",
                  "convert hdr10+ to sdr",
                  "tone map this hdr 10 plus video"):
        r = _parse(instr)
        assert r.status is ParseStatus.UNSUPPORTED, instr


def test_passthrough_main10_phrasing():
    # bare 'main10' (no 'profile' word) routes to the 'profile' codec
    # option — the schema's video_profile field is baseline/main/high only
    r = _parse("re-encode this HDR10 video as HEVC main10")
    assert r.status is ParseStatus.READY
    assert r.plan.output.video_codec == "h265"
    assert r.plan.output.video_codec_options.get("profile") == "main10"
    ep = plan_execution(r.plan, {"main": Asset(path="hdr.mp4")}, None,
                        "out.mp4")
    argv = build_argv(ep)
    assert validate_argv(argv) == []
    assert argv[argv.index("-profile") + 1] == "main10"
    # main10 is the 10-bit profile — the 8-bit codec default cannot carry it
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p10le"
    assert any("main10 profile encodes 10-bit" in d for d in ep.defaults)


def test_passthrough_10bit_phrasing():
    r = _parse("encode this video as h265 10-bit")
    assert r.status is ParseStatus.READY
    assert r.plan.output.pixel_format == "yuv420p10le"


# --------------------------------------------------------------- planner

def test_tonemap_chain_in_vf(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": _hdr(path="in.mp4")}
    ep = _plan([ToneMapOp()], assets, monkeypatch, probes)
    assert ep.mode == "reencode"
    assert ep.vf == TONEMAP_CHAIN
    assert any("tone-mapping the HDR input" in d for d in ep.defaults)
    assert validate_argv(build_argv(ep)) == []


def test_tonemap_composes_with_resize(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": _hdr(path="in.mp4")}
    ep = _plan([ToneMapOp(), ResizeOp(height=720)], assets, monkeypatch,
               probes)
    assert ep.vf == f"{TONEMAP_CHAIN},scale=-2:720"


def test_tonemap_in_complex_mode_with_watermark(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO),
              "logo": Asset(path="logo.png", kind=AssetKind.IMAGE)}
    probes = {"in.mp4": _hdr(path="in.mp4")}
    ep = _plan([ToneMapOp(), WatermarkOp(asset_id="logo")], assets,
               monkeypatch, probes)
    assert ep.filter_complex is not None
    assert TONEMAP_CHAIN in ep.filter_complex
    assert "overlay" in ep.filter_complex
    assert validate_argv(build_argv(ep)) == []


def test_tonemap_conflicts(monkeypatch):
    main = Asset(path="in.mp4", kind=AssetKind.VIDEO)
    simple = {"main": main}
    probes = {"in.mp4": _hdr(path="in.mp4")}

    def reject(ops, needle, **kw):
        with pytest.raises(PlanError) as e:
            _plan(ops, simple, monkeypatch, probes, **kw)
        assert needle.lower() in str(e.value).lower()

    reject([ToneMapOp()], "8-bit sdr",
           output_spec=OutputSpec(container="mp4", video_codec="h265",
                                  pixel_format="yuv420p10le"))
    reject([ToneMapOp()], "bitstream filter",
           bsf=[BitstreamFilterSpec(name="h264_metadata", options={})])
    reject([ToneMapOp()], "video edit",
           output_spec=OutputSpec(mode="audio", container="mp3",
                                  audio_codec="mp3"))


def test_tonemap_allowed_combos(monkeypatch):
    main = Asset(path="in.mp4", kind=AssetKind.VIDEO)
    simple = {"main": main}
    probes = {"in.mp4": _hdr(path="in.mp4")}
    for ops in ([ToneMapOp(), ResizeOp(height=720)],
                [ToneMapOp(), FilterOp(domain="video", expr="hflip")],
                [ToneMapOp(), FilterOp(domain="audio", expr="volume=0.5")],
                [ToneMapOp(), AudioResampleOp(sample_rate=48000)]):
        ep = _plan(ops, simple, monkeypatch, probes)
        assert TONEMAP_CHAIN in (ep.vf or ""), ops
        assert validate_argv(build_argv(ep)) == [], ops


# ------------------------------------------------- probe disclosures

def test_tonemap_sdr_input_warns(monkeypatch):
    # tone-mapping an SDR source washes out the image — say so
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    probes = {"in.mp4": make_media(path="in.mp4")}  # hdr=False
    ep = _plan([ToneMapOp()], assets, monkeypatch, probes)
    assert any("no hdr color metadata" in w.lower() for w in ep.warnings)


def test_tonemap_no_metadata_notes_assumption(monkeypatch):
    assets = {"main": Asset(path="in.mp4", kind=AssetKind.VIDEO)}
    ep = _plan([ToneMapOp()], assets, monkeypatch, {})
    assert any("assumes an hdr" in d.lower() for d in ep.defaults)


def test_passthrough_copy_note(monkeypatch):
    # HDR input, compatible codec, no re-encode op → stream copy carries
    # the color metadata through
    assets = {"main": Asset(path="in.mkv", kind=AssetKind.VIDEO)}
    probes = {"in.mkv": _hdr(path="in.mkv", video_codec="h264")}
    ep = _plan([], assets, monkeypatch, probes, output="out.mkv",
               output_spec=OutputSpec(container="mkv", video_codec="h264"))
    assert ep.mode == "copy"
    assert any("stream copy carries" in d for d in ep.defaults)


def test_passthrough_10bit_note(monkeypatch):
    assets = {"main": Asset(path="in.mkv", kind=AssetKind.VIDEO)}
    probes = {"in.mkv": _hdr(path="in.mkv", video_codec="h265")}
    ep = _plan([], assets, monkeypatch, probes, output="out.mkv",
               output_spec=OutputSpec(container="mkv", video_codec="h265",
                                      pixel_format="yuv420p10le"))
    assert any("10-bit output preserves" in d for d in ep.defaults)


def test_hdr_to_sdr_convert_warns(monkeypatch):
    # HDR input, 8-bit SDR re-encode, no tone-map → the existing honesty
    # warning stands
    assets = {"main": Asset(path="in.mkv", kind=AssetKind.VIDEO)}
    probes = {"in.mkv": _hdr(path="in.mkv", video_codec="h265")}
    ep = _plan([], assets, monkeypatch, probes,
               output_spec=OutputSpec(container="mp4", video_codec="h264"))
    assert any("looks like hdr" in w.lower() for w in ep.warnings)


# -------------------------------------------------------------- pipeline

def test_generate_tonemap(gen):
    r = gen("tone map this HDR video to SDR", input_path="hdr.mp4")
    assert r.status == "ready", r.errors
    assert [o["type"] for o in r.plan["operations"]] == ["tonemap"]
    assert r.arguments[r.arguments.index("-vf") + 1] == TONEMAP_CHAIN
    assert validate_argv(r.arguments) == []


def test_generate_passthrough_main10(gen):
    r = gen("re-encode this HDR10 video as HEVC main10", input_path="hdr.mp4")
    assert r.status == "ready", r.errors
    assert r.arguments[r.arguments.index("-profile") + 1] == "main10"
    assert r.arguments[r.arguments.index("-pix_fmt") + 1] == "yuv420p10le"


# ------------------------------------------------------------------ media

def _ffprobe_pix_fmt(path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_tonemap_real_media_hdr10_source(tmp_path):
    # synthetic HDR10 source (10-bit, BT.2020 primaries, PQ transfer)
    # through the emitted argv: the output must land on 8-bit SDR
    src = tmp_path / "hdr.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=2",
         "-pix_fmt", "yuv420p10le",
         "-color_primaries", "bt2020", "-color_trc", "smpte2084",
         "-colorspace", "bt2020nc",
         "-c:v", "libx264", "-crf", "30", str(src)],
        check=True,
    )
    from fass.pipeline import generate

    r = generate(
        "tone map this HDR video to SDR",
        {"main": Asset(path=str(src), kind=AssetKind.VIDEO)},
        output_filename=str(tmp_path / "out.mp4"),
        adapter_name="rules",
    )
    assert r.status == "ready", r.errors
    assert any("tone-mapping the hdr input" in d.lower() for d in r.defaults)
    proc = subprocess.run(r.arguments, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert _ffprobe_pix_fmt(tmp_path / "out.mp4") == "yuv420p"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_passthrough_main10_real_media(tmp_path):
    # the same HDR10 source through the main10 passthrough argv: the
    # 10-bit output keeps the 10-bit pixel format
    src = tmp_path / "hdr.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=2",
         "-pix_fmt", "yuv420p10le",
         "-color_primaries", "bt2020", "-color_trc", "smpte2084",
         "-colorspace", "bt2020nc",
         "-c:v", "libx264", "-crf", "30", str(src)],
        check=True,
    )
    from fass.pipeline import generate

    r = generate(
        "re-encode this HDR10 video as HEVC main10",
        {"main": Asset(path=str(src), kind=AssetKind.VIDEO)},
        output_filename=str(tmp_path / "out.mp4"),
        adapter_name="rules",
    )
    assert r.status == "ready", r.errors
    proc = subprocess.run(r.arguments, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert _ffprobe_pix_fmt(tmp_path / "out.mp4") == "yuv420p10le"
