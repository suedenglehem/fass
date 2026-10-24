"""FastAPI layer over the same generate() the CLI uses (plan0: the web
interface comes after the CLI path works end to end).

PRD §9 API contract: POST /v1/commands takes an instruction, an asset map of
local file references, an output filename, a target shell, an optional
previous plan, and optional pre-collected media metadata; it returns the
structured response (status, plan, argv, rendered command, explanation,
defaults, warnings, validation results, clarifications).

Security invariants (PRD §10), all enforced by design here:
- the service NEVER executes generated commands by default — it returns
  argv, period. Execution (M6, docker deployment) is an explicit opt-in
  per deployment via FASS_ALLOW_EXECUTE, runs the argv array with the
  shell disabled, and validates every flag against the builder's
  vocabulary before a process is spawned;
- assets are local paths bound by the client; the model never sees paths;
- no media upload endpoints; nothing leaves the machine;
- target shell is POSIX only — other shells are deferred and a non-POSIX
  value is a validation error, not a best-effort render.

Run:  fass serve [--host 0.0.0.0] [--port 8000]
     (or: uvicorn fass.api:app)
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from fass import dockerctl, execute
from fass.config import FassConfig, LLMConfig, config_path, load_config, save_config
from fass.pipeline import generate
from fass.probe.ffprobe import ffmpeg_available, parse_ffprobe_json, probe_ffmpeg
from fass.registry import REGISTRY
from fass.schema import Asset, AssetKind, EditPlan, guess_kind
from fass.nlu.rules import SUPPORTED_SUMMARY
from fass.ui import UI_HTML


class AssetRef(BaseModel):
    """An asset with an explicit kind (M3b): needed for URL inputs whose
    extension does not reveal the type (e.g. an extensionless stream)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    kind: AssetKind | None = None


