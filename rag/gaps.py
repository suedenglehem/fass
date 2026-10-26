"""M11.3 gap analysis (PRD 7.3): coverage, backlog, drift, demand.

    python -m rag.gaps --corpus-root rag/corpus \
        --registry fass/registry/registry.json [--demand demand.jsonl] \
        --out gaps.json

Coverage: every registry filter name (registry['filters']['video'] +
['audio'] keys) must resolve to a manual section in each corpus;
unresolved names are listed.

Backlog: filters documented in the manuals but absent from the registry,
ranked — names documented in ALL corpora first, then alphabetical.

Drift: registry entries carrying 'options' compared against each corpus's
build-facts capture (a missing capture is tolerated, not an error):
name / type / default mismatches.

Demand: consented JSONL lines {"instruction", "capability"} clustered by
capability. Nothing is auto-added — this is a report only. Offline,
stdlib only, registry.json is read-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .author import (DOC_FILENAME, clean_text, corpus_version_key,
                     heading_names, iter_sections, parse_build_facts)


def registry_filter_names(registry: dict) -> list[str]:
    filters = registry.get("filters", {})
    names = list(filters.get("video", {})) + list(filters.get("audio", {}))
    return sorted(set(names))


def registry_entries(registry: dict) -> dict:
    filters = registry.get("filters", {})
    entries = {}
    entries.update(filters.get("video", {}))
    entries.update(filters.get("audio", {}))
    return entries


def corpus_sections(corpus_root: Path, corpus: str) -> dict:
    """{filter_name: heading_text} for every <h3 class="section"> heading;
    comma-group headings ('setpts, asetpts') register every listed name."""
    html_path = corpus_root / corpus / "docs" / DOC_FILENAME
    if not html_path.is_file():
        return {}
    sections = {}
    for heading_html, _body in iter_sections(
            html_path.read_text(encoding="utf-8", errors="replace")):
        names = heading_names(heading_html)
        if names:
            text = clean_text(heading_html)
            for n in names:
                sections[n] = text
    return sections


def registry_default(option: dict):
    desc = option.get("description", "")
    m = re.search(r"\(default ([^)]*)\)", desc)
    return m.group(1) if m else None


_TYPE_COMPAT = {
    "int": {"int", "int64"},
    "number": {"int", "int64", "float", "double", "rational", "duration"},
    "bool": {"boolean", "int", "flags"},
    "choice": {"int", "flags", "string"},
    "string": {"string", "color", "image_size", "duration", "video_rate",
               "channel_layout", "pix_fmt"},
    "flagset": {"flags", "int"},
}


def type_compatible(reg_kind: str, build_type: str) -> bool:
    """Registry 'kind' vs build-help '<type>'. Enum options are <int>/<flags>
    in build help; numeric options may surface as <float>/<double>/<rational>
    (or <int>/<int64>, or <duration> for time values); textual options as
    <color>/<image_size>/<duration>/..."""
    if reg_kind == build_type:
        return True
    return build_type in _TYPE_COMPAT.get(reg_kind, ())


def compute_coverage(corpus_root: Path, corpora: list[str],
                     names: list[str]) -> dict:
    per_corpus = {}
    unresolved_any = set()
    for corpus in corpora:
        sections = set(corpus_sections(corpus_root, corpus))
        resolved = [n for n in names if n in sections]
        unresolved = [n for n in names if n not in sections]
        per_corpus[corpus] = {"resolved": resolved, "unresolved": unresolved}
        unresolved_any.update(unresolved)
    return {
        "corpora": corpora,
        "registry_filters": len(names),
        "per_corpus": per_corpus,
        "unresolved": sorted(unresolved_any),
    }


def compute_backlog(corpus_root: Path, corpora: list[str],
                    names: list[str]) -> list:
    documented = {c: set(corpus_sections(corpus_root, c)) for c in corpora}
    registry_set = set(names)
    all_names = set().union(*documented.values()) if documented else set()
    missing = all_names - registry_set
    in_all = sorted((n for n in missing
                     if all(n in documented[c] for c in corpora)),
                    key=lambda n: (n,))
    rest = sorted(n for n in missing if n not in in_all)
    return in_all + rest


def compute_drift(corpus_root: Path, corpora: list[str],
                  entries: dict) -> list:
    drift = []
    for corpus in corpora:
        for name in sorted(entries):
            entry = entries[name]
            reg_opts = entry.get("options")
            if not reg_opts:
                continue
            bf = corpus_root / corpus / "build-facts" / f"filter-{name}.txt"
            if not bf.is_file():
                continue  # missing capture tolerated
            build_opts = parse_build_facts(
                bf.read_text(encoding="utf-8", errors="replace"), name)
            for opt in sorted(reg_opts):
                reg = reg_opts[opt]
                b = build_opts.get(opt)
                base = {"corpus": corpus, "filter": name, "option": opt}
                if b is None:
                    drift.append(base | {"kind": "name",
                                         "detail": "registry option absent "
                                                   "from build facts"})
                    continue
                reg_kind = reg.get("kind")
                if reg_kind and not type_compatible(reg_kind, b["type"]):
                    drift.append(base | {"kind": "type",
                                          "registry": reg_kind,
                                          "build": b["type"]})
                rd = registry_default(reg)
                bd = b.get("default")
                rd_n, bd_n = _norm(rd), _norm(bd)
                if rd_n is not None and bd_n is not None and rd_n != bd_n:
                    drift.append(base | {"kind": "default",
                                          "registry": rd, "build": bd})
    return drift


def _norm(value):
    if value is None:
        return None
    s = str(value).strip().strip('"')
    if s == "false":
        s = "0"
    elif s == "true":
        s = "1"
    return s


def compute_demand(demand_path: Path | None) -> dict:
    clusters: dict = {}
    count = 0
    if demand_path is not None and demand_path.is_file():
        for line in demand_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cap = rec.get("capability", "")
            clusters.setdefault(cap, []).append(rec.get("instruction", ""))
            count += 1
    return {
        "clusters": {cap: sorted(items) for cap, items in sorted(clusters.items())},
        "count": count,
        "auto_added": [],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="rag.gaps",
        description="M11.3 gap analysis: coverage, backlog, drift, demand "
                    "(PRD 7.3).")
    ap.add_argument("--corpus-root", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--demand", default=None,
                    help="JSONL {\"instruction\", \"capability\"}")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    corpus_root = Path(args.corpus_root)
    corpora = sorted((d.name for d in corpus_root.iterdir() if d.is_dir()),
                     key=corpus_version_key)
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    names = registry_filter_names(registry)

    report = {
        "coverage": compute_coverage(corpus_root, corpora, names),
        "backlog": compute_backlog(corpus_root, corpora, names),
        "drift": compute_drift(corpus_root, corpora, registry_entries(registry)),
        "demand": compute_demand(Path(args.demand) if args.demand else None),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    print(f"rag.gaps: corpora={corpora} registry_filters={len(names)} "
          f"unresolved={len(report['coverage']['unresolved'])} "
          f"backlog={len(report['backlog'])} drift={len(report['drift'])} "
          f"demand={report['demand']['count']}")
    print(f"  written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
