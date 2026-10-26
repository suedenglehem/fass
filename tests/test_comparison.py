"""M10.4 — quality comparison: psnr/ssim/libvmaf against a reference input.

A comparison is a standalone diagnostic analysis: the reference clip is the
second input (pad 1), the metric compares the two video streams frame by
frame, and the summary lands in the run log — nothing is written to a file
(the plan runs against the null muxer, M10.2 pattern).

Layers, cheapest first:
- registry: the 'needs_reference' table (psnr, ssim, libvmaf)
- schema:   CompareOp inside the Operation union
- planner:  the labeled [0:v][1:v]metric[cmp] graph, reference scaling and
  rate/pix_fmt warnings, the libvmaf model environment, the standalone
  rejections (probes monkeypatched with fake MediaInfo — argv shape only)
- NLU:      comparison phrasings at adapter.parse level (no files needed)
- media:    the emitted argv is executed on real lavfi clips and the metric
  summary line is asserted in the run log (skipped when ffmpeg is absent;
  the libvmaf run additionally needs a build where the filter AND a model
  load — 9.0.1, not the 6.1.1 host)
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fass import execute
from fass.builder import build_argv
from fass.nlu import get_adapter
from fass.nlu.rules import SUPPORTED_SUMMARY
from fass.planner import PlanError, plan_execution
from fass.planner.planner import vmaf_model_path
from fass.probe.ffprobe import ffmpeg_available, ffprobe_available
from fass.registry import comparison_filters, filter_spec
from fass.schema import (
    Asset,
    AssetKind,
    AudioReplaceOp,
    CompareOp,
    EditPlan,
    FilterOp,
    OutputSpec,
    ResizeOp,
    SubtitleOp,
    TrimSpec,
    WatermarkOp,
)

from tests.conftest import make_media

ADAPTER = get_adapter("rules")

METRICS = ["libvmaf", "psnr", "ssim"]


# ------------------------------------------------------------------ helpers

def _assets(**named):
    """assets dict: 'main' (a video) + named extras, e.g. _assets(ref='r.mp4')"""
    out = {"main": Asset(path="/tmp/main.mp4", kind=AssetKind.VIDEO)}
    for aid, path in named.items():
        kind = AssetKind.AUDIO if str(path).lower().endswith(
            (".wav", ".mp3", ".m4a", ".flac", ".ogg")) else AssetKind.VIDEO
        out[aid] = Asset(path=path, kind=kind)
    return out


def _parse(instruction, assets, prev=None):
    return ADAPTER.parse(instruction, assets, prev, None)


def _probes(**over) -> dict:
    """Fake probe results keyed by input path (main + ref by default)."""
    base = {
        "/tmp/main.mp4": make_media(path="/tmp/main.mp4"),
        "/tmp/ref.mp4": make_media(path="/tmp/ref.mp4"),
    }
    base.update(over)
    return base


def _plan(operations, monkeypatch, probes, *, assets=None, trim=None,
          output=None):
    """plan_execution with fake probes (argv-shape tests, no files)."""
    assets = assets or _assets(ref="/tmp/ref.mp4")
    monkeypatch.setattr("fass.planner.planner.probe_media",
                        lambda p, require_video=True: probes.get(p))
    edit = EditPlan(
        output=output or OutputSpec(container="mp4", video_codec="h264"),
        trim=trim, operations=operations)
    return plan_execution(edit, assets, probes.get(assets["main"].path),
                          "out.mp4")


def _fc(argv):
    return argv[argv.index("-filter_complex") + 1]


# ------------------------------------------------------------------ registry

def test_registry_marks_the_comparison_metrics():
    assert sorted(comparison_filters()) == METRICS
    for name in METRICS:
        spec = filter_spec(name)
        assert spec is not None and spec.get("needs_reference") is True, name
        assert spec.get("diagnostic") is True, name


def test_libvmaf_carries_a_min_version():
    assert filter_spec("libvmaf").get("min_version") == "7.0"
    assert filter_spec("psnr").get("min_version") is None
    assert filter_spec("ssim").get("min_version") is None


# ------------------------------------------------------------------ schema

def test_compare_op_accepts_registry_metrics():
    for metric in METRICS:
        op = CompareOp(metric=metric, reference="ref")
        assert op.type == "compare"
        assert op.metric == metric
        assert op.reference == "ref"
        assert op.options == {}


def test_compare_op_rejects_unknown_metrics():
    with pytest.raises(ValidationError, match="not a comparison metric"):
        CompareOp(metric="mad", reference="ref")


def test_compare_op_rejects_unknown_options():
    with pytest.raises(ValidationError, match="comparison options rejected"):
        CompareOp(metric="psnr", reference="ref", options={"nonsense": "1"})


def test_duplicate_comparison_is_rejected():
    with pytest.raises(ValidationError):
        EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                 operations=[CompareOp(metric="psnr", reference="a"),
                             CompareOp(metric="ssim", reference="b")])


def test_comparison_conflicts_with_a_custom_filtergraph():
    with pytest.raises(ValidationError):
        EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                 operations=[CompareOp(metric="psnr", reference="ref")],
                 filtergraph="[0:v]scale=640:360[v]")


# ------------------------------------------------------------------ planner

def test_comparison_plan_shape(monkeypatch):
    ep = _plan([CompareOp(metric="psnr", reference="ref")],
               monkeypatch, _probes())
    assert ep.mode == "diagnostic"
    assert ep.loglevel == "info"
    assert ep.output_path == "-"
    assert ep.output_opts == ["-f", "null"]
    assert ep.vf is None and ep.af is None
    assert ep.maps == ["[cmp]"]  # labeled graph outputs are not auto-mapped
    assert [i.path for i in ep.inputs] == ["/tmp/main.mp4", "/tmp/ref.mp4"]
    # same dimensions, rate and pixel format — the bare metric segment
    assert ep.filter_complex == "[0:v][1:v]psnr[cmp]"
    assert any("nothing is written" in w for w in ep.warnings)
    assert any("compared frame by frame" in w for w in ep.warnings)


def test_comparison_scales_the_reference_when_sizes_differ(monkeypatch):
    probes = _probes(
        **{"/tmp/ref.mp4": make_media(path="/tmp/ref.mp4",
                                      width=1280, height=720)})
    ep = _plan([CompareOp(metric="psnr", reference="ref")],
               monkeypatch, probes)
    assert ep.filter_complex == "[1:v]scale=1920:1080[c1];[0:v][c1]psnr[cmp]"
    assert any("scaled to the main's size" in w for w in ep.warnings)
    # the scaled graph still passes the argv gate's filtergraph validator
    assert execute.validate_argv(build_argv(ep)) == []


def test_comparison_warns_on_rate_and_pixel_format_mismatches(monkeypatch):
    probes = _probes(
        **{"/tmp/ref.mp4": make_media(path="/tmp/ref.mp4", fps=15.0,
                                      pix_fmt="yuv420p10le")})
    ep = _plan([CompareOp(metric="ssim", reference="ref")],
               monkeypatch, probes)
    assert ep.filter_complex == "[0:v][1:v]ssim[cmp]"
    assert any("compared in arrival order" in w for w in ep.warnings)
    assert any("converts internally" in w for w in ep.warnings)


def test_comparison_metric_options_land_in_the_graph(monkeypatch):
    ep = _plan([CompareOp(metric="ssim", reference="ref",
                          options={"eof_action": "endall"})],
               monkeypatch, _probes())
    assert ep.filter_complex == "[0:v][1:v]ssim=eof_action=endall[cmp]"


def test_libvmaf_model_from_environment(monkeypatch):
    monkeypatch.setenv("FASS_VMAF_MODEL", "/opt/models/vmaf_v0.6.1.pkl")
    ep = _plan([CompareOp(metric="libvmaf", reference="ref")],
               monkeypatch, _probes())
    # the filter takes a model specification, not a bare path
    assert ep.filter_complex == (
        "[0:v][1:v]libvmaf=model=path=/opt/models/vmaf_v0.6.1.pkl[cmp]")
    assert any("libvmaf model: /opt/models/vmaf_v0.6.1.pkl" in d
               for d in ep.defaults)


def test_libvmaf_model_dir_fills_the_default_model_name(monkeypatch):
    monkeypatch.delenv("FASS_VMAF_MODEL", raising=False)
    monkeypatch.setenv("FASS_VMAF_MODEL_DIR", "/opt/vmaf")
    ep = _plan([CompareOp(metric="libvmaf", reference="ref")],
               monkeypatch, _probes())
    assert ep.filter_complex == (
        "[0:v][1:v]libvmaf=model=path=/opt/vmaf/vmaf_v0.6.1.pkl[cmp]")


def test_libvmaf_without_model_env_keeps_the_filter_default(monkeypatch):
    monkeypatch.delenv("FASS_VMAF_MODEL", raising=False)
    monkeypatch.delenv("FASS_VMAF_MODEL_DIR", raising=False)
    ep = _plan([CompareOp(metric="libvmaf", reference="ref")],
               monkeypatch, _probes())
    assert ep.filter_complex == "[0:v][1:v]libvmaf[cmp]"


def test_comparison_rejects_a_trim(monkeypatch):
    with pytest.raises(PlanError, match="trimming cannot be combined"):
        _plan([CompareOp(metric="psnr", reference="ref")], monkeypatch,
              _probes(), trim=TrimSpec(start_seconds=2, duration_seconds=5))


def test_comparison_rejects_a_watermark(monkeypatch):
    assets = _assets(ref="/tmp/ref.mp4")
    assets["mark"] = Asset(path="/tmp/mark.png", kind=AssetKind.IMAGE)
    with pytest.raises(PlanError, match="watermark cannot be added"):
        _plan([CompareOp(metric="psnr", reference="ref"),
               WatermarkOp(asset_id="mark")],
              monkeypatch, _probes(), assets=assets)


def test_comparison_rejects_replaced_audio(monkeypatch):
    assets = _assets(ref="/tmp/ref.mp4", track="/tmp/track.wav")
    with pytest.raises(PlanError, match="replacing the audio cannot"):
        _plan([CompareOp(metric="psnr", reference="ref"),
               AudioReplaceOp(asset_id="track")],
              monkeypatch, _probes(), assets=assets)


def test_comparison_rejects_burned_subtitles(monkeypatch):
    assets = _assets(ref="/tmp/ref.mp4", subs="/tmp/subs.srt")
    assets["subs"] = Asset(path="/tmp/subs.srt", kind=AssetKind.SUBTITLES)
    with pytest.raises(PlanError, match="subtitles cannot be combined"):
        _plan([CompareOp(metric="psnr", reference="ref"),
               SubtitleOp(asset_id="subs", mode="burn")],
              monkeypatch, _probes(), assets=assets)


def test_comparison_rejects_any_other_operation(monkeypatch):
    with pytest.raises(PlanError, match="cannot be combined with a "
                                         "comparison"):
        _plan([CompareOp(metric="psnr", reference="ref"),
               ResizeOp(width=640)],
              monkeypatch, _probes())


def test_comparison_rejects_a_non_video_output(monkeypatch):
    with pytest.raises(PlanError, match="cannot produce an audio output"):
        _plan([CompareOp(metric="psnr", reference="ref")], monkeypatch,
              _probes(),
              output=OutputSpec(container="m4a", audio_codec="aac",
                                mode="audio"))


def test_comparison_reference_must_differ_from_main(monkeypatch):
    with pytest.raises(PlanError, match="must be a different file"):
        _plan([CompareOp(metric="psnr", reference="main")], monkeypatch,
              _probes())


def test_comparison_reference_must_be_bound(monkeypatch):
    with pytest.raises(PlanError, match="not in the asset map"):
        _plan([CompareOp(metric="psnr", reference="ghost")], monkeypatch,
              _probes())


def test_comparison_reference_must_be_media(monkeypatch):
    assets = _assets(ref="/tmp/ref.png")
    assets["ref"] = Asset(path="/tmp/ref.png", kind=AssetKind.IMAGE)
    with pytest.raises(PlanError, match="must be a media file"):
        _plan([CompareOp(metric="psnr", reference="ref")],
              monkeypatch, _probes(), assets=assets)


def test_comparison_reference_must_be_a_local_file(monkeypatch):
    assets = _assets(ref="udp://192.168.0.5:1234")
    with pytest.raises(PlanError, match="is a stream"):
        _plan([CompareOp(metric="psnr", reference="ref")],
              monkeypatch, _probes(), assets=assets)


def test_comparison_reference_without_video_is_rejected(monkeypatch):
    probes = _probes(**{"/tmp/ref.mp4": make_media(
        path="/tmp/ref.mp4", video_streams=0, has_audio=True,
        audio_streams=1)})
    with pytest.raises(PlanError, match="has no video stream"):
        _plan([CompareOp(metric="psnr", reference="ref")],
              monkeypatch, probes)


def test_comparison_unprobeable_reference_is_rejected(monkeypatch):
    probes = {"/tmp/main.mp4": make_media(path="/tmp/main.mp4")}
    with pytest.raises(PlanError, match="could not be probed"):
        _plan([CompareOp(metric="psnr", reference="ref")],
              monkeypatch, probes)


def test_metric_as_plain_filter_needs_a_reference(monkeypatch):
    # a comparison metric on the plain -vf chain has no reference pad
    with pytest.raises(PlanError, match="needs a reference input"):
        _plan([FilterOp(expr="psnr", domain="video")],
              monkeypatch, _probes())


def test_comparison_argv_passes_the_gate(monkeypatch):
    probes = _probes(**{"/tmp/ref.mp4": make_media(path="/tmp/ref.mp4",
                                                   width=1280, height=720)})
    ep = _plan([CompareOp(metric="psnr", reference="ref")],
               monkeypatch, probes)
    argv = build_argv(ep)
    assert argv[:4] == ["ffmpeg", "-hide_banner", "-loglevel", "info"]
    assert argv[-3:] == ["-f", "null", "-"]
    assert argv.count("-i") == 2
    assert execute.validate_argv(argv) == []


# ------------------------------------------------------------------ NLU

COMPARE_PHRASES = [
    ("compare main.mp4 against ref.mp4 with psnr", "psnr"),
    ("measure the psnr against ref.mp4", "psnr"),
    ("psnr against ref.mp4", "psnr"),
    ("measure ssim between the two clips", "ssim"),
    ("how much quality loss, using ssim, against ref.mp4", "ssim"),
    ("give me the vmaf against ref.mp4", "libvmaf"),
    ("run libvmaf against ref.mp4", "libvmaf"),
]


@pytest.mark.parametrize("phrase,metric", COMPARE_PHRASES)
def test_comparison_phrases(phrase, metric):
    r = _parse(phrase, _assets(ref="ref.mp4"))
    assert r.status == "ready", (phrase, r.status, r.clarifications,
                                 r.unsupported)
    comps = [o for o in r.plan.operations if o.type == "compare"]
    assert len(comps) == 1, (phrase, r.plan.operations)
    assert comps[0].metric == metric
    assert comps[0].reference == "ref"


def test_comparison_without_metric_asks_which_metric():
    r = _parse("how much quality loss did the re-encode cause",
               _assets(ref="ref.mp4"))
    assert r.status == "needs_clarification"
    assert "PSNR" in r.clarifications[0].question
    assert "SSIM" in r.clarifications[0].question
    # libvmaf is never offered unless named
    assert "VMAF" not in r.clarifications[0].question


def test_comparison_with_several_references_asks_which_file():
    assets = _assets(ref1="ref1.mp4", ref2="ref2.mp4")
    r = _parse("compare the clip with psnr", assets)
    assert r.status == "needs_clarification"
    assert "reference" in r.clarifications[0].question


def test_comparison_without_any_reference_suggests_binding():
    r = _parse("compare main.mp4 against the original with psnr",
               _assets())
    assert r.status == "needs_clarification"
    assert "compared against" in r.clarifications[0].question
    assert "--asset" in (r.clarifications[0].hint or "")


def test_comparison_against_an_audio_file_is_unsupported():
    r = _parse("compare main.mp4 against track.wav with psnr",
               _assets(track="track.wav"))
    assert r.status == "unsupported"
    assert r.unsupported[0].fragment.startswith("comparing with 'track.wav'")


def test_comparison_into_an_audio_output_is_unsupported():
    r = _parse("compare main.mp4 against ref.mp4 with psnr "
               "and extract the audio", _assets(ref="ref.mp4"))
    assert r.status == "unsupported"
    assert r.unsupported[0].fragment == \
        "comparing the inputs with psnr into an audio output"


def test_comparison_with_a_trim_is_a_clarification():
    r = _parse("compare main.mp4 against ref.mp4 with psnr, and keep only "
               "the first 10 seconds", _assets(ref="ref.mp4"))
    assert r.status == "needs_clarification"
    assert "standalone analysis" in r.clarifications[0].question


def test_unrelated_edit_does_not_fire_a_comparison():
    r = _parse("remove the audio", _assets(ref="ref.mp4"))
    assert r.status == "ready"
    assert not [o for o in r.plan.operations if o.type == "compare"]


def test_comparison_revision_replaces_the_previous_metric():
    assets = _assets(ref="ref.mp4")
    first = _parse("compare main.mp4 against ref.mp4 with psnr", assets)
    assert first.status == "ready"
    r = _parse("instead measure ssim against ref.mp4", assets, first.plan)
    assert r.status == "ready", (r.status, r.clarifications)
    comps = [o for o in r.plan.operations if o.type == "compare"]
    assert len(comps) == 1
    assert comps[0].metric == "ssim"


def test_supported_summary_mentions_comparisons():
    assert "quality difference" in SUPPORTED_SUMMARY


# ------------------------------------------------------------------- media

def _media_ok():
    return ffmpeg_available() and ffprobe_available()


pytestmark_media = pytest.mark.skipif(
    not _media_ok(), reason="ffmpeg/ffprobe not available")


def _ffmpeg(args: list[str]) -> None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True, timeout=300, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"fixture generation failed: {args}\n{p.stderr}")


def _libvmaf_works() -> bool:
    """The libvmaf filter is present AND a model actually loads (the 9.0.1
    image; the 6.1.1 host build has no libvmaf at all)."""
    if not _media_ok():
        return False
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=30,
                           check=False)
        if not any("libvmaf" in line.split()
                   for line in p.stdout.splitlines()):
            return False
        # The filter's model option is a specification, not a bare path —
        # resolve it exactly as the planner does, so an image that ships
        # the model via FASS_VMAF_MODEL is detected as working (a bare
        # `libvmaf` fails to init when no built-in model is compiled in).
        expr = "libvmaf"
        model = vmaf_model_path()
        if model:
            expr = f"libvmaf=model=path={model}"
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=1",
             "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=1",
             "-filter_complex", f"[0:v][1:v]{expr}[cmp]",
             "-map", "[cmp]", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark_vmaf = pytest.mark.skipif(
    not _libvmaf_works(),
    reason="libvmaf filter or a loadable model is not in this ffmpeg build")


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> SimpleNamespace:
    d = tmp_path_factory.mktemp("fass_comparison_fixtures")

    def clip(name, size, *, audio=True, duration=10, rate=15):
        out = d / name
        args = ["-f", "lavfi", "-i",
                f"testsrc2=size={size}:rate={rate}:duration={duration}"]
        if audio:
            args += ["-f", "lavfi", "-i",
                     f"sine=frequency=440:sample_rate=44100:duration={duration}",
                     "-c:v", "libx264", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                     "-shortest", str(out)]
        else:
            args += ["-c:v", "libx264", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-an", str(out)]
        _ffmpeg(args)
        return out

    ns = SimpleNamespace(
        a=clip("a.mp4", "1920x1080"),                      # 10s 1080p + audio
        b=clip("b.mp4", "1280x720", duration=8),           # 8s  720p  + audio
        c=clip("c.mp4", "1280x720", duration=6, audio=False),  # 6s, silent
    )
    return ns


def _run_compare(gen, instruction, main, ref_path, out_dir):
    r = gen(instruction, input_path=str(main),
            extra={"ref": Asset(path=str(ref_path))})
    assert r.status == "ready", \
        f"{r.status}: {r.errors} {r.clarifications} {r.unsupported}"
    proc = subprocess.run(r.arguments, shell=False, cwd=str(out_dir),
                          capture_output=True, text=True, timeout=300,
                          check=False)
    assert proc.returncode == 0, f"argv={r.arguments}\n{proc.stderr[-2000:]}"
    return r, proc


@ pytestmark_media
def test_e2e_psnr_scales_the_reference_and_logs_the_summary(gen, clips,
                                                            tmp_path):
    r, proc = _run_compare(gen, "compare a.mp4 against b.mp4 with psnr",
                           clips.a, clips.b, tmp_path)
    # b is 1280x720 against a's 1920x1080 — the scale segment leads the graph
    assert _fc(r.arguments) == "[1:v]scale=1920:1080[c1];[0:v][c1]psnr[cmp]"
    assert any("scaled to the main's size" in w for w in r.warnings)
    # the metric summary lands in the run log at info level
    # (6.1 prints 'average:', other builds 'avg:' — accept both)
    assert "PSNR" in proc.stderr
    assert "avg:" in proc.stderr or "average:" in proc.stderr
    # the null muxer wrote nothing
    assert list(tmp_path.iterdir()) == []


@ pytestmark_media
def test_e2e_ssim_same_size_needs_no_scale(gen, clips, tmp_path):
    r, proc = _run_compare(gen, "measure ssim of b.mp4 against c.mp4",
                           clips.b, clips.c, tmp_path)
    assert _fc(r.arguments) == "[0:v][1:v]ssim[cmp]"
    assert "SSIM" in proc.stderr
    assert list(tmp_path.iterdir()) == []


@ pytestmark_vmaf
def test_e2e_libvmaf_logs_the_score(gen, clips, tmp_path):
    r, proc = _run_compare(gen, "compare a.mp4 against b.mp4 with libvmaf",
                           clips.a, clips.b, tmp_path)
    assert "libvmaf" in _fc(r.arguments)
    assert "VMAF" in proc.stderr
    assert list(tmp_path.iterdir()) == []
