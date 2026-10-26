"""rag-data readiness probe (joint PRD 5.6 / 6.7) — container healthcheck.

Run as ``python -m rag.health`` from the repo root (the compose healthcheck
does exactly that inside the image, where the data volume is mounted at
/data and FASS_RAG_DATA=/data).

Data directory resolution (same precedence as rag/server.py):
``FASS_RAG_DATA``, else ``FASS_RAG_DATA_DIR``, else ``rag/corpus``
(CWD-relative). Layout is ``<data_dir>/<corpus_id>/manifest.json``.

Healthy means: the data dir exists AND at least one corpus subdir under it
carries a ``manifest.json``. On health, one line to stdout and exit 0:

    rag-data OK: /data (2 corpora)

On failure, a single reason line to stderr and exit 1. No network, and the
server module is never imported (the probe must work when its dependencies
are absent or its lifespan never ran).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_DATA_DIR = "rag/corpus"


def data_dir_from_env(environ: dict | None = None) -> Path:
    """Resolve the corpus root: FASS_RAG_DATA > FASS_RAG_DATA_DIR > default."""
    env = os.environ if environ is None else environ
    return Path(env.get("FASS_RAG_DATA") or env.get("FASS_RAG_DATA_DIR")
                or DEFAULT_DATA_DIR)


def count_corpora(data_dir: Path) -> int:
    """Number of corpus subdirs under data_dir that carry a manifest.json."""
    if not data_dir.is_dir():
        return 0
    return sum(1 for child in sorted(data_dir.iterdir())
               if child.is_dir() and (child / "manifest.json").is_file())


def main() -> int:
    data_dir = data_dir_from_env()
    if not data_dir.is_dir():
        print(f"rag-data MISSING: {data_dir} (no such directory)",
              file=sys.stderr)
        return 1
    n = count_corpora(data_dir)
    if n == 0:
        print(f"rag-data EMPTY: {data_dir} (no corpus subdir with "
              f"manifest.json)", file=sys.stderr)
        return 1
    print(f"rag-data OK: {data_dir} ({n} corpora)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
