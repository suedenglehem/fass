"""Media-outcome tests (plan0.md §6): generated commands must produce the
intended *edits*, verified by probing real output media — not by comparing
command strings.

Fixtures are synthetic lavfi clips generated once per session into a tmp
dir; the whole module skips when ffmpeg/ffprobe are absent. Outputs always
go to a fresh per-test tmp dir, fixtures are never overwritten, and the
builder's argv is executed exactly as emitted (no ``-y`` is added).
"""

from __future__ import annotations

import array
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fass.probe.ffprobe import ffmpeg_available, ffprobe_available, probe_media

pytestmark = pytest.mark.skipif(
    not (ffmpeg_available() and ffprobe_available()),
    reason="ffmpeg/ffprobe not installed — media-outcome tests need real media",
)

FPS = 15


# ------------------------------------------------------------- fixtures

def _ffmpeg(args: list[str]) -> None:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"fixture generation failed: {args}\n{p.stderr}")


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> SimpleNamespace:
    """Small synthetic clips (≤15 s, 15 fps, veryfast) generated once."""
    d = tmp_path_factory.mktemp("fass_media_fixtures")

    def clip(name: str, size: str, *, audio: bool = True, duration: int = 15):
        out = d / name
        args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={FPS}:duration={duration}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}"]
            args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)]
        else:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                     "-an", str(out)]
        _ffmpeg(args)
        return out

    base = clip("base.mp4", "1920x1080")
    portrait = clip("portrait.mp4", "720x1280")
    silent = clip("silent.mp4", "1280x720", audio=False, duration=10)
    odd = clip("odd.mp4", "853x480")
    hostile = clip("my clip (v2) $weird.mp4", "1920x1080")

    # 128x128 fully transparent PNG with an opaque white 64x64 square centered.
    # drawbox/color@alpha do not write the alpha plane; pad with a transparent
    # border around a white core does, deterministically.
    logo = d / "logo.png"
    _ffmpeg([
        "-f", "lavfi", "-i", "color=c=white:s=64x64:d=1",
        "-vf", "format=rgba,pad=128:128:32:32:color=#00000000",
        "-frames:v", "1", "-pix_fmt", "rgba", str(logo),
    ])

    # 3 s of 880 Hz (PCM WAV) — replacement audio for the audio_replace
    # outcome test; distinguishable from the 440 Hz tone in the clips.
    sine880 = d / "sine880.wav"
    _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:duration=3",
             "-c:a", "pcm_s16le", str(sine880)])

    return SimpleNamespace(base=base, portrait=portrait, silent=silent,
                           odd=odd, hostile=hostile, logo=logo,
                           sine880=sine880)


# ------------------------------------------------------------- helpers

