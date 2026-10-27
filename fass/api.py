"""FastAPI layer over the same generate() the CLI uses (web interface
after the CLI path works end to end).

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

import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fass import dockerctl, execute
from fass.config import (
    FassConfig,
    LLMConfig,
    config_path,
    load_config,
    resolved_rag,
    save_config,
)
from fass.pipeline import generate
from fass.probe.ffprobe import (
    ffmpeg_available,
    ffprobe_available,
    parse_ffprobe_json,
    probe_ffmpeg,
)
from fass.rag.client import search as rag_search
from fass.registry import (
    REGISTRY,
    all_filter_names,
    format_option_specs,
    filter_names,
    input_schemes,
    output_schemes,
    sample_rates,
    speed_limits,
)
from fass.schema import Asset, AssetKind, EditPlan, guess_kind
from fass.nlu.rules import SUPPORTED_SUMMARY
from fass.ui import UI_HTML


class AssetRef(BaseModel):
    """An asset with an explicit kind (M3b): needed for URL inputs whose
    extension does not reveal the type (e.g. an extensionless stream)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    kind: AssetKind | None = None


def _strip_i_prefix(path: str) -> str:
    """Input convention (web UI): `-i <file>` names a file in the input
    folder — the `-i ` prefix is accepted everywhere a plain filename is
    (bare names resolve against $FASS_INPUT_DIR / /input at probe and run
    time). Streams and source specs never start with `-i `, so the
    stripping is pass-through for them."""
    parts = path.split(None, 1)
    if parts[0] == "-i" and len(parts) > 1:
        return parts[1].strip()
    return path


