"""M12 remote volume mapping (FTP/SFTP/S3) — hermetic unit tests.

No real network: every backend is exercised through in-memory fakes
(fake boto3 client, a FakeVolume that stands in for a store). The real
FTP/SFTP/SFTP servers on the test host are covered by the live tr4:8099
pass, not here. The focus is the CONFIG model, the factory dispatch,
the LIVE-read semantics, the fail-open input download cache, the run
finalizer upload, the API merge, and the S3 uuid_naming naming scheme.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fass import remote
from fass.api import app
from fass.config import FassConfig, RemoteVolumeConfig, VolumesConfig
from fass import execute

client = TestClient(app)


# ---------------------------------------------------------------- fakes


class FakeVol(remote.RemoteVolume):
    """An in-memory store standing in for FTP/SFTP/S3.

    files: rel -> bytes (fetchable). entries: list() rows. fail: a rel
    whose upload raises (to exercise the fail-open note path).
    """

    def __init__(self, files=None, entries=None, fail=None,
                 raise_on_fetch=False, write_but_fail=False):
        self.files = dict(files or {})
        self.entries = entries or []
        self.fail = fail
        self.raise_on_fetch = raise_on_fetch
        self.write_but_fail = write_but_fail
        self.fetched: list[str] = []
        self.uploads: list[str] = []

    def fetch(self, rel, dest):
        self.fetched.append(rel)
        if self.raise_on_fetch:
            raise remote.RemoteError("remote down")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = self.files.get(rel)
        if data is not None:
            dest.write_bytes(data)
            return True
        if self.write_but_fail:
            dest.write_bytes(b"partial")  # a tmp left behind on failure
            return False
        return False

    def upload(self, local, rel):
        if self.fail is not None and rel == self.fail:
            raise remote.RemoteError("upload boom " + rel)
        self.uploads.append(rel)

    def list(self):
        return self.entries

    def delete(self, rel):
        pass

    def test(self):
        return "ok"


class FakeS3:
    """Just enough of the boto3 S3 client for S3Volume's uuid_naming paths."""

    def __init__(self):
        self.objects: dict[str, dict] = {}  # key -> {body, meta, lastmod}

    @staticmethod
    def _lm(ts: float) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)

    def upload_file(self, local, bucket, key, ExtraArgs=None):
        meta = dict((ExtraArgs or {}).get("Metadata") or {})
        self.objects[key] = {
            "body": Path(local).read_bytes(), "meta": meta,
            "lastmod": time.time(),
        }

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            e = Exception("not found")
            e.response = {"Error": {"Code": "404"}}
            raise e
        return {"Metadata": dict(self.objects[Key]["meta"])}

    def list_objects_v2(self, Bucket, Prefix="", Delimiter=None, **kw):
        contents, prefixes = [], set()
        for key, o in self.objects.items():
            if not key.startswith(Prefix):
                continue
            rest = key[len(Prefix):]
            if Delimiter and "/" in rest:
                prefixes.add(Prefix + rest.split("/", 1)[0] + "/")
                continue
            contents.append({"Key": key, "Size": len(o["body"]),
                             "LastModified": self._lm(o["lastmod"])})
        out: dict = {"Contents": contents, "IsTruncated": False}
        if prefixes:
            out["CommonPrefixes"] = [{"Prefix": p} for p in sorted(prefixes)]
        return out

    def download_file(self, bucket, key, dest):
        if key not in self.objects:
            raise Exception("no such key")
        Path(dest).write_bytes(self.objects[key]["body"])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def head_bucket(self, Bucket):
        return {}


def _s3vol(monkeypatch, tmp_path, uuid_naming=True, base=""):
    rc = RemoteVolumeConfig(
        type="s3", host="127.0.0.1", port=9100, user="minioadmin",
        password="minioadmin", bucket="fass-rt", path=base,
        uuid_naming=uuid_naming)
    vol = remote.S3Volume(rc)
    fake = FakeS3()
    monkeypatch.setattr(vol, "_client", lambda: fake)
    return vol, fake


@pytest.fixture(autouse=True)
def _clear_remote_input_cache():
    # ensure_input_local caches per-process — keep tests independent
    execute._REMOTE_INPUT_CACHE.clear()
    execute._REMOTE_NAME_LOCKS.clear()
    yield
    execute._REMOTE_INPUT_CACHE.clear()
    execute._REMOTE_NAME_LOCKS.clear()


# =========================================================== config model


def test_remote_config_defaults():
    rc = RemoteVolumeConfig()
    assert rc.type == "none"
    assert rc.host == ""
    assert rc.port is None
    assert rc.user == ""
    assert rc.password == ""
    assert rc.bucket == ""
    assert rc.path == ""
    assert rc.region == ""
    assert rc.secure is False
    assert rc.uuid_naming is False


def test_remote_config_roundtrip():
    rc = RemoteVolumeConfig(
        type="s3", host="minio", port=9100, user="u", password="p",
        bucket="b", path="in", region="us-west-2", secure=True,
        uuid_naming=True)
    rc2 = RemoteVolumeConfig.model_validate(rc.model_dump())
    assert rc2 == rc


