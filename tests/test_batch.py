"""Batch mode (M12): --batch over a directory or an explicit file list.

Covers the contract: one instruction applied to every file, sequential and in
sorted filename order, a failing file reported and skipped, a per-file table,
exit 0 only when every file succeeded, top-level-only directory scan limited to
the registry's video extensions — and the single-file CLI path unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fass.batch import collect_files, format_report, run_batch, video_input_extensions
from fass.probe.ffprobe import ffmpeg_available, probe_media
from fass.schema import Asset, guess_kind

ROOT = Path(__file__).resolve().parent.parent

NO_LLM = dict(os.environ)
NO_LLM.pop("ANTHROPIC_API_KEY", None)


def run_cli(*args, cwd=ROOT):
    """`python -m fass …` from the repo root (the suite's normal CLI harness)."""
    return subprocess.run(
        [sys.executable, "-m", "fass", *args],
        capture_output=True, text=True, cwd=str(cwd), env=NO_LLM, timeout=180,
    )


def run_cli_in(cwd: Path, *args):
    """Same, run in a foreign cwd (batch outputs land in the cwd) — fass is
    imported from the repo root via PYTHONPATH, as the package always is."""
    env = dict(NO_LLM)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "fass", *args],
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=300,
    )


def make_clip(path: Path, size: str = "320x240", duration: int = 2) -> Path:
    """A tiny synthetic clip (lavfi testsrc2, no audio) — the batch E2E media."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15:duration={duration}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-an", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


def build_assets(path: str) -> dict:
    return {"main": Asset(path=path, kind=guess_kind(path))}


# ------------------------------------------------------------- target collection

def test_directory_target_is_top_level_only(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "a.mp4").write_bytes(b"x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.mp4").write_bytes(b"x")

    files = collect_files([str(tmp_path)])

    assert [os.path.basename(f) for f in files] == ["a.mp4", "b.mp4"]  # sorted, no recursion


def test_directory_target_uses_the_registry_extension_list(tmp_path):
    for name in ("v.mp4", "w.mkv", "x.mov", "y.ts"):
        (tmp_path / name).write_bytes(b"x")
    for name in ("notes.txt", "pic.png", "song.mp3", "subs.srt"):
        (tmp_path / name).write_bytes(b"x")

    files = [os.path.basename(f) for f in collect_files([str(tmp_path)])]

    assert files == ["v.mp4", "w.mkv", "x.mov", "y.ts"]
    exts = video_input_extensions()
    assert {".mp4", ".mkv", ".mov", ".ts"} <= exts
    assert not {".txt", ".png", ".mp3", ".srt"} & exts


def test_extension_list_is_derived_from_the_registry():
    """Every extension names a registry container with a video codec."""
    from fass.registry import REGISTRY, container_from_extension

    exts = video_input_extensions()
    assert exts  # never an empty set — the registry is the source
    for ext in exts:
        container = container_from_extension(ext.lstrip("."))
        assert container is not None, ext
        assert REGISTRY["matrix"][container]["video"], ext
        assert REGISTRY["containers"][container].get("file") is not False, ext
    # protocol endpoints are not files: they never appear
    assert ".rtsp" not in exts and ".srt" not in exts


def test_explicit_list_is_taken_exactly_as_given(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    missing = str(tmp_path / "gone.mp4")

    files = collect_files([str(tmp_path / "a.mp4"), str(tmp_path / "notes.txt"),
                           missing, str(tmp_path / "a.mp4")])

    # exactly those files (extension filter is for directories only),
    # de-duplicated, sorted
    assert files == sorted([str(tmp_path / "a.mp4"), str(tmp_path / "notes.txt"), missing])


# ------------------------------------------------------------------ run_batch

def test_run_batch_continues_past_a_missing_file(tmp_path):
    good = make_clip(tmp_path / "good.mp4")
    report = run_batch("resize to 720p",
                       [str(good), str(tmp_path / "gone.mp4")],
                       build_assets, adapter_name="rules")

    assert [r.status for r in report.results] == ["ok", "missing"]
    assert report.results[1].exit_code == 1
    assert report.exit_code == 1  # one failure: the run is not green
    assert report.succeeded == 1


def test_run_batch_all_green(tmp_path):
    make_clip(tmp_path / "a.mp4")
    make_clip(tmp_path / "b.mp4")
    files = sorted(str(p) for p in tmp_path.glob("*.mp4"))

    report = run_batch("resize to 720p", files, build_assets,
                       output_dir=str(tmp_path / "out"), adapter_name="rules")

    assert report.exit_code == 0
    assert [r.status for r in report.results] == ["ok", "ok"]
    assert [r.output for r in report.results] == [
        str(tmp_path / "out" / "a.mp4"), str(tmp_path / "out" / "b.mp4")]


def test_run_batch_stream_target_is_not_a_missing_file():
    """A stream URL is not a file: its absence from disk is not a failure."""
    report = run_batch("resize to 720p", ["rtmp://host:1935/live"], build_assets,
                       adapter_name="rules")

    r = report.results[0]
    assert r.status == "ok" and r.exit_code == 0, (r.status, r.error)
    assert report.exit_code == 0


def test_run_batch_reports_a_rejected_file_and_keeps_going(tmp_path):
    good = make_clip(tmp_path / "a.mp4")
    report = run_batch("make it look sepia", [str(good)], build_assets,
                       adapter_name="rules")

    r = report.results[0]
    assert r.status == "unsupported" and r.exit_code == 4
    assert "sepia" in r.error
    assert report.exit_code == 1


def test_run_batch_never_raises_on_a_broken_file(tmp_path):
    """A file the pipeline cannot read is a row, not a crash: the loop ends."""
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    report = run_batch("resize to 720p", [str(junk)], build_assets,
                       output_dir=str(tmp_path / "out"),
                       adapter_name="rules", execute=True)

    r = report.results[0]
    assert r.status in ("exec_failed", "error")
    assert r.exit_code != 0
    assert report.exit_code == 1


def test_report_table_shows_every_file(tmp_path):
    good = make_clip(tmp_path / "a.mp4")
    report = run_batch("resize to 720p",
                       [str(good), str(tmp_path / "gone.mp4")],
                       build_assets, adapter_name="rules")
    table = format_report(report)

    assert "FILE" in table and "STATUS" in table and "EXIT" in table
    assert "a.mp4" in table and "gone.mp4" in table
    assert "input file not found" in table


# ------------------------------------------------------------------- CLI wiring

def test_cli_batch_directory_all_succeed(tmp_path):
    make_clip(tmp_path / "a.mp4")
    make_clip(tmp_path / "b.mp4")

    r = run_cli("resize to 720p", "--batch", str(tmp_path),
                "--adapter", "rules", "--batch-out", str(tmp_path / "out"))

    assert r.returncode == 0, r.stderr
    assert "a.mp4" in r.stdout and "b.mp4" in r.stdout
    assert "2 of 2 file(s) succeeded" in r.stdout


def test_cli_batch_one_failing_file_exit_one(tmp_path):
    make_clip(tmp_path / "a.mp4")
    junk = tmp_path / "b.mp4"
    junk.write_bytes(b"not a video")

    r = run_cli("resize to 720p", "--batch", str(tmp_path / "a.mp4"),
                str(tmp_path / "b.mp4"), "--adapter", "rules",
                "--batch-out", str(tmp_path / "out"), "--execute")

    assert r.returncode == 1, r.stdout
    assert "a.mp4" in r.stdout and "b.mp4" in r.stdout  # both rows are shown
    assert "ok" in r.stdout and "exec_failed" in r.stdout
    assert "1 of 2 file(s) succeeded" in r.stdout


def test_cli_batch_explicit_file_list(tmp_path):
    make_clip(tmp_path / "a.mp4")
    make_clip(tmp_path / "b.mp4")

    r = run_cli("resize to 720p", "--batch", str(tmp_path / "b.mp4"),
                str(tmp_path / "a.mp4"), "--adapter", "rules")

    assert r.returncode == 0, r.stderr
    rows = r.stdout.splitlines()  # the list was given b, a — the table is sorted
    ia = next(i for i, ln in enumerate(rows) if "/a.mp4  ok" in ln)
    ib = next(i for i, ln in enumerate(rows) if "/b.mp4  ok" in ln)
    assert ia < ib


def test_cli_batch_instruction_last_form(tmp_path):
    """`fass --batch DIR "instruction"` — the flag form the shell reads first."""
    make_clip(tmp_path / "a.mp4")

    r = run_cli("--batch", str(tmp_path), "resize to 720p", "--adapter", "rules")

    assert r.returncode == 0, r.stderr
    assert "1 of 1 file(s) succeeded" in r.stdout


def test_cli_batch_json_report(tmp_path):
    make_clip(tmp_path / "a.mp4")

    r = run_cli("resize to 720p", "--batch", str(tmp_path),
                "--adapter", "rules", "--json")

    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ok" and data["exit_code"] == 0
    assert data["total"] == 1 and data["succeeded"] == 1
    assert data["results"][0]["filename"].endswith("a.mp4")
    assert data["results"][0]["output"]


def test_cli_batch_rejects_single_file_flags(tmp_path):
    make_clip(tmp_path / "a.mp4")
    for flag, value in (("-o", "one.mp4"), ("--metadata", "meta.json")):
        r = run_cli("resize to 720p", "--batch", str(tmp_path),
                    "--adapter", "rules", flag, value)
        assert r.returncode == 2, (flag, r.stdout)
        assert "batch mode" in r.stderr

    prev = tmp_path / "prev.json"
    prev.write_text("{}")
    r = run_cli("resize to 720p", "--batch", str(tmp_path),
                "--adapter", "rules", "--previous", str(prev))
    assert r.returncode == 2 and "batch mode" in r.stderr


def test_cli_batch_empty_directory_is_an_error(tmp_path):
    (tmp_path / "notes.txt").write_text("no media here")

    r = run_cli("resize to 720p", "--batch", str(tmp_path), "--adapter", "rules")

    assert r.returncode == 2
    assert "no input files" in r.stderr


def test_cli_batch_validate_only_runs_nothing(tmp_path):
    """The CLI's default is validate-only; batch mode respects it (no output)."""
    src = make_clip(tmp_path / "a.mp4")
    out_dir = tmp_path / "out"

    r = run_cli("resize to 720p", "--batch", str(src), "--adapter", "rules",
                "--batch-out", str(out_dir))

    assert r.returncode == 0, r.stderr
    assert not out_dir.exists() or not list(out_dir.iterdir())
    assert src.read_bytes()  # the input was never touched


