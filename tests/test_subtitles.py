"""M5 subtitles: registry helpers, escaping, schema, planner argv shape,
rule-based NLU behavior, explain lines, and real-media outcomes.

Design contract (locked): burn (libass `subtitles=`/`ass=`, forces a
re-encode, no extra input — the filter opens the file itself) is the
default; soft (selectable track, mp4/mov → mov_text, mkv → srt) is
compatible with stream-copy remuxing and is requested explicitly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fass.builder import build_argv
from fass.planner import PlanError, plan_execution
from fass.registry import (
    soft_subtitle_codec,
    subtitle_burn_expr,
    subtitle_burn_filter,
    subtitle_input_format,
)
from fass.probe.ffprobe import (
    ffmpeg_available,
    ffprobe_available,
    probe_media,
)
from fass.schema import (
    Asset,
    AssetKind,
    AudioReplaceOp,
    EditPlan,
    OutputSpec,
    ResizeOp,
    SpeedOp,
    SubtitleOp,
    TrimSpec,
    WatermarkOp,
    guess_kind,
)
from tests.conftest import make_media

REPO = Path(__file__).resolve().parent.parent
TITAN = REPO / "media" / "titan.mp4"

# ---------------------------------------------------------------- helpers


def sub_plan(operations, trim=None, output=None, media=None, out=None,
             input_path="clip.mp4", sub_path="subs.srt",
             watermark_path=None, audio_path=None):
    output = output or OutputSpec(container="mp4", video_codec="h264")
    edit = EditPlan(output=output, trim=trim, operations=operations or [])
    assets = {"main": Asset(path=input_path, kind=AssetKind.VIDEO)}
    if sub_path:
        assets["subs"] = Asset(path=sub_path, kind=AssetKind.SUBTITLES)
    if watermark_path:
        assets["wm"] = Asset(path=watermark_path, kind=AssetKind.IMAGE)
    if audio_path:
        assets["aud"] = Asset(path=audio_path, kind=AssetKind.AUDIO)
    return plan_execution(edit, assets, media, out)


def sub_argv(**kw):
    return build_argv(sub_plan(**kw))


def maps_of(argv):
    return [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]


SUBS_ASSET = Asset(path="subs.srt", kind=AssetKind.SUBTITLES)


def ops_by_type(plan: dict | None) -> dict[str, list]:
    out: dict[str, list] = {}
    if not plan:
        return out
    for o in plan.get("operations", []):
        out.setdefault(o["type"], []).append(o)
    return out


# -------------------------------------------------------- registry helpers

@pytest.mark.parametrize("name", ["a.srt", "a.ass", "a.ssa", "a.vtt"])
def test_subtitle_input_format_supported(name):
    assert subtitle_input_format(name) == Path(name).suffix.lstrip(".")


@pytest.mark.parametrize("name", ["a.mp4", "a.txt", "a.srt.bak", "a"])
def test_subtitle_input_format_unsupported(name):
    assert subtitle_input_format(name) is None


def test_subtitle_input_format_ignores_query_and_fragment():
    assert subtitle_input_format("https://cdn/x.srt?sig=1#t=5") == "srt"


@pytest.mark.parametrize(
    ("path", "filter"),
    [("a.srt", "subtitles"), ("a.vtt", "subtitles"),
     ("a.ass", "ass"), ("a.ssa", "ass"), ("a.mp4", None)],
)
def test_subtitle_burn_filter(path, filter):
    assert subtitle_burn_filter(path) == filter


@pytest.mark.parametrize(
    ("container", "codec"),
    [("mp4", "mov_text"), ("mov", "mov_text"), ("mkv", "srt"),
     ("webm", None), ("mpegts", None), ("flv", None),
     ("rtsp", None), ("srt", None)],
)
def test_soft_subtitle_codec_matrix(container, codec):
    assert soft_subtitle_codec(container) == codec


# ------------------------------------------------------------- burn escape

def test_burn_expr_plain_path():
    assert subtitle_burn_expr("/tmp/subs.srt") == "subtitles=/tmp/subs.srt"


def test_burn_expr_ass_extension_uses_ass_filter():
    assert subtitle_burn_expr("notes.ass").startswith("ass=")


def _unescape_layer(s: str) -> str:
    """One FFmpeg filter-option unescape layer: backslash drops, keeping
    the character it protected."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