def test_remote_config_extra_forbid():
    with pytest.raises(ValueError):
        RemoteVolumeConfig(type="none", bogus=1)


def test_remote_config_type_literal():
    with pytest.raises(ValueError):
        RemoteVolumeConfig(type="gs")


def test_remote_config_port_bounds():
    with pytest.raises(ValueError):
        RemoteVolumeConfig(type="ftp", host="h", port=0)
    with pytest.raises(ValueError):
        RemoteVolumeConfig(type="ftp", host="h", port=70000)
    assert RemoteVolumeConfig(type="ftp", host="h", port=21).port == 21


def test_volumes_config_has_three_remotes():
    v = VolumesConfig()
    assert v.input_remote.type == "none"
    assert v.output_remote.type == "none"
    assert v.log_remote.type == "none"


def test_fass_config_three_remotes_roundtrip():
    cfg = FassConfig()
    cfg.volumes.input_remote = RemoteVolumeConfig(
        type="ftp", host="h", user="u", password="p")
    cfg.volumes.output_remote = RemoteVolumeConfig(
        type="s3", bucket="b", host="m", port=9100, user="u", password="p",
        uuid_naming=True)
    cfg.volumes.log_remote = RemoteVolumeConfig(
        type="sftp", host="h", user="u", password="p", path="log")
    cfg2 = FassConfig.model_validate(cfg.model_dump())
    assert cfg2 == cfg


def test_legacy_config_loads_without_remote_keys():
    # a pre-M12 config.json (volumes are bare host paths) still loads,
    # defaulting every remote to 'none'
    cfg = FassConfig.model_validate(
        {"volumes": {"input": "/a", "output": "/b", "log": "/c"}})
    assert cfg.volumes.input == "/a"
    assert cfg.volumes.input_remote.type == "none"
    assert cfg.volumes.output_remote.type == "none"
    assert cfg.volumes.log_remote.type == "none"


# =========================================================== factory / dispatch


def test_make_volume_none_is_none():
    assert remote.make_volume(RemoteVolumeConfig()) is None


def test_make_volume_ftp():
    v = remote.make_volume(RemoteVolumeConfig(type="ftp", host="h",
                                              user="u", password="p"))
    assert isinstance(v, remote.FtpVolume)


def test_make_volume_ftp_needs_host():
    with pytest.raises(remote.RemoteError):
        remote.make_volume(RemoteVolumeConfig(type="ftp"))


def test_make_volume_sftp():
    v = remote.make_volume(RemoteVolumeConfig(type="sftp", host="h",
                                              user="u", password="p"))
    assert isinstance(v, remote.SftpVolume)


def test_make_volume_sftp_missing_paramiko(monkeypatch):
    monkeypatch.setitem(sys.modules, "paramiko", None)
    with pytest.raises(remote.RemoteError, match="paramiko"):
        remote.make_volume(RemoteVolumeConfig(type="sftp", host="h"))


def test_make_volume_s3():
    v = remote.make_volume(RemoteVolumeConfig(type="s3", bucket="b"))
    assert isinstance(v, remote.S3Volume)


def test_make_volume_s3_needs_bucket():
    with pytest.raises(remote.RemoteError):
        remote.make_volume(RemoteVolumeConfig(type="s3"))


