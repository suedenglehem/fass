"""Remote volume mapping (M12, Config -> Volumes -> *_remote).

A volume (/input, /output, /log) can be associated with a remote store —
FTP, SFTP, or an S3-compatible bucket (MinIO/LocalStack/AWS). The mapping
decides the direction:

- /input mapped  -> referenced files are DOWNLOADED from the remote before
  they are probed/planned/run (fail-open: a dead remote never blocks a run
  whose file is present locally);
- /output/log mapped -> created files are UPLOADED after the run.

Design notes:
- Every operation opens a fresh connection (stateless client, thread-safe
  for the watcher threads in execute.py).
- ftp uses stdlib ftplib with EXPLICIT passive mode (works behind NAT;
  the industry default for server-side clients); sftp uses paramiko;
  s3 uses boto3 with path-style addressing whenever a custom endpoint
  host is set (MinIO requirement) — host left blank means real AWS.
- boto3/paramiko are imported lazily inside their backends so the core
  package (and ftp, which is stdlib) works without the [storage] extra.
- fetch() returns False for "not present on the remote" (a normal state,
  the file may be local-only) and raises RemoteError for transport or
  credential problems; callers treat those as notes, not failures.
"""

from __future__ import annotations

import datetime as _dt
import ftplib
import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from fass.config import RemoteVolumeConfig, load_config

# Connect/IO timeout for FTP and SFTP (seconds). Remote stores are local
# infrastructure in the supported layouts, so a tight timeout keeps a
# dead endpoint from stalling probes or runs for minutes.
CONNECT_TIMEOUT = 15


class RemoteError(Exception):
    """Transport, credential, or configuration problem talking to the store."""


class RemoteVolume(ABC):
    """One remote store. All paths are RELATIVE to the volume root
    (RemoteVolumeConfig.path)."""

    @abstractmethod
    def fetch(self, rel: str, dest: Path) -> bool:
        """Download `rel` to local `dest`, streamed. Returns False when the
        remote object does not exist (normal — the file may be local-only).
        Raises RemoteError on transport/credential problems."""

    @abstractmethod
    def upload(self, local: Path, rel: str) -> None:
        """Upload local file to `rel` (creating intermediate dirs)."""

    def upload_tree(self, local_dir: Path, rel_prefix: str) -> int:
        """Recursively upload a directory tree under `rel_prefix`.
        Returns the number of files uploaded; missing dir -> 0."""
        root = Path(local_dir)
        if not root.is_dir():
            return 0
        prefix = str(rel_prefix).strip("/").strip(".")
        n = 0
        for dirpath, _dirnames, filenames in os.walk(root):
            base = Path(dirpath)
            for name in sorted(filenames):
                f = base / name
                rel = f.relative_to(root).as_posix()
                if prefix:
                    rel = f"{prefix}/{rel}"
                self.upload(f, rel)
                n += 1
        return n

    @abstractmethod
    def list(self) -> list[dict]:
        """Top-level entries of the volume root: [{name, size, mtime}]
        (mtime epoch seconds or None). Directories are included."""

    @abstractmethod
    def delete(self, rel: str) -> None:
        """Delete the object at `rel` (no error when absent)."""

    @abstractmethod
    def test(self) -> str:
        """Connect and prove the mapping works; returns a short human
        summary, raises RemoteError otherwise."""


def _norm_prefix(path: str) -> str:
    """'./a/b/' -> 'a/b' (the volume-root prefix, no slashes around)."""
    return (path or "").strip("/").strip(".")


# ---------------------------------------------------------------- FTP


def _q(name: str) -> str:
    """Quote a path for an FTP command argument. RFC 959 names may contain
    spaces; the convention (and Python 2 ftplib's old quotefn) is to wrap
    them in double quotes. ftplib in Python 3 ships no quoting helper."""
    if any(c.isspace() for c in name):
        return '"' + name + '"'
    return name