@pytest.mark.parametrize("char", ["'", '"', " ", "=", ",", ";", "\\"])
def test_burn_expr_escapes_special_characters(char):
    path = f"/tmp/s{char}x.srt"
    expr = subtitle_burn_expr(path)
    # the filename crosses two unescape layers (graph parser, option
    # parser): unescaping the expr twice must recover the exact path
    body = expr.split("=", 1)[1]
    assert _unescape_layer(_unescape_layer(body)) == path, (path, body)


def test_burn_expr_apostrophe_double_escape():
    # verified round-trip on ffmpeg 6.1.1: the apostrophe must survive two
    # unescape layers, so the raw argv carries THREE backslashes before it
    expr = subtitle_burn_expr("/tmp/it's.srt")
    expected = "subtitles=/tmp/it" + chr(92) * 3 + "'s.srt"
    assert expr == expected


# ------------------------------------------------------------------ schema

def test_subtitle_op_defaults_to_burn():
    op = SubtitleOp(asset_id="subs")
    assert op.type == "subtitles" and op.mode == "burn"


def test_subtitle_op_rejects_bad_mode():
    with pytest.raises(Exception):
        SubtitleOp(asset_id="subs", mode="hard")


def test_subtitle_op_rejects_extra_fields():
    with pytest.raises(Exception):
        SubtitleOp(asset_id="subs", mode="burn", fontsize=24)


def test_at_most_one_subtitle_op():
    with pytest.raises(Exception):
        EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                 operations=[SubtitleOp(asset_id="a"), SubtitleOp(asset_id="b")])


@pytest.mark.parametrize("name", ["a.srt", "a.ass", "a.ssa", "a.vtt"])
def test_guess_kind_subtitle_files(name):
    assert guess_kind(name) == AssetKind.SUBTITLES


# -------------------------------------------------------------- planner: burn

def test_burn_forces_reencode_even_when_copy_would_fit():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")], media=media)
    assert ep.mode == "reencode"
    assert "libx264" in ep.output_opts


def test_burn_goes_into_vf_without_extra_input():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")])
    assert ep.mode == "reencode"
    assert ep.vf == "subtitles=subs.srt"
    assert len(ep.inputs) == 1  # the filter opens the file itself
    argv = build_argv(ep)
    assert "-c:s" not in argv
    assert not any(m.endswith(":s:0") for m in maps_of(argv))


def test_burn_default_note_explains_hard_coding():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")])
    assert any("burned into the video" in d for d in ep.defaults)


def test_burn_with_resize_runs_after_scale():
    ep = sub_plan(operations=[ResizeOp(height=720), SubtitleOp(asset_id="subs")])
    vf = ep.vf
    assert vf.index("scale=") < vf.index("subtitles=")


def test_burn_with_watermark_uses_complex_graph_and_final_label():
    ep = sub_plan(operations=[WatermarkOp(asset_id="wm"),
                              SubtitleOp(asset_id="subs")],
                  watermark_path="logo.png")
    argv = build_argv(ep)
    assert argv.count("-i") == 2  # main + watermark only
    fc = argv[argv.index("-filter_complex") + 1]
    assert "overlay=" in fc and "subtitles=" in fc
    assert fc.index("overlay") < fc.index("subtitles=")  # burn on final dims
    assert any(m.startswith("[v") for m in maps_of(argv))


def test_burn_with_audio_replace_keeps_audio_last():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs"),
                              AudioReplaceOp(asset_id="aud")],
                  audio_path="song.mp3")
    argv = build_argv(ep)
    inputs = [argv[i + 1] for i, v in enumerate(argv) if v == "-i"]
    assert inputs == ["clip.mp4", "song.mp3"]  # no subtitle input for burn