class CommandRequest(BaseModel):
    """POST /v1/commands body. Malformed bodies → 422 (FastAPI validation)."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=4000)
    assets: dict[str, str | AssetRef] = Field(
        description="asset id -> local path or stream URL "
                    "(http(s)://, rtmp(s)://, rtsp(s)://, srt://, udp://, rtp://, tcp://); "
                    "a value may be a plain string or {path, kind}; an input may be "
                    "written `-i <file>` (the web UI convention) — <file> is a name in "
                    "the input folder ($FASS_INPUT_DIR / /input), or a bare name that "
                    "resolves to a previous run's result in the output folder "
                    "($FASS_OUTPUT_DIR / /output); "
                    "must include 'main' (the input video). For a quality comparison "
                    "(vmaf/psnr/ssim), 'main' is the DISTORTED clip and the reference "
                    "is another video asset (the web UI does this remap automatically: "
                    "reference in the Input field, distorted in the Output field, or "
                    "as the first additional input when Output is empty)"
    )
    output_filename: str | None = Field(
        default=None, max_length=1024,
        description="output filename, or a stream URL to send the output to "
                    "(rtmp://, rtmps://, rtsp://, rtsps://, srt://, udp://, rtp://, "
                    "tcp://, http://, https:// — http(s) derive the container from "
                    "the URL path extension, defaulting to mpegts); a name "
                    "starting with '/input/' is written into the input folder "
                    "instead of the output folder, so a later command can "
                    "reference it by bare name (chained ffmpeg processings). "
                    "Leave unset for a quality comparison — nothing is written, "
                    "the metric score is printed in the run log"
    )
    target_shell: Literal["posix"] = "posix"
    previous_plan: EditPlan | None = None
    media: dict | None = Field(
        default=None, description="pre-collected ffprobe JSON for the input"
    )
    adapter: Literal["auto", "rules", "llm"] = "auto"
    preflight: bool = False
    verbose: bool = Field(
        default=False,
        description="verbose log mode: every pass runs with -loglevel verbose "
                    "and the run log is written to the output folder's log/ "
                    "directory (browsable at GET /v1/logs)",
    )


class RunRequest(BaseModel):
    """POST /v1/runs body (M6): the argv ARRAY from a ready command —
    never a shell string. M10.5: a multi-pass plan posts its steps
    (every pass's argv, in execution order, the final pass last) and
    arguments stays the FINAL step's argv."""

    model_config = ConfigDict(extra="forbid")

    arguments: list[str] = Field(min_length=2, max_length=256)
    steps: list[list[str]] = Field(
        default_factory=list, max_length=8,
        description="optional: every argv of a multi-pass plan, in "
                    "execution order (the final pass last) — the run "
                    "executes them sequentially in one working directory",
    )
    timeout: float | None = Field(
        default=None, gt=0, le=86400,
        description="optional wall-clock limit in seconds (file outputs "
                    "finish on their own; streams run until stopped; "
                    "with steps the limit applies per step)",
    )
    verbose: bool = Field(
        default=False,
        description="write this run's log to the output folder's log/ "
                    "directory (the command was generated with verbose=true)",
    )

    @model_validator(mode="after")
    def _steps_consistent(self) -> "RunRequest":
        for i, step in enumerate(self.steps, 1):
            if len(step) < 2:
                raise ValueError(f"steps[{i - 1}] needs at least two elements")
        if self.steps and self.steps[-1] != self.arguments:
            raise ValueError(
                "arguments must be the final step's argv "
                "(steps[-1] == arguments)")
        return self


class LLMTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_unsaved: bool = False
    llm: LLMConfig | None = None


class RagQueryRequest(BaseModel):
    """POST /v1/rag/query body (M11.2, PRD §9): a direct evidence lookup
    for the RAG tab. A blank (or whitespace-only) query is a 422 — the
    tab must not fire a search that can only return noise."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1,
                              description="chunks requested for this query "
                                          "(the service caps at 8, PRD §5.4)")

    @model_validator(mode="after")
    def _query_not_blank(self) -> "RagQueryRequest":
        if not self.query.strip():
            raise ValueError("query must not be whitespace-only")
        return self


class FrameRequest(BaseModel):
    """POST /v1/files/frame body (Files tab): the first frame of one file of
    a deployment volume, as a 360-px-high JPEG. ``file`` is a bare basename
    of ``dir`` — the same stance as /v1/mediainfo and the /media routes."""

    model_config = ConfigDict(extra="forbid")

    dir: Literal["input", "output"] = "input"
    file: str = Field(min_length=1, max_length=1024)


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
                path = _strip_i_prefix(ref.path if isinstance(ref, AssetRef) else ref)
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
            verbose=body.verbose,
        )
        # Every domain outcome (ready / needs_clarification / unsupported /
        # error) is a structured 200; 4xx is reserved for malformed requests.
        return resp.to_dict()

    @app.get("/v1/support")
    def support():
        # The full M7 capability surface (registry-driven): output modes,
        # the container/codec matrix, subtitle dump formats, the filter
        # allowlist, per-container format options, stream schemes, pixel
        # formats, profiles, presets, sample rates and speed limits.
        matrix = REGISTRY["matrix"]
        speed_min, speed_max = speed_limits()
        return {
            "summary": SUPPORTED_SUMMARY,
            "registry_version": REGISTRY["registry_version"],
            "modes": ["video", "audio", "subtitles"],
            "video_codecs": list(REGISTRY["video_codecs"]),
            "audio_codecs": list(REGISTRY["audio_codecs"]),
            "matrix": {
                container: {
                    "video": list(row["video"]),
                    "audio": list(row["audio"]),
                    "default_video": row["default_video"],
                    "audio_codec": row["audio_codec"],
                }
                for container, row in matrix.items()
            },
            "subtitle_dumps": dict(REGISTRY["subtitles"]["dump_codecs"]),
            "filters": {
                "video": sorted(filter_names("video")),
                "audio": sorted(filter_names("audio")),
                "filtergraph": True,
                # M10: the full filter table — per-option kinds/ranges/choices
                # plus the category flags — the same table the loader and the
                # argv validator enforce (mirrors the bitstream_filters block)
                "table": {
                    name: {
                        "domain": domain,
                        "diagnostic": bool(spec.get("diagnostic")),
                        "multi_input": spec.get("multi_input"),
                        "needs_reference": bool(spec.get("needs_reference")),
                        "gpu": bool(spec.get("gpu")),
                        "min_version": spec.get("min_version"),
                        "options": {
                            key: {
                                "kind": opt.get("kind"),
                                "values": opt.get("values"),
                                "range": opt.get("range"),
                            }
                            for key, opt in spec.get("options", {}).items()
                        },
                        "description": spec.get("description"),
                    }
                    for domain in ("video", "audio")
                    for name, spec in REGISTRY["filters"][domain].items()
                },
            },
            "format_options": {
                container: sorted(format_option_specs(container))
                for container in matrix
            },
            "input_schemes": input_schemes(),
            "output_schemes": output_schemes(),
            "pixel_formats": {
                k: list(v) for k, v in REGISTRY["pixel_formats"].items()
                if k != "description"
            },
            "video_profiles": {
                k: list(v) for k, v in REGISTRY["video_profiles"].items()
                if k != "description"
            },
            "video_presets": {
                k: list(v) for k, v in REGISTRY["video_presets"].items()
                if k != "description"
            },
            "sample_rates": sample_rates(),
            "speed": {"min": speed_min, "max": speed_max},
            "gpu": {"enabled": load_config().gpu.enabled,
                    "coders": {k: v["encoder"] for k, v in REGISTRY["gpu"]["coders"].items()}},
            "qsv": {"coders": {k: v["encoder"] for k, v in REGISTRY["qsv"]["coders"].items()}},
            # M8: the private codec options each encoder accepts
            # (-crf, -preset, -dialnorm, …) — the same table the NLU and
            # the argv validator enforce
            "codec_options": {
                enc: sorted(opts)
                for enc, opts in REGISTRY["codec_options"].items()
                if enc != "description" and isinstance(opts, dict)
            },
            # bitstream filters addressable by name (-bsf:v/-bsf:a), with
            # their options, codec constraints and min_version — the same
            # table the NLU and the argv validator enforce
            "bitstream_filters": {
                name: {
                    "kind": spec.get("kind"),
                    "codecs": spec.get("codecs"),
                    "min_version": spec.get("min_version"),
                    "diagnostic": bool(spec.get("diagnostic")),
                    "copy_cut": bool(spec.get("copy_cut")),
                    "options": sorted(spec["options"]) if spec.get("options") else [],
                    "description": spec.get("description"),
                }
                for name, spec in REGISTRY["bitstream_filters"]["specs"].items()
                if name != "description"
            },
        }

    @app.get("/v1/rag")
    def rag_status():
        """M11.2: read-only RAG state for the config tab — the resolved
        client config plus the service's corpus list. The service probe
        is best-effort with a short timeout: a dead fass-rag must render
        as a status line, never break the endpoint (RAG is fail-open)."""
        cfg = resolved_rag()
        service: dict | None = None
        if cfg.enabled:
            import urllib.request

            req = urllib.request.Request(cfg.url.rstrip("/") + "/v1/corpora")
            if cfg.auth_token:
                req.add_header("Authorization", f"Bearer {cfg.auth_token}")
            try:
                with urllib.request.urlopen(req, timeout=2.0) as r:
                    service = {"ok": True, **json.loads(r.read().decode())}
            except Exception as e:  # noqa: BLE001 — informational only
                reason = getattr(e, "reason", None)
                if isinstance(reason, BaseException):
                    reason = (getattr(reason, "strerror", None)
                              or type(reason).__name__)
                service = {"ok": False,
                           "error": str(reason or e) or type(e).__name__}
        return {"enabled": cfg.enabled, "config": cfg.model_dump(),
                "service": service}

    @app.post("/v1/rag/query")
    def rag_query(body: RagQueryRequest):
        """M11.2: direct evidence lookup for the RAG tab — one search
        against the fass-rag service, the raw chunks passed through.

        Fail-open like the whole RAG layer (PRD §4.4/§9): disabled RAG,
        a dead service and any client error all answer 200 with
        ``ok=false`` and a human-readable ``error`` — this endpoint
        never 5xx's, and the tab renders the degraded state as a line."""
        cfg = resolved_rag()
        if not cfg.enabled:
            return {"ok": False,
                    "error": "RAG disabled — enable with FASS_RAG_ENABLED=1 "
                             "+ FASS_RAG_URL",
                    "chunks": []}
        if body.top_k is not None:
            cfg = cfg.model_copy(update={"top_k": body.top_k})
        try:
            res = rag_search(cfg, body.query)
        except Exception as e:  # noqa: BLE001 — belt-and-braces: the client
            # is fail-open itself, this only guards against a broken import
            return {"ok": False, "error": str(e) or type(e).__name__,
                    "chunks": []}
        if not res.ok:
            return {"ok": False,
                    "error": "; ".join(res.warnings) or "RAG search failed",
                    "chunks": []}
        return {"ok": True,
                "corpus_id": res.corpus_id,
                "corpus_match": res.corpus_match,
                "evidence_status": res.evidence_status,
                "retrieval_ms": res.retrieval_ms,
                "warnings": res.warnings,
                "chunks": res.chunks}

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
        for name, coder in REGISTRY["qsv"]["coders"].items():
            encoders[f"video:{name}_qsv"] = {
                "encoder": coder["encoder"], "present": coder["encoder"] in ff.encoders,
            }
        for name, spec in REGISTRY["audio_codecs"].items():
            encoders[f"audio:{name}"] = {
                "encoder": spec["encoder"], "present": spec["encoder"] in ff.encoders,
            }
        bsfs = {}
        for name, spec in REGISTRY["bitstream_filters"]["specs"].items():
            if name == "description":
                continue
            bsfs[name] = {"present": name in ff.bsfs}
        return {
            "available": True,
            "build": ff.version,
            "registry_version": REGISTRY["registry_version"],
            "encoders": encoders,
            # every registry filter, probed against THIS build (like bsfs)
            "filters": {n: {"present": n in ff.filters}
                        for n in sorted(all_filter_names())},
            "bsfs": bsfs,
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
            if body.steps:
                run = execute.start_run_sequence(
                    body.steps, timeout=body.timeout, verbose=body.verbose)
            else:
                run = execute.start_run(
                    body.arguments, timeout=body.timeout, verbose=body.verbose)
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
        # Resolved through the run record's log_path (a verbose run's log
        # lives in {output}/log/), falling back to run_dir(); the >10000-line
        # cap applies (last 1000 lines + banner).
        log = execute.read_run_log(run_id)
        if log is None:
            raise HTTPException(404, "run not found")
        return log

    @app.post("/v1/runs/{run_id}/stop")
    def stop_run(run_id: str):
        return {"stopped": execute.stop_run(run_id)}

    # --------------------------------------------- media info & logs (M10.7)

    @app.get("/v1/mediainfo")
    def mediainfo(target: str = "input", file: str = ""):
        """Media info = ffprobe's own JSON report on a file in the input
        volume (target=input) or the output volume (target=output). Named
        'mediainfo' for UI stability: the mediainfo binary is not part of
        the image, ffprobe is — and it is the same data the planner reads.
        File parameters are basenames of the selected volume only."""
        if target not in ("input", "output"):
            raise HTTPException(400, "target must be 'input' or 'output'")
        given = file.strip()
        if not given:
            raise HTTPException(400, "file: give a bare filename")
        name = _strip_i_prefix(given)
        if not name:
            raise HTTPException(400, "file: give a bare filename")
        if "/" in name or name.startswith(".."):
            raise HTTPException(
                400, "file must be a bare name in the input or output folder")
        path = execute.volume_file(target, name)
        if path is None:
            raise HTTPException(404, f"{name} not found in the {target} folder")
        if not ffprobe_available():
            raise HTTPException(502, "ffprobe is not installed on this build")
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HTTPException(502, f"ffprobe failed: {e}")
        if proc.returncode != 0:
            raise HTTPException(502, f"ffprobe failed: {proc.stderr[-500:]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise HTTPException(
                502, f"ffprobe output was not JSON: {proc.stdout[-500:]}")

    # ------------------------------------------------------ files (Files tab)

    def _volume_dir(which: str) -> str:
        """The deployment directory behind the Files tab's two columns —
        the same resolution the runner uses ($FASS_INPUT_DIR / /input,
        $FASS_OUTPUT_DIR / /output, '' when the volume is not mounted)."""
        return execute._input_dir() if which == "input" else execute._output_dir()

    def _bare_name(file: str) -> str:
        """The Files tab's basename guard: a bare name inside the selected
        volume, never a path (the stance of /v1/mediainfo and /media)."""
        name = file.strip()
        if not name:
            raise HTTPException(400, "file: give a bare filename")
        if "/" in name or ".." in name:
            raise HTTPException(
                400, "file must be a bare name in the input or output folder")
        return name

    @app.get("/v1/files")
    def files_list(dir: str = "input"):
        """Top-level regular files of one volume, newest first — the Files
        tab's two columns. An unmounted volume (no $FASS_*_DIR and no
        /input|/output) answers exists=false with an empty list; a directory
        is never a row, and a directory name that is not one of the two
        volumes is a 400."""
        if dir not in ("input", "output"):
            raise HTTPException(400, "dir must be 'input' or 'output'")
        vol = _volume_dir(dir)
        if not vol:
            return {"dir": dir, "exists": False, "files": []}
        root = Path(vol)
        files: list[dict] = []
        try:
            if not root.is_dir():
                return {"dir": dir, "exists": False, "files": []}
            for p in root.iterdir():
                try:
                    st = p.stat()
                except OSError:
                    continue
                if not p.is_file():
                    continue
                files.append({"name": p.name, "size": st.st_size,
                              "mtime": st.st_mtime})
        except OSError:  # unreadable volume: render it as empty
            return {"dir": dir, "exists": False, "files": []}
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return {"dir": dir, "exists": True, "files": files}

    @app.get("/v1/files/inspect")
    def files_inspect(dir: str = "input", file: str = "", tool: str = "ffprobe"):
        """Media info for one file of a volume, through the tool the Files
        tab's menu names: ffprobe (the image ships it, the same JSON the
        planner reads) or the mediainfo binary (JSON output; a deployment
        without it answers 503, like the ffprobe case)."""
        if dir not in ("input", "output"):
            raise HTTPException(400, "dir must be 'input' or 'output'")
        if tool not in ("ffprobe", "mediainfo"):
            raise HTTPException(400, "tool must be 'ffprobe' or 'mediainfo'")
        name = _bare_name(file)
        path = execute.volume_file(dir, name)
        if path is None:
            raise HTTPException(404, f"{name} not found in the {dir} folder")
        if tool == "mediainfo":
            if shutil.which("mediainfo") is None:
                raise HTTPException(
                    503, "mediainfo is not installed on this deployment")
            cmd, timeout = ["mediainfo", "--Output=JSON", str(path)], 30
        else:
            if not ffprobe_available():
                raise HTTPException(
                    503, "ffprobe is not installed on this deployment")
            cmd, timeout = (
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)],
                120)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HTTPException(502, f"{tool} failed: {e}")
        if proc.returncode != 0:
            raise HTTPException(502, f"{tool} failed: {proc.stderr[-500:]}")
        try:
            return {"tool": tool, "json": json.loads(proc.stdout)}
        except json.JSONDecodeError:
            raise HTTPException(
                502, f"{tool} output was not JSON: {proc.stdout[-500:]}")

    def _jpeg_dims(data: bytes) -> tuple[int, int] | None:
        """Width/height straight out of the JPEG's own SOF marker — the
        fallback when ffprobe cannot read the frame."""
        if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
            return None
        n, i = len(data), 2
        while i < n - 1:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > n:
                return None
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if i + 9 > n:
                    return None
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return (w, h) if w and h else None
            i += 2 + max(seg, 2)
        return None

    def _frame_size(data: bytes, path: Path) -> tuple[int, int] | None:
        """The written frame's real dimensions: ffprobe on the JPEG, falling
        back to its SOF marker (scale=-2:360 guarantees an even width)."""
        if ffprobe_available():
            try:
                proc = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_streams", str(path)],
                    capture_output=True, text=True, timeout=30, check=False)
                if proc.returncode == 0:
                    for s in json.loads(proc.stdout).get("streams", []):
                        if (s.get("codec_type") == "video"
                                and s.get("width") and s.get("height")):
                            return int(s["width"]), int(s["height"])
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
        return _jpeg_dims(data)

    @app.post("/v1/files/frame")
    def files_frame(body: FrameRequest):
        """The file's first frame as a JPEG 360 px high (even width from the
        aspect: scale=-2:360), base64-encoded for the popup.

        Fail-open like the rest of the info layer: a file with no video
        stream, a failing ffmpeg and a timeout all answer 200 with
        ``ok=false`` and ffmpeg's own message tail — the popup shows it as a
        line, the tab never breaks."""
        name = _bare_name(body.file)
        path = execute.volume_file(body.dir, name)
        if path is None:
            raise HTTPException(404, f"{name} not found in the {body.dir} folder")
        if not ffmpeg_available():
            return {"ok": False,
                    "error": "ffmpeg is not installed on this deployment"}
        with tempfile.TemporaryDirectory(prefix="fass-frame-") as td:
            out = Path(td) / "frame.jpg"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(path), "-frames:v", "1",
                   "-vf", "scale=-2:360", str(out)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as e:
                return {"ok": False, "error": f"ffmpeg failed: {e}"}
            if proc.returncode != 0 or not out.is_file():
                tail = (proc.stderr or "").strip()[-500:]
                return {"ok": False,
                        "error": tail
                        or f"ffmpeg produced no frame (exit {proc.returncode})"}
            data = out.read_bytes()
            size = _frame_size(data, out)
        if size is None:
            return {"ok": False,
                    "error": "could not read the frame's dimensions"}
        return {"ok": True, "width": size[0], "height": size[1],
                "jpeg_b64": base64.b64encode(data).decode()}

    @app.get("/v1/logs")
    def list_logs():
        """Every run log on this deployment: the runs dir (kind 'run') and
        the verbose logs in {output}/log (kind 'verbose'), newest first."""
        return {"logs": execute.list_logs()}

    @app.get("/v1/logs/{name}", response_class=PlainTextResponse)
    def get_log(name: str):
        """One log file by basename (runs dir or {output}/log). A log longer
        than 10000 lines is served as its last 1000 lines with a banner."""
        path = execute.log_path_for(name)
        if path is None:
            raise HTTPException(404, "log not found")
        return execute.serve_log(path)

    @app.get("/media/output/{filename}")
    def media_output(filename: str):
        path = execute.output_file(filename)
        if path is None:
            raise HTTPException(404, "file not found in the /output volume")
        return FileResponse(path, filename=path.name)

    @app.get("/media/run/{run_id}/output")
    def run_output(run_id: str):
        """The run's output file, whichever volume it landed in — /output,
        or the input folder for a chained processing ('/input/<name>')."""
        path = execute.run_output_file(run_id)
        if path is None:
            raise HTTPException(404, "output file not found for this run")
        return FileResponse(path, filename=path.name)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return UI_HTML

    return app


app = create_app()