class FtpVolume(RemoteVolume):
    """ftp:// via stdlib ftplib, explicit passive mode, plain credentials."""

    def __init__(self, rc: RemoteVolumeConfig) -> None:
        if not rc.host:
            raise RemoteError("ftp mapping needs a host")
        self.host = rc.host
        self.port = rc.port or 21
        self.user = rc.user
        self.password = rc.password
        self.base = _norm_prefix(rc.path)

    def _connect(self) -> ftplib.FTP:
        try:
            ftp = ftplib.FTP()
            ftp.connect(self.host, self.port, timeout=CONNECT_TIMEOUT)
            ftp.login(self.user, self.password)
            ftp.set_pasv(True)  # explicit passive: works through NAT/firewalls
        except (*ftplib.all_errors, OSError) as e:
            raise RemoteError(f"FTP {self.host}:{self.port}: {e}") from e
        if self.base:
            self._ensure_dir(ftp, "/" + self.base)
            ftp.cwd("/" + self.base)
        return ftp

    def _ensure_dir(self, ftp: ftplib.FTP, remote_dir: str) -> None:
        """Walk the path components, creating missing dirs ('already exists'
        is fine — other clients may have made them)."""
        parts = [p for p in remote_dir.split("/") if p]
        cur = ""
        for p in parts:
            cur += "/" + p
            try:
                ftp.cwd(cur)
                continue
            except ftplib.error_perm:
                pass
            try:
                ftp.mkd(cur)
            except ftplib.error_perm as e:
                if "exists" not in str(e).lower():
                    raise
            ftp.cwd(cur)

    def _full(self, rel: str) -> str:
        return "/" + "/".join(filter(None, [self.base, rel]))

    def _full_dir(self, rel: str) -> str:
        parent = os.path.dirname(rel.replace("\\", "/"))
        return self._full(parent) if parent else "/" + (self.base or "")

    def fetch(self, rel: str, dest: Path) -> bool:
        ftp = self._connect()
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(dest, "wb") as f:
                    ftp.retrbinary("RETR " + _q(self._full(rel)), f.write)
                return True
            except ftplib.error_perm:
                return False  # 550 — not present on the remote
        finally:
            try:
                ftp.quit()
            except Exception:
                pass

    def upload(self, local: Path, rel: str) -> None:
        ftp = self._connect()
        try:
            parent = self._full_dir(rel)
            if parent:
                self._ensure_dir(ftp, parent)
            with open(local, "rb") as f:
                ftp.storbinary("STOR " + _q(self._full(rel)), f)
        except (*ftplib.all_errors, OSError) as e:
            raise RemoteError(f"FTP upload {rel}: {e}") from e
        finally:
            try:
                ftp.quit()
            except Exception:
                pass

    def list(self) -> list[dict]:
        ftp = self._connect()
        try:
            try:
                names = ftp.nlst()
            except ftplib.error_perm:
                return []  # volume root not created yet
            out = []
            for name in names:
                if name in (".", ".."):
                    continue  # some servers (pure-ftpd) list these; not real
                size, mtime = 0, None
                try:
                    size = ftp.size(name) or 0
                except (*ftplib.all_errors, OSError):
                    pass
                try:
                    resp = ftp.sendcmd("MDTM " + _q(name))
                    mtime = int(_dt.datetime.strptime(
                        resp[4:15], "%Y%m%d%H%M%S").timestamp())
                except (*ftplib.all_errors, OSError, ValueError):
                    pass
                out.append({"name": name, "size": size, "mtime": mtime})
            return out
        finally:
            try:
                ftp.quit()
            except Exception:
                pass

    def delete(self, rel: str) -> None:
        ftp = self._connect()
        try:
            try:
                ftp.delete(self._full(rel))  # file
            except ftplib.error_perm:
                try:
                    ftp.rmd(self._full(rel))  # directory (must be empty)
                except ftplib.error_perm:
                    pass  # absent or non-empty: delete is idempotent by contract
        finally:
            try:
                ftp.quit()
            except Exception:
                pass

    def test(self) -> str:
        ftp = self._connect()
        try:
            n = len(ftp.nlst())
        except ftplib.error_perm:
            n = -1
        where = f"/{self.base}" if self.base else "/"
        if n >= 0:
            n = max(0, n - 2)  # pure-ftpd lists '.' and '..'
        if n < 0:
            raise RemoteError(
                f"FTP {self.host}:{self.port} login ok but directory {where} "
                "does not exist")
        return f"ok: FTP {self.host}:{self.port} as {self.user}, " \
               f"{n} entries in {where}"


# ---------------------------------------------------------------- SFTP


