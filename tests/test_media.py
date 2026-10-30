"""Media-outcome tests: generated commands must produce the
intended *edits*, verified by probing real output media — not by comparing
command strings.

Fixtures are synthetic lavfi clips generated once per session into a tmp
dir; the whole module skips when ffmpeg/ffprobe are absent. Outputs always
go to a fresh per-test tmp dir, fixtures are never overwritten, and the
builder's argv is executed exactly as emitted (no ``-y`` is added).
"""

from __future__ import annotations

import array
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fass.builder import build_argv
from fass.planner import plan_execution
from fass.probe.ffprobe import ffmpeg_available, ffprobe_available, probe_media
from fass.schema import (
    Asset,
    AssetKind,
    AudioResampleOp,
    CropOp,
    EditPlan,
    OutputSpec,
    RotateOp,
    SpeedOp,
    TrimSpec,
)

REPO = Path(__file__).resolve().parent.parent
TITAN = REPO / "media" / "titan.mp4"

pytestmark = pytest.mark.skipif(
    not (ffmpeg_available() and ffprobe_available() and TITAN.exists()),
    reason="ffmpeg/ffprobe or media/titan.mp4 not available — media-outcome tests need real media",
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


# ------------------------------------------- difficult fixtures

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


def test_second_audio_track_added_from_other_video(gen, media, tmp_path):
    # The reporter's remux: keep the output's own video and audio, mux in
    # the second file's first audio stream as an extra selectable track,
    # no re-encode. The second input is only 3 s: without -shortest the
    # output must keep the full 15 s of the main video.
    second = tmp_path / "clip2.mp4"
    _ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate={FPS}:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100:duration=3",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-shortest", str(second)])
    out = tmp_path / "second_track.mp4"
    r = gen("copy the audio and video tracks, and add the audio track of "
            "clip2.mp4 as a second audio track",
            input_path=str(media.base),
            output_filename=str(out),
            extra={"clip2": Asset(path=str(second), kind=AssetKind.VIDEO)})
    assert r.status == "ready", (r.status, r.errors, r.clarifications)
    args = r.arguments
    maps = [args[i + 1] for i, v in enumerate(args) if v == "-map"]
    assert maps == ["0:v:0", "0:a?", "1:a:0"]
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "copy"
    assert "-shortest" not in args
    proc = subprocess.run(
        args, shell=False, cwd=str(out.parent),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, f"argv={args}\nstderr={proc.stderr}"
    info = probe_media(str(out))
    assert info is not None
    assert info.video_streams == 1
    assert info.audio_streams == 2  # the original tone + the added track
    assert info.video_codec == "h264"  # the copy survived
    assert info.duration is not None
    assert abs(info.duration - 15.0) <= 0.75, info.duration


# ------------------------------------------------------------- stream I/O (M3b)

def test_stream_output_udp_mpegts(gen, media, tmp_path):
    """Generated argv streams to a local UDP socket; the datagrams that
    arrive must be valid MPEG-TS (0x47 sync, 188-byte packets)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(1.0)
    port = sock.getsockname()[1]
    received: list[bytes] = []
    done = threading.Event()

    def recv():
        # keep listening until ffmpeg exits: on a loaded machine ffmpeg can
        # take longer than the 1 s socket timeout to emit its first datagram
        while not done.is_set():
            try:
                d, _ = sock.recvfrom(65536)
            except socket.timeout:
                continue
            received.append(d)

    t = threading.Thread(target=recv, daemon=True)
    t.start()

    r = gen(f"cut the first 2 seconds and send it to udp://127.0.0.1:{port}",
            input_path=str(media.silent))
    assert r.status == "ready", r.status
    assert r.arguments[-1] == f"udp://127.0.0.1:{port}"
    proc = subprocess.run(
        r.arguments, shell=False, cwd=str(tmp_path),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, f"argv={r.arguments}\nstderr={proc.stderr}"
    done.set()
    t.join(timeout=10)
    # The udp muxer chunks the TS byte stream into variable-length datagrams
    # (up to ~1.5 KB), so datagram boundaries don't align with 188-byte TS
    # packets — reassemble the stream, then validate it as MPEG-TS: 0x47 sync
    # on every packet, and a PAT (PID 0) present.
    buf = b"".join(received)
    assert len(buf) > 50_000, len(buf)
    assert len(buf) % 188 == 0, len(buf)
    pids = set()
    for i in range(0, len(buf), 188):
        assert buf[i] == 0x47, (i, hex(buf[i]))
        pids.add(((buf[i + 1] & 0x1F) << 8) | buf[i + 2])
    assert 0 in pids


def test_stream_input_tcp_mpegts(gen, media, tmp_path):
    """A local TCP MPEG-TS source (streamed once by a background sender)
    is read, trimmed, and saved to a file: remote inputs work end to end.
    TCP, not UDP: a udp:// input has no end-of-stream, so a short feed
    would leave ffmpeg waiting for data that never comes."""
    src = tmp_path / "src.ts"
    _ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=15:duration=3",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-an", "-f", "mpegts", str(src)])
    data = src.read_bytes()
    # Reserve a free port, then release it: ffmpeg (the receiver) listens
    # on the URL's port. ffmpeg's tcp:// default is to connect, so the URL
    # carries ?listen=1 — fass passes the URL through exactly as given.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    url = f"tcp://127.0.0.1:{port}?listen=1"

    out = tmp_path / "from_stream.mp4"
    r = gen("cut the first 2 seconds", input_path=url, output_filename=str(out))
    assert r.status == "ready", r.status
    # raw tcp input needs an explicit demuxer (registry streaming.input_format)
    f = r.arguments.index("-f")
    assert r.arguments[f + 1] == "mpegts" and f < r.arguments.index("-i")

    proc = subprocess.Popen(
        r.arguments, shell=False, cwd=str(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # ffmpeg opens its listening socket at startup; retry until it is up.
    con = None
    for _ in range(50):
        try:
            con = socket.create_connection(("127.0.0.1", port), timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    if con is None:
        proc.kill()
        raise AssertionError("ffmpeg never opened its listening socket")
    con.sendall(data)
    con.close()
    pout, perr = proc.communicate(timeout=60)
    assert proc.returncode == 0, f"argv={r.arguments}\nstderr={perr.decode()}"
    info = probe_media(str(out))
    assert info is not None
    assert info.duration is not None
    assert abs(info.duration - 2.0) <= 0.5, info.duration


# ------------------------------------------------------------- M4 outcomes
#
# The NLU layer does not yet speak crop/speed/rotate/resample, so these
# tests build the EditPlan directly and drive plan_execution + build_argv —
# the same planner/builder the pipeline uses. Inputs are the committed
# real-world fixture media/titan.mp4 (1280x720 h264+AAC, ~20.08 s).

def _titan_assets() -> dict:
    return {"main": Asset(path=str(TITAN), kind=AssetKind.VIDEO)}


@pytest.fixture(scope="module")
def titan_media():
    info = probe_media(str(TITAN))
    assert info is not None, "media/titan.mp4 is not probeable"
    return info


def _run_direct(edit: EditPlan, assets: dict, media, out: Path):
    """Plan + build + execute the argv exactly as emitted, then probe."""
    ep = plan_execution(edit, assets, media, str(out))
    argv = build_argv(ep)
    proc = subprocess.run(
        argv, shell=False, cwd=str(out.parent),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, f"argv={argv}\nstderr={proc.stderr}"
    info = probe_media(str(out))
    assert info is not None, f"output {out} is not probeable"
    return ep, argv, info


def _probe_audio_stream(path: Path) -> tuple[int, int]:
    """(sample_rate, channels) of the first audio stream."""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert p.returncode == 0, p.stderr
    kv = dict(line.split("=", 1) for line in p.stdout.strip().splitlines() if "=" in line)
    return int(kv["sample_rate"]), int(kv["channels"])


def _probe_format_name(path: Path) -> str:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def test_titan_crop_to_exact_dims(titan_media, tmp_path):
    out = tmp_path / "crop.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[CropOp(width=640, height=480)])
    _, argv, info = _run_direct(edit, _titan_assets(), titan_media, out)
    assert (info.width, info.height) == (640, 480)
    assert argv[argv.index("-vf") + 1] == "crop=640:480"


def test_titan_rotate_90_swaps_dims(titan_media, tmp_path):
    out = tmp_path / "rot90.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[RotateOp(angle=90, clockwise=True)])
    _, argv, info = _run_direct(edit, _titan_assets(), titan_media, out)
    assert (info.width, info.height) == (720, 1280)
    assert argv[argv.index("-vf") + 1] == "transpose=1"


def test_titan_speed_2x_over_10s_trim(titan_media, tmp_path):
    # 10 source seconds at 2x -> ~5 output seconds (-t 5)
    out = tmp_path / "speed2.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    trim=TrimSpec(start_seconds=0, duration_seconds=10),
                    operations=[SpeedOp(factor=2)])
    _, argv, info = _run_direct(edit, _titan_assets(), titan_media, out)
    assert argv[argv.index("-t") + 1] == "5"
    assert argv[argv.index("-af") + 1] == "atempo=2"
    assert info.duration is not None
    assert abs(info.duration - 5.0) <= 0.3, info.duration


def test_titan_resample_22050_mono(titan_media, tmp_path):
    out = tmp_path / "resampled.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"),
                    operations=[AudioResampleOp(sample_rate=22050, channels=1)])
    _, argv, info = _run_direct(edit, _titan_assets(), titan_media, out)
    assert argv[argv.index("-ar") + 1] == "22050"
    assert argv[argv.index("-ac") + 1] == "1"
    assert info.has_audio
    rate, channels = _probe_audio_stream(out)
    assert rate == 22050
    assert channels == 1


def test_titan_10bit_pixel_format(tmp_path):
    # No probe metadata: the planner re-encodes, so -pix_fmt is emitted
    # (a copy remux would carry the source's pixel format untouched).
    out = tmp_path / "p10le.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264",
                                      pixel_format="yuv420p10le"))
    _, argv, info = _run_direct(edit, _titan_assets(), None, out)
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p10le"
    assert info.pix_fmt == "yuv420p10le"


def test_titan_mp4_to_ts_bsf_remux(titan_media, tmp_path):
    # No ops, matching codecs: stream copy, with the Annex-B restamp bsf.
    out = tmp_path / "remux.ts"
    edit = EditPlan(output=OutputSpec(container="mpegts", video_codec="h264"))
    ep, argv, info = _run_direct(edit, _titan_assets(), titan_media, out)
    assert ep.mode == "copy"
    assert argv[argv.index("-bsf:v") + 1] == "h264_mp4toannexb"
    assert _probe_format_name(out) == "mpegts"
    assert info.video_codec == "h264"
    assert info.has_audio  # audio survived the remux
    assert abs(info.duration - titan_media.duration) <= 0.5


def test_lavfi_source_input_end_to_end(tmp_path):
    # A 'lavfi:<spec>' source asset (kind='source') is read as a synthetic
    # input: -f lavfi + the bare spec, producing a playable file.
    out = tmp_path / "from_source.mp4"
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    assets = {"main": Asset(path="lavfi:testsrc=duration=2:size=320x240",
                            kind=AssetKind.SOURCE)}
    _, argv, info = _run_direct(edit, assets, None, out)
    i = argv.index("-i")
    assert argv[i + 1] == "testsrc=duration=2:size=320x240"
    assert argv[i - 1] == "lavfi" and argv[i - 2] == "-f"
    assert (info.width, info.height) == (320, 240)
    assert info.duration is not None
    assert 1.5 <= info.duration <= 2.5


def test_bare_input_resolved_against_input_volume(gen, media, tmp_path, monkeypatch):
    # M6 docker deployment: the user types "titan.mp4" (a file in /input).
    # The pipeline must find it through the same bare-name resolution the
    # executor uses at run time — probe it, and no "not found" warning.
    from fass import execute

    vol = tmp_path / "input"
    vol.mkdir()
    (vol / "titan.mp4").write_bytes(media.base.read_bytes())
    monkeypatch.setenv(execute.INPUT_DIR_ENV, str(vol))
    r = gen("resize to 720p", input_path="titan.mp4",
            output_filename=str(tmp_path / "out.mp4"))
    assert r.status == "ready"
    assert not any("not found on this machine" in w for w in r.warnings)
    assert r.validation_level == "preflight"  # proves the file was probed


def test_video_copy_preflight_with_real_probe(gen, media, tmp_path):
    # Regression: the user's video-copy instruction reached the live API
    # with a REAL probed input. generate() auto-probes local files, which
    # takes the preflight branch — and that branch looked up the video
    # encoder as REGISTRY["video_codecs"]["copy"] (KeyError → HTTP 500).
    # "copy" has no encoder to check: the video-encoder check is skipped
    # and the plan keeps its true validation level.
    r = gen("transcode audio to aac 180kbps and copy video as is",
            input_path=str(media.base),
            output_filename=str(tmp_path / "out.mp4"))
    assert r.status == "ready", r.errors
    args = r.arguments
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "180k"
    assert r.validation_level == "preflight"  # the block actually ran
