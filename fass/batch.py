"""Batch mode (M12): one instruction, many files.

``fass "resize to 720p" --batch clips/`` (or ``--batch a.mp4 b.mp4``) runs the
existing generate -> build -> execute pipeline once per input file,
sequentially, in sorted filename order, with the SAME instruction for every
file. A file that is rejected by the pipeline, fails to build, or exits
non-zero is reported and SKIPPED — the loop always continues. The run ends
with a per-file table (filename, status, exit code, output path or error
snippet); the process exit code is 0 only when every file succeeded.

Directory targets are scanned at the top level only (no recursion) and
restricted to the video container extensions the registry knows for inputs —
derived from the registry's own container/matrix data, never a local list.
An explicit file target is processed exactly as given.

Execution follows the CLI's rule exactly: without ``--execute`` nothing runs
(validate-only), with ``--execute`` every pass of a multi-pass plan runs in
order and the first non-zero exit is that file's exit code (M10.5).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from fass.cli import EXIT_CODES
from fass.pipeline import GenerationResponse, generate
from fass.probe.ffprobe import MediaInfo
from fass.registry import REGISTRY, is_source_spec, stream_scheme
from fass.schema import EditPlan

# A file target that is not on disk is a failure, not a pipeline status.
MISSING_EXIT_CODE = 1
# How much of a pipeline message the table shows for a failed file.
SNIPPET_CHARS = 96


@dataclass
class FileResult:
    """One row of the batch table."""

    filename: str
    status: str  # ok | needs_clarification | unsupported | error | exec_failed | missing
    exit_code: int
    output: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "status": self.status,
            "exit_code": self.exit_code,
            "output": self.output,
            "error": self.error,
        }


@dataclass
class BatchReport:
    """The whole run: one result per file, in the order they were processed."""

    results: list[FileResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0 only when every file succeeded — one failure makes the run 1."""
        return 0 if all(r.exit_code == 0 for r in self.results) else 1

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.exit_code == 0)

    def to_dict(self) -> dict:
        return {
            "status": "ok" if self.exit_code == 0 else "failed",
            "exit_code": self.exit_code,
            "total": len(self.results),
            "succeeded": self.succeeded,
            "results": [r.to_dict() for r in self.results],
        }


def video_input_extensions() -> set[str]:
    """Extensions of the video containers the registry knows as files.

    Derived, not hardcoded: a container counts when its matrix row carries a
    video codec (so audio-only and subtitle-only containers drop out) and it
    is a file container (``file: false`` marks protocol endpoints like
    rtsp/srt). Returned lowercase with the leading dot, matching
    :func:`fass.schema.guess_kind`'s convention."""
    exts: set[str] = set()
    for name, row in REGISTRY["matrix"].items():
        if not row.get("video"):
            continue
        container = REGISTRY["containers"].get(name)
        if container is None or container.get("file") is False:
            continue
        exts.update("." + e.lower() for e in container["extensions"])
    return exts


def collect_files(targets: list[str]) -> list[str]:
    """Expand batch targets into the file list to process, in sorted order.

    A directory contributes its top-level video files only (no recursion,
    registry extension list); a non-directory target is taken exactly as
    given — including a name that does not exist, which the loop reports as
    a failure instead of silently dropping. Duplicates collapse; sorting is
    by the path as given, which is filename order within one directory."""
    files: list[str] = []
    seen: set[str] = set()
    for target in targets:
        if os.path.isdir(target):
            exts = video_input_extensions()
            for entry in sorted(os.listdir(target)):
                p = os.path.join(target, entry)
                if not os.path.isfile(p):
                    continue
                if os.path.splitext(entry)[1].lower() not in exts:
                    continue
                if p not in seen:
                    seen.add(p)
                    files.append(p)
        elif target not in seen:
            seen.add(target)
            files.append(target)
    return sorted(files)


def _snippet(resp: GenerationResponse) -> str:
    """The failed file's reason, as one short line for the table.

    The first pass's unsupported reason leads — resp.errors now carries
    the second LLM try's own failure ('second LLM try failed: …'), which
    is meta about the retry, not the file's reason."""
    if resp.unsupported:
        u = resp.unsupported[0]
        msg = f"{u.get('fragment')}: {u.get('reason')}"
    elif resp.errors:
        msg = resp.errors[0]
    elif resp.clarifications:
        msg = resp.clarifications[0].get("question") or "clarification required"
    else:
        msg = f"status {resp.status}"
    msg = " ".join(str(msg).split())
    return msg if len(msg) <= SNIPPET_CHARS else msg[:SNIPPET_CHARS - 1] + "…"


