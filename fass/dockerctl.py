"""Docker Engine control (M6) — the config tab's Apply button.

The swap is orchestrated by the **fass-worker sidecar** (same image,
`fass worker` command), not by the fass container itself: the process that
answers the UI's Apply request runs inside the container being swapped,
and nothing inside a container survives the container's own stop — a
self-swap would always be killed mid-operation (Docker SIGKILLs the
graceful-shutdown deadlock and the create step never runs). The worker is
a separate container with the same engine socket; it stops fass, creates
and starts the replacement, and records the outcome in the shared
/config volume (last-apply.json), which the UI reads once fass is back.

The worker talks to the Engine API directly (unix socket, JSON-RPC) — no
docker CLI, no SDK. Recreate order is stop-the-old-first: two containers
cannot publish the same host port, so the replacement can only come up
after the old one is stopped and removed (a service gap of a few
seconds). If the replacement cannot start, the old container is
re-created from the spec captured before the swap (best effort).

Without the worker, PUT /v1/config still persists and the UI shows the
generated compose to apply by hand — the feature degrades, never fails
the save.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.parse

MANAGE_ENV = "FASS_DOCKER_MANAGE"
SOCKET_ENV = "FASS_DOCKER_SOCKET"
NAME_ENV = "FASS_CONTAINER_NAME"
WORKER_ENV = "FASS_DOCKER_WORKER"  # "host:port" of the fass-worker sidecar
APPLY_STATE_ENV = "FASS_APPLY_STATE"

WEB_PORT = "8000"


class DockerError(Exception):
    pass


def socket_path() -> str:
    return os.environ.get(SOCKET_ENV, "/var/run/docker.sock")


def container_name() -> str:
    return os.environ.get(NAME_ENV, "fass")


def manage_enabled() -> bool:
    return (
        os.environ.get(MANAGE_ENV, "") not in ("", "0", "false", "no")
        and os.path.exists(socket_path())
    )


def worker_address() -> str | None:
    """'host:port' of the fass-worker sidecar, or None when unset."""
    addr = os.environ.get(WORKER_ENV, "").strip()
    return addr if addr and ":" in addr else None


# ------------------------------------------------------- apply-state file

def apply_state_path() -> str:
    env = os.environ.get(APPLY_STATE_ENV, "")
    if env:
        return env
    if os.path.isdir("/config"):
        return "/config/last-apply.json"
    return os.path.join(os.getcwd(), ".fass-apply.json")


def write_apply_state(**kw) -> None:
    """Merge kw into the apply-state file (atomic; keeps 'started')."""
    path = apply_state_path()
    try:
        try:
            with open(path, encoding="utf-8") as f:
                state = json.loads(f.read() or "{}")
        except (OSError, json.JSONDecodeError):
            state = {}
        state.update(kw)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=1) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass  # the state file is a convenience, never a failure source


def read_apply_state() -> dict | None:
    try:
        with open(apply_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _call(method: str, path: str, body: dict | None = None,
          timeout: float = 30.0) -> dict | list | None:
    try:
        conn = http.client.HTTPConnection("localhost", timeout=timeout)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path())
        conn.sock = s
        headers = {"content-type": "application/json"}
        conn.request(method, path,
                     body=json.dumps(body) if body is not None else None,
                     headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
    except (OSError, socket.timeout) as e:
        raise DockerError(f"docker engine unreachable: {e}") from e
    if resp.status >= 300:
        raise DockerError(f"docker API {method} {path} -> {resp.status}: {data[:400]}")
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def engine_version() -> dict:
    return _call("GET", "/version") or {}


def find_container() -> dict:
    """Our container's full config (GET /containers/{id}/json)."""
    flt = urllib.parse.quote(json.dumps({"name": [container_name()]}))
    rows = _call("GET", f"/containers/json?all=1&filters={flt}") or []
    for row in rows:
        if row.get("Names") == ["/" + container_name()]:
            return _call("GET", f"/containers/{row['Id']}/json")
    raise DockerError(
        f"container '{container_name()}' not found — start it with the "
        "compose file first, then Apply from the config tab"
    )


def _config_binds(cur: dict) -> str:
    """The host source currently bound to /config (must exist to recreate:
    that volume is where this config.json lives)."""
    binds = cur.get("HostConfig", {}).get("Binds") or []
    for b in binds:
        # bind form is src:dest[:mode] — the mode is optional
        parts = b.split(":")
        if len(parts) >= 2 and parts[1] == "/config":
            return parts[0]
    raise DockerError(
        "the running container has no /config bind — mount the config "
        "folder and restart before using Apply"
    )


