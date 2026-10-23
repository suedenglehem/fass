"""Real-media outcome tests against the committed fixture media/titan.mp4
(1280x720 h264 + AAC, ~20.08 s, 25 fps). Skipped when the file or ffmpeg is
absent so the suite stays portable; outputs go to tmp dirs, the fixture is
never written to.

Complements tests/test_media.py (synthetic lavfi fixtures) with a real-world
file: the planner decides copy vs re-encode from genuine ffprobe metadata of
an off-the-shelf encode, and the emitted argv must work on it unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fass.probe.ffprobe import ffmpeg_available, ffprobe_available, probe_media

REPO = Path(__file__).resolve().parent.parent
TITAN = REPO / "media" / "titan.mp4"

pytestmark = pytest.mark.skipif(
    not (ffmpeg_available() and ffprobe_available() and TITAN.exists()),
    reason="ffmpeg/ffprobe or media/titan.mp4 not available",
)


def _run(gen, instruction: str, out: Path):
    """Generate a plan for the real fixture, run the argv, probe the output."""
    r = gen(instruction, input_path=str(TITAN), output_filename=str(out))
    assert r.status == "ready", (
        f"expected ready, got {r.status}: {r.clarifications} {r.unsupported}"
    )
    proc = subprocess.run(
        r.arguments, shell=False, cwd=str(out.parent),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, f"argv={r.arguments}\nstderr={proc.stderr}"
    info = probe_media(str(out))
    assert info is not None, f"output {out} is not probeable"
    return r, info


def test_titan_convert_mov_stream_copy(gen, tmp_path):
    r, info = _run(gen, "convert to mov", tmp_path / "titan.mov")
    assert info.video_codec == "h264"
    assert abs(info.duration - 20.079) <= 0.5
    # h264 is valid in mov: the planner must remux, not re-encode
    assert "-c" in r.arguments and "copy" in r.arguments, r.arguments


def test_titan_trim_first_10s(gen, tmp_path):
    _, info = _run(gen, "trim the first 10 seconds", tmp_path / "titan_trim.mp4")
    assert info.duration is not None
    assert abs(info.duration - 10.0) <= 0.25


def test_titan_resize_480p(gen, tmp_path):
    _, info = _run(gen, "resize to 480p", tmp_path / "titan_480.mp4")
    assert info.height == 480
    assert info.width % 2 == 0


def test_titan_remove_audio(gen, tmp_path):
    _, info = _run(gen, "remove the audio", tmp_path / "titan_silent.mp4")
    assert not info.has_audio
    assert info.audio_streams == 0


def test_titan_cbr_10mbps_mpegts(gen, tmp_path):
    # The user's real request, with a range that fits this 20 s fixture:
    # trim + CBR 10 Mbps target + mpeg-ts container. The CBR target must
    # actually land near 10 Mbps (10 s * 10 Mbps ~= 12.5 MB of video).
    out = tmp_path / "titan.ts"
    r, info = _run(gen, "cut from 5 to 15 secs and transcode to cbr 10Mbps mpeg ts", out)
    assert info.video_codec == "h264"
    assert abs(info.duration - 10.0) <= 0.25
    # CBR-style 10 Mbps target: the 10 s output must be close to ~12.5 MB,
    # not the multi-MB a quality preset (CRF 23) would produce for this clip.
    size_mb = out.stat().st_size / (1024 * 1024)
    assert 8.0 <= size_mb <= 20.0, f"10 Mbps CBR 10s output should be ~12.5 MB, got {size_mb:.1f} MB"