def _output_path(resp: GenerationResponse) -> str | None:
    """The artifact the plan produces: the final pass's output path (the
    top-level arguments always describe that pass — see pipeline)."""
    if resp.steps:
        return resp.steps[-1].get("output")
    if resp.arguments:
        return resp.arguments[-1]
    return None


def _run_one(
    instruction: str,
    path: str,
    build_assets: Callable[[str], dict],
    *,
    output_filename: str | None = None,
    previous_plan: EditPlan | None = None,
    media: MediaInfo | None = None,
    adapter_name: str = "auto",
    preflight_check: bool = False,
    execute: bool = False,
) -> FileResult:
    """One file through generate -> build -> execute. Never raises: a crash
    in one file is that file's failure, the batch continues."""
    # A stream URL or a source spec is not a file — its "absence" is its
    # nature (the pipeline applies the same rule to its not-found warning).
    if stream_scheme(path) is None and not is_source_spec(path) \
            and not os.path.exists(path):
        return FileResult(path, "missing", MISSING_EXIT_CODE,
                          error="input file not found")
    try:
        assets = build_assets(path)
        resp = generate(
            instruction,
            assets,
            output_filename=output_filename,
            previous_plan=previous_plan,
            media=media,
            adapter_name=adapter_name,
            preflight_check=preflight_check,
        )
    except Exception as e:  # noqa: BLE001 — one bad file must not kill the loop
        return FileResult(path, "error", EXIT_CODES["error"],
                          error=f"pipeline error: {e}")

    if resp.status != "ready":
        return FileResult(path, resp.status, EXIT_CODES.get(resp.status, 1),
                          error=_snippet(resp))

    out = _output_path(resp)
    if not execute:
        # Validate-only (the CLI's default): a plan built and validated is
        # a success; nothing was run, so nothing was produced yet.
        return FileResult(path, "ok", 0, output=out)

    # M10.5 semantics, identical to the CLI: multi-pass plans run their
    # steps in order, the first non-zero exit stops the sequence.
    argvs = [s["arguments"] for s in resp.steps] if resp.steps else [resp.arguments]
    for a in argvs:
        rc = subprocess.run(a, shell=False).returncode
        if rc != 0:
            return FileResult(path, "exec_failed", rc, output=out,
                              error=f"ffmpeg exited {rc}")
    return FileResult(path, "ok", 0, output=out)


def run_batch(
    instruction: str,
    files: list[str],
    build_assets: Callable[[str], dict],
    *,
    output_dir: str | None = None,
    adapter_name: str = "auto",
    preflight_check: bool = False,
    execute: bool = False,
) -> BatchReport:
    """Run the instruction over every file, sequentially, in sorted order.

    ``build_assets`` maps a file path to the pipeline's asset map (the CLI
    passes its own asset builder, so shared assets like a watermark apply to
    every file). ``output_dir`` redirects each file's derived output name into
    that directory; without it the plan's own default output applies.
    A rejected or failing file is recorded and skipped — the loop continues."""
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    report = BatchReport()
    for path in files:
        out = os.path.join(output_dir, os.path.basename(path)) if output_dir else None
        r = _run_one(instruction, path, build_assets,
                     output_filename=out, adapter_name=adapter_name,
                     preflight_check=preflight_check, execute=execute)
        report.results.append(r)
    return report


def format_report(report: BatchReport) -> str:
    """The per-file table: filename, status, exit code, output path or the
    error snippet that explains the failure."""
    rows = [(r.filename, r.status, str(r.exit_code),
             r.output if r.exit_code == 0 else (r.error or r.status))
            for r in report.results]
    headers = ("FILE", "STATUS", "EXIT", "OUTPUT / ERROR")
    widths = [max(len(str(x[i])) for x in (headers, *rows)) for i in range(4)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [f"Batch results ({len(rows)} file(s)):", f"  {line}"]
    for row in rows:
        out.append("  " + "  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    out.append(f"{report.succeeded} of {len(report.results)} file(s) succeeded")
    return "\n".join(out)


def print_report(report: BatchReport) -> None:
    print(format_report(report))


__all__ = [
    "BatchReport",
    "FileResult",
    "collect_files",
    "format_report",
    "print_report",
    "run_batch",
    "video_input_extensions",
]