class SftpVolume(RemoteVolume):
    """sftp:// via paramiko. AutoAddPolicy: this is a dev tool that talks to
    the user's own servers (same convention as the LLM host fields); the
    UI notes host keys are not pinned."""

    def __init__(self, rc: RemoteVolumeConfig) -> None:
        import paramiko  # lazy: [storage] extra
        if not rc.host:
            raise RemoteError("sftp mapping needs a host")
        self._paramiko = paramiko
        self.host = rc.host
        self.port = rc.port or 22
        self.user = rc.user
        self.password = rc.password
        self.base = _norm_prefix(rc.path)
        self._base_abs = os.path.isabs((rc.path or "").strip())
        self._home = ""  # remote home, resolved on connect for relative bases

    def _connect(self):
        from paramiko import AutoAddPolicy, SSHClient
        try:
            client = SSHClient()
            client.set_missing_host_key_policy(AutoAddPolicy())
            client.connect(
                self.host, port=self.port, username=self.user,
                password=self.password, timeout=CONNECT_TIMEOUT,
                allow_agent=False, look_for_keys=False,
                banner_timeout=CONNECT_TIMEOUT, auth_timeout=CONNECT_TIMEOUT,
            )
        except Exception as e:  # paramiko raises assorted socket/SSH types
            raise RemoteError(f"SFTP {self.host}:{self.port}: {e}") from e
        try:
            sftp = client.open_sftp()
            if not self._base_abs:
                # relative (or empty) paths resolve against the remote
                # home, the same way the sftp client's initial cwd does
                self._home = sftp.normalize(".")
            if self.base:
                self._ensure_dir(sftp, self._full_dir(""))
        except Exception as e:
            client.close()
            raise RemoteError(
                f"SFTP directory {self._full_dir('')}: {e}") from e
        return client

    def _ensure_dir(self, sftp, remote_dir: str) -> None:
        parts = [p for p in remote_dir.split("/") if p]
        cur = ""
        for p in parts:
            cur += "/" + p
            try:
                sftp.stat(cur)
                continue
            except OSError:
                pass
            try:
                sftp.mkdir(cur)
            except OSError:
                pass  # exists (concurrent creation) — stat again to verify
                sftp.stat(cur)

    def _full(self, rel: str) -> str:
        if self._base_abs:
            return "/" + "/".join(filter(None, [self.base, rel]))
        return "/".join(filter(None, [self._home, self.base, rel]))

    def _full_dir(self, rel: str) -> str:
        parent = os.path.dirname(rel.replace("\\", "/"))
        return self._full(parent)

    def fetch(self, rel: str, dest: Path) -> bool:
        client = self._connect()
        try:
            sftp = client.open_sftp()
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                sftp.stat(self._full(rel))
            except OSError:
                return False  # not present on the remote
            sftp.get(self._full(rel), str(dest))
            return True
        except RemoteError:
            raise
        except Exception as e:
            raise RemoteError(f"SFTP fetch {rel}: {e}") from e
        finally:
            client.close()

    def upload(self, local: Path, rel: str) -> None:
        client = self._connect()
        try:
            sftp = client.open_sftp()
            parent = self._full_dir(rel)
            if parent:
                self._ensure_dir(sftp, parent)
            sftp.put(str(local), self._full(rel))
        except RemoteError:
            raise
        except Exception as e:
            raise RemoteError(f"SFTP upload {rel}: {e}") from e
        finally:
            client.close()

    def list(self) -> list[dict]:
        client = self._connect()
        try:
            sftp = client.open_sftp()
            where = self._full_dir("") or "/"
            try:
                attrs = sftp.listdir_attr(where)
            except OSError:
                return []  # volume root not created yet
            return [
                {"name": a.filename,
                 "size": a.st_size or 0,
                 "mtime": int(a.st_mtime) if a.st_mtime else None}
                for a in attrs
            ]
        except RemoteError:
            raise
        except Exception as e:
            raise RemoteError(f"SFTP list: {e}") from e
        finally:
            client.close()

    def delete(self, rel: str) -> None:
        client = self._connect()
        try:
            sftp = client.open_sftp()
            try:
                sftp.remove(self._full(rel))
            except OSError:
                pass  # absent: idempotent by contract
        finally:
            client.close()

    def test(self) -> str:
        client = self._connect()
        try:
            sftp = client.open_sftp()
            where = self._full_dir("") or "/"
            try:
                n = len(sftp.listdir(where))
            except OSError:
                raise RemoteError(
                    f"SFTP {self.host}:{self.port} auth ok but directory "
                    f"{where} does not exist")
        finally:
            client.close()
        return f"ok: SFTP {self.host}:{self.port} as {self.user}, " \
               f"{n} entries in {where}"


# ---------------------------------------------------------------- S3

# S3 user-metadata key carrying the original (human) basename when
# uuid_naming is on — boto3 prefixes it x-amz-meta- on the wire.
S3_NAME_META = "fass_name"


