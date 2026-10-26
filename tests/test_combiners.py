"""M10.3 combiners: multi-input filters through labeled -filter_complex.

Layers, cheapest first:
- registry: the nine combiner entries and their option schemas
- schema:   CombinerOp validation inside the Operation union
- planner:  graph shape, pad labels, stream maps, semantic rejections
  (probes are monkeypatched with fake MediaInfo — argv shape only)
- NLU:      two-input phrasings at adapter.parse level (no files needed)
- media:    the emitted argv is executed on real lavfi clips and the
  output is probed (same contract as test_media.py; skipped when
  ffmpeg is absent)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fass.builder import build_argv
from fass.nlu import get_adapter
from fass.planner import PlanError, plan_execution
from fass.probe.ffprobe import ffmpeg_available, ffprobe_available, probe_media
from fass.registry import combiner_domain, combiner_filters, filter_specs
from fass.schema import (
    Asset,
    AssetKind,
    CombinerOp,
    EditPlan,
    OutputSpec,
    ResizeOp,
    TrimSpec,
    WatermarkOp,
)
from tests.conftest import make_media

ADAPTER = get_adapter("rules")

COMBINERS = {
    "video": ["xfade", "concat", "hstack", "vstack", "overlay"],
    "audio": ["acrossfade", "amix", "amerge", "join"],
}


def _combine(filter_name, asset_ids, options=None):
    return CombinerOp(filter=filter_name, asset_ids=asset_ids,
                      options=options or {})


def _parse(instruction, assets):
    return ADAPTER.parse(instruction, assets, None, None)


def _assets(**named):
    """assets dict: 'main' (a video) + named extras, e.g. _assets(clip2='b.mp4')"""
    out = {"main": Asset(path="main.mp4", kind=AssetKind.VIDEO)}
    for aid, path in named.items():
        kind = AssetKind.AUDIO if path.lower().endswith((".wav", ".mp3", ".m4a",
                                                         ".flac", ".ogg")) \
            else AssetKind.VIDEO
        out[aid] = Asset(path=path, kind=kind)
    return out


def _plan(operations, assets, monkeypatch, probes, *, trim=None, output=None):
    """plan_execution with fake probes (argv-shape tests, no files)."""
    monkeypatch.setattr("fass.planner.planner.probe_media",
                        lambda p, require_video=True: probes.get(p))
    edit = EditPlan(
        output=output or OutputSpec(container="mp4", video_codec="h264"),
        trim=trim, operations=operations)
    return plan_execution(edit, assets, probes.get(assets["main"].path),
                          "out.mp4")


def _maps(argv):
    return [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]


def _fc(argv):
    return argv[argv.index("-filter_complex") + 1]


# ---------------------------------------------------------------- registry

def test_combiner_set_is_nine():
    got = combiner_filters()
    assert sorted(got) == sorted(sum(COMBINERS.values(), []))
    assert all("multi_input" in spec for spec in got.values())


def test_combiner_exact_two_inputs():
    specs = filter_specs()
    for name in ("xfade", "acrossfade", "overlay"):
        assert specs[name]["multi_input"] == 2
    for name in ("concat", "hstack", "vstack", "amix", "amerge", "join"):
        assert specs[name]["multi_input"] == 0  # variadic: 0 = "at least 2"


def test_combiner_domains():
    for name in COMBINERS["video"]:
        assert combiner_domain(name) == "video"
    for name in COMBINERS["audio"]:
        assert combiner_domain(name) == "audio"
    assert combiner_domain("scale") is None
    assert combiner_domain("nosuchfilter") is None


def test_combiner_option_schemas_present():
    specs = filter_specs()
    assert set(specs["concat"]["options"]) == {"n", "v", "a", "unsafe"}
    assert specs["hstack"]["options"]["inputs"]["range"] == [2, 2147483647]
    for opt in ("x", "y", "eof_action", "format"):
        assert opt in specs["overlay"]["options"]


# ------------------------------------------------------------------ schema

def test_combiner_op_in_operation_union():
    plan = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                   operations=[_combine("xfade", ["clip2"])])
    assert plan.operations[0].type == "combine"


def test_two_combiners_rejected():
    with pytest.raises(ValidationError):
        EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                 operations=[_combine("xfade", ["a"]),
                             _combine("hstack", ["b"])])


def test_unknown_combiner_filter_rejected():
    with pytest.raises(ValidationError):
        _combine("vstack2", ["a"])


def test_combiner_bad_option_rejected():
    with pytest.raises(ValidationError):
        _combine("xfade", ["a"], {"transition": "not-a-transition"})
    with pytest.raises(ValidationError):
        _combine("hstack", ["a"], {"inputs": "1"})


def test_combiner_conflicts_with_filtergraph():
    with pytest.raises(ValidationError):
        EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                 operations=[_combine("xfade", ["a"])],
                 filtergraph="[0:v]hue[s]")


# ----------------------------------------------------------------- planner

def test_xfade_offset_from_main_duration(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("xfade", ["b"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert argv.count("-i") == 2
    fc = _fc(argv)
    assert "[0:v]fps=30,format=yuv420p,settb=AVTB[c0]" in fc
    assert "[1:v]fps=30,format=yuv420p,settb=AVTB[c1]" in fc
    assert "[c0][c1]xfade=transition=fade:duration=1:offset=119[cv]" in fc
    assert "[cv]" in _maps(argv) and "0:a?" in _maps(argv)
    assert any("offset 119s" in d for d in ep.defaults)


def test_xfade_explicit_duration_and_transition(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("xfade", ["b"],
                         {"duration": "2", "transition": "dissolve"})],
               assets, monkeypatch, probes)
    assert "xfade=transition=dissolve:duration=2:offset=118[cv]" in _fc(
        build_argv(ep))


def test_xfade_dropped_second_audio_warned(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("xfade", ["b"])], assets, monkeypatch, probes)
    assert any("second input's audio is dropped" in w for w in ep.warnings)


def test_xfade_duration_exceeds_main_rejected(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    with pytest.raises(PlanError):
        _plan([_combine("xfade", ["b"], {"duration": "130"})],
              assets, monkeypatch, probes)


def test_acrossfade_maps_graph_audio(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "t.wav": make_media(path="t.wav", has_audio=True)}
    assets = {"main": Asset(path="a.mp4"),
              "t": Asset(path="t.wav", kind=AssetKind.AUDIO)}
    ep = _plan([_combine("acrossfade", ["t"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[0:a][1:a]acrossfade=d=1[ca]" in _fc(argv)
    maps = _maps(argv)
    assert "[ca]" in maps and "0:a?" not in maps
    assert any("defaulted" in d for d in ep.defaults)


def test_concat_keeps_audio_when_all_have_it(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("concat", ["b"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[cv][ca]" in _fc(argv)
    maps = _maps(argv)
    assert "[cv]" in maps and "[ca]" in maps and "0:a?" not in maps


def test_concat_drops_audio_when_none_have_it(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4", has_audio=False),
              "b.mp4": make_media(path="b.mp4", has_audio=False)}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("concat", ["b"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[0:v][1:v]concat=n=2:v=1:a=0[cv]" in _fc(argv)
    maps = _maps(argv)
    assert "[cv]" in maps and not any(m.endswith(":a") or "a" in m
                                      for m in maps if m != "[cv]")
    assert "-an" in argv


def test_concat_mixed_audio_rejected(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4", has_audio=False)}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    with pytest.raises(PlanError, match="audio does not line up"):
        _plan([_combine("concat", ["b"])], assets, monkeypatch, probes)


def test_concat_explicit_a0(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("concat", ["b"], {"a": "0"})],
               assets, monkeypatch, probes)
    assert "concat=n=2:v=1:a=0[cv]" in _fc(build_argv(ep))
    assert any("dropped" in w for w in ep.warnings)


def test_hstack_scales_mismatched_input(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4", width=1280, height=720)}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("hstack", ["b"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[1:v]scale=-2:1080[s1]" in _fc(argv)
    assert "[0:v][s1]hstack=inputs=2[cv]" in _fc(argv)
    assert any("scaled to the main's height" in w for w in ep.warnings)


def test_vstack_scales_mismatched_input(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4", width=1280, height=720)}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("vstack", ["b"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[1:v]scale=1920:-2[s1]" in _fc(argv)
    assert "[0:v][s1]vstack=inputs=2[cv]" in _fc(argv)


def test_hstack_equal_sizes_no_scale(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("hstack", ["b"])], assets, monkeypatch, probes)
    assert "[0:v][1:v]hstack=inputs=2[cv]" in _fc(build_argv(ep))


def test_overlay_position_options(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("overlay", ["b"], {"x": "W-w-16", "y": "16"})],
               assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[0:v][1:v]overlay=x=W-w-16:y=16[cv]" in _fc(argv)
    maps = _maps(argv)
    assert "[cv]" in maps and "0:a?" in maps


def test_amix_maps_graph_audio(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "t.wav": make_media(path="t.wav")}
    assets = {"main": Asset(path="a.mp4"),
              "t": Asset(path="t.wav", kind=AssetKind.AUDIO)}
    ep = _plan([_combine("amix", ["t"])], assets, monkeypatch, probes)
    argv = build_argv(ep)
    assert "[0:a][1:a]amix=inputs=2:duration=longest[ca]" in _fc(argv)
    assert "[ca]" in _maps(argv)


def test_join_requires_layout(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "t.wav": make_media(path="t.wav")}
    assets = {"main": Asset(path="a.mp4"),
              "t": Asset(path="t.wav", kind=AssetKind.AUDIO)}
    with pytest.raises(PlanError, match="channel layout"):
        _plan([_combine("join", ["t"])], assets, monkeypatch, probes)


def test_join_with_layout(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "t.wav": make_media(path="t.wav")}
    assets = {"main": Asset(path="a.mp4"),
              "t": Asset(path="t.wav", kind=AssetKind.AUDIO)}
    ep = _plan([_combine("join", ["t"], {"channel_layout": "5.1"})],
               assets, monkeypatch, probes)
    assert "[0:a][1:a]join=inputs=2:channel_layout=5.1[ca]" in _fc(
        build_argv(ep))


def test_combine_rejects_trim(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    with pytest.raises(PlanError, match="trimming cannot be combined"):
        _plan([_combine("xfade", ["b"])], assets, monkeypatch, probes,
              trim=TrimSpec(start_seconds=0, duration_seconds=5))


def test_combine_rejects_watermark(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4"),
              "wm": Asset(path="logo.png", kind=AssetKind.IMAGE)}
    with pytest.raises(PlanError, match="watermark"):
        _plan([_combine("xfade", ["b"]),
               WatermarkOp(asset_id="wm", position="top_right")],
              assets, monkeypatch, probes)


def test_combine_rejects_audio_mode(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    with pytest.raises(PlanError, match="audio-only output"):
        _plan([_combine("xfade", ["b"])], assets, monkeypatch, probes,
              output=OutputSpec(container="mp3", audio_codec="mp3",
                                mode="audio"))


def test_combine_rejects_removed_audio_for_audio_combiner(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "t.wav": make_media(path="t.wav")}
    assets = {"main": Asset(path="a.mp4"),
              "t": Asset(path="t.wav", kind=AssetKind.AUDIO)}
    out = OutputSpec(container="mp4", video_codec="h264", audio_mode="remove")
    with pytest.raises(PlanError, match="cannot be removed"):
        _plan([_combine("amix", ["t"])], assets, monkeypatch, probes,
              output=out)


def test_combine_rejects_stream_input(monkeypatch):
    assets = {"main": Asset(path="a.mp4"),
              "cam": Asset(path="rtsp://cam/1", kind=AssetKind.VIDEO)}
    probes = {"a.mp4": make_media(path="a.mp4")}
    with pytest.raises(PlanError, match="stream"):
        _plan([_combine("xfade", ["cam"])], assets, monkeypatch, probes)


def test_combine_rejects_image_input(monkeypatch):
    assets = {"main": Asset(path="a.mp4"),
              "img": Asset(path="logo.png", kind=AssetKind.IMAGE)}
    probes = {"a.mp4": make_media(path="a.mp4")}
    with pytest.raises(PlanError, match="must be a media file"):
        _plan([_combine("xfade", ["img"])], assets, monkeypatch, probes)


def test_combine_missing_second_input_rejected():
    # the schema guarantees at least one extra input — a combiner with none
    # is rejected before planning
    with pytest.raises(ValidationError, match="at least 1 item"):
        _combine("xfade", [])


def test_single_stream_ops_chain_after_combiner(monkeypatch):
    probes = {"a.mp4": make_media(path="a.mp4"),
              "b.mp4": make_media(path="b.mp4")}
    assets = {"main": Asset(path="a.mp4"), "b": Asset(path="b.mp4")}
    ep = _plan([_combine("xfade", ["b"]),
                ResizeOp(width=1280, height=None)],
               assets, monkeypatch, probes)
    argv = build_argv(ep)
    fc = _fc(argv)
    # the resize runs on the combiner's [cv] pad, relabeled v1
    assert "[cv]scale=1280" in fc
    maps = _maps(argv)
    assert "[v1]" in maps and "[cv]" not in maps


# --------------------------------------------------------------------- NLU

def test_crossfade_videos():
    r = _parse("crossfade the clips", _assets(clip2="clip2.mp4"))
    assert r.status.value == "ready"
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert len(comb) == 1
    assert comb[0].filter == "xfade"
    assert comb[0].asset_ids == ["clip2"]
    assert comb[0].options == {}


def test_crossfade_audio():
    r = _parse("crossfade the audio", _assets(track="track.wav"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "acrossfade"
    assert comb[0].asset_ids == ["track"]


def test_crossfade_duration():
    r = _parse("crossfade the clips over 2 seconds",
               _assets(clip2="clip2.mp4"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "xfade"
    assert comb[0].options == {"duration": "2"}


def test_stack_side_by_side():
    r = _parse("stack them side by side", _assets(clip2="clip2.mp4"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "hstack"


def test_stack_vertical():
    r = _parse("stack the clips vertically", _assets(clip2="clip2.mp4"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "vstack"


def test_stack_direction_ambiguous_clarifies():
    r = _parse("stack the two clips", _assets(clip2="clip2.mp4"))
    assert r.status.value == "needs_clarification"
    assert "side by side" in r.clarifications[0].question


def test_concatenate_named():
    r = _parse("concatenate main.mp4 and clip2.mp4",
               _assets(clip2="clip2.mp4"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "concat"
    assert comb[0].asset_ids == ["clip2"]


def test_concat_without_audio():
    r = _parse("concatenate the clips without the audio",
               _assets(clip2="clip2.mp4"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].options == {"a": "0"}


def test_overlay_named_position():
    r = _parse("overlay clip2.mp4 on top right", _assets(clip2="clip2.mp4"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "overlay"
    assert comb[0].options == {"x": "W-w-16", "y": "16"}


def test_overlay_of_image_stays_watermark():
    assets = {"main": Asset(path="main.mp4", kind=AssetKind.VIDEO),
              "logo": Asset(path="logo.png", kind=AssetKind.IMAGE)}
    r = _parse("overlay logo.png on top right", assets)
    assert all(o.type != "combine" for o in r.plan.operations)
    assert any(o.type == "watermark" for o in r.plan.operations)


def test_mix_audio():
    r = _parse("mix the audio", _assets(track="track.wav"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "amix"


def test_merge_audio():
    r = _parse("merge the audio tracks", _assets(track="track.wav"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "amerge"


def test_join_with_layout():
    r = _parse("join the audio tracks to 5.1", _assets(track="track.wav"))
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert comb[0].filter == "join"
    assert comb[0].options == {"channel_layout": "5.1"}


def test_join_without_layout_clarifies():
    r = _parse("join the audio tracks", _assets(track="track.wav"))
    assert r.status.value == "needs_clarification"
    assert "layout" in r.clarifications[0].question


def test_no_second_input_clarifies():
    r = _parse("crossfade the clips", {})
    r = _parse("crossfade the clips", _assets())
    assert r.status.value == "needs_clarification"
    assert "combined with" in r.clarifications[0].question


def test_two_candidates_clarifies():
    r = _parse("crossfade the clips",
               _assets(clip2="clip2.mp4", clip3="clip3.mp4"))
    assert r.status.value == "needs_clarification"
    assert "which file" in r.clarifications[0].question


def test_crossfade_audio_file_unsupported():
    r = _parse("crossfade the clips", _assets(track="track.wav"))
    assert r.status.value == "unsupported"
    assert r.unsupported[0].fragment.startswith("combining with 'track.wav'")


def test_combine_audio_mode_unsupported():
    r = _parse("crossfade the audio and extract the audio as mp3",
               _assets(track="track.wav"))
    assert r.status.value == "unsupported"
    assert "multi-input" in r.unsupported[0].reason


def test_combine_conflicts_with_trim():
    r = _parse("crossfade the clips, but first trim the first 5 seconds",
               _assets(clip2="clip2.mp4"))
    assert r.status.value == "needs_clarification"
    assert "the trim" in r.clarifications[0].question


def test_combine_conflicts_with_watermark():
    assets = {"main": Asset(path="main.mp4", kind=AssetKind.VIDEO),
              "clip2": Asset(path="clip2.mp4", kind=AssetKind.VIDEO),
              "logo": Asset(path="logo.png", kind=AssetKind.IMAGE)}
    r = _parse("crossfade the clips and add a watermark logo.png on top right",
               assets)
    assert r.status.value == "needs_clarification"
    assert "the watermark" in r.clarifications[0].question


def test_combine_revision_replaces_previous():
    # the previous plan carried an xfade; the follow-up restacks instead
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[_combine("xfade", ["clip2"])])
    assets = _assets(clip2="clip2.mp4")
    r = ADAPTER.parse("instead, stack them side by side", assets, prev, None)
    assert r.status.value == "ready"
    comb = [o for o in r.plan.operations if o.type == "combine"]
    assert len(comb) == 1
    assert comb[0].filter == "hstack"


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


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> SimpleNamespace:
    d = tmp_path_factory.mktemp("fass_combiner_fixtures")

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
        portrait=clip("portrait.mp4", "720x1280"),         # 10s 720x1280
    )
    _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:"
                                "duration=5", "-c:a", "pcm_s16le",
             str(d / "tone.wav")])
    ns.tone = d / "tone.wav"
    return ns


def _run(gen, instruction, main, extra, out):
    r = gen(instruction, input_path=str(main), extra=extra,
            output_filename=str(out))
    assert r.status == "ready", \
        f"{r.status}: {r.errors} {r.clarifications} {r.unsupported}"
    proc = subprocess.run(r.arguments, shell=False, cwd=str(out.parent),
                          capture_output=True, text=True, timeout=300,
                          check=False)
    assert proc.returncode == 0, f"argv={r.arguments}\n{proc.stderr[-2000:]}"
    info = probe_media(str(out))
    assert info is not None, "output is not probeable"
    return r, info


@ pytestmark_media
def test_e2e_xfade_duration_math(gen, clips, tmp_path):
    out = tmp_path / "xfade.mp4"
    _, info = _run(gen, "crossfade a.mp4 and b.mp4", clips.a,
                   {"b": Asset(path=str(clips.b))}, out)
    # 10 + 8 - 1 (the default crossfade) = 17
    assert abs(info.duration - 17.0) <= 0.5
    assert info.width == 1920 and info.height == 1080
    assert info.has_audio


@ pytestmark_media
def test_e2e_xfade_explicit_duration(gen, clips, tmp_path):
    out = tmp_path / "xfade2.mp4"
    _, info = _run(gen, "crossfade a.mp4 and b.mp4 over 2 seconds", clips.a,
                   {"b": Asset(path=str(clips.b))}, out)
    assert abs(info.duration - 16.0) <= 0.5


@ pytestmark_media
def test_e2e_acrossfade(gen, clips, tmp_path):
    out = tmp_path / "acrossfade.mp4"
    r, info = _run(gen, "crossfade the audio", clips.a,
                   {"tone": Asset(path=str(clips.tone),
                                  kind=AssetKind.AUDIO)}, out)
    # 10 + 5 - 1 = 14
    assert abs(info.duration - 14.0) <= 0.5
    assert info.has_audio
    assert "[ca]" in [r.arguments[i + 1]
                      for i, v in enumerate(r.arguments) if v == "-map"]


@ pytestmark_media
def test_e2e_concat(gen, clips, tmp_path):
    out = tmp_path / "concat.mp4"
    _, info = _run(gen, "concatenate a.mp4 and b.mp4", clips.a,
                   {"b": Asset(path=str(clips.b))}, out)
    assert abs(info.duration - 18.0) <= 0.5
    assert info.has_audio


@ pytestmark_media
def test_e2e_concat_without_audio(gen, clips, tmp_path):
    out = tmp_path / "concat0.mp4"
    _, info = _run(gen, "concatenate a.mp4 and c.mp4 without the audio",
                   clips.a, {"c": Asset(path=str(clips.c))}, out)
    assert abs(info.duration - 16.0) <= 0.5
    assert not info.has_audio


@ pytestmark_media
def test_e2e_hstack_widths_add(gen, clips, tmp_path):
    out = tmp_path / "hstack.mp4"
    r, info = _run(gen, "stack a.mp4 and b.mp4 side by side", clips.a,
                   {"b": Asset(path=str(clips.b))}, out)
    # hstack needs a common height: the 1280x720 input is auto-scaled to
    # 1920x1080 first, so the widths add up as 1920 + 1920
    assert any("scaled to the main's height" in w for w in r.warnings)
    assert info.width == 1920 + 1920
    assert info.height == 1080


@ pytestmark_media
def test_e2e_vstack_scaled(gen, clips, tmp_path):
    out = tmp_path / "vstack.mp4"
    _, info = _run(gen, "stack a.mp4 and portrait.mp4 vertically", clips.a,
                   {"portrait": Asset(path=str(clips.portrait))}, out)
    # 720x1280 scaled to the main's width 1920 -> height 3412 (even)
    assert info.width == 1920
    assert abs(info.height - (1080 + 3412)) <= 2


@ pytestmark_media
def test_e2e_overlay_keeps_main_dimensions(gen, clips, tmp_path):
    out = tmp_path / "overlay.mp4"
    _, info = _run(gen, "overlay portrait.mp4 on top right", clips.a,
                   {"portrait": Asset(path=str(clips.portrait))}, out)
    assert info.width == 1920 and info.height == 1080
    assert info.has_audio


@ pytestmark_media
def test_e2e_amix(gen, clips, tmp_path):
    out = tmp_path / "amix.mp4"
    _, info = _run(gen, "mix the audio", clips.a,
                   {"tone": Asset(path=str(clips.tone),
                                  kind=AssetKind.AUDIO)}, out)
    assert info.has_audio
    # amix duration=longest -> the main's 10s
    assert abs(info.duration - 10.0) <= 0.5


@ pytestmark_media
def test_e2e_combiner_bare_names_resolved_in_input_dir(gen, clips, monkeypatch):
    # M10.6 container condition: the web UI binds inputs by bare name; the
    # planner must probe them in $FASS_INPUT_DIR, not relative to the cwd
    monkeypatch.setenv("FASS_INPUT_DIR", str(clips.a.parent))
    r = gen("crossfade a.mp4 and b.mp4", input_path="a.mp4",
            extra={"b": Asset(path="b.mp4")})
    assert r.status == "ready", \
        f"{r.status}: {r.errors} {r.clarifications} {r.unsupported}"
