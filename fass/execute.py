"""Command execution (M6) — the docker deployment can RUN the generated
commands and return the results.

Security model (extends PRD §10):
- execution is OFF unless FASS_ALLOW_EXECUTE is set (the docker image sets
  it; a development checkout never runs anything);
- only an argv ARRAY is accepted and run (subprocess, shell=False) — a
  rendered shell string is never parsed, so shell metacharacters are inert;
- the argv is validated against the builder's flag vocabulary: every flag
  must be one the deterministic builder itself emits, codec/format/profile
  values must come from the registry, map/filter/number values are
  pattern-checked. Anything else is rejected before a process is spawned.

Paths: bare filenames (no scheme, no slash) are resolved against the
deployment volume mounts — inputs against $FASS_INPUT_DIR (the /input
volume) when the file is there, the output against $FASS_OUTPUT_DIR
(/output) — so the user's UI paths ('clip.mp4') work inside the container.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fass.registry import (
    REGISTRY,
    all_muxers,
    bsf_errors,
    codec_option_errors,
    codec_option_specs,
    container_from_extension,
    diagnostic_output,
    filtergraph_problems,
    format_option_errors,
    is_source_spec,
    output_device_formats,
    source_input_formats,
    stream_output_info,
    stream_scheme,
)

ALLOW_EXECUTE_ENV = "FASS_ALLOW_EXECUTE"
INPUT_DIR_ENV = "FASS_INPUT_DIR"
OUTPUT_DIR_ENV = "FASS_OUTPUT_DIR"
RUN_DIR_ENV = "FASS_RUN_DIR"

# ------------------------------------------------------------ argv validation

# Every '-<key> value' format option the registry lets a container carry
# (generic set + the per-container groups: movflags, mpegts_flags, hls_*, …).
_FORMAT_OPTION_KEYS = {
    key
    for group, opts in REGISTRY["format_options"].items()
    if group != "description" and isinstance(opts, dict)
    for key in opts
}

# Every '-<key> value' private codec option the registry lets an encoder
# carry (M8: crf, x264-params, dialnorm, …). A flag that is not in the
# builder's generic vocabulary is accepted only when it is a documented
# option of the plan's resolved video/audio encoder.
_CODEC_OPTION_KEYS = {
    key
    for enc, opts in REGISTRY["codec_options"].items()
    if enc != "description" and isinstance(opts, dict)
    for key in opts
}

_BOOL_FLAGS = {"-hide_banner", "-an", "-shortest"}
_VALUE_FLAGS = {
    "-loglevel", "-ss", "-t", "-i", "-f",
    "-filter_complex", "-vf", "-af", "-map",
    "-c", "-c:v", "-c:a", "-c:s", "-acodec",
    "-b:v", "-b:a", "-maxrate", "-minrate", "-bufsize",
    "-level", "-s",
    "-crf", "-rc", "-cq", "-profile:v", "-preset", "-pix_fmt",
    "-ar", "-ac", "-movflags",
    "-bsf:v", "-bsf:a",
} | {f"-{key}" for key in _FORMAT_OPTION_KEYS}
KNOWN_FLAGS = _BOOL_FLAGS | _VALUE_FLAGS

# A per-stream option with an explicit stream index — the ABR ladder form
# ('-b:v:0', '-s:v:1', '-b:a:0', …). The index addresses the Nth mapped
# stream OF THAT TYPE, so the value rules are the base flag's; the ladder
# planner is the only emitter, an expert Command-tab edit may carry them.
_SPEC_RX = re.compile(
    r"^-(b|c|minrate|maxrate|bufsize|profile|level|s)(:[va]):(\d+)$")
# the indexed flags whose BASE flag already carries the stream type
# ('-b:v:0' -> '-b:v', '-c:v:0' -> '-c:v', '-profile:v:0' -> '-profile:v');
# the rest address a bare base flag ('-s:v:0' -> '-s', '-minrate:v:0'
# -> '-minrate')
_SPEC_KEEP_TYPE = {"b", "c", "profile"}


def _spec_base(flag: str) -> str | None:
    m = _SPEC_RX.match(flag)
    if not m:
        return None
    base, typ = m.group(1), m.group(2)
    return f"-{base}{typ}" if base in _SPEC_KEEP_TYPE else f"-{base}"

_FILTER_FLAGS = {"-filter_complex", "-vf", "-af"}
_CODECS = (
    {spec["encoder"] for spec in REGISTRY["video_codecs"].values()}
    | {spec["encoder"] for spec in REGISTRY["audio_codecs"].values()}
    | {c for coder in REGISTRY["gpu"]["coders"].values() for c in (coder["encoder"],)}
    | {c for coder in REGISTRY["qsv"]["coders"].values() for c in (coder["encoder"],)}
    | set(REGISTRY["subtitles"]["soft_codecs"].values())
    | {"pcm_s16le", "copy"}
)
# Registry-driven: every container muxer, the fixed-container stream muxers,
# the source-input demuxers (lavfi, …), the output device formats, and the
# diagnostic null muxer (M10.2 — '-f null -' discards the decoded stream).
_MUXERS = (all_muxers() | set(source_input_formats()) | set(output_device_formats())
           | {diagnostic_output().get("muxer", "null")})
_PIX_FMTS = {pf for pfs in REGISTRY["pixel_formats"].values() for pf in pfs}
_PRESETS = {p for ps in REGISTRY["video_presets"].values() for p in ps}
_PROFILES = {"baseline", "main", "high"}
_MAP_RE = re.compile(r"^\d+:[vas](:\d+)?\??$|^\[[^\[\]]+\]$")
_NUM_RE = re.compile(r"^\d+(\.\d+)?([kmK])?$")
_RATE_RE = re.compile(r"^\d+k$|^\d+$")
_PATH_RE = re.compile(r"^[^\x00\n\r]+$")
# the builder's double-escape for burned-subtitle paths (registry loader)
_FILTER_ESC = re.compile(r"([\\'\":=,;])")


def _filter_escape(path: str) -> str:
    return _FILTER_ESC.sub(r"\\\1", _FILTER_ESC.sub(r"\\\1", path))


def _is_path_token(tok: str) -> bool:
    """A filesystem path or a stream URL / source spec (never a flag)."""
    return (
        bool(_PATH_RE.match(tok))
        and (stream_scheme(tok) is not None or is_source_spec(tok)
             or ":" not in tok)
    )


def _output_container(argv: list[str]) -> str | None:
    """Container of the plan's output destination — the context format-option
    values are validated against. Stream destinations resolve through the
    registry (http/https derive the container from the URL path extension,
    defaulting to mpegts); plain files through their extension. None for
    destinations with no container to speak of (audio devices)."""
    if len(argv) < 2:
        return None
    dest = argv[-1]
    info = stream_output_info(dest)
    if info is not None:
        return info[0]
    name = dest.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return container_from_extension(name.rsplit(".", 1)[-1])


def _codec_option_encoder(key: str, video: str | None,
                          audio: str | None) -> str | None:
    """The encoder that owns a private codec option: the resolved -c:v
    encoder when it documents the key, else the -c:a encoder. Video wins
    for keys both tables share (a bare 'preset' targets the video
    encoder). None when neither resolved encoder knows the option."""
    if key not in _CODEC_OPTION_KEYS:
        return None
    for enc in (video, audio):
        if enc and key in codec_option_specs(enc):
            return enc
    return None


def _codec_option_value(flag: str, value: str, prefix: str,
                        video: str | None,
                        audio: str | None) -> list[str] | None:
    """Registry-kind validation for a flag that overlaps the private
    codec-option vocabulary (-crf, -preset, -rc, …). None when the
    resolved encoder doesn't document the option — the caller falls back
    to its generic check."""
    enc = _codec_option_encoder(flag[1:], video, audio)
    if enc is None:
        return None
    return [f"{prefix}: {msg}" for msg in codec_option_errors(enc, {flag[1:]: value})]


def validate_argv(argv: list[str]) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems: list[str] = []
    if not argv or argv[0] != "ffmpeg":
        return ["argv must start with 'ffmpeg'"]
    container = _output_container(argv)
    expect_value: str | None = None
    video_encoder: str | None = None
    audio_encoder: str | None = None
    positionals = 0
    for i, tok in enumerate(argv[1:], start=1):
        if expect_value is not None:
            if expect_value == "-c:v" or _spec_base(expect_value) == "-c:v":
                video_encoder = tok
            elif expect_value == "-c:a" or _spec_base(expect_value) == "-c:a":
                audio_encoder = tok
            problems.extend(
                _check_value(expect_value, tok, i, container,
                             video_encoder, audio_encoder))
            expect_value = None
            continue
        if tok in _BOOL_FLAGS:
            continue
        if tok in _VALUE_FLAGS or _spec_base(tok) is not None:
            expect_value = tok
            if i == len(argv) - 1:
                problems.append(f"flag '{tok}' at the end has no value")
            continue
        if tok in KNOWN_FLAGS:
            continue
        if tok == "-" and i == len(argv) - 1:
            # the null muxer's destination (M10.2 diagnostic runs): '-' is
            # the output path, not a flag
            positionals += 1
            continue
        if tok.startswith("-"):
            if _codec_option_encoder(tok[1:], video_encoder, audio_encoder):
                # a private codec option of the resolved encoder
                # (-x264-params, -dialnorm, …) — value flag
                expect_value = tok
                if i == len(argv) - 1:
                    problems.append(f"flag '{tok}' at the end has no value")
            else:
                problems.append(f"unknown flag '{tok}' (not in the builder's vocabulary)")
            continue
        # positional: inputs are always consumed after -i; the only free
        # positional is the single final output path
        positionals += 1
        if i == len(argv) - 1:
            # final token: file path, stream URL or device spec (all already
            # validated by the planner) — control characters are the only
            # thing that can hurt
            if not _PATH_RE.match(tok):
                problems.append(f"invalid output token '{tok}'")
        else:
            problems.append(
                f"unexpected bare token '{tok}' at position {i} — only the "
                "final output path may be bare"
            )
    if expect_value is not None:
        problems.append(f"flag '{expect_value}' at the end has no value")
    return problems


def _check_value(flag: str, value: str, index: int,
                 container: str | None = None,
                 video: str | None = None,
                 audio: str | None = None) -> list[str]:
    p = f"value for '{flag}'"
    if not _PATH_RE.match(value):
        return [f"{p} contains control characters"]
    base = _spec_base(flag)
    if base is not None:
        # a stream-indexed per-stream option ('-b:v:0' …): the value rules
        # are the base flag's; the message above still names the actual flag
        flag = base
    if flag in _FILTER_FLAGS:
        if not value:
            return [f"{p} is empty or malformed"]
        # full graph validation (filter names, chain/instance syntax) —
        # the same validator the schema layer used on the plan
        return [f"{p}: {msg}" for msg in filtergraph_problems(value)]
    if flag == "-i":
        return []  # input path / stream URL / source spec
    if flag == "-f":
        if value not in _MUXERS:
            return [f"{p} '{value}' is not a known FFmpeg format"]
        return []
    if flag in ("-c", "-c:v", "-c:a", "-c:s", "-acodec"):
        if value not in _CODECS:
            return [f"{p} '{value}' is not a known encoder (registry)"]
        return []
    if flag == "-map":
        if not _MAP_RE.match(value):
            return [f"{p} '{value}' is not a valid stream specifier"]
        return []
    if flag in ("-bsf:v", "-bsf:a"):
        # 'name' or 'name=k=v:k2=v2' — options are colon-joined inside the
        # argument. The name must be a registered bitstream filter and
        # every option key/value must match its registry kind.
        name, sep, opts = value.partition("=")
        if not name:
            return [f"{p} '{value}' is malformed (expected name or name=k=v)"]
        if name == "trim":
            return [
                f"{p}: 'trim' is not an FFmpeg bitstream filter — a "
                "stream-copy cut comes from the trim start/duration with -c copy"
            ]
        options: dict[str, str] = {}
        if sep:
            for pair in opts.split(":"):
                key, eq, val = pair.partition("=")
                if not eq or not key:
                    return [f"{p} '{value}' is malformed (options are k=v pairs)"]
                options[key] = val
        return [f"{p}: {msg}" for msg in bsf_errors(name, options)]
    if flag == "-pix_fmt":
        if value not in _PIX_FMTS:
            return [f"{p} '{value}' is not a supported pixel format"]
        return []
    if flag == "-preset":
        issues = _codec_option_value(flag, value, p, video, audio)
        if issues is not None:
            return issues
        if value not in _PRESETS:
            return [f"{p} '{value}' is not a supported x264 preset"]
        return []
    if flag == "-profile:v":
        if value not in _PROFILES:
            return [f"{p} '{value}' is not a supported profile"]
        return []
    if flag in ("-crf", "-cq"):
        issues = _codec_option_value(flag, value, p, video, audio)
        if issues is not None:
            return issues
        if not re.match(r"^\d{1,2}$", value) or int(value) > 69:
            return [f"{p} must be an integer 0-69"]
        return []
    if flag == "-rc":
        issues = _codec_option_value(flag, value, p, video, audio)
        if issues is not None:
            return issues
        if value not in ("cbr", "vbr", "constqp", "lossless"):
            return [f"{p} '{value}' is not a known rate-control mode"]
        return []
    if flag in ("-b:v", "-maxrate", "-minrate", "-bufsize", "-b:a"):
        if not _RATE_RE.match(value):
            return [f"{p} must be a bitrate like '10000k'"]
        return []
    if flag == "-level":
        # Annex A encoder level: '3.0' (x264) or '31' (short form, 1-2 digits)
        if not re.match(r"^\d\.\d+$|^\d{1,2}$", value):
            return [f"{p} must be an encoder level like '3.0' or '31'"]
        return []
    if flag == "-s":
        # output frame size WxH (the ladder's per-level geometry)
        if not re.match(r"^\d+x\d+$", value):
            return [f"{p} must be a size like '640x360'"]
        return []
    if flag in ("-ss", "-t"):
        if not _NUM_RE.match(value):
            return [f"{p} must be a number of seconds"]
        return []
    if flag == "-ar":
        if not re.match(r"^\d{4,6}$", value):
            return [f"{p} must be a sample rate in Hz"]
        return []
    if flag == "-ac":
        if value not in ("1", "2", "6"):
            return [f"{p} must be 1, 2 or 6 channels"]
        return []
    if flag == "-loglevel":
        if value not in ("quiet", "error", "warning", "info", "verbose"):
            return [f"{p} '{value}' is not a log level"]
        return []
    key = flag[1:]
    if key in _FORMAT_OPTION_KEYS:
        # '-<key> value' format option (movflags, brand, hls_time, …):
        # validated per its registry kind — flagset members, numeric
        # ranges, enums — against the plan's output container
        if container is None:
            return []
        return [f"{p}: {msg}"
                for msg in format_option_errors(container, {key: value})]
    owner = _codec_option_encoder(key, video, audio)
    if owner is not None:
        # '-<key> value' private codec option (x264-params, dialnorm, …):
        # validated per its registry kind against the resolved encoder
        return [f"{p}: {msg}" for msg in codec_option_errors(owner, {key: value})]
    return []


# ------------------------------------------------------------ path resolution

def _input_dir() -> str:
    return os.environ.get(INPUT_DIR_ENV, "/input" if Path("/input").is_dir() else "")


def _output_dir() -> str:
    return os.environ.get(OUTPUT_DIR_ENV, "/output" if Path("/output").is_dir() else "")


def resolve_input_name(name: str) -> str:
    """Resolve a bare input filename against the deployment's input volume
    ($FASS_INPUT_DIR, or /input when it exists) — the same rule
    resolve_paths applies at run time, shared with the pipeline's
    inspection so a bare name that exists in the volume is probed and
    not reported "not found". Stream URLs, source specs and non-bare
    names pass through unchanged, as do bare names with no matching
    file in the volume. A bare name that is not in the input volume but
    exists in the output volume resolves there (a previous run's result —
    the comparison convention: the distorted clip lives in /output)."""
    in_dir = _input_dir()
    if (
        in_dir
        and stream_scheme(name) is None
        and not is_source_spec(name)
        and "/" not in name
    ):
        joined = os.path.join(in_dir, name)
        if os.path.exists(joined):
            return joined
    out_dir = _output_dir()
    if (
        out_dir
        and stream_scheme(name) is None
        and not is_source_spec(name)
        and "/" not in name
    ):
        joined = os.path.join(out_dir, name)
        if os.path.exists(joined):
            return joined
    return name


def resolve_paths(argv: list[str]) -> tuple[list[str], list[str]]:
    """Resolve bare filenames against the volume mounts.

    Returns (argv, notes): inputs after -i resolve to $FASS_INPUT_DIR when
    the file is there; the final output path resolves to $FASS_OUTPUT_DIR,
    and a '/input/<name>' destination resolves to the input folder (chained
    processings — the next run picks the file up by bare name);
    burned-subtitle paths inside filter tokens are rewritten with the same
    double-escaping the builder used, so the filter graph stays valid."""
    in_dir = _input_dir()
    out_dir = _output_dir()
    notes: list[str] = []
    out = list(argv)
    bare_inputs: dict[str, str] = {}  # original bare name -> resolved path

    for i, tok in enumerate(out):
        if tok == "-i" and i + 1 < len(out):
            path = out[i + 1]
            if (
                in_dir
                and stream_scheme(path) is None
                and not is_source_spec(path)
                and "/" not in path
                and os.path.exists(os.path.join(in_dir, path))
            ):
                out[i + 1] = os.path.join(in_dir, path)
                bare_inputs[path] = os.path.join(in_dir, path)
                notes.append(f"input '{path}' resolved to /input volume: {out[i + 1]}")
            elif (
                out_dir
                and stream_scheme(path) is None
                and not is_source_spec(path)
                and "/" not in path
                and os.path.exists(os.path.join(out_dir, path))
            ):
                # a bare name absent from the input volume but present in
                # the output volume is a previous run's result — the
                # comparison convention (the distorted clip lives in
                # /output)
                out[i + 1] = os.path.join(out_dir, path)
                bare_inputs[path] = os.path.join(out_dir, path)
                notes.append(
                    f"input '{path}' resolved to the /output volume "
                    f"(not in /input): {out[i + 1]}")
    if len(out) >= 2:
        dest = out[-1]
        if dest == "-":
            # diagnostic run (M10.2): '-' is FFmpeg's null-muxer token, not
            # a filename — it must not resolve into the output volume
            notes.append("diagnostic run — output discarded (null muxer)")
        elif (
            out_dir
            and stream_scheme(dest) is None
            and ":" not in dest
            and "/" not in dest
        ):
            out[-1] = os.path.join(out_dir, dest)
            notes.append(f"output '{dest}' resolved to /output volume: {out[-1]}")
        elif (
            in_dir
            and stream_scheme(dest) is None
            and dest.startswith("/input/")
        ):
            # '/input/<name>' means "write into the deployment's input
            # folder" ($FASS_INPUT_DIR — /input itself inside the container),
            # so the next run can pick the file up by bare name: chained
            # processings. Inside the container the rewrite is a no-op.
            name = dest[len("/input/"):]
            if name and not name.startswith("/"):
                target = os.path.join(in_dir, name)
                if target != dest:
                    out[-1] = target
                    notes.append(f"output '{dest}' resolves to the input folder: {out[-1]}")
    for i, tok in enumerate(out):
        if tok in _FILTER_FLAGS and i + 1 < len(out):
            fval = out[i + 1]
            for name, resolved in bare_inputs.items():
                # re-escape with the builder's exact double-escape, then
                # substitute inside the filter token
                if name in fval:
                    fval = fval.replace(_filter_escape(name), _filter_escape(resolved))
                    notes.append(f"subtitle path in filter updated to {resolved}")
            out[i + 1] = fval
    return out, notes


# ------------------------------------------------------------ run records

@dataclass
class Run:
    id: str
    argv: list[str]
    command: str
    started: str  # iso
    status: str  # running | success | error | stopped | interrupted
    exit_code: int | None = None
    output: str | None = None
    notes: list[str] = field(default_factory=list)
    log: str = ""  # log filename
    # M10.5: multi-pass runs (two-pass stabilization) hold every step's
    # argv, in execution order (the final pass last); argv/command above
    # always describe the FINAL pass — the one that produces the artifact.
    steps: list[list[str]] | None = None
    # M10.7: where the log ACTUALLY lives (a verbose run writes into
    # {output}/log/, the normal run into run_dir()); verbose records that
    # the run was started in verbose-log mode.
    log_path: str | None = None
    verbose: bool = False
    # M13.3: an ABR ladder run (HLS) — the declared levels, derived from
    # the run's argv at start time. The data the post-run master.m3u8
    # synthesis uses (never parsed from the filesystem); None otherwise.
    ladder: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "argv": self.argv, "command": self.command,
            "started": self.started, "status": self.status,
            "exit_code": self.exit_code, "output": self.output,
            "notes": self.notes, "steps": self.steps,
            "log_path": self.log_path, "verbose": self.verbose,
            "ladder": self.ladder,
        }


def run_dir() -> Path:
    env = os.environ.get(RUN_DIR_ENV, "")
    if env:
        p = Path(env)
    elif Path("/log").is_dir():
        p = Path("/log/runs")
    else:
        p = Path.cwd() / ".fass-runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sidecar(run_id: str) -> Path:
    return run_dir() / f"{run_id}.json"


def verbose_log_dir() -> Path | None:
    """{output_dir}/log — where a VERBOSE run's log lands (the '/output/log'
    directory the UI's log browser reads). None when no output dir is
    configured, in which case the log falls back to run_dir()."""
    out_dir = _output_dir()
    if not out_dir:
        return None
    p = Path(out_dir) / "log"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return p


def _log_file(run_id: str, verbose: bool = False) -> Path:
    if verbose:
        d = verbose_log_dir()
        if d is not None:
            return d / f"{run_id}.log"
    return run_dir() / f"{run_id}.log"


_ACTIVE: dict[str, subprocess.Popen] = {}
_STOPPING: set[str] = set()  # run ids WE asked to stop (so the watcher
# records "stopped", not "error", when the kill lands)
_LOCK = threading.Lock()


def _save(run: Run) -> None:
    _sidecar(run.id).write_text(
        json.dumps(run.to_dict(), indent=1) + "\n", encoding="utf-8"
    )


def _update(run_id: str, **kw) -> None:
    p = _sidecar(run_id)
    if not p.exists():
        return
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec.update(kw)
    p.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")


def _append_note(run_id: str, note: str) -> None:
    """Append one note to a run record — the finalizer's _update replaces
    the notes list wholesale, so a post-finalize note (the M13.3 master
    synthesis) gets its own read-modify-write pass."""
    p = _sidecar(run_id)
    if not p.exists():
        return
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec.setdefault("notes", []).append(note)
    p.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")


# --------------------------------------------- ABR ladder master (M13.3)

_AVC_PROFILE_ID = {"baseline": 66, "main": 77, "high": 100}


def _ladder_from_argv(argv: list[str]) -> dict | None:
    """An ABR ladder run, recognized from its fass-generated argv shape
    (-var_stream_map plus per-level stream-specifier options — -b:v:N,
    -s:v:N, -b:a:N — against a %v .m3u8 destination). Returns the
    declared ladder (levels in var_stream_map order) or None.

    The argv is the run's ground truth — never derived from the
    filesystem — which also keeps the synthesis honest for a ladder an
    expert hand-edited in the Command tab (it gets a master from its own
    values). DASH ladders write a self-describing .mpd and need no
    master, so a non-%v .m3u8-less destination yields None too."""
    if "-var_stream_map" not in argv:
        return None
    out_name = os.path.basename(argv[-1])
    if os.path.splitext(out_name)[1].lower() != ".m3u8" or "%v" not in out_name:
        return None
    try:
        map_val = argv[argv.index("-var_stream_map") + 1]
    except IndexError:
        return None
    # flag -> value over the whole argv (the last occurrence of a flag
    # wins, as it does in FFmpeg)
    kv: dict[str, str] = {}
    for i, t in enumerate(argv[:-1]):
        if t.startswith("-") and i + 1 < len(argv):
            kv[t] = argv[i + 1]
    levels: list[dict] = []
    for k, group in enumerate(map_val.split()):
        spec: dict[str, int] = {}
        for part in group.split(","):
            m = re.match(r"^([va]):(\d+)$", part)
            if not m:
                return None  # malformed var_stream_map — no synthesis
            spec[m.group(1)] = int(m.group(2))
        if "v" not in spec or "a" not in spec:
            return None
        v, a = spec["v"], spec["a"]
        try:
            vkb = int(kv[f"-b:v:{v}"].rstrip("k"))
            w, h = (int(x) for x in kv[f"-s:v:{v}"].split("x"))
            akb = int(kv[f"-b:a:{a}"].rstrip("k"))
        except (KeyError, ValueError):
            return None  # a level without its declared options — no synthesis
        levels.append({
            "width": w, "height": h, "video_kbps": vkb, "audio_kbps": akb,
            "profile": kv.get(f"-profile:v:{v}"),
            "level": kv.get(f"-level:v:{v}"),
            "bandwidth": (vkb + akb) * 1000,
            "playlist": out_name.replace("%v", str(k)),
        })
    if not levels:
        return None
    return {
        "container": "hls",
        "video_codec": {"libx264": "h264", "libx265": "h265"}.get(
            kv.get("-c:v", "")),
        "audio_codec": kv.get("-c:a"),
        "levels": levels,
    }


def _ladder_codecs(ladder: dict, lv: dict) -> str | None:
    """The master's CODECS tag for one level: the avc1 profile/level
    fourcc (h264) + mp4a.40.2 (AAC). Omitted when not computable (e.g.
    h265 — there is no common fourcc for HEVC here)."""
    parts: list[str] = []
    if ladder.get("video_codec") == "h264" \
            and lv.get("profile") and lv.get("level"):
        pid = _AVC_PROFILE_ID.get(lv["profile"])
        if pid is not None:
            lvl = lv["level"]
            lid = int(lvl.replace(".", "")) if "." in lvl else int(lvl)
            parts.append(f"avc1.{pid:02x}00{lid:02x}")
    if ladder.get("audio_codec") in ("aac", "fdk_aac"):
        parts.append("mp4a.40.2")
    return ",".join(parts) if parts else None


def _master_from_ladder(ladder: dict) -> str:
    """The master.m3u8 text: one #EXT-X-STREAM-INF per declared level,
    BANDWIDTH = (video + audio kbps) x 1000, relative variant playlist
    names (the playlists live next to the master)."""
    lines = ["#EXTM3U"]
    for lv in ladder["levels"]:
        parts = [f"BANDWIDTH={lv['bandwidth']}",
                 f"AVERAGE-BANDWIDTH={lv['bandwidth']}",
                 f"RESOLUTION={lv['width']}x{lv['height']}"]
        codecs = _ladder_codecs(ladder, lv)
        if codecs:
            parts.append(f'CODECS="{codecs}"')
        lines.append("#EXT-X-STREAM-INF:" + ",".join(parts))
        lines.append(lv["playlist"])
    return "\n".join(lines) + "\n"


def _synthesize_master(cwd, ladder: dict) -> str | None:
    """After a clean ladder run, write master.m3u8 into the run's working
    directory — next to the variant playlists (the hls muxer writes no
    master; fass synthesizes one from the run's declared levels). Returns
    the run note, or None when nothing was written."""
    out_dir = Path(cwd) if cwd else Path.cwd()
    try:
        (out_dir / "master.m3u8").write_text(
            _master_from_ladder(ladder), encoding="utf-8")
    except OSError as e:
        return f"master.m3u8 could not be written: {e}"
    return (f"master.m3u8 synthesized from the run's declared levels "
            f"({len(ladder['levels'])} variants)")


def execution_enabled() -> bool:
    return os.environ.get(ALLOW_EXECUTE_ENV, "") not in ("", "0", "false", "no")


def start_run(argv: list[str], timeout: float | None = None,
              verbose: bool = False) -> Run:
    """Validate and start a run; raises ValueError for invalid argv or a
    disabled execution mode. verbose (M10.7): the log goes to
    {output_dir}/log/{run_id}.log instead of run_dir()."""
    if not execution_enabled():
        raise PermissionError(
            "execution is disabled on this deployment "
            f"(set {ALLOW_EXECUTE_ENV}=1 to enable it)"
        )
    from fass.builder import render_posix

    problems = validate_argv(argv)
    if problems:
        raise ValueError("refusing to run — argv failed validation: " + "; ".join(problems))
    argv, notes = resolve_paths(argv)
    ladder = _ladder_from_argv(argv)

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out_name = argv[-1]
    log_path = _log_file(run_id, verbose)
    run = Run(
        id=run_id, argv=argv, command=render_posix(argv),
        started=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        status="running",
        output=None if (stream_scheme(out_name) or out_name == "-") else out_name,
        notes=notes, log=f"{run_id}.log",
        log_path=str(log_path), verbose=verbose,
        ladder=ladder,
    )
    out_dir = _output_dir()
    cwd = (
        str(Path(out_dir).resolve())
        if out_dir and not stream_scheme(out_name)
        else None
    )
    with _LOCK:
        logf = open(log_path, "wb")
        proc = subprocess.Popen(
            argv, stdout=logf, stderr=subprocess.STDOUT,
            shell=False, start_new_session=True, cwd=cwd,
        )
        _ACTIVE[run_id] = proc
    _save(run)
    threading.Thread(
        target=_watch,
        args=(run_id, proc, logf, timeout, cwd, ladder, run.command,
              time.monotonic()),
        daemon=True,
    ).start()
    return run


def start_run_sequence(argvs: list[list[str]],
                       timeout: float | None = None,
                       verbose: bool = False) -> Run:
    """Validate and start a MULTI-PASS run (M10.5, two-pass vidstab).

    The steps run one at a time, in order, in the SAME working directory
    (the final output's): the detect pass writes its side-file (the .trf)
    as a bare name and the transform pass reads it back, so nothing is
    rewritten between the steps. The first non-zero exit — or a timeout,
    or a stop — finalizes the run; a step's timeout applies per step.
    One Run record: argv/command describe the FINAL step (the artifact
    producer), steps every step, notes and the log the whole sequence."""
    if not execution_enabled():
        raise PermissionError(
            "execution is disabled on this deployment "
            f"(set {ALLOW_EXECUTE_ENV}=1 to enable it)"
        )
    if len(argvs) < 2:
        raise ValueError("a run sequence needs at least two steps")
    from fass.builder import render_posix

    resolved: list[list[str]] = []
    notes: list[str] = []
    for i, argv in enumerate(argvs, 1):
        problems = validate_argv(argv)
        if problems:
            raise ValueError(
                f"refusing to run — step {i} argv failed validation: "
                + "; ".join(problems))
        argv, step_notes = resolve_paths(argv)
        resolved.append(argv)
        for n in step_notes:
            notes.append(f"step {i}: {n}")

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    final_argv = resolved[-1]
    out_name = final_argv[-1]
    log_path = _log_file(run_id, verbose)
    run = Run(
        id=run_id, argv=final_argv, command=render_posix(final_argv),
        started=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        status="running",
        output=None if (stream_scheme(out_name) or out_name == "-") else out_name,
        notes=notes, log=f"{run_id}.log", steps=resolved,
        log_path=str(log_path), verbose=verbose,
    )
    out_dir = _output_dir()
    cwd = (
        str(Path(out_dir).resolve())
        if out_dir and not stream_scheme(out_name)
        else None
    )
    with _LOCK:
        logf = open(log_path, "wb")
    _save(run)
    threading.Thread(
        target=_watch_sequence,
        args=(run_id, resolved, cwd, timeout, logf, notes,
              [render_posix(a) for a in resolved], time.monotonic()),
        daemon=True,
    ).start()
    return run


def _log_footer(logf, commands: list[str], t0: float | None) -> None:
    """Self-contained log tail: the command(s) that ran plus the wall time.

    Written to the shared log after the process has exited. The child
    wrote through its own (inherited) fd, so the Python object's position
    is still 0 — seek to the end before appending, or the footer clobbers
    the start of the log."""
    if not commands and t0 is None:
        return
    try:
        lines: list[str] = []
        if commands:
            if len(commands) == 1:
                lines.append(f"# command: {commands[0]}")
            else:
                for i, c in enumerate(commands, 1):
                    lines.append(f"# command step {i}/{len(commands)}: {c}")
        if t0 is not None:
            lines.append(f"# duration: {time.monotonic() - t0:.1f}s")
        if lines:
            logf.seek(0, 2)
            logf.write(("\n" + "\n".join(lines) + "\n").encode("utf-8"))
            logf.flush()
    except (OSError, ValueError):
        pass


def _watch_sequence(run_id: str, steps: list[list[str]], cwd,
                    timeout: float | None, logf, notes: list[str],
                    commands: list[str] | None = None,
                    t0: float | None = None) -> None:
    """Run the steps one at a time into the shared log; the first
    non-zero exit (or a timeout, or a stop) finalizes the run. _ACTIVE
    holds the CURRENT step's process, so stop_run kills whichever step
    is running; the timeout is per step."""
    progress: list[str] = []
    status: str | None = None
    rc: int | None = 0
    try:
        for i, argv in enumerate(steps, 1):
            with _LOCK:
                proc = subprocess.Popen(
                    argv, stdout=logf, stderr=subprocess.STDOUT,
                    shell=False, start_new_session=True, cwd=cwd,
                )
                _ACTIVE[run_id] = proc
            try:
                rc = proc.wait(timeout=timeout) if timeout else proc.wait()
            except subprocess.TimeoutExpired:
                _kill(proc)
                status, rc = "stopped", None
                break
            progress.append(f"step {i}/{len(steps)} finished (exit {rc})")
            if rc != 0:
                status = "error"
                break
        if status is None and run_id in _STOPPING:
            status = "stopped"
            rc = None
        if status is None:
            status = "success"
    finally:
        _update(run_id, status=status, exit_code=rc, notes=notes + progress)
        _log_footer(logf, commands or [], t0)
        logf.close()
        with _LOCK:
            _ACTIVE.pop(run_id, None)
            _STOPPING.discard(run_id)


def _watch(run_id: str, proc: subprocess.Popen, logf, timeout: float | None,
           cwd=None, ladder: dict | None = None,
           command: str | None = None, t0: float | None = None) -> None:
    try:
        if timeout:
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill(proc)
                _update(run_id, status="stopped", exit_code=None)
                return
        else:
            rc = proc.wait()
        if run_id in _STOPPING:
            _update(run_id, status="stopped", exit_code=None)
        else:
            status = "success" if rc == 0 else "error"
            _update(run_id, status=status, exit_code=rc)
            if status == "success" and ladder is not None:
                # M13.3: the hls muxer writes no master playlist — after a
                # clean ladder run fass synthesizes one from the run's
                # declared levels (into the run's working directory).
                note = _synthesize_master(cwd, ladder)
                if note:
                    _append_note(run_id, note)
    finally:
        _log_footer(logf, [command] if command else [], t0)
        logf.close()
        with _LOCK:
            _ACTIVE.pop(run_id, None)
            _STOPPING.discard(run_id)


def _kill(proc: subprocess.Popen) -> None:
    """Terminate the process group WE spawned (exact group, no patterns)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        if proc.poll() is not None:
            return
        time.sleep(0.25)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def stop_run(run_id: str) -> bool:
    with _LOCK:
        proc = _ACTIVE.get(run_id)
        if proc is not None and proc.poll() is None:
            _STOPPING.add(run_id)
    if proc is None or proc.poll() is not None:
        return False
    _kill(proc)
    return True


def list_runs() -> list[dict]:
    recs = []
    for p in sorted(run_dir().glob("*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        with _LOCK:
            proc = _ACTIVE.get(rec["id"])
        if rec["status"] == "running" and (proc is None or proc.poll() is not None):
            # the server restarted (or the process died without the watcher)
            rc = proc.poll() if proc is not None else None
            rec["status"] = "success" if rc == 0 else "interrupted"
            if rc is not None:
                rec["exit_code"] = rc
        recs.append(rec)
    return recs


def get_run(run_id: str) -> dict | None:
    p = _sidecar(run_id)
    if not p.exists() or "/" in run_id or run_id.startswith("."):
        return None
    rec = json.loads(p.read_text(encoding="utf-8"))
    with _LOCK:
        proc = _ACTIVE.get(run_id)
    if rec["status"] == "running" and proc is not None and proc.poll() is not None:
        rc = proc.poll()
        rec["status"] = "success" if rc == 0 else "interrupted"
        rec["exit_code"] = rc
    return rec


def log_tail(run_id: str, limit: int = 65536) -> str | None:
    p = _log_file(run_id)
    if not p.exists() or "/" in run_id or run_id.startswith("."):
        return None
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


# ------------------------------------------------------- log browsing (M10.7)

LOG_CAP_LINES = 10000   # a log longer than this is served as its tail
LOG_TAIL_LINES = 1000   # ... exactly this many last lines, with a banner


def _count_lines(p: Path) -> int:
    """Line count by streaming the file in chunks (never loaded whole)."""
    n = 0
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += chunk.count(b"\n")
    return n


def _tail_lines(p: Path, n: int) -> str:
    """The last n lines, read backwards from the end of the file."""
    with open(p, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        data = b""
        while pos > 0:
            step = min(1 << 16, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            if data.count(b"\n") > n:
                break
    lines = data.rstrip(b"\n").split(b"\n")
    return b"\n".join(lines[-n:]).decode("utf-8", "replace")


def serve_log(p: Path) -> str:
    """A log file's text for the browser. Files longer than LOG_CAP_LINES
    lines are served as their LAST LOG_TAIL_LINES lines, headed by a
    truncation banner (the whole file is never loaded)."""
    try:
        total = _count_lines(p)
    except OSError:
        return ""
    if total <= LOG_CAP_LINES:
        try:
            return p.read_text("utf-8", "replace")
        except OSError:
            return ""
    tail = _tail_lines(p, LOG_TAIL_LINES)
    return (f"[truncated: showing last {LOG_TAIL_LINES} of {total} lines]\n"
            + tail)


def _run_log_path(run_id: str) -> Path | None:
    """Where a run's log lives: the recorded log_path (a verbose run's log
    sits in {output}/log/), falling back to run_dir()/{run_id}.log."""
    if "/" in run_id or run_id.startswith("."):
        return None
    rec = get_run(run_id)
    if rec is None:
        return None
    lp = rec.get("log_path")
    if lp:
        p = Path(lp)
        if p.is_file():
            return p
    p = run_dir() / f"{run_id}.log"
    return p if p.is_file() else None


def read_run_log(run_id: str) -> str | None:
    """A run's log text (capped — see serve_log); None when unknown."""
    p = _run_log_path(run_id)
    return serve_log(p) if p is not None else None


def log_path_for(name: str) -> Path | None:
    """A log file by BASENAME in the runs dir or the verbose log dir
    (path-traversal guarded like output_file)."""
    if not name or "/" in name or name.startswith(".") or not name.endswith(".log"):
        return None
    for d in (run_dir(), verbose_log_dir()):
        if d is None:
            continue
        p = d / name
        if p.is_file():
            return p
    return None


def list_logs() -> list[dict]:
    """Every *.log in the runs dir (kind 'run') and in {output}/log
    (kind 'verbose'), newest first."""
    out: list[dict] = []
    seen: set[str] = set()
    for d, kind in ((run_dir(), "run"), (verbose_log_dir(), "verbose")):
        if d is None:
            continue
        for p in sorted(d.glob("*.log")):
            if p.name in seen:
                continue
            seen.add(p.name)
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "name": p.name,
                "kind": kind,
                "size": st.st_size,
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                       time.localtime(st.st_mtime)),
                "run_id": p.stem,
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def volume_file(target: str, name: str) -> Path | None:
    """A file in the input volume (target 'input') or the output volume
    (target 'output') by bare name — guarded like output_file."""
    if not name or "/" in name or name.startswith(".."):
        return None
    vol = _input_dir() if target == "input" else _output_dir()
    if not vol:
        return None
    p = Path(vol) / name
    return p if p.is_file() else None


def output_file(filename: str) -> Path | None:
    """A file inside the /output volume (path-traversal guarded)."""
    out_dir = _output_dir()
    if not out_dir:
        return None
    name = os.path.basename(filename)
    if not name or name != filename.strip() or ".." in filename:
        return None
    p = Path(out_dir) / name
    if not p.is_file():
        return None
    return p


def run_output_file(run_id: str) -> Path | None:
    """The recorded output file of a run, whichever volume it landed in —
    the /output volume, or the input folder for a chained processing
    (a '/input/<name>' destination). The output path is ours (resolve_paths),
    but it is still guarded: it must be a real file inside one of the volume
    directories, never an arbitrary path."""
    rec = get_run(run_id)
    if rec is None:
        return None
    dest = rec.get("output")
    if not dest or stream_scheme(dest) is not None:
        return None
    p = Path(dest)
    if not p.is_file():
        return None
    try:
        rp = p.resolve()
    except OSError:
        return None
    for vol in (_input_dir(), _output_dir()):
        if not vol:
            continue
        v = Path(vol).resolve()
        if rp == v or v in rp.parents:
            return p
    return None
