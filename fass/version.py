"""The fass version string — single source of truth (bump here).

Format: vVERSION_ID.Commit_ID — Commit_ID is the short sha of the code
commit this version was cut from. On EVERY code commit: recover that
commit's id, write it here, commit again (the version-bump commit points
back at the code commit it follows — no recursive bump; docs-only commits
do not trigger a bump).
"""

__version__ = "v1.137036.f16e209"