def test_make_volume_s3_missing_boto3(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(remote.RemoteError, match="boto3"):
        remote.make_volume(RemoteVolumeConfig(type="s3", bucket="b"))


def test_make_volume_unknown_type():
    rc = RemoteVolumeConfig.model_construct(type="carrier-pigeon")
    with pytest.raises(remote.RemoteError, match="unknown remote type"):
        remote.make_volume(rc)


# =========================================================== remote_for (live read)


def test_remote_for_invalid_which():
    with pytest.raises(remote.RemoteError, match="unknown volume"):
        remote.remote_for("bogus")


def test_remote_for_unmapped_is_none(monkeypatch):
    cfg = FassConfig()
    monkeypatch.setattr(remote, "load_config", lambda: cfg)
    assert remote.remote_for("input") is None
    assert remote.remote_for("output") is None
    assert remote.remote_for("log") is None


def test_remote_for_live_read(monkeypatch):
    cfg = FassConfig()
    cfg.volumes.input_remote = RemoteVolumeConfig(
        type="ftp", host="h", user="u", password="p")
    monkeypatch.setattr(remote, "load_config", lambda: cfg)
    assert isinstance(remote.remote_for("input"), remote.FtpVolume)
    # only the mapped volume builds a backend
    assert remote.remote_for("output") is None
    assert remote.remote_for("log") is None


# =========================================================== ensure_input_local


def _set_input_dir(tmp_path, monkeypatch, sub="in"):
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FASS_INPUT_DIR", str(d))
    return d


def test_ensure_downloads_then_short_circuits(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol(files={"src.mp4": b"video"})
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    p = execute.ensure_input_local("src.mp4")
    assert p == d / "src.mp4"
    assert p.read_bytes() == b"video"
    # second call: the file is local now — no further fetch
    p2 = execute.ensure_input_local("src.mp4")
    assert p2 == p
    assert fake.fetched == ["src.mp4"]  # exactly one download


def test_ensure_local_first_no_fetch(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    (d / "local.mp4").write_bytes(b"here")
    fake = FakeVol()
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    assert execute.ensure_input_local("local.mp4") == d / "local.mp4"
    assert fake.fetched == []


def test_ensure_negative_cache_blocks_refetch(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol()  # nothing to fetch
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    assert execute.ensure_input_local("gone.mp4") is None
    assert fake.fetched == ["gone.mp4"]
    # still within the _NEG_TTL window: not re-fetched
    assert execute.ensure_input_local("gone.mp4") is None
    assert fake.fetched == ["gone.mp4"]


def test_ensure_negative_cache_expires(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol()
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    execute.ensure_input_local("gone.mp4")
    # age the negative entry past its TTL, then make the file appear
    execute._REMOTE_INPUT_CACHE["gone.mp4"] = time.monotonic() - 1
    fake.files["gone.mp4"] = b"now"
    p = execute.ensure_input_local("gone.mp4")
    assert p == d / "gone.mp4"
    assert p.read_bytes() == b"now"


def test_ensure_error_fail_open_and_retry(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol(raise_on_fetch=True)
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    assert execute.ensure_input_local("x.mp4") is None  # fails open
    # the short _ERR_TTL blocks an immediate retry
    n = len(fake.fetched)
    assert execute.ensure_input_local("x.mp4") is None
    assert len(fake.fetched) == n
    # but after the window a retry is allowed
    execute._REMOTE_INPUT_CACHE["x.mp4"] = time.monotonic() - 1
    assert execute.ensure_input_local("x.mp4") is None
    assert len(fake.fetched) == n + 1


def test_ensure_no_remote_is_noop(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(remote, "remote_for", lambda which: None)
    assert execute.ensure_input_local("src.mp4") is None


def test_ensure_no_input_dir_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_INPUT_DIR", "")
    # no /input on the test host either
    fake = FakeVol(files={"src.mp4": b"x"})
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    assert execute.ensure_input_local("src.mp4") is None
    assert fake.fetched == []


def test_ensure_cleans_tmp_on_partial_fetch(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol(write_but_fail=True)  # leaves a .downloading tmp, returns False
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    assert execute.ensure_input_local("part.mp4") is None
    assert not (d / "part.mp4").exists()
    assert not (d / "part.mp4.downloading").exists()


# =========================================================== resolution hooks


def test_resolve_input_name_remote_only(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol(files={"src.mp4": b"video"})
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    got = execute.resolve_input_name("src.mp4")
    assert got == str(d / "src.mp4")
    assert (d / "src.mp4").is_file()


def test_resolve_input_name_absent_unchanged(tmp_path, monkeypatch):
    _set_input_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(remote, "remote_for", lambda which: None)
    assert execute.resolve_input_name("nope.mp4") == "nope.mp4"


def test_resolve_paths_rewrites_remote_input(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("FASS_OUTPUT_DIR", str(out_dir))
    fake = FakeVol(files={"src.mp4": b"video"})
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    argv, notes = execute.resolve_paths(
        ["ffmpeg", "-i", "src.mp4", "-c", "copy", "out.mp4"])
    assert argv[2] == str(d / "src.mp4")
    assert any("src.mp4" in n and "/input volume" in n for n in notes)
    # the bare dest still resolves into the output volume
    assert argv[-1] == str(out_dir / "out.mp4")


def test_volume_file_downloads_remote_input(tmp_path, monkeypatch):
    d = _set_input_dir(tmp_path, monkeypatch)
    fake = FakeVol(files={"src.mp4": b"video"})
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    p = execute.volume_file("input", "src.mp4")
    assert p == d / "src.mp4"
    assert p.read_bytes() == b"video"


def test_volume_file_output_never_downloads(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("FASS_OUTPUT_DIR", str(out_dir))
    fake = FakeVol(files={"x.mp4": b"video"})
    monkeypatch.setattr(remote, "remote_for", lambda which: fake)
    assert execute.volume_file("output", "x.mp4") is None
    assert fake.fetched == []


# =========================================================== _sync_run_to_remote


def _make_run(tmp_path, monkeypatch, run_id, status, *,
              output=None, http=False, log_path=None,
              out_dir, logroot, run_sub="runs"):
    """Write a finalized sidecar + the artifact files it points at."""
    rd = logroot / run_sub
    rd.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FASS_RUN_DIR", str(rd))
    monkeypatch.setenv("FASS_OUTPUT_DIR", str(out_dir))
    rec = {
        "id": run_id, "argv": ["ffmpeg"], "command": "ffmpeg",
        "started": "2026-01-01T00:00:00", "status": status,
        "exit_code": 0 if status == "success" else 1, "output": output,
        "notes": [], "steps": None, "log_path": log_path,
        "verbose": False, "ladder": None, "http": http,
    }
    (rd / f"{run_id}.json").write_text(json.dumps(rec))
    (rd / f"{run_id}.log").write_text("run log line\n")
    if output and "/" not in output:
        f = out_dir / output
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"output-bytes")
    if log_path:
        lp = Path(log_path)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text("verbose log line\n")
    return rd


def _patch_remote_for(monkeypatch, **vols):
    def _rf(which):
        v = vols.get(which)
        if v is ...:  # a marker for "raise RemoteError"
            raise remote.RemoteError(f"mapping for {which} broken")
        return v
    monkeypatch.setattr(remote, "remote_for", _rf)


def test_sync_success_uploads_output_vlog_and_log(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-testaa"
    vlog = str(out_dir / "log" / f"{run_id}.log")
    _make_run(tmp_path, monkeypatch, run_id, "success",
              output="dest.mp4", log_path=vlog,
              out_dir=out_dir, logroot=logroot)
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol, log_vol = FakeVol(), FakeVol()
    _patch_remote_for(monkeypatch, output=out_vol, log=log_vol)
    execute._sync_run_to_remote(run_id)
    assert "dest.mp4" in out_vol.uploads
    assert f"log/{run_id}.log" in out_vol.uploads
    assert f"runs/{run_id}.log" in log_vol.uploads
    assert f"runs/{run_id}.json" in log_vol.uploads


def test_sync_error_skips_output_but_uploads_log(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-erraa"
    _make_run(tmp_path, monkeypatch, run_id, "error",
              output="dest.mp4", out_dir=out_dir, logroot=logroot)
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol, log_vol = FakeVol(), FakeVol()
    _patch_remote_for(monkeypatch, output=out_vol, log=log_vol)
    execute._sync_run_to_remote(run_id)
    assert out_vol.uploads == []  # success-only gate held
    assert f"runs/{run_id}.log" in log_vol.uploads
    assert f"runs/{run_id}.json" in log_vol.uploads


def test_sync_http_subtree_via_upload_tree(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-httpaa"
    _make_run(tmp_path, monkeypatch, run_id, "success", http=True,
              out_dir=out_dir, logroot=logroot)
    httpd = out_dir / "http" / run_id
    httpd.mkdir(parents=True)
    (httpd / "master.m3u8").write_text("#EXTM3U\n")
    (httpd / "seg0.ts").write_bytes(b"seg")
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol = FakeVol()
    _patch_remote_for(monkeypatch, output=out_vol)
    execute._sync_run_to_remote(run_id)
    assert f"http/{run_id}/master.m3u8" in out_vol.uploads
    assert f"http/{run_id}/seg0.ts" in out_vol.uploads


def test_sync_log_skipped_off_docker_layout(tmp_path, monkeypatch):
    # run_dir not under LOG_VOLUME_ROOT (a bare dev checkout): the log
    # volume has nothing to mirror, but the output volume still uploads
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    else_runs = tmp_path / "elsewhere" / "runs"
    run_id = "20260101-000000-offdock"
    rd = _make_run(tmp_path, monkeypatch, run_id, "success",
                   output="dest.mp4", out_dir=out_dir, logroot=logroot,
                   run_sub="runs")
    # move the run record OUTSIDE the log root so relative_to fails
    (else_runs).mkdir(parents=True, exist_ok=True)
    for f in rd.iterdir():
        f.rename(else_runs / f.name)
    monkeypatch.setenv("FASS_RUN_DIR", str(else_runs))
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol, log_vol = FakeVol(), FakeVol()
    _patch_remote_for(monkeypatch, output=out_vol, log=log_vol)
    execute._sync_run_to_remote(run_id)
    assert "dest.mp4" in out_vol.uploads
    assert log_vol.uploads == []


def test_sync_output_absolute_path_uploads_relative_name(tmp_path, monkeypatch):
    # a real run record stores the output as the RESOLVED absolute path
    # (resolve_paths rewrites argv before start_run records it) — the
    # mirror must still be uploaded under its volume-relative name
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-absout"
    dest = str(out_dir / "dest.mp4")
    _make_run(tmp_path, monkeypatch, run_id, "success",
              output=dest, out_dir=out_dir, logroot=logroot)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dest.mp4").write_bytes(b"output-bytes")  # helper skips "/"
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol, log_vol = FakeVol(), FakeVol()
    _patch_remote_for(monkeypatch, output=out_vol, log=log_vol)
    execute._sync_run_to_remote(run_id)
    assert "dest.mp4" in out_vol.uploads
    assert dest not in out_vol.uploads


def test_sync_verbose_log_captured_into_log_volume(tmp_path, monkeypatch):
    # a verbose run's log lives in the OUTPUT volume and run_dir holds no
    # log file — the log volume must still receive runs/<id>.log (from the
    # record's log_path), not only the .json record
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-vloglv"
    vlog = str(out_dir / "log" / f"{run_id}.log")
    rd = _make_run(tmp_path, monkeypatch, run_id, "success",
                   output="dest.mp4", log_path=vlog,
                   out_dir=out_dir, logroot=logroot)
    (rd / f"{run_id}.log").unlink()  # no fallback log in run_dir
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol, log_vol = FakeVol(), FakeVol()
    _patch_remote_for(monkeypatch, output=out_vol, log=log_vol)
    execute._sync_run_to_remote(run_id)
    assert f"log/{run_id}.log" in out_vol.uploads
    assert f"runs/{run_id}.log" in log_vol.uploads
    assert f"runs/{run_id}.json" in log_vol.uploads


def test_sync_output_failure_becomes_note(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-failaa"
    rd = _make_run(tmp_path, monkeypatch, run_id, "success",
                   output="dest.mp4", out_dir=out_dir, logroot=logroot)
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    out_vol = FakeVol(fail="dest.mp4")
    _patch_remote_for(monkeypatch, output=out_vol, log=FakeVol())
    execute._sync_run_to_remote(run_id)
    rec = json.loads((rd / f"{run_id}.json").read_text())
    assert any("remote output upload failed" in n for n in rec["notes"])


def test_sync_broken_mapping_becomes_note(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    run_id = "20260101-000000-broken"
    rd = _make_run(tmp_path, monkeypatch, run_id, "success",
                   output="dest.mp4", out_dir=out_dir, logroot=logroot)
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    _patch_remote_for(monkeypatch, output=..., log=None)
    execute._sync_run_to_remote(run_id)
    rec = json.loads((rd / f"{run_id}.json").read_text())
    assert any("remote output mapping unusable" in n for n in rec["notes"])


def test_sync_unknown_run_is_noop(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    logroot = tmp_path / "logroot"
    rd = logroot / "runs"
    rd.mkdir(parents=True)
    monkeypatch.setenv("FASS_RUN_DIR", str(rd))
    monkeypatch.setenv("FASS_OUTPUT_DIR", str(out_dir))
    monkeypatch.setattr(execute, "LOG_VOLUME_ROOT", str(logroot))
    _patch_remote_for(monkeypatch, output=FakeVol(), log=FakeVol())
    execute._sync_run_to_remote("does-not-exist")  # must not raise


# =========================================================== S3 uuid_naming


def test_s3_upload_keys_by_uuid_with_name_meta(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    local = tmp_path / "out.mp4"
    local.write_bytes(b"payload")
    vol.upload(local, "out.mp4")
    assert len(fake.objects) == 1
    key = next(iter(fake.objects))
    assert key != "out.mp4"
    assert remote._is_uuid(key)
    assert fake.objects[key]["meta"] == {remote.S3_NAME_META: "out.mp4"}


def test_s3_upload_keeps_a_uuid_name_as_is(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    uid = str(uuid.uuid4())
    local = tmp_path / (uid + ".bin")
    local.write_bytes(b"p")
    vol.upload(local, uid)
    key = next(iter(fake.objects))
    assert key == uid
    assert fake.objects[key]["meta"] == {remote.S3_NAME_META: uid}


def test_s3_upload_subtree_not_uuided(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    d = tmp_path / "http" / "r1"
    d.mkdir(parents=True)
    (d / "master.m3u8").write_text("#EXTM3U\n")
    (d / "seg0.ts").write_bytes(b"seg")
    n = vol.upload_tree(d, "http/r1")
    assert n == 2
    assert set(fake.objects) == {"http/r1/master.m3u8", "http/r1/seg0.ts"}
    # the playlist's segments keep their relative names (no metadata)
    assert fake.objects["http/r1/master.m3u8"]["meta"] == {}


def test_s3_fetch_by_human_name_resolves_uuid(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    uid = str(uuid.uuid4())
    fake.objects[uid] = {"body": b"video",
                         "meta": {remote.S3_NAME_META: "src.mp4"},
                         "lastmod": 100.0}
    dest = tmp_path / "got.mp4"
    assert vol.fetch("src.mp4", dest) is True
    assert dest.read_bytes() == b"video"


def test_s3_fetch_most_recent_wins(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    fake.objects[u1] = {"body": b"old",
                        "meta": {remote.S3_NAME_META: "src.mp4"}, "lastmod": 100.0}
    fake.objects[u2] = {"body": b"new",
                        "meta": {remote.S3_NAME_META: "src.mp4"}, "lastmod": 200.0}
    dest = tmp_path / "got.mp4"
    assert vol.fetch("src.mp4", dest) is True
    assert dest.read_bytes() == b"new"


def test_s3_fetch_by_uuid_direct(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    uid = str(uuid.uuid4())
    fake.objects[uid] = {"body": b"video",
                         "meta": {remote.S3_NAME_META: "src.mp4"},
                         "lastmod": 100.0}
    dest = tmp_path / "got.mp4"
    assert vol.fetch(uid, dest) is True
    assert dest.read_bytes() == b"video"


def test_s3_fetch_absent_is_false(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    dest = tmp_path / "got.mp4"
    assert vol.fetch("missing.mp4", dest) is False
    assert not dest.exists()


def test_s3_fetch_slashed_rel_keeps_key_in_uuid_mode(monkeypatch, tmp_path):
    # uuid_naming keys only bare top-level names: a slashed rel (the
    # verbose log log/<id>.log, the run log runs/<id>.log) is uploaded with
    # its relative key and must be fetchable by that same rel — no metadata
    # scan, which only sees top-level objects
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True, base="log")
    fake.objects["log/runs/r1.log"] = {"body": b"log", "meta": {},
                                       "lastmod": 100.0}
    dest = tmp_path / "got.log"
    assert vol.fetch("runs/r1.log", dest) is True
    assert dest.read_bytes() == b"log"


def test_s3_list_shows_human_names(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    fake.objects[u1] = {"body": b"a",
                        "meta": {remote.S3_NAME_META: "a.mp4"}, "lastmod": 100.0}
    fake.objects[u2] = {"body": b"b",
                        "meta": {remote.S3_NAME_META: "b.mp4"}, "lastmod": 200.0}
    names = {e["name"] for e in vol.list()}
    assert names == {"a.mp4", "b.mp4"}


def test_s3_list_subdir_entry_single_slash(monkeypatch, tmp_path):
    # S3 CommonPrefixes arrive already trailing-slashed ("output/log/") —
    # the listing must strip only the volume prefix, not append another '/'
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True, base="output")
    fake.objects["output/out.mp4"] = {"body": b"x", "meta": {},
                                      "lastmod": 100.0}
    fake.objects["output/log/r1.log"] = {"body": b"l", "meta": {},
                                         "lastmod": 100.0}
    names = {e["name"] for e in vol.list()}
    assert "out.mp4" in names
    assert "log/" in names
    assert "log//" not in names


def test_s3_delete_by_human_name(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    uid = str(uuid.uuid4())
    fake.objects[uid] = {"body": b"x",
                         "meta": {remote.S3_NAME_META: "src.mp4"},
                         "lastmod": 100.0}
    vol.delete("src.mp4")
    assert uid not in fake.objects


# ---------------------------- S3 without uuid_naming (plain keys)


def test_s3_plain_fetch_present(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=False, base="in")
    fake.objects["in/src.mp4"] = {"body": b"video", "meta": {}, "lastmod": 1.0}
    dest = tmp_path / "got.mp4"
    assert vol.fetch("src.mp4", dest) is True
    assert dest.read_bytes() == b"video"


def test_s3_plain_fetch_absent_is_false(monkeypatch, tmp_path):
    vol, fake = _s3vol(monkeypatch, tmp_path, uuid_naming=False, base="in")
    dest = tmp_path / "got.mp4"
    assert vol.fetch("nope.mp4", dest) is False


def test_s3_test_reports_ok(monkeypatch, tmp_path):
    vol, _fake = _s3vol(monkeypatch, tmp_path, uuid_naming=True)
    msg = vol.test()
    assert msg.startswith("ok: S3")
    assert "fass-rt" in msg


# =========================================================== API


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("FASS_CONFIG", str(p))
    monkeypatch.delenv("FASS_ALLOW_EXECUTE", raising=False)
    monkeypatch.delenv("FASS_DOCKER_MANAGE", raising=False)
    return p


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    for name, d in (("FASS_RUN_DIR", "runs"), ("FASS_INPUT_DIR", "in"),
                    ("FASS_OUTPUT_DIR", "out")):
        (tmp_path / d).mkdir(exist_ok=True)
        monkeypatch.setenv(name, str(tmp_path / d))
    return tmp_path


def test_files_merge_remote_entries(run_env, monkeypatch):
    in_dir = run_env / "in"
    (in_dir / "local.mp4").write_bytes(b"local")
    fake = FakeVol(entries=[
        {"name": "remote.mp4", "size": 10, "mtime": 500.0},
        {"name": "local.mp4", "size": 99, "mtime": 1.0},  # shadowed by local
        {"name": "http/", "size": 0, "mtime": None},      # a dir, skipped
    ])
    monkeypatch.setattr(remote, "remote_for",
                        lambda which: fake if which == "input" else None)
    r = client.get("/v1/files?dir=input")
    assert r.status_code == 200
    by_name = {f["name"]: f for f in r.json()["files"]}
    assert set(by_name) == {"local.mp4", "remote.mp4"}
    assert by_name["remote.mp4"].get("remote") is True
    assert by_name["local.mp4"].get("remote") is not True


def test_files_local_only_when_unmapped(run_env, monkeypatch):
    in_dir = run_env / "in"
    (in_dir / "local.mp4").write_bytes(b"local")
    monkeypatch.setattr(remote, "remote_for", lambda which: None)
    r = client.get("/v1/files?dir=input")
    names = {f["name"] for f in r.json()["files"]}
    assert names == {"local.mp4"}


def test_files_merge_fail_open_on_dead_remote(run_env, monkeypatch):
    in_dir = run_env / "in"
    (in_dir / "local.mp4").write_bytes(b"local")

    def _dead(which):
        raise remote.RemoteError("no route to store")

    monkeypatch.setattr(remote, "remote_for", _dead)
    r = client.get("/v1/files?dir=input")
    assert r.status_code == 200
    names = {f["name"] for f in r.json()["files"]}
    assert names == {"local.mp4"}


def test_remote_test_ok(run_env, monkeypatch):
    monkeypatch.setattr(remote, "make_volume",
                        lambda rc: FakeVol())
    r = client.post("/v1/config/remote-test", json={
        "which": "input",
        "remote": {"type": "ftp", "host": "127.0.0.1",
                   "user": "u", "password": "p"}})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["which"] == "input"
    assert d["type"] == "ftp"
    assert d["message"] == "ok"


def test_remote_test_none_reports_plain_volume(cfg_env):
    # no mapping saved anywhere -> reports 'none' without touching the network
    r = client.post("/v1/config/remote-test", json={"which": "output"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["type"] == "none"


def test_remote_test_none_falls_back_to_saved_mapping(cfg_env, monkeypatch):
    # a 'none' block tests the SAVED mapping of that volume
    p = cfg_env
    saved = FassConfig()
    saved.volumes.output_remote = RemoteVolumeConfig(
        type="ftp", host="h", user="u", password="p")
    p.write_text(saved.model_dump_json())
    monkeypatch.setattr(remote, "make_volume",
                        lambda rc: FakeVol())
    r = client.post("/v1/config/remote-test", json={"which": "output"})
    assert r.status_code == 200
    assert r.json()["type"] == "ftp"


def test_remote_test_broken_mapping_400(cfg_env, monkeypatch):
    def _bad(rc):
        raise remote.RemoteError("login failed")

    monkeypatch.setattr(remote, "make_volume", _bad)
    r = client.post("/v1/config/remote-test", json={
        "which": "input",
        "remote": {"type": "sftp", "host": "127.0.0.1",
                   "user": "u", "password": "p"}})
    assert r.status_code == 400
    assert "login failed" in r.json()["detail"]


def test_copy_to_input_mirrors_to_remote(run_env, monkeypatch):
    out_dir, in_dir = run_env / "out", run_env / "in"
    (out_dir / "clip.mp4").write_bytes(b"clip")
    fake = FakeVol()
    monkeypatch.setattr(remote, "remote_for",
                        lambda which: fake if which == "input" else None)
    r = client.post("/v1/files/copy-to-input", json={"name": "clip.mp4"})
    assert r.status_code == 200
    assert (in_dir / "clip.mp4").read_bytes() == b"clip"
    assert "clip.mp4" in fake.uploads  # mirrored to the input remote


def test_copy_to_input_dead_remote_still_ok(run_env, monkeypatch):
    out_dir, in_dir = run_env / "out", run_env / "in"
    (out_dir / "clip.mp4").write_bytes(b"clip")

    def _dead(which):
        raise remote.RemoteError("no route")

    monkeypatch.setattr(remote, "remote_for", _dead)
    r = client.post("/v1/files/copy-to-input", json={"name": "clip.mp4"})
    assert r.status_code == 200
    assert (in_dir / "clip.mp4").exists()


def test_copy_to_input_404_when_absent(run_env, monkeypatch):
    monkeypatch.setattr(remote, "remote_for", lambda which: None)
    r = client.post("/v1/files/copy-to-input", json={"name": "ghost.mp4"})
    assert r.status_code == 404


def test_ftp_list_filters_dots_and_swallows_probe_errors(monkeypatch):
    # pure-ftpd's NLST includes '.' and '..' — list() must not surface them.
    # And a bare `ftplib.all_errors` (a LIST, not a class) in an except clause
    # raises TypeError the moment an exception reaches that clause — the
    # probes (SIZE/MDTM) failing on dot entries is exactly that path, so the
    # `(*ftplib.all_errors, ...)` unpacking must be valid.
    import ftplib

    class _FakeFtp:
        def nlst(self):
            return [".", "..", "src.mp4"]

        def size(self, name):
            raise ftplib.error_perm("550")

        def sendcmd(self, cmd):
            raise ftplib.error_perm("502")

        def quit(self):
            pass

    # RFC 959 quoting: plain names pass through, whitespace names are
    # double-quoted (Python 3's ftplib ships no quotefn).
    assert remote._q("src.mp4") == "src.mp4"
    assert remote._q("/in/clip a.mp4") == '"/in/clip a.mp4"'

    vol = remote.FtpVolume(RemoteVolumeConfig(
        type="ftp", host="h", user="u", password="p", path="input"))
    monkeypatch.setattr(vol, "_connect", lambda: _FakeFtp())
    out = vol.list()
    assert [e["name"] for e in out] == ["src.mp4"]
    assert out[0]["size"] == 0
    assert out[0]["mtime"] is None


def test_ftp_ensure_dir_uses_real_ftplib_api(monkeypatch):
    # The fake implements ONLY the real ftplib.FTP method surface
    # (mkd — not mkdir/makedir; no quotefn). A call to a wrong method
    # name raises AttributeError and fails the test — this is what
    # live pure-ftpd testing caught (ftp.mkdir, ftp.quotefn).
    import ftplib

    class _FakeFtp:
        def __init__(self):
            self.dirs = {"/"}

        def cwd(self, p):
            if p not in self.dirs:
                raise ftplib.error_perm("550 no such directory")
            self.cur = p

        def mkd(self, p):
            self.dirs.add(p)

    vol = remote.FtpVolume(RemoteVolumeConfig(
        type="ftp", host="h", user="u", password="p", path="a/b"))
    fake = _FakeFtp()
    vol._ensure_dir(fake, "/a/b")
    assert fake.dirs == {"/", "/a", "/a/b"}


class _FakeSftpFs:
    """In-memory remote filesystem for SftpVolume tests."""

    HOME = "/home/u"

    def __init__(self):
        self.files = {}
        self.dirs = {"/", self.HOME}

    def normalize(self, p):
        return self.HOME if p == "." else p

    def stat(self, p):
        if p in self.files or p in self.dirs:
            return object()
        raise OSError(2, "no such file", p)

    def mkdir(self, p):
        self.dirs.add(p)

    def listdir(self, where):
        if where not in self.dirs:
            raise OSError(2, "no such file", where)
        return [os.path.basename(p) for p in self.files
                if os.path.dirname(p) == where]

    def listdir_attr(self, where):
        if where not in self.dirs:
            raise OSError(2, "no such file", where)
        return [
            type("A", (), {"filename": os.path.basename(p),
                           "st_size": len(self.files[p]), "st_mtime": 1})()
            for p in self.files if os.path.dirname(p) == where
        ]

    def get(self, rem, loc):
        if rem not in self.files:
            raise OSError(2, "no such file", rem)
        with open(loc, "wb") as f:
            f.write(self.files[rem])

    def put(self, loc, rem):
        with open(loc, "rb") as f:
            self.files[rem] = f.read()

    def remove(self, p):
        if p not in self.files:
            raise OSError(2, "no such file", p)
        del self.files[p]


def _fake_paramiko(fs):
    """A stand-in paramiko module whose SSHClient() serves `fs`."""
    import types

    class _SSHClient:
        def set_missing_host_key_policy(self, pol):
            pass

        def connect(self, *a, **k):
            pass

        def open_sftp(self):
            return fs

        def close(self):
            pass

    mod = types.ModuleType("paramiko")
    mod.SSHClient = _SSHClient
    mod.AutoAddPolicy = object
    return mod


def test_sftp_relative_base_resolves_against_remote_home(monkeypatch, tmp_path):
    # A relative volume path must resolve against the remote home (the
    # sftp client's initial cwd), not the filesystem root — a live
    # host-sshd run 500'd on root-absolute /fass-remote/....
    fs = _FakeSftpFs()
    monkeypatch.setitem(sys.modules, "paramiko", _fake_paramiko(fs))
    vol = remote.SftpVolume(RemoteVolumeConfig(
        type="sftp", host="h", user="u", password="p",
        path="fass-remote/output"))
    local = tmp_path / "out1.mp4"
    local.write_bytes(b"media")

    vol.upload(local, "out1.mp4")
    assert fs.files == {"/home/u/fass-remote/output/out1.mp4": b"media"}

    entries = vol.list()
    assert [e["name"] for e in entries] == ["out1.mp4"]
    assert entries[0]["size"] == 5

    assert "/home/u/fass-remote/output" in vol.test()

    dest = tmp_path / "back.bin"
    assert vol.fetch("out1.mp4", dest) is True
    assert dest.read_bytes() == b"media"
    assert vol.fetch("nope.mp4", dest) is False

    vol.delete("out1.mp4")
    assert fs.files == {}
    vol.delete("out1.mp4")  # absent: idempotent by contract


def test_sftp_absolute_base_used_as_is(monkeypatch, tmp_path):
    # An absolute path keeps its leading slash and is NOT home-prefixed
    # (_norm_prefix strips slashes, so the flag must be captured first).
    fs = _FakeSftpFs()
    monkeypatch.setitem(sys.modules, "paramiko", _fake_paramiko(fs))
    vol = remote.SftpVolume(RemoteVolumeConfig(
        type="sftp", host="h", user="u", password="p", path="/data/vol"))
    assert vol._base_abs is True
    local = tmp_path / "f.bin"
    local.write_bytes(b"x")

    vol.upload(local, "f.bin")
    assert list(fs.files) == ["/data/vol/f.bin"]
    assert vol._home == ""