def _host_port_bindings(cfg) -> dict:
    pb = {f"{WEB_PORT}/tcp": [{"HostPort": "8000"}]}
    for mp in cfg.streaming.port_mappings:
        key = f"{mp.container_port}/{mp.proto}"
        pb[key] = [{"HostPort": str(mp.host_port)}]
    return pb


def _unmanaged_binds(binds: list[str]) -> list[str]:
    """Binds whose destination is not one of the managed volumes — the
    docker socket and any user-added mounts, carried over to the
    replacement (regression: the first Apply dropped the socket)."""
    managed_dests = {"/input", "/output", "/log", "/config"}
    return [b for b in binds
            if not (len(b.split(":")) >= 2 and b.split(":")[1] in managed_dests)]


def _binds(cfg, config_src: str) -> list[str]:
    vols = cfg.volumes
    missing = [name for name, p in (("input", vols.input),
                                    ("output", vols.output),
                                    ("log", vols.log)) if not p.startswith("/")]
    if missing:
        raise DockerError(
            "volume host folders must be absolute paths: " + ", ".join(missing)
        )
    return [f"{vols.input}:/input", f"{vols.output}:/output",
            f"{vols.log}:/log", f"{config_src}:/config"]


def _device_requests(cfg) -> list[dict] | None:
    if not cfg.gpu.enabled:
        return None
    if cfg.gpu.device == "all":
        return [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}]
    return [{
        "Driver": "nvidia",
        "DeviceIds": cfg.gpu.device.split(","),
        "Capabilities": [["gpu"]],
    }]


def recreate(cfg) -> dict:
    """Stop and remove the current container, then create+start a
    replacement from the config. If the replacement cannot come up, the
    old container is re-created from the spec captured before the swap
    (best effort) and a DockerError explains what failed."""
    cur = find_container()
    config = cur.get("Config") or {}
    host = cur.get("HostConfig") or {}
    image = config.get("Image")
    if not image:
        raise DockerError("could not determine the container image")
    cur_env = config.get("Env") or []
    if not any(e.startswith("FASS_CONFIG=") for e in cur_env):
        cur_env.append("FASS_CONFIG=/config/config.json")

    # spec of the old container, for rollback
    old_spec = {
        "Image": image,
        "Env": cur_env,
        "WorkingDir": config.get("WorkingDir") or "",
        "Labels": config.get("Labels") or {},
        "ExposedPorts": config.get("ExposedPorts") or {f"{WEB_PORT}/tcp": {}},
        "HostConfig": {
            "Binds": host.get("Binds"),
            "PortBindings": host.get("PortBindings"),
            "NetworkMode": host.get("NetworkMode"),
            "RestartPolicy": host.get("RestartPolicy"),
            "DeviceRequests": host.get("DeviceRequests"),
        },
        "Networks": list(((cur.get("NetworkSettings") or {})
                          .get("Networks") or {}).keys()),
    }

    # keep unmanaged binds (e.g. the docker socket) so the replacement
    # matches what compose actually created — the four managed volumes
    # come from the config, everything else is carried over as-is
    extra_binds = _unmanaged_binds(host.get("Binds") or [])
    body = {
        "Image": image,
        "Name": container_name(),
        "Env": cur_env,
        "WorkingDir": old_spec["WorkingDir"],
        "Labels": old_spec["Labels"],
        "ExposedPorts": old_spec["ExposedPorts"],
        "HostConfig": {
            "Binds": _binds(cfg, _config_binds(cur)) + extra_binds,
            "PortBindings": _host_port_bindings(cfg),
            "RestartPolicy": (old_spec["HostConfig"]["RestartPolicy"]
                              or {"Name": "unless-stopped"}),
        },
    }
    # keep the container on its current network (compose projects use a
    # dedicated bridge, not the default one)
    nm = old_spec["HostConfig"]["NetworkMode"] or ""
    if old_spec["Networks"] and nm in ("", "default", "bridge"):
        body["NetworkingConfig"] = {"EndpointsConfig": {n: {}
                                                        for n in old_spec["Networks"]}}
    else:
        body["HostConfig"]["NetworkMode"] = nm or "bridge"
    devices = _device_requests(cfg)
    if devices:
        body["HostConfig"]["DeviceRequests"] = devices

    old_id = cur.get("Id")
    try:
        _call("POST", f"/containers/{old_id}/stop", timeout=45)
    except DockerError:
        pass
    _safe_delete(old_id)

    try:
        created = _call("POST", f"/containers/create?name={container_name()}", body)
        new_id = (created or {}).get("Id")
        if not new_id:
            raise DockerError(f"container create returned no id: {created}")
        _call("POST", f"/containers/{new_id}/start")
        # promote only when the engine reports the new container running
        deadline = time.time() + 60
        running = False
        while time.time() < deadline:
            state = (_call("GET", f"/containers/{new_id}/json") or {}).get("State") or {}
            if state.get("Running"):
                running = True
                break
            if state.get("ExitCode") not in (None, 0):
                raise DockerError(
                    f"replacement container exited with code {state.get('ExitCode')}"
                )
            time.sleep(1)
        if not running:
            raise DockerError("replacement container did not start within 60 s")
    except DockerError as e:
        restored = _restore(old_spec)
        suffix = "" if restored else " and the rollback restore FAILED too"
        raise DockerError(f"{e} — the old container was re-created{suffix}") from e
    return {
        "applied": True,
        "new_container": new_id[:12],
        "old_container": (old_id or "")[:12],
        "ports": {k: v[0]["HostPort"]
                  for k, v in body["HostConfig"]["PortBindings"].items()},
        "volumes": body["HostConfig"]["Binds"],
        "gpu": bool(devices),
    }