class CommandRequest(BaseModel):
    """POST /v1/commands body. Malformed bodies → 422 (FastAPI validation)."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=4000)
    assets: dict[str, str | AssetRef] = Field(
        description="asset id -> local path or stream URL "
                    "(http(s)://, rtmp(s)://, rtsp(s)://, srt://, udp://, rtp://, tcp://); "
                    "a value may be a plain string or {path, kind}; "
                    "must include 'main' (the input video)"
    )
    output_filename: str | None = Field(
        default=None, max_length=1024,
        description="output filename, or a stream URL to send the output to "
                    "(rtmp://, rtmps://, rtsp://, rtsps://, srt://, udp://, rtp://, tcp://)"
    )
    target_shell: Literal["posix"] = "posix"
    previous_plan: EditPlan | None = None
    media: dict | None = Field(
        default=None, description="pre-collected ffprobe JSON for the input"
    )
    adapter: Literal["auto", "rules", "llm"] = "auto"
    preflight: bool = False


class RunRequest(BaseModel):
    """POST /v1/runs body (M6): the argv ARRAY from a ready command —
    never a shell string."""

    model_config = ConfigDict(extra="forbid")

    arguments: list[str] = Field(min_length=2, max_length=256)
    timeout: float | None = Field(
        default=None, gt=0, le=86400,
        description="optional wall-clock limit in seconds (file outputs "
                    "finish on their own; streams run until stopped)",
    )


class LLMTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_unsaved: bool = False
    llm: LLMConfig | None = None


def _llm_status() -> dict:
    """Docker-control availability for the config tab. 'worker' (not
    'manage') is what gates the Apply button: the sidecar does the swap."""
    if not dockerctl.manage_enabled():
        return {"manage": False, "worker": bool(dockerctl.worker_address()),
                "version": None, "container": None,
                "error": "docker socket not mounted / FASS_DOCKER_MANAGE not set"}
    try:
        ver = dockerctl.engine_version()
    except dockerctl.DockerError as e:
        return {"manage": False, "worker": bool(dockerctl.worker_address()),
                "version": None, "container": None, "error": str(e)}
    container = None
    try:
        container = dockerctl.find_container().get("Id", "")[:12]
    except dockerctl.DockerError:
        pass
    return {"manage": True, "worker": bool(dockerctl.worker_address()),
            "version": ver.get("Version"), "container": container, "error": None}


def create_app() -> FastAPI:
    app = FastAPI(
        title="fass",
        version="1.0",
        description="Natural language -> validated FFmpeg commands "
                    "(argv first; execution is an explicit per-deployment opt-in).",
    )

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/v1/commands")
    def create_command(body: CommandRequest):
        if "main" not in body.assets:
            raise HTTPException(422, "assets must include 'main' (the input video)")
        try:
            assets = {}
            for aid, ref in body.assets.items():
                path = ref.path if isinstance(ref, AssetRef) else ref
                kind = (ref.kind if isinstance(ref, AssetRef) else None) or guess_kind(path)
                assets[aid] = Asset(path=path, kind=kind)
        except ValueError as e:  # pydantic ValidationError is a ValueError
            raise HTTPException(422, f"invalid asset reference: {e}")

        media = parse_ffprobe_json(body.media) if body.media else None
        resp = generate(
            body.instruction,
            assets,
            output_filename=body.output_filename,
            previous_plan=body.previous_plan,
            media=media,
            adapter_name=body.adapter,
            preflight_check=body.preflight,
        )
        # Every domain outcome (ready / needs_clarification / unsupported /
        # error) is a structured 200; 4xx is reserved for malformed requests.
        return resp.to_dict()

    @app.get("/v1/support")
    def support():
        return {
            "summary": SUPPORTED_SUMMARY,
            "registry_version": REGISTRY["registry_version"],
            "video_codecs": list(REGISTRY["video_codecs"]),
            "audio_codecs": list(REGISTRY["audio_codecs"]),
            "gpu": {"enabled": load_config().gpu.enabled,
                    "coders": {k: v["encoder"] for k, v in REGISTRY["gpu"]["coders"].items()}},
        }

    @app.get("/v1/env")
    def env():
        if not ffmpeg_available():
            return {"available": False, "note": "ffmpeg not found on PATH",
                    "execution": execute.execution_enabled()}
        ff = probe_ffmpeg()
        encoders = {}
        for name, spec in REGISTRY["video_codecs"].items():
            encoders[f"video:{name}"] = {
                "encoder": spec["encoder"], "present": spec["encoder"] in ff.encoders,
            }
        for name, coder in REGISTRY["gpu"]["coders"].items():
            encoders[f"video:{name}_gpu"] = {
                "encoder": coder["encoder"], "present": coder["encoder"] in ff.encoders,
            }
        for name, spec in REGISTRY["audio_codecs"].items():
            encoders[f"audio:{name}"] = {
                "encoder": spec["encoder"], "present": spec["encoder"] in ff.encoders,
            }
        return {
            "available": True,
            "build": ff.version,
            "registry_version": REGISTRY["registry_version"],
            "encoders": encoders,
            "filters": {n: n in ff.filters for n in ("scale", "pad", "hue", "overlay", "format")},
            "execution": execute.execution_enabled(),
            "gpu": load_config().gpu.model_dump(),
        }

    # ------------------------------------------------------- config (M6)

    @app.get("/v1/config")
    def get_config():
        cfg = load_config()
        return {
            "config": cfg.model_dump(),
            "path": str(config_path()),
            "compose": dockerctl.compose_preview(cfg),
            "docker": _llm_status(),
        }

    @app.put("/v1/config")
    def put_config(body: FassConfig):
        path = save_config(body)
        return {
            "saved": str(path),
            "config": body.model_dump(),
            "compose": dockerctl.compose_preview(body),
            "note": "LLM and stream-address changes are live immediately; "
                    "volume/port/GPU changes need Apply (container recreate)",
        }

    @app.post("/v1/config/apply")
    def apply_config():
        """Ask the fass-worker sidecar to recreate this container from the
        saved config. The worker stops THIS container to run the swap, so
        the UI should expect the connection to drop after the ack and
        poll /v1/config/apply-result until fass is back."""
        worker = dockerctl.worker_address()
        if not worker:
            raise HTTPException(
                409,
                "Apply needs the fass-worker sidecar (FASS_DOCKER_WORKER) — "
                "add the fass-worker service from docker/compose.yaml, or "
                "apply the compose preview on the host (docker compose up -d)",
            )
        import json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"http://{worker}/swap",
            data=json.dumps({"config": load_config().model_dump()}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode()).get("detail", str(e))
            except Exception:
                detail = str(e)
            raise HTTPException(e.code, f"fass-worker: {detail}")
        except (urllib.error.URLError, OSError) as e:
            raise HTTPException(
                502, f"fass-worker at {worker} is not reachable: {e}"
            )

    @app.get("/v1/config/apply-result")
    def apply_result():
        state = dockerctl.read_apply_state()
        if state is None:
            raise HTTPException(404, "no apply has been recorded yet")
        return state

    @app.post("/v1/config/llm-test")
    def llm_test(body: LLMTestRequest | None = None):
        from fass.nlu.llm import LLMAdapter

        body = body or LLMTestRequest()
        llm = body.llm if (body.use_unsaved and body.llm) else load_config().llm
        if llm.provider == "openai":
            adapter = LLMAdapter(
                api_key=llm.api_key or None, model=llm.model or None,
                provider="openai", host=llm.host or None, port=llm.port or None,
            )
        else:
            adapter = LLMAdapter(api_key=llm.api_key or None, model=llm.model or None)
        if not adapter.available():
            raise HTTPException(
                422,
                "LLM not configured" if llm.provider == "anthropic"
                else "OpenAI-compatible LLM needs host, port and model",
            )
        try:
            answer, seconds = adapter.test()
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "model": adapter.model, "seconds": seconds,
                "answer": answer}

    # -------------------------------------------------------- runs (M6)

    @app.post("/v1/runs")
    def start_run(body: RunRequest):
        try:
            run = execute.start_run(body.arguments, timeout=body.timeout)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"run": run.to_dict()}

    @app.get("/v1/runs")
    def list_runs():
        return {"runs": execute.list_runs()}

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str):
        run = execute.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/v1/runs/{run_id}/log", response_class=PlainTextResponse)
    def get_run_log(run_id: str):
        log = execute.log_tail(run_id)
        if log is None:
            raise HTTPException(404, "run not found")
        return log

    @app.post("/v1/runs/{run_id}/stop")
    def stop_run(run_id: str):
        return {"stopped": execute.stop_run(run_id)}

    @app.get("/media/output/{filename}")
    def media_output(filename: str):
        path = execute.output_file(filename)
        if path is None:
            raise HTTPException(404, "file not found in the /output volume")
        return FileResponse(path, filename=path.name)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return UI_HTML

    return app


app = create_app()