def _is_uuid(name: str) -> bool:
    """True when `name` parses as a UUID (canonical form, braces optional)."""
    try:
        uuid.UUID(name)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class S3Volume(RemoteVolume):
    """S3-compatible bucket via boto3.

    host set   -> custom endpoint (MinIO/LocalStack): path-style addressing,
                  scheme from `secure` (http for on-host MinIO/LocalStack),
                  keys from user/password.
    host blank -> real AWS: region + keys from the config (blank keys fall
                  through to the ambient boto3 credential chain).

    uuid_naming (S3 only): objects are keyed by UUID and the real basename
    is kept in the object's S3 metadata (S3_NAME_META). Input fetch
    resolves a human name to its uuid by scanning that metadata (a name
    that is itself a UUID is used directly); output upload stores the file
    as a fresh uuid (the name as-is when it already is one) with the
    basename in the metadata. Multi-file uploads (the http/ stream subtree)
    keep their relative names — only bare top-level names are uuid'd, or
    the playlists would stop resolving their segments.
    """

    def __init__(self, rc: RemoteVolumeConfig) -> None:
        import boto3
        import botocore.config
        if not rc.bucket:
            raise RemoteError("s3 mapping needs a bucket")
        self._boto3 = boto3
        self._boto_cfg = botocore.config
        self.bucket = rc.bucket
        self.base = _norm_prefix(rc.path)
        self._host = rc.host
        self._port = rc.port
        self._secure = rc.secure
        self._user = rc.user
        self._password = rc.password
        self._region = rc.region or "us-east-1"
        self._uuid = rc.uuid_naming

    def _client(self):
        try:
            if self._host:
                scheme = "https" if self._secure else "http"
                port = f":{self._port}" if self._port else ""
                endpoint = f"{scheme}://{self._host}{port}"
                return self._boto3.client(
                    "s3",
                    endpoint_url=endpoint,
                    aws_access_key_id=self._user or None,
                    aws_secret_access_key=self._password or None,
                    region_name=self._region,
                    config=self._boto_cfg.Config(
                        s3={"addressing_style": "path"},
                        connect_timeout=CONNECT_TIMEOUT,
                    ),
                )
            kwargs: dict = {"region_name": self._region}
            if self._user:
                kwargs["aws_access_key_id"] = self._user
            if self._password:
                kwargs["aws_secret_access_key"] = self._password
            return self._boto3.client("s3", **kwargs)
        except RemoteError:
            raise
        except Exception as e:
            raise RemoteError(f"S3 client init: {e}") from e

    def _key(self, rel: str) -> str:
        return f"{self.base}/{rel}" if self.base else rel

    def _parent_key(self, rel: str) -> str:
        parent = os.path.dirname(rel.replace("\\", "/"))
        if not parent:
            return self.base
        return f"{self.base}/{parent}" if self.base else parent

    def _head_meta(self, s3, key: str) -> dict:
        """The object's user metadata, {} when it cannot be read."""
        try:
            r = s3.head_object(Bucket=self.bucket, Key=key)
            return (r or {}).get("Metadata") or {}
        except Exception:
            return {}

    def _resolve_input_key(self, s3, rel: str) -> str | None:
        """uuid_naming: the object key behind a local (human) name. A name
        that is itself a UUID is the key; otherwise scan the volume's
        objects for one whose stored basename (S3 metadata) matches — most
        recent wins (re-uploads create new uuid objects)."""
        if _is_uuid(rel):
            return self._key(rel)
        prefix = self.base + "/" if self.base else ""
        cands: list[tuple] = []
        token = ""
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "Delimiter": "/"}
            if token:
                kw["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                name = os.path.basename(
                    (self._head_meta(s3, key) or {}).get(S3_NAME_META, ""))
                if name and name == os.path.basename(rel):
                    cands.append((obj.get("LastModified"), key))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken") or ""
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0] is not None, c[0] or 0), reverse=True)
        return cands[0][1]

    def fetch(self, rel: str, dest: Path) -> bool:
        s3 = self._client()
        rel = rel.replace("\\", "/")
        if self._uuid and "/" not in rel:
            # only bare top-level names are uuid'd (same guard as
            # upload/delete) — a slashed rel (log/<id>.log, runs/<id>.log,
            # the http/ subtree) keeps its relative key
            key = self._resolve_input_key(s3, rel)
            if key is None:
                return False  # no object carries this name
        else:
            key = self._key(rel)
        try:
            s3.head_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            resp = getattr(e, "response", None)
            code = resp.get("Error", {}).get("Code") if isinstance(resp, dict) \
                else None
            if code in ("404", "NoSuchKey", "NotFound"):
                return False  # not present on the remote
            raise RemoteError(f"S3 head {key}: {e}") from e
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            s3.download_file(self.bucket, key, str(dest))
            return True
        except Exception as e:
            raise RemoteError(f"S3 download {key}: {e}") from e

    def upload(self, local: Path, rel: str) -> None:
        s3 = self._client()
        rel = rel.replace("\\", "/")
        if self._uuid and "/" not in rel:
            # top-level deliverable: store it as a UUID and keep the human
            # basename in the object metadata (recovered on the input side)
            name = os.path.basename(rel)
            uid = name if _is_uuid(name) else str(uuid.uuid4())
            key = f"{self.base}/{uid}" if self.base else uid
            extra = {"Metadata": {S3_NAME_META: name}}
        else:
            key = self._key(rel)
            extra = None
        try:
            if extra is not None:
                s3.upload_file(str(local), self.bucket, key,
                               ExtraArgs=extra)
            else:
                s3.upload_file(str(local), self.bucket, key)
        except Exception as e:
            raise RemoteError(f"S3 upload {rel}: {e}") from e

    def list(self) -> list[dict]:
        s3 = self._client()
        prefix = self.base + "/" if self.base else ""
        try:
            resp = s3.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, Delimiter="/")
        except Exception as e:
            raise RemoteError(f"S3 list {self.bucket}: {e}") from e
        out = []
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            name = key[len(prefix):] if prefix and key.startswith(prefix) \
                else key
            if self._uuid:
                # uuid_naming: the key is an opaque uuid — show the real
                # basename from the object metadata instead
                real = (self._head_meta(s3, key) or {}).get(S3_NAME_META)
                if real:
                    name = os.path.basename(real)
            out.append({
                "name": name,
                "size": obj.get("Size", 0),
                "mtime": int(obj["LastModified"].timestamp())
                if obj.get("LastModified") else None,
            })
        for cp in resp.get("CommonPrefixes", []):
            p = cp["Prefix"]  # already ends in '/' — strip only the prefix
            out.append({"name": p[len(prefix):] if prefix
                        and p.startswith(prefix) else p,
                        "size": 0, "mtime": None})
        return out

    def delete(self, rel: str) -> None:
        s3 = self._client()
        key = self._key(rel)
        if self._uuid and "/" not in rel and not _is_uuid(rel):
            resolved = self._resolve_input_key(s3, rel)
            if resolved is not None:
                key = resolved
        try:
            s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass  # absent: idempotent by contract

    def test(self) -> str:
        s3 = self._client()
        try:
            s3.head_bucket(Bucket=self.bucket)
        except Exception as e:
            raise RemoteError(f"S3 bucket {self.bucket}: {e}") from e
        entries = []
        try:
            entries = s3.list_objects_v2(
                Bucket=self.bucket, Prefix=self.base + "/" if self.base else "",
                Delimiter="/")
            n = len(entries.get("Contents", [])) + \
                len(entries.get("CommonPrefixes", []))
        except Exception:
            n = -1
        where = f"{self.base}/" if self.base else ""
        if self._host:
            scheme = "https" if self._secure else "http"
            port = f":{self._port}" if self._port else ""
            return f"ok: S3 {scheme}://{self._host}{port} bucket " \
                   f"{self.bucket}/{where} ({n} entries)"
        return f"ok: S3 AWS bucket {self.bucket}/{where} " \
               f"region {self._region} ({n} entries)"