# ------------------------------------------------- single-file path regression

def test_single_file_cli_path_is_unchanged():
    """The exact assertions tests/test_cli.py makes on the single-file path."""
    r = run_cli("resize to 720p", "clip.mp4", "--adapter", "rules", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready"
    assert data["command"].startswith("ffmpeg ")
    assert data["arguments"][0] == "ffmpeg"

    assert run_cli("make it smaller", "clip.mp4", "--adapter", "rules").returncode == 3
    assert run_cli("make it look sepia", "clip.mp4", "--adapter", "rules").returncode == 4
    assert run_cli("resize to 720p", "--adapter", "rules").returncode == 2
    assert run_cli("--version").returncode == 0
    assert run_cli().returncode == 0


def test_single_file_execute_is_unchanged(tmp_path):
    """`fass <file> "instruction" --execute` still produces its output."""
    if not ffmpeg_available():
        pytest.skip("ffmpeg not available — execution test needs it")
    src = make_clip(tmp_path / "in.mp4", size="640x480")

    r = run_cli_in(tmp_path, "resize to 720p", "in.mp4",
                   "--adapter", "rules", "--execute")

    assert r.returncode == 0, r.stderr
    media = probe_media(str(tmp_path / "in.mp4"))
    assert media is not None and media.height == 480  # input untouched


# ------------------------------------------------------------------- E2E media

@pytest.mark.skipif(not ffmpeg_available(),
                    reason="ffmpeg not available — batch E2E needs real media")
def test_cli_batch_execute_produces_playable_outputs(tmp_path):
    make_clip(tmp_path / "a.mp4", size="640x480")
    make_clip(tmp_path / "b.mp4", size="640x480")
    out_dir = tmp_path / "out"

    r = run_cli_in(tmp_path, "resize to 720p", "--batch", str(tmp_path),
                   "--adapter", "rules", "--batch-out", str(out_dir),
                   "--execute")

    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 of 2 file(s) succeeded" in r.stdout
    for name in ("a.mp4", "b.mp4"):
        p = out_dir / name
        assert p.exists() and p.stat().st_size > 0
        media = probe_media(str(p))
        assert media is not None, name  # ffprobe reads it: playable
        assert media.video_codec == "h264"
        assert media.height == 720  # the instruction was applied
