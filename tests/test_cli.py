"""CLI behavior: exit codes, JSON mode, subcommands (run as a subprocess)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*args, cwd=ROOT):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # force deterministic rules adapter
    return subprocess.run(
        [sys.executable, "-m", "fass", *args],
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=120,
    )


def test_no_args_shows_help_and_examples():
    r = run_cli()
    assert r.returncode == 0
    assert "Examples" in r.stdout
    assert "resize to 720p" in r.stdout


def test_version():
    r = run_cli("--version")
    assert r.returncode == 0
    assert "fass" in r.stdout
    # commit-based versioning: vVERSION_ID.Commit_ID, where Commit_ID is
    # the short sha of the code commit the version was cut from
    assert re.fullmatch(r"fass v\d+\.\d+\.[0-9a-f]{7}", r.stdout.strip())


def test_ready_exit_zero_and_json():
    r = run_cli('resize to 720p', "clip.mp4", "--adapter", "rules", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready"
    assert data["command"].startswith("ffmpeg ")
    assert data["arguments"][0] == "ffmpeg"


def test_clarification_exit_code():
    r = run_cli("make it smaller", "clip.mp4", "--adapter", "rules", "--json")
    assert r.returncode == 3
    data = json.loads(r.stdout)
    assert data["status"] == "needs_clarification"
    assert data["clarifications"]


def test_unsupported_exit_code():
    # M10.5: 'stabilize' is supported (two-pass vidstab) — a sepia request
    # still exercises the unsupported path
    r = run_cli("make it look sepia", "clip.mp4", "--adapter", "rules", "--json")
    assert r.returncode == 4
    data = json.loads(r.stdout)
    assert data["status"] == "unsupported"


def test_missing_input_is_error():
    r = run_cli("resize to 720p", "--adapter", "rules")
    assert r.returncode == 2
    assert "input video is required" in r.stderr


def test_examples_subcommand():
    r = run_cli("examples")
    assert r.returncode == 0
    assert "Supported" in r.stdout or "resize" in r.stdout


def test_feedback_writes_jsonl(tmp_path):
    out = tmp_path / "fb.jsonl"
    env = dict(os.environ)
    env["FASS_FEEDBACK_FILE"] = str(out)
    r = subprocess.run(
        [sys.executable, "-m", "fass", "feedback", "--useful",
         "--category", "builder_bug", "--note", "it worked"],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    line = json.loads(out.read_text().strip().splitlines()[-1])
    assert line["useful"] is True
    assert line["category"] == "builder_bug"
    assert "instruction" not in line  # no consent -> instruction not stored


def test_feedback_instruction_only_with_consent(tmp_path):
    out = tmp_path / "fb.jsonl"
    env = dict(os.environ)
    env["FASS_FEEDBACK_FILE"] = str(out)
    subprocess.run(
        [sys.executable, "-m", "fass", "feedback", "--incorrect",
         "--instruction", "resize to 720p", "--consent"],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=60,
    )
    line = json.loads(out.read_text().strip().splitlines()[-1])
    assert line["instruction"] == "resize to 720p"


# ------------------------------------------------------------- stream I/O (M3b)

def test_stream_output_destination_cli():
    r = run_cli("cut the first 5 seconds and send it to udp://127.0.0.1:5004",
                "clip.mp4", "--adapter", "rules", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready"
    assert data["arguments"][-1] == "udp://127.0.0.1:5004"
    f = data["arguments"].index("-f")
    assert data["arguments"][f + 1] == "mpegts"


def test_stream_output_flag_fixes_defaulted_container():
    # -o is a stream URL: the user named no format, so the destination's
    # scheme fixes the container.
    r = run_cli("resize to 720p", "clip.mp4", "--adapter", "rules", "--json",
                "-o", "rtmp://host:1935/live")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready", data
    assert data["plan"]["output_destination"] == "rtmp://host:1935/live"
    assert data["plan"]["output"]["container"] == "flv"
    assert data["arguments"][-1] == "rtmp://host:1935/live"


def test_stream_input_url_cli():
    r = run_cli("resize to 720p", "rtmp://host:1935/live",
                "--adapter", "rules", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready"
    assert "rtmp://host:1935/live" in data["arguments"]
    assert not any("not found on this machine" in w for w in data["warnings"])


def test_asset_kind_prefix_cli():
    # --asset id=kind:path pins the kind for extensionless stream URLs
    r = run_cli("replace the audio with the music stream", "clip.mp4",
                "--adapter", "rules", "--json",
                "--asset", "radio=audio:http://radio.example.com/stream")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready", data
    ops = data["plan"]["operations"]
    assert len(ops) == 1 and ops[0]["type"] == "audio_replace"
    assert ops[0]["asset_id"] == "radio"


def test_case_file_cli(tmp_path):
    # a test case described entirely by a JSON file
    case = {
        "instruction": "resize to 720p",
        "assets": {"main": "clip.mp4", "watermark": "image:logo.png"},
        "output": "small.mp4",
        "adapter": "rules",
    }
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(case), encoding="utf-8")
    r = run_cli("--case", str(case_file))
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["status"] == "ready", data
    assert data["arguments"][-1] == "small.mp4"
    # bare asset names resolve against the case file's directory
    assert os.path.join(str(case_file.parent), "clip.mp4") in data["arguments"]


def test_case_file_rejects_conflicting_flags(tmp_path):
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(
        {"instruction": "resize to 720p", "assets": {"main": "clip.mp4"}}),
        encoding="utf-8")
    r = run_cli("--case", str(case_file), "--asset", "in1=extra.mp4")
    assert r.returncode == 2
    assert "--asset" in r.stderr


def test_case_file_requires_main(tmp_path):
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(
        {"instruction": "resize to 720p", "assets": {"in1": "clip.mp4"}}),
        encoding="utf-8")
    r = run_cli("--case", str(case_file))
    assert r.returncode == 2
    assert "main" in r.stderr


def test_case_file_bad_json(tmp_path):
    case_file = tmp_path / "case.json"
    case_file.write_text("{ not json", encoding="utf-8")
    r = run_cli("--case", str(case_file))
    assert r.returncode == 2
