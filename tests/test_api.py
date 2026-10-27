"""API tests for the FastAPI layer: the web service is a
thin wrapper over the same generate() the CLI uses, so the tests assert the
PRD §9 contract — structured domain outcomes are 200 responses, malformed
requests are 422 — and the security invariants (assets stay local, POSIX
only, the service never executes).

Run under pytest from the repo root (no server needed: fastapi TestClient).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fass import execute
from fass.api import app

FFMPEG = shutil.which("ffmpeg") is not None
MEDIAINFO = shutil.which("mediainfo") is not None

client = TestClient(app)


def post(instruction: str, assets: dict, **kw):
    body = {"instruction": instruction, "assets": assets, "adapter": "rules"}
    body.update(kw)
    return client.post("/v1/commands", json=body)


# ------------------------------------------------------------- basics

def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_ready_returns_argv_command_and_plan():
    r = post("resize to 720p", {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    assert d["arguments"][0] == "ffmpeg"
    assert d["command"].startswith("ffmpeg ")
    assert d["plan"] is not None
    assert d["validation_level"] == "static"
    assert d["adapter"] == "rules"


def test_ready_without_existing_file_is_still_ready():
    # The service never requires the input to exist — it plans, it doesn't
    # execute; a missing file just means no probe metadata (documented).
    r = post("resize to 720p", {"main": "does-not-exist.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    assert d["arguments"][0] == "ffmpeg"


def test_i_prefix_input_convention_stripped():
    # web UI convention: `-i <file>` names a file in the input folder;
    # the prefix is stripped before planning (both plain-string and
    # {path, kind} asset forms)
    r = post("resize to 720p", {"main": "-i clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    assert "clip.mp4" in d["arguments"]
    assert "-i clip.mp4" not in d["arguments"]
    r = post("resize to 720p",
             {"main": {"path": "-i clip.mp4", "kind": "video"}})
    d = r.json()
    assert d["status"] == "ready"
    assert "clip.mp4" in d["arguments"]


def test_needs_clarification_is_structured_200():
    r = post("make it smaller", {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "needs_clarification"
    assert d["clarifications"], d
    assert d["arguments"] is None
    assert d["plan"] is None


def test_unsupported_is_structured_200_with_no_partial_plan():
    # M10.5: 'stabilize' is supported (two-pass vidstab) — a sepia request
    # still exercises the unsupported path
    r = post("make it look sepia", {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "unsupported"
    assert d["plan"] is None
    assert d["unsupported"], d


# ------------------------------------------------------------- request errors

def test_missing_main_asset_is_422():
    r = post("resize to 720p", {})
    assert r.status_code == 422


def test_missing_instruction_is_422():
    r = client.post("/v1/commands", json={"assets": {"main": "clip.mp4"}})
    assert r.status_code == 422


def test_unknown_field_is_422():
    r = post("resize to 720p", {"main": "clip.mp4"}, bogus=True)
    assert r.status_code == 422


def test_non_posix_shell_is_422():
    # PRD §9: the copyable renderer is POSIX-only; Windows is deferred and
    # must not be represented as supported — a non-POSIX value is a
    # validation error, not a best-effort render.
    r = post("resize to 720p", {"main": "clip.mp4"}, target_shell="powershell")
    assert r.status_code == 422


def test_stream_input_url_is_accepted():
    # M3b: assets may be local paths OR allowlisted stream URLs. http input is
    # legal — the command references the URL exactly as given.
    r = post("resize to 720p", {"main": "http://example.com/x.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready", d
    assert "http://example.com/x.mp4" in d["arguments"]
    assert "http://example.com/x.mp4" in d["command"]


@pytest.mark.parametrize("url", ["ftp://example.com/x.mp4", "file:///tmp/x.mp4"])
def test_non_allowlisted_scheme_is_422(url):
    # Only the registry's input-scheme allowlist is accepted — anything else
    # (including file://, which would reach the local filesystem) is rejected
    # at validation, not planned.
    r = post("resize to 720p", {"main": url})
    assert r.status_code == 422


def test_output_equal_to_input_is_error_status_not_http_error():
    r = post("resize to 720p", {"main": "clip.mp4"}, output_filename="clip.mp4")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "error"
    assert d["errors"], d


# ------------------------------------------------------------- features

def test_revision_with_previous_plan():
    first = post("resize to 720p", {"main": "clip.mp4"}).json()
    assert first["status"] == "ready"
    r = post("make it black and white", {"main": "clip.mp4"},
             previous_plan=first["plan"])
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    types = sorted(op["type"] for op in d["plan"]["operations"])
    assert types == ["grayscale", "resize"]


def test_bitrate_and_mpegts_round_trip():
    r = post("cut from 20 to 30 secs and transcode to cbr 10Mbps mpeg ts",
             {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    assert d["plan"]["output"]["container"] == "mpegts"
    assert d["plan"]["output"]["video_bitrate_kbps"] == 10000
    args = d["arguments"]
    assert "-b:v" in args and args[args.index("-b:v") + 1] == "10000k"
    assert "-crf" not in args
    assert args[-1].endswith(".ts")


def test_audio_replace_round_trip_with_extra_input():
    # The web UI's "+ add input" path: an asset id the user mentions in the
    # instruction, typed automatically from its extension.
    r = post("replace the audio with song.mp3",
             {"main": "clip.mp4", "in1": "song.mp3"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready", d
    ops = d["plan"]["operations"]
    assert len(ops) == 1 and ops[0]["type"] == "audio_replace"
    assert ops[0]["asset_id"] == "in1"
    args = d["arguments"]
    maps = [args[i + 1] for i, v in enumerate(args) if v == "-map"]
    assert maps == ["0:v:0", "1:a:0"]
    assert "-shortest" in args
    assert "song.mp3" in d["command"]


def test_audio_replace_without_audio_input_clarifies():
    r = post("replace the audio with song.mp3", {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "needs_clarification"
    assert d["arguments"] is None


def test_media_metadata_payload_is_accepted():
    # Pre-collected ffprobe JSON (as the local helper would emit) changes the
    # planner's decision: matching codecs in mov → stream copy.
    media = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "width": 1920, "height": 1080, "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
        ],
        "format": {"duration": "15.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    r = post("convert to mov", {"main": "clip.mp4"}, media=media)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    assert "-c" in d["arguments"] and "copy" in d["arguments"]


def test_support_endpoint_lists_capabilities():
    r = client.get("/v1/support")
    assert r.status_code == 200
    d = r.json()
    assert "resize" in d["summary"]
    assert "h264" in d["video_codecs"]
    assert d["registry_version"]


def test_support_exposes_m7_surface():
    # The full M7 capability surface: modes, the container/codec matrix,
    # subtitle dump formats, filter names, per-container format options,
    # stream schemes, pixel formats, profiles, presets, sample rates, speed.
    d = client.get("/v1/support").json()
    assert d["modes"] == ["video", "audio", "subtitles"]
    assert "h264" in d["matrix"]["mp4"]["video"]
    assert d["matrix"]["mp4"]["audio_codec"] == "aac"
    assert d["matrix"]["mp3"]["video"] == []
    assert d["matrix"]["mp3"]["audio"] == ["mp3"]
    assert "mp3" in d["audio_codecs"] and "pcm_s16le" in d["audio_codecs"]
    assert d["subtitle_dumps"] == {"srt_file": "srt", "vtt_file": "webvtt",
                                   "ass_file": "ass"}
    assert "scale" in d["filters"]["video"] and "crop" in d["filters"]["video"]
    assert "volume" in d["filters"]["audio"] and "atempo" in d["filters"]["audio"]
    assert d["filters"]["filtergraph"] is True
    # M10: the full filter table with per-option kinds and category flags
    table = d["filters"]["table"]
    assert table["xfade"]["domain"] == "video"
    assert table["xfade"]["multi_input"] == 2
    assert "fade" in table["xfade"]["options"]["transition"]["values"]
    assert table["acompressor"]["domain"] == "audio"
    assert table["acompressor"]["options"]["level_in"]["range"] == [0.015625, 64]
    assert table["silencedetect"]["diagnostic"] is True
    assert table["libvmaf"]["needs_reference"] is True
    assert table["scale_cuda"]["gpu"] is True
    assert table["blackdetect_vulkan"]["min_version"] == "7.1"
    assert table["scale"]["options"] == {}  # name-only entry keeps its shape
    assert "movflags" in d["format_options"]["mp4"]
    assert "hls_time" in d["format_options"]["hls"]
    assert "mpegts_flags" in d["format_options"]["mpegts"]
    assert "srt" in d["input_schemes"]
    assert "http" in d["output_schemes"] and "https" in d["output_schemes"]
    assert "yuv420p10le" in d["pixel_formats"]["h264"]
    assert "baseline" in d["video_profiles"]["h264"]
    assert "veryslow" in d["video_presets"]["h264"]
    assert 44100 in d["sample_rates"]
    assert d["speed"] == {"min": 0.25, "max": 8.0}


@pytest.mark.parametrize("url,container", [
    ("http://example.com/live.ts", "mpegts"),
    ("https://example.com/live.mp4", "mp4"),
    ("http://example.com/stream", "mpegts"),   # no extension -> scheme default
])
def test_http_output_destination_accepted(url, container):
    # CommandRequest accepts http/https output destinations: the container
    # is derived from the URL path extension (default mpegts) and the URL
    # is the final argv token, exactly as given.
    r = post("resize to 720p", {"main": "clip.mp4"}, output_filename=url)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready", d
    assert d["plan"]["output_destination"] == url
    assert d["plan"]["output"]["container"] == container
    assert d["arguments"][-1] == url
    assert any("remote endpoint" in w for w in d["warnings"])


def test_env_endpoint_reports_build():
    r = client.get("/v1/env")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert "ffmpeg version" in d["build"]
    assert d["encoders"]["video:h264"]["present"] is True
    assert d["filters"]["scale"]["present"] is True
    # M10: every registry filter is probed against this build (like bsfs)
    assert d["filters"]["acrossfade"]["present"] is True
    assert d["filters"]["blackdetect_vulkan"]["present"] is False


def test_ui_served_at_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/v1/commands" in r.text
    assert "ffmpeg" in r.text.lower()


def test_ui_script_is_valid_javascript():
    # Regression: the confirm() dialog's text contained a '\n' that Python
    # interpreted while building UI_HTML, so the browser received a real
    # line break inside a JS single-quoted string and rejected the whole
    # <script> — every tab button and Generate/Save/Apply went dead.
    # Check the bytes the server actually serves, not the Python source.
    import os
    import re
    import shutil
    import subprocess
    import tempfile

    js = re.search(r"<script>(.*?)</script>", client.get("/").text, re.S)
    assert js, "the UI must carry an inline script"
    # the specific victim: one physical line with the escape intact
    line = next(l for l in js.group(1).splitlines() if "confirm(" in l)
    assert "saved config?\\nThe UI" in line
    # the whole block: a real JS parser on the served bytes
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for the JS syntax check")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js.group(1))
        path = f.name
    try:
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert proc.returncode == 0, proc.stderr


def test_ui_validation_line_uses_check_fields():
    # Regression: the Validation line once mapped c.check/c.status, which
    # don't exist on check objects ({name, ok, detail}) — the UI showed
    # "preflight — undefined: undefined; …" instead of the check results.
    html = client.get("/").text
    assert "c.check + ': ' + c.status" not in html
    assert "c.detail" in html and "c.name" in html and "c.ok" in html


# ------------------------------------------------------------- M3b streaming


def test_asset_ref_with_explicit_kind_for_extensionless_stream():
    # A stream URL with no extension gives guess_kind no signal; the client
    # supplies the kind explicitly via the {path, kind} asset reference.
    r = post("replace the audio with the radio stream",
             {"main": "clip.mp4",
              "radio": {"path": "http://radio.example.com/stream", "kind": "audio"}})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready", d
    ops = d["plan"]["operations"]
    assert len(ops) == 1 and ops[0]["type"] == "audio_replace"
    assert ops[0]["asset_id"] == "radio"
    assert "http://radio.example.com/stream" in d["arguments"]


def test_asset_ref_with_bad_kind_is_422():
    r = post("resize to 720p",
             {"main": {"path": "clip.mp4", "kind": "hologram"}})
    assert r.status_code == 422


def test_stream_output_destination_via_instruction():
    r = post("cut the first 5 seconds and send it to udp://127.0.0.1:5004",
             {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready", d
    assert d["plan"]["output_destination"] == "udp://127.0.0.1:5004"
    assert d["plan"]["output"]["container"] == "mpegts"
    args = d["arguments"]
    assert "-f" in args and args[args.index("-f") + 1] == "mpegts"
    assert args[-1] == "udp://127.0.0.1:5004"
    assert any("remote endpoint" in w for w in d["warnings"])


def test_output_filename_stream_url_sets_container_by_scheme():
    # The -o / output field is a stream URL: its scheme fixes the container
    # (the user named no format, so the input-derived default yields to it).
    r = post("resize to 720p", {"main": "clip.mp4"},
             output_filename="rtmp://host:1935/live")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready", d
    assert d["plan"]["output_destination"] == "rtmp://host:1935/live"
    assert d["plan"]["output"]["container"] == "flv"
    assert any("by the output destination" in x for x in d["defaults"])
    args = d["arguments"]
    assert args[-1] == "rtmp://host:1935/live"
    f = args.index("-f")
    assert args[f + 1] == "flv"


def test_explicit_container_conflicting_with_stream_output_is_error():
    # The user named a format in the instruction AND a stream output whose
    # scheme carries a different container: a contradiction, reported — not
    # silently resolved in favor of one.
    r = post("convert to webm", {"main": "clip.mp4"},
             output_filename="rtmp://host:1935/live")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "error", d
    assert d["errors"], d


# ------------------------------------------------------- M7 preflight (mocked build)


def test_preflight_reports_missing_output_muxer(monkeypatch):
    # The 6.1.1 case (registry note: the Ubuntu 6.1.1 build lacks the
    # standalone AAC/ADTS muxer): when the probed build is missing the
    # muxer the plan will write with, preflight reports it — availability
    # is probed, never assumed. Mocked probe_ffmpeg so the test is
    # deterministic on any machine.
    from fass.pipeline import generate
    from fass.probe.ffprobe import FfmpegInfo
    from tests.conftest import assets_for, make_media

    ff = FfmpegInfo(version="ffmpeg version 6.1.1",
                    encoders={"libx264", "aac"},
                    filters={"scale"},
                    muxers={"mp4", "mov", "webm"})  # no matroska
    monkeypatch.setattr("fass.pipeline.ffmpeg_available", lambda: True)
    monkeypatch.setattr("fass.pipeline.probe_ffmpeg", lambda: ff)
    r = generate("convert to mkv", assets_for(), media=make_media(),
                 adapter_name="rules", preflight_check=True)
    assert r.status == "ready", r.errors
    mux = [c for c in r.checks if c["name"] == "muxer matroska"]
    assert mux and mux[0]["ok"] is False
    assert any("matroska" in w for w in r.warnings)
    assert r.validation_level == "static"  # a failed check is not "preflight"


def test_ui_offers_kind_select_for_extra_inputs():
    # The web UI's "+ add input" rows must let the client pin a kind for
    # extensionless stream URLs (the {path, kind} asset reference).
    html = client.get("/").text
    assert 'class="extra-kind"' in html
    for kind in ("video", "audio", "image"):
        assert f"<option value=\"{kind}\">" in html
    assert "assets[i.dataset.id] = (kind === 'auto') ? v : { path: v, kind };" in html


# ------------------------------------------------- GET /v1/rag (M11.2)

class _FakeCorporaResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_rag_status_disabled_by_default(monkeypatch):
    # default config is RAG off: the endpoint reports disabled, no probe
    monkeypatch.delenv("FASS_RAG_ENABLED", raising=False)
    monkeypatch.delenv("FASS_RAG_URL", raising=False)
    r = client.get("/v1/rag")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is False
    assert d["service"] is None
    assert set(d["config"]) == {"url", "timeout_ms", "top_k",
                                "max_evidence_chars", "corpus", "auth_token",
                                "send_instruction", "enabled"}


def test_rag_status_enabled_service_down_fails_open(monkeypatch):
    # a dead fass-rag must render as a status line, never break the endpoint
    import fass.api as api
    from fass.config import RagConfig

    monkeypatch.setattr(api, "resolved_rag",
                        lambda: RagConfig(enabled=True,
                                          url="http://127.0.0.1:9"))
    r = client.get("/v1/rag")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is True
    assert d["service"]["ok"] is False
    assert d["service"]["error"]


def test_rag_status_enabled_service_up(monkeypatch):
    import fass.api as api
    import urllib.request
    from fass.config import RagConfig

    body = b'{"corpora": [{"corpus_id": "ffmpeg-6.1.1", ' \
            b'"version_status": "stable", "loaded": true, ' \
            b'"kinds": ["filter", "codec"]}], ' \
            b'"loaded_corpus_id": "ffmpeg-6.1.1"}'
    monkeypatch.setattr(api, "resolved_rag",
                        lambda: RagConfig(enabled=True,
                                          url="http://127.0.0.1:8100"))
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeCorporaResp(body))
    d = client.get("/v1/rag").json()
    assert d["enabled"] is True
    assert d["service"]["ok"] is True
    assert d["service"]["loaded_corpus_id"] == "ffmpeg-6.1.1"
    assert d["service"]["corpora"][0]["corpus_id"] == "ffmpeg-6.1.1"


# ------------------------------------------------ POST /v1/rag/query (M11.2)


def test_rag_query_disabled_by_default_is_200_ok_false(monkeypatch):
    # RAG is the advisory fail-open layer: disabled RAG answers 200 with a
    # readable error, never a 4xx/5xx (the RAG tab renders it as a line)
    monkeypatch.delenv("FASS_RAG_ENABLED", raising=False)
    monkeypatch.delenv("FASS_RAG_URL", raising=False)
    r = client.post("/v1/rag/query", json={"query": "overlay a logo"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False
    assert "disabled" in d["error"]
    assert d["chunks"] == []


def test_rag_query_enabled_passes_corpus_fields_and_chunks_through(monkeypatch):
    import fass.api as api
    from fass.config import RagConfig
    from fass.rag.client import RagSearchResult

    chunks = [
        {"chunk_id": "ffmpeg-6.1.1:filter:overlay:0", "title": "overlay",
         "url": "https://ffmpeg.org/ffmpeg-filters.html", "anchor": "overlay",
         "component_type": "filter", "component_name": "overlay",
         "option_names": ["x", "y", "format"],
         "text": "Overlay a video on top of another.", "score": 0.4231},
        {"chunk_id": "ffmpeg-6.1.1:filter:scale:0", "title": "scale",
         "url": "https://ffmpeg.org/ffmpeg-filters.html", "anchor": "scale",
         "component_type": "filter", "component_name": "scale",
         "option_names": ["w", "h"],
         "text": "Scale the input video size.", "score": 0.3117},
    ]
    res = RagSearchResult(ok=True, corpus_id="ffmpeg-6.1.1",
                          corpus_match="exact", evidence_status="supported",
                          chunks=chunks, retrieval_ms=12, warnings=[])
    monkeypatch.setattr(api, "resolved_rag",
                        lambda: RagConfig(enabled=True))
    monkeypatch.setattr(api, "rag_search", lambda cfg, q, **kw: res)
    r = client.post("/v1/rag/query",
                    json={"query": "overlay a logo bottom right"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["corpus_id"] == "ffmpeg-6.1.1"
    assert d["corpus_match"] == "exact"
    assert d["evidence_status"] == "supported"
    assert d["retrieval_ms"] == 12
    assert d["warnings"] == []
    assert d["chunks"] == chunks  # raw dicts, pass-through


def test_rag_query_blank_is_422():
    # min_length 1 + the not-blank validator: an empty/whitespace query is a
    # request error, the endpoint never fires a search that can only be noise
    for q in ("", "   "):
        r = client.post("/v1/rag/query", json={"query": q})
        assert r.status_code == 422, q


def test_rag_query_top_k_override_reaches_the_client_config(monkeypatch):
    import fass.api as api
    from fass.config import RagConfig
    from fass.rag.client import RagSearchResult

    captured = {}

    def fake_search(cfg, q, **kw):
        captured["cfg"] = cfg
        return RagSearchResult(ok=True, corpus_id="ffmpeg-6.1.1",
                               chunks=[], warnings=[])

    monkeypatch.setattr(api, "resolved_rag",
                        lambda: RagConfig(enabled=True, top_k=6))
    monkeypatch.setattr(api, "rag_search", fake_search)
    r = client.post("/v1/rag/query", json={"query": "scale", "top_k": 3})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert captured["cfg"].top_k == 3


# ------------------------------------------------- Files tab (volumes + info)

def _vols(tmp_path, monkeypatch):
    """Isolated input / output volumes (the docker deployment's layout)."""
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    for d in (in_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(execute.INPUT_DIR_ENV, str(in_dir))
    monkeypatch.setenv(execute.OUTPUT_DIR_ENV, str(out_dir))
    return in_dir, out_dir


def _make_clip(path: Path, size: str = "320x240", frames: int = 2) -> Path:
    """A real tiny lavfi clip (the suite's ffmpeg gate: only used under
    @pytest.mark.skipif(not FFMPEG)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration=0.2:size={size}:rate=10",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-f", "mp4", str(path)],
        check=True, capture_output=True, timeout=90)
    return path


def _make_wav(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=duration=0.2:frequency=440",
         "-f", "wav", str(path)],
        check=True, capture_output=True, timeout=90)
    return path


def test_files_lists_volume_files_newest_first(tmp_path, monkeypatch):
    in_dir, out_dir = _vols(tmp_path, monkeypatch)
    old, new = in_dir / "old.mp4", in_dir / "new.mp4"
    old.write_bytes(b"x" * 100)
    new.write_bytes(b"y" * 2048)
    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))
    (in_dir / "sub").mkdir()  # a directory is never a row
    r = client.get("/v1/files", params={"dir": "input"})
    assert r.status_code == 200
    d = r.json()
    assert d["dir"] == "input"
    assert d["exists"] is True
    assert [f["name"] for f in d["files"]] == ["new.mp4", "old.mp4"]
    assert d["files"][0]["size"] == 2048
    assert d["files"][1]["size"] == 100
    assert d["files"][0]["mtime"] > d["files"][1]["mtime"]
    # the other volume is listed independently (empty here)
    d = client.get("/v1/files", params={"dir": "output"}).json()
    assert d["exists"] is True and d["files"] == []


def test_files_unmounted_volume_is_exists_false(tmp_path, monkeypatch):
    # no $FASS_*_DIR and no /input|/output on this host: the volume is simply
    # not there — rendered as an empty column, not as an error
    monkeypatch.delenv(execute.INPUT_DIR_ENV, raising=False)
    monkeypatch.delenv(execute.OUTPUT_DIR_ENV, raising=False)
    monkeypatch.setattr(execute, "_input_dir", lambda: "")
    d = client.get("/v1/files", params={"dir": "input"}).json()
    assert d["exists"] is False and d["files"] == []
    # an env dir that does not exist is the same case
    monkeypatch.setenv(execute.OUTPUT_DIR_ENV, str(tmp_path / "nope"))
    d = client.get("/v1/files", params={"dir": "output"}).json()
    assert d["exists"] is False and d["files"] == []


def test_files_bogus_dir_is_400(tmp_path, monkeypatch):
    _vols(tmp_path, monkeypatch)
    for bad in ("bogus", "", "in"):
        assert client.get("/v1/files", params={"dir": bad}).status_code == 400


def test_files_inspect_path_traversal_is_400(tmp_path, monkeypatch):
    # the same stance as /v1/mediainfo and the /media routes: a bare basename
    # inside the selected volume, never a path
    _vols(tmp_path, monkeypatch)
    for bad in ("../secret", "a/b.mp4", "..", "sub/../x"):
        r = client.get("/v1/files/inspect",
                       params={"dir": "input", "file": bad})
        assert r.status_code == 400, bad
    assert client.get("/v1/files/inspect",
                      params={"dir": "input", "file": ""}).status_code == 400
    assert client.get("/v1/files/inspect",
                      params={"dir": "bogus", "file": "x.mp4"}).status_code == 400
    assert client.get("/v1/files/inspect",
                      params={"dir": "input", "file": "x.mp4",
                              "tool": "exiftool"}).status_code == 400


def test_files_inspect_missing_file_is_404(tmp_path, monkeypatch):
    _vols(tmp_path, monkeypatch)
    r = client.get("/v1/files/inspect",
                   params={"dir": "input", "file": "ghost.mp4"})
    assert r.status_code == 404
    assert "ghost.mp4" in r.json()["detail"]


@pytest.mark.skipif(MEDIAINFO, reason="mediainfo is installed here")
def test_files_inspect_mediainfo_not_installed_is_503(tmp_path, monkeypatch):
    # the deployment ships ffprobe, not the mediainfo binary: the menu item
    # reports that plainly instead of pretending to work
    in_dir, _out = _vols(tmp_path, monkeypatch)
    (in_dir / "clip.mp4").write_bytes(b"x")
    r = client.get("/v1/files/inspect",
                   params={"dir": "input", "file": "clip.mp4",
                           "tool": "mediainfo"})
    assert r.status_code == 503
    assert "not installed" in r.json()["detail"]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_files_inspect_ffprobe_real_clip(tmp_path, monkeypatch):
    in_dir, _out = _vols(tmp_path, monkeypatch)
    _make_clip(in_dir / "clip.mp4")
    r = client.get("/v1/files/inspect",
                   params={"dir": "input", "file": "clip.mp4",
                           "tool": "ffprobe"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tool"] == "ffprobe"
    assert d["json"]["streams"], d
    assert any(s.get("codec_type") == "video" for s in d["json"]["streams"])
    assert d["json"]["format"]["filename"].endswith("clip.mp4")
    # the default tool is ffprobe
    r = client.get("/v1/files/inspect",
                   params={"dir": "input", "file": "clip.mp4"})
    assert r.status_code == 200 and r.json()["tool"] == "ffprobe"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_files_frame_first_frame_jpeg(tmp_path, monkeypatch):
    in_dir, _out = _vols(tmp_path, monkeypatch)
    _make_clip(in_dir / "clip.mp4", size="1280x720")
    r = client.post("/v1/files/frame",
                    json={"dir": "input", "file": "clip.mp4"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True, d
    assert d["height"] == 360 and d["width"] == 640  # even width from the aspect
    raw = base64.b64decode(d["jpeg_b64"])
    assert raw[:2] == b"\xff\xd8"  # a real JPEG
    # the output volume is reachable too
    assert client.post("/v1/files/frame",
                       json={"dir": "output", "file": "clip.mp4"}).status_code == 404
    # the basename guard
    assert client.post("/v1/files/frame",
                       json={"dir": "input", "file": "../clip.mp4"}).status_code == 400


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_files_frame_audio_only_fails_open(tmp_path, monkeypatch):
    # no video stream: a 200 with ok=false and ffmpeg's own message — the
    # popup shows the line, the tab never breaks
    in_dir, _out = _vols(tmp_path, monkeypatch)
    _make_wav(in_dir / "tone.wav")
    r = client.post("/v1/files/frame", json={"dir": "input", "file": "tone.wav"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is False
    assert d["error"]
    assert "jpeg_b64" not in d


def test_ui_has_the_files_tab():
    html = client.get("/").text
    assert 'id="tab-files"' in html and "showTab('files')" in html
    assert 'id="pane-files"' in html
    assert "['cmd','runs','info','cfg'].concat('rag','files')" in html
    for needle in ("files-input", "files-output", "filemenu", "filemodal",
                   "loadFiles", "fileAction", "closeModal", "1st frame"):
        assert needle in html


# --------------------------------------------- edited command (expert override)


def _run_env(tmp_path, monkeypatch):
    """Volumes + run dir + the execution gate open (FASS_ALLOW_EXECUTE=1) —
    the /v1/runs fixture (mirrors tests/test_m6_api.py's cfg_env/run_env)."""
    in_dir, out_dir = _vols(tmp_path, monkeypatch)
    (tmp_path / "runs").mkdir(exist_ok=True)
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(tmp_path / "runs"))
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    return in_dir, out_dir


def test_runs_command_override_runs_the_edited_argv(tmp_path, monkeypatch):
    _run_env(tmp_path, monkeypatch)
    r = client.post("/v1/runs", json={
        "arguments": ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-crf", "28", "o.mp4"],
        "command": "ffmpeg -i a.mp4 -c:v libx264 o.mp4",
    })
    assert r.status_code == 200, r.text
    argv = r.json()["run"]["argv"]
    # the shlex-split of the EDITED string, not the posted arguments array
    assert argv[:5] == ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264"]
    assert "-crf" not in argv and "28" not in argv
    # volume resolution of the output path is unchanged
    assert argv[-1] == str(tmp_path / "out" / "o.mp4")


def test_runs_command_unbalanced_quotes_422(tmp_path, monkeypatch):
    _run_env(tmp_path, monkeypatch)
    r = client.post("/v1/runs", json={
        "arguments": ["ffmpeg", "-i", "a.mp4", "o.mp4"],
        "command": 'ffmpeg -i "a.mp4 o.mp4',
    })
    assert r.status_code == 422
    assert "quotes" in r.json()["detail"]
    assert client.get("/v1/runs").json() == {"runs": []}  # nothing started


def test_runs_command_failing_validation_422(tmp_path, monkeypatch):
    _run_env(tmp_path, monkeypatch)
    r = client.post("/v1/runs", json={
        "arguments": ["ffmpeg", "-i", "a.mp4", "o.mp4"],
        "command": "ffmpeg -i a.mp4 -not_an_option o.mp4",
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "failed validation" in detail and "-not_an_option" in detail
    assert client.get("/v1/runs").json() == {"runs": []}  # the gate held


def test_runs_multipass_cannot_be_edited(tmp_path, monkeypatch):
    _run_env(tmp_path, monkeypatch)
    steps = [["ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
              "-c:v", "libx264", "step1.mp4"],
             ["ffmpeg", "-i", "step1.mp4", "-c:v", "libx264", "o.mp4"]]
    r = client.post("/v1/runs", json={
        "arguments": steps[-1], "steps": steps, "command": "ffmpeg -i a.mp4 o.mp4"})
    assert r.status_code == 422
    assert "multi-pass" in r.json()["detail"]


def test_runs_arguments_only_body_is_unchanged(tmp_path, monkeypatch):
    """Regression: no command field (and a blank one) → today's behavior."""
    _run_env(tmp_path, monkeypatch)
    argv = ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "o.mp4"]
    r = client.post("/v1/runs", json={"arguments": argv})
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["argv"][:4] == argv[:4]
    assert run["argv"][-1] == str(tmp_path / "out" / "o.mp4")
    r2 = client.post("/v1/runs", json={"arguments": argv, "command": "   "})
    assert r2.status_code == 200, r2.text
    assert r2.json()["run"]["argv"] == run["argv"]


def test_ui_command_tab_is_editable():
    html = client.get("/").text
    assert '<textarea class="cmd" id="cmd" rows="4" spellcheck="false">' in html
    assert 'id="cmdorig"' in html  # the generated command, for the edit check
    assert "re-validated server-side" in html
    assert "body.command = text" in html  # sent only when the text was edited
    # the copy button reads the textarea's live value (textContent would copy
    # the pre-edit text — a textarea's child node never changes)
    assert "writeText($('cmd').value)" in html