def test_burn_vtt_uses_subtitles_filter():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")],
                  sub_path="a.vtt")
    assert ep.vf == "subtitles=a.vtt"


def test_burn_ass_uses_ass_filter():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")],
                  sub_path="a.ass")
    assert ep.vf == "ass=a.ass"


# -------------------------------------------------------------- planner: soft

def test_soft_alone_is_a_copy_remux():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs", mode="soft")],
                  media=media)
    assert ep.mode == "copy"
    assert len(ep.inputs) == 2
    argv = build_argv(ep)
    assert "libx264" not in argv
    assert "-c:s" in argv
    assert argv[argv.index("-c:s") + 1] == "mov_text"
    # -c:s must come AFTER -c copy to override it for the subtitle stream
    assert argv.index("-c:s") > argv.index("copy")
    assert "1:s:0" in maps_of(argv)
    assert "-shortest" not in argv
    assert any("mov_text" in d and "no re-encode" in d for d in ep.defaults)


def test_soft_with_trim_reencodes_but_keeps_track():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs", mode="soft")],
                  trim=TrimSpec(start_seconds=0, duration_seconds=10))
    assert ep.mode == "reencode"
    argv = build_argv(ep)
    assert "libx264" in argv
    assert "1:s:0" in maps_of(argv)
    assert argv[argv.index("-c:s") + 1] == "mov_text"
    assert "-shortest" not in argv  # subtitles may end before the video


def test_soft_with_speed_reencodes_and_tracks():
    ep = sub_plan(operations=[SpeedOp(factor=2),
                              SubtitleOp(asset_id="subs", mode="soft")])
    argv = build_argv(ep)
    assert "setpts=PTS/2" in argv
    assert "1:s:0" in maps_of(argv)
    assert "-c:s" in argv


def test_soft_with_watermark_complex_graph_skips_filter_segment():
    ep = sub_plan(operations=[WatermarkOp(asset_id="wm"),
                              SubtitleOp(asset_id="subs", mode="soft")],
                  watermark_path="logo.png")
    argv = build_argv(ep)
    assert argv.count("-i") == 3  # main, watermark, subtitles
    fc = argv[argv.index("-filter_complex") + 1]
    assert "overlay=" in fc
    assert "subtitles=" not in fc and "ass=" not in fc  # track, not filter
    assert any(m.startswith("[v") for m in maps_of(argv))
    assert "2:s:0" in maps_of(argv)
    assert argv[argv.index("-c:s") + 1] == "mov_text"


def test_soft_with_audio_replace_input_order():
    # main=0, subtitles=1, replacement audio last=2 (audio stays last)
    ep = sub_plan(operations=[AudioReplaceOp(asset_id="aud"),
                              SubtitleOp(asset_id="subs", mode="soft")],
                  audio_path="song.mp3")
    argv = build_argv(ep)
    inputs = [argv[i + 1] for i, v in enumerate(argv) if v == "-i"]
    assert inputs == ["clip.mp4", "subs.srt", "song.mp3"]
    maps = maps_of(argv)
    assert "1:s:0" in maps and "2:a:0" in maps
    assert "-shortest" in argv  # from the audio replacement, not the subs


def test_soft_mkv_uses_srt_codec():
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs", mode="soft")],
                  output=OutputSpec(container="mkv", video_codec="h264"))
    argv = build_argv(ep)
    assert argv[argv.index("-c:s") + 1] == "srt"


# ------------------------------------------------------------ planner: errors

def test_soft_on_unsupported_container_rejected():
    with pytest.raises(PlanError, match="no codec in WEBM"):
        sub_plan(operations=[SubtitleOp(asset_id="subs", mode="soft")],
                 output=OutputSpec(container="webm", video_codec="vp9"))


