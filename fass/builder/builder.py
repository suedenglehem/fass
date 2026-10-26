"""Deterministic command generation (PRD FR-4).

The argument array is built before any string is rendered. Paths travel
as single argv elements and are never concatenated into a shell command;
the POSIX renderer quotes each element independently (PRD security
section).
"""

from __future__ import annotations

from fass.planner import ExecutionPlan


def _pass_argv(loglevel: str, inputs, vf: str | None,
               filter_complex: str | None, af: str | None, maps: list[str],
               output_opts: list[str], output_path: str) -> list[str]:
    argv: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", loglevel]
    for inp in inputs:
        argv += inp.pre_opts
        argv += ["-i", inp.path]
    if filter_complex:
        argv += ["-filter_complex", filter_complex]
    elif vf:
        argv += ["-vf", vf]
    if af:
        argv += ["-af", af]
    for m in maps:
        argv += ["-map", m]
    argv += list(output_opts)
    argv.append(output_path)
    return argv


def build_argv(ep: ExecutionPlan) -> list[str]:
    return _pass_argv(ep.loglevel, ep.inputs, ep.vf, ep.filter_complex,
                      ep.af, ep.maps, ep.output_opts, ep.output_path)


def build_argv_all(ep: ExecutionPlan) -> list[list[str]]:
    """Every argv of the plan, in execution order (the final pass last).

    Single-pass plans (passes is None) are the common case: the result is
    the one-pass argv, so callers can use this uniformly."""
    if not ep.passes:
        return [build_argv(ep)]
    return [
        _pass_argv(p.loglevel, p.inputs, p.vf, p.filter_complex, p.af,
                   p.maps, p.output_opts, p.output_path)
        for p in ep.passes
    ]