# ---------------------------------------------------------------- factory


def make_volume(rc: RemoteVolumeConfig) -> RemoteVolume | None:
    """Build the backend for a config block; None for type 'none'."""
    if rc.type == "none":
        return None
    if rc.type == "ftp":
        return FtpVolume(rc)
    if rc.type == "sftp":
        try:
            import paramiko  # noqa: F401  (presence check, lazy backend)
        except ImportError as e:
            raise RemoteError(
                "sftp mapping needs paramiko — install the [storage] extra "
                "(pip install 'fass[storage]')") from e
        return SftpVolume(rc)
    if rc.type == "s3":
        try:
            import boto3  # noqa: F401
        except ImportError as e:
            raise RemoteError(
                "s3 mapping needs boto3 — install the [storage] extra "
                "(pip install 'fass[storage]')") from e
        return S3Volume(rc)
    raise RemoteError(f"unknown remote type: {rc.type!r}")


def remote_for(which: str) -> RemoteVolume | None:
    """LIVE-read the mapping for one of 'input'/'output'/'log' from the
    current config.json and build its backend (or None when unmapped).
    Called per access — config edits take effect without Apply."""
    which = (which or "").strip().lower()
    if which not in ("input", "output", "log"):
        raise RemoteError(f"unknown volume: {which!r}")
    cfg = load_config()
    rc = getattr(cfg.volumes, f"{which}_remote")
    if rc.type == "none":
        return None
    return make_volume(rc)