def test_burn_on_unsupported_container_is_fine():
    # burning is container-agnostic: libass writes pixels, not a track
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")],
                  output=OutputSpec(container="webm", video_codec="vp9"))
    assert ep.mode == "reencode"


def test_subtitle_path_with_colon_rejected():
    with pytest.raises(PlanError, match="contains ':'"):
        sub_plan(operations=[SubtitleOp(asset_id="subs")],
                 sub_path="/tmp/wei:rd.srt")


def test_subtitle_unsupported_extension_rejected():
    with pytest.raises(PlanError, match="not a supported subtitle format"):
        sub_plan(operations=[SubtitleOp(asset_id="subs")],
                 sub_path="subs.txt")


def test_subtitle_wrong_kind_rejected():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[SubtitleOp(asset_id="subs")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO),
              "subs": Asset(path="subs.srt", kind=AssetKind.VIDEO)}
    with pytest.raises(PlanError, match="not a subtitle file"):
        plan_execution(edit, assets, None, None)


def test_subtitle_missing_asset_rejected():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[SubtitleOp(asset_id="ghost")])
    assets = {"main": Asset(path="clip.mp4", kind=AssetKind.VIDEO)}
    with pytest.raises(PlanError, match="not in the asset map"):
        plan_execution(edit, assets, None, None)


def test_output_collision_with_subtitles_file_rejected():
    with pytest.raises(PlanError, match="resolves to the subtitles file"):
        sub_plan(operations=[SubtitleOp(asset_id="subs")],
                 sub_path="subs.srt", out="subs.srt")


def test_subtitle_stream_carried_over_note():
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True,
                       subtitle_streams=2)
    ep = sub_plan(operations=[], media=media)
    assert any("2 subtitle stream" in w and "not carried over" in w
               for w in ep.warnings)
    # adding subtitles silences the note
    ep = sub_plan(operations=[SubtitleOp(asset_id="subs")], media=media)
    assert not any("not carried over" in w for w in ep.warnings)


def test_device_output_rejects_subtitles():
    with pytest.raises(PlanError, match="carries audio only"):
        sub_plan(operations=[SubtitleOp(asset_id="subs")], out="alsa:hw:0")


# ------------------------------------------------------------ NLU: rules

def test_subtitles_default_burn_with_note(gen):
    r = gen("add subtitles from subs.srt", "clip.mp4", extra={"subs": SUBS_ASSET})
    assert r.status == "ready"
    subs = ops_by_type(r.plan)["subtitles"]
    assert len(subs) == 1
    assert subs[0]["asset_id"] == "subs" and subs[0]["mode"] == "burn"
    assert any("burned into the video (default" in n for n in r.defaults)


def test_subtitles_by_id_reference(gen):
    r = gen("add subtitles from the subs file", "clip.mp4",
            extra={"subs": SUBS_ASSET})
    assert r.status == "ready"
    assert ops_by_type(r.plan)["subtitles"][0]["asset_id"] == "subs"


@pytest.mark.parametrize("phrase", [
    "add selectable subtitles from subs.srt",
    "add soft subtitles from subs.srt",
    "embed subtitles from subs.srt",
    "add captions as a selectable track from subs.srt",
    "add the subtitles from subs.srt and make the subtitles soft",  # post-noun
])
def test_soft_wording_selects_soft(gen, phrase):
    r = gen(phrase, "clip.mp4", extra={"subs": SUBS_ASSET})
    assert r.status == "ready"
    assert ops_by_type(r.plan)["subtitles"][0]["mode"] == "soft"


@pytest.mark.parametrize("phrase", [
    "burn in subtitles from subs.srt",
    "hardcode subtitles from subs.srt",
])
def test_burn_wording_is_explicit_no_default_note(gen, phrase):
    r = gen(phrase, "clip.mp4", extra={"subs": SUBS_ASSET})
    assert r.status == "ready"
    assert ops_by_type(r.plan)["subtitles"][0]["mode"] == "burn"
    assert not any("default" in n and "subtitle" in n.lower()
                   for n in r.defaults)


