"""Deterministic command generation (PRD FR-4).

The argument array is built before any string is rendered. Paths travel
as single argv elements and are never concatenated into a shell command;
the POSIX renderer quotes each element independently (plan0.md / PRD
security section).
"""

from __future__ import annotations

from fass.planner import ExecutionPlan


def build_argv(ep: ExecutionPlan) -> list[str]:
    argv: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    for inp in ep.inputs:
        argv += inp.pre_opts
        argv += ["-i", inp.path]
    if ep.filter_complex:
        argv += ["-filter_complex", ep.filter_complex]
    elif ep.vf:
        argv += ["-vf", ep.vf]
    for m in ep.maps:
        argv += ["-map", m]
    argv += ep.output_opts
    argv.append(ep.output_path)
    return argv
