"""API tests for the FastAPI layer (plan0 milestone 2): the web service is a
thin wrapper over the same generate() the CLI uses, so the tests assert the
PRD §9 contract — structured domain outcomes are 200 responses, malformed
requests are 422 — and the security invariants (assets stay local, POSIX
only, the service never executes).

Run under pytest from the repo root (no server needed: fastapi TestClient).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fass.api import app

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


def test_needs_clarification_is_structured_200():
    r = post("make it smaller", {"main": "clip.mp4"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "needs_clarification"
    assert d["clarifications"], d
    assert d["arguments"] is None
    assert d["plan"] is None


def test_unsupported_is_structured_200_with_no_partial_plan():
    r = post("make it 2x faster", {"main": "clip.mp4"})
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


def test_remote_asset_reference_is_422():
    r = post("resize to 720p", {"main": "http://example.com/x.mp4"})
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


def test_env_endpoint_reports_build():
    r = client.get("/v1/env")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert "ffmpeg version" in d["build"]
    assert d["encoders"]["video:h264"]["present"] is True
    assert d["filters"]["scale"] is True


def test_ui_served_at_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/v1/commands" in r.text
    assert "ffmpeg" in r.text.lower()


def test_ui_validation_line_uses_check_fields():
    # Regression: the Validation line once mapped c.check/c.status, which
    # don't exist on check objects ({name, ok, detail}) — the UI showed
    # "preflight — undefined: undefined; …" instead of the check results.
    html = client.get("/").text
    assert "c.check + ': ' + c.status" not in html
    assert "c.detail" in html and "c.name" in html and "c.ok" in html
