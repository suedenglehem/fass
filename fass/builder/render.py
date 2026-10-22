"""Shell rendering for display only (PRD: copyable POSIX command).

Other platforms should consume the argument array directly. Windows
shell rendering is intentionally not implemented and must not be
represented as supported.
"""

from __future__ import annotations

import shlex


def render_posix(argv: list[str]) -> str:
    """Render an argument array as a POSIX shell line.

    Each element is quoted independently with shlex, so filenames with
    spaces, quotes or shell metacharacters stay filename content and
    cannot introduce new commands.
    """
    return shlex.join(argv)