def test_subtitles_without_asset_clarifies(gen):
    r = gen("add subtitles from the file", "clip.mp4")
    assert r.status == "needs_clarification"
    assert any("which file should provide the subtitles" in c["question"]
               for c in r.clarifications)


def test_remove_subtitles_fresh_plan_is_no_op(gen):
    # a fresh plan never carries input subtitle streams, so "remove" has
    # nothing to do — it must not invent a subtitle operation
    r = gen("remove the subtitles", "clip.mp4", extra={"subs": SUBS_ASSET})
    assert "subtitles" not in ops_by_type(r.plan)


def test_extract_subtitles_is_a_dump(gen):
    # Extracting the input's subtitles is a subtitle dump (srt_file, subtitles
    # mode) — a stream copy, no asset needed.
    r = gen("extract the subtitles to a file", "clip.mp4",
            extra={"subs": SUBS_ASSET})
    assert r.status == "ready"
    out = r.plan["output"]
    assert out["mode"] == "subtitles"
    assert out["container"] == "srt_file"


def test_soften_audio_is_not_a_subtitle_request(gen):
    # 'soften' alone must not arm the soft-subtitle detector: it is an audio
    # volume filter, not a subtitle request.
    r = gen("soften the audio", "clip.mp4", extra={"subs": SUBS_ASSET})
    assert r.status == "ready"
    f = [o for o in r.plan["operations"] if o["type"] == "filter"]
    assert any("volume" in o["expr"] for o in f), "expected a volume filter"
    assert all(o["type"] != "subtitles" for o in r.plan["operations"])


def test_subtitles_revision_remove(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[SubtitleOp(asset_id="subs", mode="burn")])
    r = gen("remove the subtitles", "clip.mp4", extra={"subs": SUBS_ASSET},
            previous_plan=prev)
    assert r.status == "ready"
    assert "subtitles" not in ops_by_type(r.plan)


def test_subtitles_revision_mode_switch(gen):
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[SubtitleOp(asset_id="subs", mode="burn")])
    r = gen("make the subtitles soft", "clip.mp4", extra={"subs": SUBS_ASSET},
            previous_plan=prev)
    assert r.status == "ready"
    subs = ops_by_type(r.plan)["subtitles"]
    assert len(subs) == 1 and subs[0]["mode"] == "soft"
    assert subs[0]["asset_id"] == "subs"  # same file, new mode


def test_subtitles_revision_keeps_previous_file_single_asset(gen):
    # one subtitle asset in the map: the fallback binds it, the file is
    # preserved, and the mode follows the new wording
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[SubtitleOp(asset_id="subs")])
    r = gen("the subtitles should be burned in", "clip.mp4",
            extra={"subs": SUBS_ASSET}, previous_plan=prev)
    assert r.status == "ready"
    subs = ops_by_type(r.plan)["subtitles"]
    assert subs[0]["asset_id"] == "subs" and subs[0]["mode"] == "burn"


def test_subtitles_revision_keeps_previous_file_among_several(gen):
    # several subtitle assets, none named in the follow-up: instead of
    # asking, the revision keeps the previous plan's file
    prev = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[SubtitleOp(asset_id="alpha")])
    r = gen("the subtitles should be soft", "clip.mp4",
            extra={"alpha": Asset(path="alpha.srt", kind=AssetKind.SUBTITLES),
                   "beta": Asset(path="beta.srt", kind=AssetKind.SUBTITLES)},
            previous_plan=prev)
    assert r.status == "ready"
    subs = ops_by_type(r.plan)["subtitles"]
    assert subs[0]["asset_id"] == "alpha" and subs[0]["mode"] == "soft"
    assert any("kept the previous file" in n for n in r.defaults)


# ------------------------------------------------------------- explain lines