def _restore(spec: dict) -> bool:
    """Re-create the old container from its captured spec (best effort)."""
    body = {
        "Name": container_name(),
        "Image": spec["Image"],
        "Env": spec["Env"],
        "WorkingDir": spec["WorkingDir"],
        "Labels": spec["Labels"],
        "ExposedPorts": spec["ExposedPorts"],
        "HostConfig": dict(spec["HostConfig"]),
    }
    nm = spec["HostConfig"].get("NetworkMode") or ""
    if spec["Networks"] and nm in ("", "default", "bridge"):
        body["NetworkingConfig"] = {"EndpointsConfig": {n: {}
                                                        for n in spec["Networks"]}}
    try:
        created = _call("POST", f"/containers/create?name={container_name()}", body)
        _call("POST", f"/containers/{(created or {}).get('Id')}/start")
        return True
    except DockerError:
        return False


def _safe_delete(cid: str) -> None:
    try:
        _call("DELETE", f"/containers/{cid}")
    except DockerError:
        pass


def compose_preview(cfg) -> str:
    """The compose file equivalent of the current config — what Apply
    would create, shown in the config tab (and usable by hand on the
    host: docker compose -f ... up -d)."""
    vols = cfg.volumes
    lines = [
        "services:",
        "  fass:",
        '    image: fass:latest',
        "    container_name: fass",
        "    ports:",
        '      - "8000:8000"',
    ]
    for mp in cfg.streaming.port_mappings:
        label = f"  # {mp.name}" if mp.name else ""
        lines.append(f'      - "{mp.host_port}:{mp.container_port}/{mp.proto}"{label}')
    lines += [
        "    volumes:",
        f"      - {vols.input or '/host/path/input'}:/input",
        f"      - {vols.output or '/host/path/output'}:/output",
        f"      - {vols.log or '/host/path/log'}:/log",
        "      - ./config:/config",
    ]
    if cfg.gpu.enabled:
        lines += [
            "    deploy:",
            "      resources:",
            "        reservations:",
            "          devices:",
            "            - driver: nvidia",
            f"              capabilities: [gpu]",
        ]
        if cfg.gpu.device != "all":
            ids = ", ".join(f'"{d}"' for d in cfg.gpu.device.split(","))
            lines.append(f"              device_ids: [{ids}]")
    lines += [
        "    restart: unless-stopped",
        "    environment:",
        '      - FASS_ALLOW_EXECUTE=1',
        "      - FASS_CONFIG=/config/config.json",
        "      - FASS_RUN_DIR=/log/runs",
    ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ worker app

def worker_app():
    """The fass-worker sidecar: one endpoint (POST /swap) that recreates
    the fass container from the config the UI saved. The swap runs in a
    background thread so the 202 ack reaches the fass container BEFORE
    the worker stops it — fass then returns that ack to the UI, and the
    UI polls /v1/config/apply-result (this sidecar's state file) until
    fass is back."""
    from fastapi import FastAPI, HTTPException

    from fass.config import FassConfig

    app = FastAPI(title="fass-worker")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/swap")
    def swap(body: dict):
        try:
            cfg = FassConfig.model_validate(body.get("config") or {})
        except Exception as e:
            raise HTTPException(422, f"invalid config: {e}") from e
        if not manage_enabled():
            raise HTTPException(
                409, "worker has no docker control "
                     f"({MANAGE_ENV} + socket required)"
            )
        write_apply_state(status="recreating",
                          started=time.strftime("%Y-%m-%dT%H:%M:%S"))
        t = threading.Thread(target=_swap, args=(cfg,), daemon=True)
        t.start()
        return {"status": "recreating"}

    def _swap(cfg) -> None:
        try:
            result = recreate(cfg)
            write_apply_state(status="applied", **result)
        except DockerError as e:
            write_apply_state(status="failed", error=str(e))
        except Exception as e:  # defensive: state file must say what happened
            write_apply_state(status="failed", error=f"{type(e).__name__}: {e}")

    return app