def _run_plan(gen, instruction: str, input_path: Path, out_path: Path,
              watermark: Path | None = None, audio: Path | None = None):
    """Generate the plan for a real input file, run argv shell=False, probe output."""
    r = gen(instruction, input_path=str(input_path),
            watermark=str(watermark) if watermark else None,
            audio=str(audio) if audio else None,
            output_filename=str(out_path))
    assert r.status == "ready", f"expected ready plan, got {r.status}"
    proc = subprocess.run(
        r.arguments, shell=False, cwd=str(out_path.parent),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, f"argv={r.arguments}\nstderr={proc.stderr}"
    info = probe_media(str(out_path))
    assert info is not None, f"output {out_path} is not probeable"
    return r, info


def _crop_luma(src: Path, crop: str) -> float:
    """Mean luma of the first frame's cropped region, via ffmpeg → PPM (no image libs)."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-frames:v", "1", "-vf", f"crop={crop}", "-pix_fmt", "rgb24",
         "-f", "image2pipe", "-c:v", "ppm", "-"],
        capture_output=True, timeout=120, check=False,
    )
    assert p.returncode == 0, p.stderr.decode()
    head = p.stdout.split(b"\n", 3)
    assert head[0] == b"P6", head[0]
    w, h = (int(x) for x in head[1].split())
    raw = head[3]
    assert len(raw) == w * h * 3, (len(raw), w, h)
    total = sum(raw[i] * 299 + raw[i + 1] * 587 + raw[i + 2] * 114
               for i in range(0, len(raw), 3))
    return total / 1000 / (w * h)


def _mean_chroma(src: Path) -> tuple[float, float]:
    """Mean U and V of the first frame, via ffmpeg → raw yuv420p (no image libs)."""
    info = probe_media(str(src))
    assert info is not None and info.width and info.height
    w, h = info.width, info.height
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        capture_output=True, timeout=120, check=False,
    )
    assert p.returncode == 0, p.stderr.decode()
    y_size = w * h
    c_size = (w // 2) * (h // 2)
    data = p.stdout
    assert len(data) >= y_size + 2 * c_size, (len(data), w, h)
    u = data[y_size:y_size + c_size]
    v = data[y_size + c_size:y_size + 2 * c_size]
    return sum(u) / c_size, sum(v) / c_size


def _audio_dominant_freq(src: Path, seconds: float = 2.0) -> float:
    """Dominant frequency of the output audio, via zero-crossing rate at
    8 kHz mono (clean sine tones — no audio libs needed)."""
    rate = 8000
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-t", str(seconds), "-ac", "1", "-ar", str(rate),
         "-c:a", "pcm_s16le", "-f", "s16le", "-"],
        capture_output=True, timeout=120, check=False,
    )
    assert p.returncode == 0, p.stderr.decode()
    a = array.array("h")
    a.frombytes(p.stdout[:len(p.stdout) // 2 * 2])
    zc = sum(1 for i in range(1, len(a)) if (a[i - 1] < 0) != (a[i] < 0))
    return zc / 2 / (len(a) / rate)


# ------------------------------------------------------------- outcomes

def test_resize_720p_output_height_and_even_width(gen, media, tmp_path):
    out = tmp_path / "resized.mp4"
    _, info = _run_plan(gen, "resize to 720p", media.base, out)
    assert info.height == 720
    assert info.width % 2 == 0


def test_trim_first_5s_duration(gen, media, tmp_path):
    out = tmp_path / "trimmed.mp4"
    _, info = _run_plan(gen, "trim the first 5 seconds", media.base, out)
    assert info.duration is not None
    assert abs(info.duration - 5.0) <= 0.25


def test_grayscale_chroma_neutral(gen, media, tmp_path):
    out = tmp_path / "bw.mp4"
    _run_plan(gen, "make it black and white", media.base, out)
    u, v = _mean_chroma(out)
    assert abs(u - 128) <= 5, f"mean U={u}"
    assert abs(v - 128) <= 5, f"mean V={v}"


def test_remove_audio_has_no_audio_stream(gen, media, tmp_path):
    out = tmp_path / "mute.mp4"
    _, info = _run_plan(gen, "remove the audio", media.base, out)
    assert not info.has_audio
    assert info.audio_streams == 0


def test_convert_to_mov_stream_copy(gen, media, tmp_path):
    src = probe_media(str(media.base))
    assert src is not None and src.duration is not None
    out = tmp_path / "remuxed.mov"
    r, info = _run_plan(gen, "convert to mov", media.base, out)
    assert info.video_codec == "h264"
    assert abs(info.duration - src.duration) <= 0.5
    # the planner chose remux, not re-encode: outcome above proves the
    # original streams survived the container change intact
    assert "-c" in r.arguments and "copy" in r.arguments, r.arguments


def test_watermark_brightens_top_left_corner(gen, media, tmp_path):
    out = tmp_path / "wm.mp4"
    _run_plan(gen, "add my logo to the top-left corner", media.base, out,
              watermark=media.logo)
    # logo overlays at (16,16); its white 64x64 square lands at (48,48)–(112,112),
    # fully inside a 128x128 crop at (32,32). Bottom-right crop is far from it.
    tl, br = "128:128:32:32", "128:128:1760:920"
    tl_with, tl_without = _crop_luma(out, tl), _crop_luma(media.base, tl)
    br_with, br_without = _crop_luma(out, br), _crop_luma(media.base, br)
    assert tl_with - tl_without > 10, (tl_with, tl_without)
    assert abs(br_with - br_without) < 8, (br_with, br_without)


def test_hostile_filename_runs_end_to_end(gen, media, tmp_path):
    out = tmp_path / "hostile_out.mp4"
    _, info = _run_plan(gen, "resize to 720p", media.hostile, out)
    assert info.height == 720
    assert info.width % 2 == 0


# ------------------------------------------- difficult fixtures (plan0 §6)

def test_resize_portrait_keeps_even_width(gen, media, tmp_path):
    out = tmp_path / "portrait_720.mp4"
    _, info = _run_plan(gen, "resize to 720p", media.portrait, out)
    assert info.height == 720
    assert info.width % 2 == 0


def test_odd_dimensions_resize_even_width(gen, media, tmp_path):
    out = tmp_path / "odd_720.mp4"
    _, info = _run_plan(gen, "resize to 720p", media.odd, out)
    assert info.height == 720
    assert info.width % 2 == 0


def test_silent_clip_trim_stays_silent(gen, media, tmp_path):
    out = tmp_path / "silent_trimmed.mp4"
    _, info = _run_plan(gen, "trim the first 5 seconds", media.silent, out)
    assert abs(info.duration - 5.0) <= 0.25
    assert not info.has_audio


def test_audio_replaced_with_other_file(gen, media, tmp_path):
    out = tmp_path / "replaced.mp4"
    r, info = _run_plan(gen, "replace the audio with sine880.wav",
                        media.base, out, audio=media.sine880)
    # the replacement stream is the output's only audio stream
    assert info.has_audio and info.audio_streams == 1
    assert info.video_streams == 1
    # -shortest: the 15 s video ends at the 3 s replacement audio,
    # not the other way around
    assert info.duration is not None
    assert abs(info.duration - 3.0) <= 0.75, info.duration
    # the audio content is the 880 Hz replacement, not the original 440 Hz
    # fixture tone (zero-crossing rate reports the tone's own frequency)
    f = _audio_dominant_freq(out)
    assert 750 < f < 1000, f
    # and the argv really mapped the second input's audio
    maps = [r.arguments[i + 1] for i, v in enumerate(r.arguments) if v == "-map"]
    assert "1:a:0" in maps and "-shortest" in r.arguments
