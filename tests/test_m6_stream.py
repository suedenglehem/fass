"""M6 stream host rewrite: the 'host' placeholder in stream URLs becomes
0.0.0.0 for listen (udp/rtp receive) and the configured docker host
address for everything else — only when streaming is enabled in config."""

from __future__ import annotations

import pytest

from fass.config import FassConfig, StreamingConfig, save_config
from fass.pipeline import (
    GenerationResponse,
    _reconcile_output_destination,
    _rewrite_stream_host,
    generate,
)
from fass.schema import Asset, EditPlan, OutputSpec, guess_kind
from tests.conftest import make_media

HA = "172.17.0.1"


def _streaming(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("FASS_CONFIG", str(p))
    save_config(FassConfig(
        streaming=StreamingConfig(enabled=True, host_address=HA)))


def test_udp_listen_binds_all():
    assert _rewrite_stream_host("udp://host:5000", HA, listen=True) \
        == "udp://0.0.0.0:5000"


def test_rtp_listen_binds_all():
    assert _rewrite_stream_host("rtp://host:1234", HA, listen=True) \
        == "rtp://0.0.0.0:1234"


def test_rtp_pull_uses_host_address():
    assert _rewrite_stream_host("rtp://host:1234", HA, listen=False) \
        == "rtp://172.17.0.1:1234"


def test_push_outputs_use_host_address():
    assert _rewrite_stream_host("rtmp://host:1935/live", HA, listen=False) \
        == "rtmp://172.17.0.1:1935/live"
    assert _rewrite_stream_host("srt://host:9000/stream", HA, listen=False) \
        == "srt://172.17.0.1:9000/stream"


def test_non_stream_and_named_hosts_untouched():
    assert _rewrite_stream_host("clip.mp4", HA, listen=True) == "clip.mp4"
    # a real hostname is not the placeholder
    assert _rewrite_stream_host("rtsp://cam.local/stream", HA, listen=False) \
        == "rtsp://cam.local/stream"


def test_generate_rewrites_udp_input(tmp_path, monkeypatch):
    _streaming(tmp_path, monkeypatch)
    assets = {"main": Asset(path="udp://host:5000",
                            kind=guess_kind("udp://host:5000"))}
    r = generate("resize to 480p", assets, media=make_media(),
                 adapter_name="rules")
    assert r.status == "ready", r.errors
    assert "udp://0.0.0.0:5000" in r.command


def test_generate_rewrites_rtmp_destination(tmp_path, monkeypatch):
    _streaming(tmp_path, monkeypatch)
    assets = {"main": Asset(path="clip.mp4", kind=guess_kind("clip.mp4"))}
    r = generate("resize to 480p and stream to rtmp://host:1935/live",
                 assets, media=make_media(), adapter_name="rules")
    assert r.status == "ready", r.errors
    assert r.command.rstrip().endswith("rtmp://172.17.0.1:1935/live")


def test_generate_leaves_placeholder_when_streaming_off(tmp_path, monkeypatch):
    # no config file -> defaults -> streaming disabled -> no rewrite
    monkeypatch.setenv("FASS_CONFIG", str(tmp_path / "absent.json"))
    assets = {"main": Asset(path="udp://host:5000",
                            kind=guess_kind("udp://host:5000"))}
    r = generate("resize to 480p", assets, media=make_media(),
                 adapter_name="rules")
    assert r.status == "ready", r.errors
    assert "udp://host:5000" in r.command


# ---------------- M13.1: file-destination container reconciliation (bug: an
# mp4/mov default must not ship -movflags +faststart into a .ts output)

def test_file_destination_reconciles_defaulted_container():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    resp = GenerationResponse(status="ready")
    _reconcile_output_destination(
        edit, "out.ts",
        ["container defaulted to mp4 (from the input filename)"], resp)
    assert edit.output.container == "mpegts"
    assert any("out.ts" in d and "mpegts" in d for d in resp.defaults)


def test_file_destination_keeps_explicit_container():
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    resp = GenerationResponse(status="ready")
    _reconcile_output_destination(
        edit, "out.ts", ["container set to mp4"], resp)
    # explicit container: the planner reports the conflict, no silent switch
    assert edit.output.container == "mp4"
    assert edit.output_destination is None


def test_file_destination_reconcile_skips_subtitle_dumps():
    # the stream 'srt' container shadows 'srt_file' for the .srt extension
    edit = EditPlan(output=OutputSpec(mode="subtitles", container="srt_file"))
    resp = GenerationResponse(status="ready")
    _reconcile_output_destination(
        edit, "subs.srt", ["container defaulted to srt_file"], resp)
    assert edit.output.container == "srt_file"
    assert edit.output_destination is None
