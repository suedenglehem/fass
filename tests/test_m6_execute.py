"""M6 execution: argv validation against the builder's vocabulary,
volume path resolution, the FASS_ALLOW_EXECUTE gate, and (when ffmpeg is
on PATH) a real lavfi run with stop/log/list."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from fass import execute

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv(execute.INPUT_DIR_ENV, str(tmp_path / "in"))
    monkeypatch.setenv(execute.OUTPUT_DIR_ENV, str(tmp_path / "out"))
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(tmp_path / "runs"))
    for d in ("in", "out", "runs"):
        (tmp_path / d).mkdir(exist_ok=True)
    monkeypatch.delenv(execute.ALLOW_EXECUTE_ENV, raising=False)
    yield


def _valid():
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "clip.mp4",
            "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "out.mp4"]


# ------------------------------------------------------------ validation

def test_valid_argv_passes():
    assert execute.validate_argv(_valid()) == []


def test_unknown_flag_rejected():
    p = execute.validate_argv(
        ["ffmpeg", "-totally", "bogus", "-i", "a.mp4", "o.mp4"])
    assert any("unknown flag" in x for x in p)


def test_non_final_bare_token_rejected():
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "extra.mp4", "o.mp4"])
    assert any("bare token" in x for x in p)


def test_trailing_flag_without_value_rejected():
    p = execute.validate_argv(["ffmpeg", "-i", "a.mp4", "-crf"])
    assert any("no value" in x for x in p)


def test_unknown_codec_rejected():
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "not_a_codec", "o.mp4"])
    assert any("not a known encoder" in x for x in p)


def test_nvenc_coders_accepted():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "h264_nvenc", "-rc", "vbr",
         "-cq", "23", "o.mp4"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "hevc_nvenc", "o.mp4"]) == []


def test_bad_bitrate_rejected():
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-b:v", "8000x", "o.mp4"])
    assert p


def test_map_values_checked():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-map", "0:v", "o.mp4"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-map", "0:xyz", "o.mp4"])


def test_crf_range_checked():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-crf", "23", "o.mp4"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-crf", "70", "o.mp4"])


def test_unknown_format_rejected():
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-f", "not_a_format", "o.mp4"])
    assert any("not a known FFmpeg format" in x for x in p)


def test_non_ffmpeg_binary_rejected():
    assert execute.validate_argv(["rm", "-rf", "/"])


# ------------------------------------------- M7 format options (per registry kind)

def test_format_option_valid_flagset():
    # movflags is a flagset: '+'-joined members, optional +/- prefix
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-movflags", "+faststart", "o.mp4"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-movflags", "+faststart+empty_moov",
         "o.mp4"]) == []


def test_format_option_unknown_flagset_member_rejected():
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-movflags", "+not_a_flag", "o.mp4"])
    assert any("not_a_flag" in x for x in p)


def test_format_option_number_out_of_range_rejected():
    # hls_time range is [0.1, 3600] seconds
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-hls_time", "6", "o.m3u8"]) == []
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-hls_time", "99999", "o.m3u8"])
    assert any("above the maximum" in x for x in p)


def test_format_option_unknown_for_container_rejected():
    # hls_time is a known format-option key (builder vocabulary) but not a
    # valid option for the plan's mp4 output — the container context decides
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-hls_time", "6", "o.mp4"])
    assert any("not a valid option for mp4" in x for x in p)


def test_format_option_choice_value_checked():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-hls_segment_type", "fmp4", "o.m3u8"]) == []
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-hls_segment_type", "bogus", "o.m3u8"])
    assert any("hls_segment_type" in x for x in p)


def test_format_option_stream_destination_container():
    # http output derives its container from the URL path (.m3u8 -> hls),
    # so hls_* options validate against the hls group
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-hls_time", "6",
         "http://example.com/live.m3u8"]) == []


# ------------------------------------------- M13.1 DASH (registry 2.9.0)

def test_dash_muxer_accepted():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-f", "dash", "o.mpd"]) == []


def test_dash_format_options_accepted():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-seg_duration", "6", "o.mpd"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-dash_segment_type", "fmp4", "o.mpd"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-window_size", "10", "o.mpd"]) == []


def test_dash_format_option_unknown_for_mp4_rejected():
    # seg_duration is a known format-option key (builder vocabulary) but not
    # a valid option for the plan's mp4 output — the container context decides
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-seg_duration", "6", "o.mp4"])
    assert any("not a valid option for mp4" in x for x in p)


def test_dash_segment_type_choice_checked():
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-dash_segment_type", "bogus", "o.mpd"])
    assert any("dash_segment_type" in x for x in p)


# ------------------------------------------- M13.3 ABR ladder (execute)

# the planner's ladder shape (the probed form): all '-map 0:v:0 -map 0:a:0'
# pairs first, the shared bare encoders, then per-level options addressed
# by stream specifier (-b:v:K … -b:a:K), then '-var_stream_map', then the
# %v playlist name
def _ladder_argv(dest: str = "stream_%v.m3u8", fmt: str = "hls",
                 c_v: str = "libx264", c_a: str = "aac",
                 levels=((384, 216, 300, 56, "3.0"),
                         (640, 360, 700, 64, "3.1"),
                         (854, 480, 1500, 80, "3.2"))):
    a = ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", "in.mp4", "-f", fmt]
    for _ in levels:
        a += ["-map", "0:v:0", "-map", "0:a:0"]
    a += ["-c:v", c_v, "-c:a", c_a, "-pix_fmt", "yuv420p"]
    for i, (w, h, vkb, akb, lvl) in enumerate(levels):
        a += [f"-b:v:{i}", f"{vkb}k", f"-minrate:v:{i}", f"{vkb}k",
              f"-maxrate:v:{i}", f"{vkb}k", f"-bufsize:v:{i}", f"{2 * vkb}k",
              f"-profile:v:{i}", "main", f"-level:v:{i}", lvl,
              f"-s:v:{i}", f"{w}x{h}", f"-b:a:{i}", f"{akb}k"]
    a += ["-var_stream_map",
          " ".join(f"v:{k},a:{k}" for k in range(len(levels)))]
    a.append(dest)
    return a


def test_ladder_argv_passes_validation():
    # the full ladder: per-level stream-specifier options (-b:v:N …) and
    # -var_stream_map in the hls output context
    assert execute.validate_argv(_ladder_argv()) == []


def test_ladder_dash_context_accepted():
    # var_stream_map is a valid option for the dash container too (the
    # DASH ladder, M13.4)
    assert execute.validate_argv(
        _ladder_argv(dest="ladder.mpd", fmt="dash")) == []


def test_var_stream_map_unknown_for_mp4_rejected():
    p = execute.validate_argv(_ladder_argv(dest="o.mp4", fmt="mp4"))
    assert any("not a valid option for mp4" in x for x in p)


def test_minrate_value_checked():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-b:v", "300k",
         "-minrate", "300k", "o.mp4"]) == []
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-minrate", "300000",
         "o.mp4"]) == []
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-minrate", "300x",
         "o.mp4"])
    assert any("bitrate" in x for x in p)


def test_level_value_checked():
    for v in ("3.0", "31", "4"):
        assert execute.validate_argv(
            ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-level", v,
             "o.mp4"]) == []
    for v in ("3.0x", "x3", "1000"):
        p = execute.validate_argv(
            ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-level", v,
             "o.mp4"])
        assert any("encoder level" in x for x in p)


def test_size_value_checked():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-s", "384x216",
         "o.mp4"]) == []
    for v in ("384", "384x", "384x216x", "axb"):
        p = execute.validate_argv(
            ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-s", v, "o.mp4"])
        assert any("size" in x for x in p)


def test_stream_specifier_value_checked():
    # a stream-indexed per-stream option takes the base flag's value rules
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-s:v:1", "640x360",
         "o.mp4"]) == []
    for flag, val in (("-s:v:0", "384"), ("-b:v:1", "fast"),
                      ("-level:v:2", "3.0x"), ("-b:a:0", "56x")):
        p = execute.validate_argv(
            ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", flag, val, "o.mp4"])
        assert p, (flag, val)


def test_ladder_from_argv_recognizes_ladder():
    ld = execute._ladder_from_argv(_ladder_argv())
    assert ld["container"] == "hls"
    assert ld["video_codec"] == "h264"
    assert ld["audio_codec"] == "aac"
    assert [ (lv["width"], lv["height"], lv["video_kbps"], lv["audio_kbps"],
              lv["profile"], lv["level"], lv["bandwidth"], lv["playlist"])
             for lv in ld["levels"] ] == [
        (384, 216, 300, 56, "main", "3.0", 356000, "stream_0.m3u8"),
        (640, 360, 700, 64, "main", "3.1", 764000, "stream_1.m3u8"),
        (854, 480, 1500, 80, "main", "3.2", 1580000, "stream_2.m3u8"),
    ]


def test_ladder_from_argv_plain_run_is_none():
    assert execute._ladder_from_argv(_valid()) is None


def test_ladder_from_argv_dash_dest_needs_no_master():
    # a DASH ladder is self-describing (.mpd) — no master synthesis
    assert execute._ladder_from_argv(
        _ladder_argv(dest="ladder.mpd", fmt="dash")) is None


def test_ladder_from_argv_no_percent_v_is_none():
    assert execute._ladder_from_argv(_ladder_argv(dest="live.m3u8")) is None


def test_ladder_from_argv_resolved_path_uses_basename():
    # start_run resolves the bare name to the output volume — the master
    # must still reference the playlists by their bare names
    argv = _ladder_argv(dest="/output/stream_%v.m3u8")
    ld = execute._ladder_from_argv(argv)
    assert [lv["playlist"] for lv in ld["levels"]] == [
        "stream_0.m3u8", "stream_1.m3u8", "stream_2.m3u8"]


def test_ladder_from_argv_malformed_group_is_none():
    argv = _ladder_argv()
    # drop one level's -s:v:0 value → unparseable geometry, no synthesis
    i = argv.index("-s:v:0")
    del argv[i:i + 2]
    assert execute._ladder_from_argv(argv) is None


def test_master_from_ladder_content():
    ld = execute._ladder_from_argv(_ladder_argv())
    text = execute._master_from_ladder(ld)
    assert text == (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=356000,AVERAGE-BANDWIDTH=356000,'
        'RESOLUTION=384x216,CODECS="avc1.4d001e,mp4a.40.2"\n'
        "stream_0.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=764000,AVERAGE-BANDWIDTH=764000,'
        'RESOLUTION=640x360,CODECS="avc1.4d001f,mp4a.40.2"\n'
        "stream_1.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=1580000,AVERAGE-BANDWIDTH=1580000,'
        'RESOLUTION=854x480,CODECS="avc1.4d0020,mp4a.40.2"\n'
        "stream_2.m3u8\n"
    )


def test_master_codecs_omitted_for_h265():
    # HEVC has no common fourcc here — the audio part stays, the video
    # part is dropped
    argv = _ladder_argv(c_v="libx265", levels=((384, 216, 300, 56, "3.0"),))
    ld = execute._ladder_from_argv(argv)
    text = execute._master_from_ladder(ld)
    assert 'CODECS="mp4a.40.2"' in text
    assert "avc1" not in text


def test_master_codecs_omitted_when_no_parts():
    # no computable video fourcc (h265) and no AAC audio → no CODECS at all
    argv = _ladder_argv(c_v="libx265", c_a="libopus",
                        levels=((384, 216, 300, 56, "3.0"),))
    ld = execute._ladder_from_argv(argv)
    text = execute._master_from_ladder(ld)
    assert "CODECS=" not in text


def test_synthesize_master_writes_file(tmp_path):
    ld = execute._ladder_from_argv(_ladder_argv())
    note = execute._synthesize_master(tmp_path, ld)
    assert note == "master.m3u8 synthesized from the run's declared levels (3 variants)"
    assert (tmp_path / "master.m3u8").read_text(encoding="utf-8") == \
        execute._master_from_ladder(ld)


# ------------------------------------------------- M7 filtergraph validation

def test_vf_valid_graph_accepted_bad_name_rejected():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-vf", "scale=-2:480,hue=s=0", "o.mp4"]) == []
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-vf", "movie=x,scale=-2:480", "o.mp4"])
    assert any("not in the supported filter set" in x for x in p)


def test_af_valid_graph_accepted_empty_rejected():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-af", "volume=2", "o.mp4"]) == []
    # an empty filter value never reaches _check_value as a value token
    # (validate_argv rejects the bare "" positional), so check the value
    # validator directly — the builder must never be able to emit it
    assert execute._check_value("-af", "", 4)
    assert execute._check_value("-af", "volume=2", 4) == []


def test_filter_complex_validated():
    assert execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-filter_complex",
         "[0:v]scale=-2:480[v]", "-map", "[v]", "o.mp4"]) == []
    p = execute.validate_argv(
        ["ffmpeg", "-i", "a.mp4", "-filter_complex", "[0:v]bogus[v]", "o.mp4"])
    assert any("bogus" in x for x in p)


# ------------------------------------------------------------ paths

def test_resolve_paths_uses_volume_mounts():
    in_dir = Path(os.environ[execute.INPUT_DIR_ENV])
    out_dir = Path(os.environ[execute.OUTPUT_DIR_ENV])
    (in_dir / "clip.mp4").write_bytes(b"0")
    argv, notes = execute.resolve_paths(_valid())
    assert str(in_dir / "clip.mp4") in argv
    assert argv[-1] == str(out_dir / "out.mp4")
    assert any("/input" in n for n in notes)


def test_resolve_paths_leaves_streams_and_abs_paths():
    argv, notes = execute.resolve_paths(
        ["ffmpeg", "-i", "udp://0.0.0.0:5000", "-c:v", "libx264",
         "/abs/out.mp4"])
    assert "udp://0.0.0.0:5000" in argv
    assert argv[-1] == "/abs/out.mp4"
    assert notes == []


def test_resolve_paths_input_folder_destination():
    # '/input/<name>' means "write into the deployment's input folder" so
    # the next run can pick the file up by bare name (chained processings)
    in_dir = Path(os.environ[execute.INPUT_DIR_ENV])
    argv, notes = execute.resolve_paths(
        ["ffmpeg", "-i", "clip.mp4", "-c:v", "libx264", "/input/step1.mp4"])
    assert argv[-1] == str(in_dir / "step1.mp4")
    assert any("input folder" in n for n in notes)


def test_resolve_paths_input_folder_without_input_dir(monkeypatch):
    # no input folder configured: the literal path passes through untouched
    monkeypatch.setattr(execute, "_input_dir", lambda: "")
    argv, notes = execute.resolve_paths(
        ["ffmpeg", "-i", "clip.mp4", "-c:v", "libx264", "/input/step1.mp4"])
    assert argv[-1] == "/input/step1.mp4"
    assert notes == []


def test_resolve_paths_input_folder_noop_in_container(monkeypatch):
    # inside the container the input folder IS /input: no rewrite, no note
    monkeypatch.setenv(execute.INPUT_DIR_ENV, "/input")
    argv, notes = execute.resolve_paths(
        ["ffmpeg", "-i", "clip.mp4", "-c:v", "libx264", "/input/step1.mp4"])
    assert argv[-1] == "/input/step1.mp4"
    assert notes == []


def test_run_output_file_guard(tmp_path, monkeypatch):
    import json
    in_dir = Path(os.environ[execute.INPUT_DIR_ENV])
    out_dir = Path(os.environ[execute.OUTPUT_DIR_ENV])
    (in_dir / "step1.mp4").write_bytes(b"0")
    (out_dir / "step2.mp4").write_bytes(b"1")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"2")
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(tmp_path / "runs"))
    (tmp_path / "runs").mkdir(exist_ok=True)
    cases = [
        ("run-in", str(in_dir / "step1.mp4"), True),
        ("run-out", str(out_dir / "step2.mp4"), True),
        ("run-outside", str(outside), False),
        ("run-missing", str(in_dir / "nope.mp4"), False),
        ("run-stream", "udp://0.0.0.0:9000", False),
    ]
    for run_id, output, expect in cases:
        rec = {"id": run_id, "argv": ["ffmpeg"], "command": "ffmpeg",
               "started": "2026-01-01T00:00:00", "status": "success",
               "exit_code": 0, "output": output, "notes": []}
        (tmp_path / "runs" / f"{run_id}.json").write_text(json.dumps(rec))
        got = execute.run_output_file(run_id)
        assert (got is not None) is expect, run_id
        if expect:
            assert got == Path(output)
    assert execute.run_output_file("no-such-run") is None


def test_resolve_input_name_matches_run_time_rule():
    # The pipeline's inspection shares this exact rule, so a bare name that
    # exists in the input volume is probed and not reported "not found".
    in_dir = Path(os.environ[execute.INPUT_DIR_ENV])
    (in_dir / "titan.mp4").write_bytes(b"0")
    assert execute.resolve_input_name("titan.mp4") == str(in_dir / "titan.mp4")
    assert execute.resolve_input_name("missing.mp4") == "missing.mp4"
    assert execute.resolve_input_name("/abs/titan.mp4") == "/abs/titan.mp4"
    assert execute.resolve_input_name("udp://0.0.0.0:5000") == "udp://0.0.0.0:5000"
    assert execute.resolve_input_name("lavfi:testsrc=duration=1") == "lavfi:testsrc=duration=1"


def test_output_file_guard():
    out_dir = Path(os.environ[execute.OUTPUT_DIR_ENV])
    (out_dir / "x.mp4").write_bytes(b"0")
    assert execute.output_file("x.mp4") == out_dir / "x.mp4"
    assert execute.output_file("missing.mp4") is None
    assert execute.output_file("../config.json") is None
    assert execute.output_file("a/b.mp4") is None


# ------------------------------------------------------------ gate

def test_execution_disabled_raises_permission():
    with pytest.raises(PermissionError):
        execute.start_run(_valid())


def test_enabled_but_invalid_argv_raises_value(monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    with pytest.raises(ValueError):
        execute.start_run(["rm", "-rf", "/"])


# ------------------------------------------------------------ real runs

def _lavfi_argv(duration: str, size: str = "320x240") -> list[str]:
    out = Path(os.environ[execute.OUTPUT_DIR_ENV])
    return ["ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size={size}:rate=10",
            "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
            "-f", "mp4", str(out / f"demo{duration}.mp4")]


def _wait(run_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = execute.get_run(run_id)
        if st["status"] != "running":
            return st
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} still running after {timeout}s")


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_real_lavfi_run(tmp_path, monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    out = Path(os.environ[execute.OUTPUT_DIR_ENV])
    run = execute.start_run(_lavfi_argv("1"))
    assert run.status == "running"
    st = _wait(run.id)
    assert st["status"] == "success", st
    assert st["exit_code"] == 0
    assert (out / "demo1.mp4").exists()
    assert any(r["id"] == run.id for r in execute.list_runs())
    assert isinstance(execute.log_tail(run.id), str)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_chained_processings_share_input_folder(monkeypatch):
    # The chaining flow: the first processing writes into the input folder
    # ('/input/step1.mp4'), the second picks the result up by bare name.
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    in_dir = Path(os.environ[execute.INPUT_DIR_ENV])
    out_dir = Path(os.environ[execute.OUTPUT_DIR_ENV])
    run1 = execute.start_run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
         "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
         "-f", "mp4", "/input/step1.mp4"])
    st1 = _wait(run1.id)
    assert st1["status"] == "success", st1
    assert (in_dir / "step1.mp4").is_file()
    run2 = execute.start_run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-i", "step1.mp4", "-vf", "scale=-2:120",
         "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
         "-f", "mp4", "step2.mp4"])
    st2 = _wait(run2.id)
    assert st2["status"] == "success", st2
    assert (out_dir / "step2.mp4").is_file()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_stop_run_kills_process(monkeypatch):
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    # 720p x 300s of content (3000 frames) is a real multi-second encode —
    # the 320x240 testsrc finishes faster than we can react to it
    run = execute.start_run(_lavfi_argv("300", size="1280x720"))
    time.sleep(0.7)
    assert execute.stop_run(run.id) is True
    st = _wait(run.id, timeout=15.0)
    assert st["status"] == "stopped"
    assert st["exit_code"] is None