def test_explain_burn_line(gen):
    r = gen("add subtitles from subs.srt", "clip.mp4", extra={"subs": SUBS_ASSET})
    assert any(s.startswith("Subtitles: burned into the video from 'subs'")
               and "not selectable" in s for s in r.summary)


def test_explain_soft_line(gen):
    r = gen("add selectable subtitles from subs.srt", "clip.mp4",
            extra={"subs": SUBS_ASSET})
    assert any(s.startswith("Subtitles: added as a selectable track from 'subs'")
               and "mov_text" in s for s in r.summary)


# --------------------------------------------------------- pipeline: preflight

def test_preflight_soft_adds_subtitle_encoder_to_checks(gen):
    if not ffmpeg_available():
        pytest.skip("needs ffmpeg")
    media = make_media(video_codec="h264", audio_codec="aac", has_audio=True)
    r = gen("add selectable subtitles from subs.srt", "clip.mp4",
            extra={"subs": SUBS_ASSET}, media=media, preflight_check=True)
    assert r.status == "ready"
    checked = [c["name"] for c in r.checks]
    assert any("mov_text" in n for n in checked), checked


# ---------------------------------------------------------- real media (M5)


def _write_srt(tmp_path: Path) -> Path:
    srt = tmp_path / "smoke.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nfass smoke line\n\n"
        "2\n00:00:04,000 --> 00:00:07,000\nsecond line\n",
        encoding="utf-8",
    )
    return srt


def _run_titan(gen, instruction: str, out: Path, srt: Path):
    if not (ffmpeg_available() and ffprobe_available()):
        pytest.skip("ffmpeg/ffprobe not available")
    r = gen(instruction, input_path=str(TITAN), output_filename=str(out),
            extra={"subs": Asset(path=str(srt), kind=AssetKind.SUBTITLES)})
    assert r.status == "ready", (
        f"expected ready, got {r.status}: {r.clarifications} {r.unsupported}"
    )
    proc = subprocess.run(
        r.arguments, shell=False, cwd=str(out.parent),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, f"argv={r.arguments}\nstderr={proc.stderr}"
    return r


@pytest.mark.skipif(not TITAN.exists(), reason="media/titan.mp4 not available")
def test_titan_burn_subtitles(gen, tmp_path):
    srt = _write_srt(tmp_path)
    out = tmp_path / "titan_burn.mp4"
    r = _run_titan(gen, "burn in subtitles from smoke.srt", out, srt)
    # burn forces a re-encode even though the codecs would allow a remux
    assert "libx264" in r.arguments
    assert "-c:s" not in r.arguments  # no selectable track
    info = probe_media(str(out))
    assert info is not None
    assert info.subtitle_streams == 0  # hard-coded, not a stream
    assert abs(info.duration - 20.08) <= 0.5


@pytest.mark.skipif(not TITAN.exists(), reason="media/titan.mp4 not available")
def test_titan_soft_subtitles_stream_copy(gen, tmp_path):
    srt = _write_srt(tmp_path)
    out = tmp_path / "titan_soft.mp4"
    r = _run_titan(gen, "add selectable subtitles from smoke.srt", out, srt)
    # soft + matching codecs = a remux with an added track, no re-encode
    assert "copy" in r.arguments and "libx264" not in r.arguments
    info = probe_media(str(out))
    assert info is not None
    assert info.subtitle_streams == 1
    assert abs(info.duration - 20.08) <= 0.5


@pytest.mark.skipif(not TITAN.exists(), reason="media/titan.mp4 not available")
def test_titan_soft_subtitles_with_trim(gen, tmp_path):
    srt = _write_srt(tmp_path)
    out = tmp_path / "titan_soft_trim.mp4"
    r = _run_titan(gen, "cut the first 10 seconds and add selectable "
                        "subtitles from smoke.srt", out, srt)
    assert "libx264" in r.arguments  # trim forces the re-encode
    info = probe_media(str(out))
    assert info is not None
    assert info.subtitle_streams == 1
    assert abs(info.duration - 10.0) <= 0.25
