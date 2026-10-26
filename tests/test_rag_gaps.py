"""M11.3 gap analysis tests (PRD 7.3): coverage, backlog ranking, drift,
demand clusters, missing build-facts tolerated. Fully synthetic tmp_path
root — never rag/corpus.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CORPORA = ["ffmpeg-6.1.1", "ffmpeg-9.0.1"]


def section(name: str, num: str) -> str:
    return (f'<h3 class="section"><span>{num} {name}'
            f'<a class="copiable-link" href="#{name}-1"> &para;</a>'
            f"</span></h3>\n<p>{name} body.\n</p>\n")


def html_for(corpus: str) -> str:
    html = section("alpha", "1.1") + section("beta", "1.2") + section("zeta", "1.3")
    if corpus == "ffmpeg-9.0.1":
        html += section("gamma", "1.4")  # documented in one corpus only
    return html


ALPHA_BUILD = """Filter alpha
alpha AVOptions:
   x                 <double>     ..FV....... set x (from 0 to 10) (default 9)

Exiting with exit code 0
"""

REGISTRY = {
    "filters": {
        "video": {
            "alpha": {
                "description": "alpha filter",
                "options": {
                    "x": {"kind": "int", "description": "set x (default 5)"},
                    "q": {"kind": "string", "description": "set q"},
                },
            },
            "beta": {"description": "beta filter"},
        },
        "audio": {},
    },
}


def build_root(tmp_path: Path) -> Path:
    root = tmp_path / "corpus-root"
    for corpus in CORPORA:
        docs = root / corpus / "docs"
        docs.mkdir(parents=True)
        (docs / "ffmpeg-filters.html").write_text(html_for(corpus),
                                                  encoding="utf-8")
    bf = root / "ffmpeg-6.1.1" / "build-facts"
    bf.mkdir()
    (bf / "filter-alpha.txt").write_text(ALPHA_BUILD, encoding="utf-8")
    # ffmpeg-9.0.1 deliberately has NO build-facts dir (tolerated case)
    return root


def run_gaps(corpus_root, registry, out, demand=None):
    args = ["-m", "rag.gaps", "--corpus-root", str(corpus_root),
            "--registry", str(registry)]
    if demand is not None:
        args += ["--demand", str(demand)]
    args += ["--out", str(out)]
    return subprocess.run([sys.executable, *args], cwd=ROOT,
                          capture_output=True, text=True)


def _setup(tmp_path: Path, demand: bool = True):
    root = build_root(tmp_path)
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    out = tmp_path / "gaps.json"
    demand_path = None
    if demand:
        demand_path = tmp_path / "demand.jsonl"
        demand_path.write_text(
            json.dumps({"instruction": "make it louder",
                        "capability": "loudness"}) + "\n"
            + json.dumps({"instruction": "boost the volume",
                          "capability": "loudness"}) + "\n"
            + json.dumps({"instruction": "add reverb",
                          "capability": "audio_fx"}) + "\n",
            encoding="utf-8")
    r = run_gaps(root, reg, out, demand_path)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text())


def test_coverage(tmp_path):
    report = _setup(tmp_path)
    cov = report["coverage"]
    assert cov["corpora"] == CORPORA
    assert cov["registry_filters"] == 2
    for corpus in CORPORA:
        assert cov["per_corpus"][corpus]["resolved"] == ["alpha", "beta"]
        assert cov["per_corpus"][corpus]["unresolved"] == []
    assert cov["unresolved"] == []


def test_backlog_ranking(tmp_path):
    report = _setup(tmp_path)
    # zeta documented in ALL corpora -> first; gamma in one corpus -> after
    assert report["backlog"] == ["zeta", "gamma"]


def test_drift(tmp_path):
    report = _setup(tmp_path)
    drift = report["drift"]
    kinds = {(d["corpus"], d["filter"], d["option"], d["kind"])
             for d in drift}
    assert kinds == {
        ("ffmpeg-6.1.1", "alpha", "q", "name"),        # absent from build
        ("ffmpeg-6.1.1", "alpha", "x", "type"),        # int vs double
        ("ffmpeg-6.1.1", "alpha", "x", "default"),     # 5 vs 9
    }
    # beta has no 'options' -> never drifted; 9.0.1 has no capture -> none
    assert all(d["corpus"] == "ffmpeg-6.1.1" for d in drift)


def test_missing_build_facts_tolerated(tmp_path):
    report = _setup(tmp_path)
    assert all(d["corpus"] != "ffmpeg-9.0.1" for d in report["drift"])


def test_demand_clusters(tmp_path):
    report = _setup(tmp_path)
    demand = report["demand"]
    assert demand["count"] == 3
    assert demand["clusters"] == {
        "audio_fx": ["add reverb"],
        "loudness": ["boost the volume", "make it louder"],
    }
    assert demand["auto_added"] == []


def test_no_demand_flag(tmp_path):
    root = build_root(tmp_path)
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    out = tmp_path / "gaps.json"
    r = run_gaps(root, reg, out)
    assert r.returncode == 0, r.stderr
    report = json.loads(out.read_text())
    assert report["demand"] == {"clusters": {}, "count": 0,
                                "auto_added": []}


def test_comma_group_heading(tmp_path):
    # 'foo, bfoo' heading registers both names: 'foo' resolves coverage,
    # 'bfoo' lands in the backlog (all corpora -> first tier)
    root = tmp_path / "corpus-root"
    for corpus in CORPORA:
        docs = root / corpus / "docs"
        docs.mkdir(parents=True)
        (docs / "ffmpeg-filters.html").write_text(
            section("alpha", "1.1") + section("foo, bfoo", "1.2"),
            encoding="utf-8")
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    out = tmp_path / "gaps.json"
    r = run_gaps(root, reg, out)
    assert r.returncode == 0, r.stderr
    report = json.loads(out.read_text())
    for c in CORPORA:
        assert report["coverage"]["per_corpus"][c]["resolved"] == ["alpha"], c
        assert report["coverage"]["per_corpus"][c]["unresolved"] == ["beta"], c
    assert report["backlog"] == ["bfoo", "foo"]


def test_type_compatible_mapping():
    from rag.gaps import type_compatible
    assert type_compatible("int", "int")          # exact
    assert type_compatible("number", "float")
    assert type_compatible("number", "double")
    assert type_compatible("number", "rational")
    assert type_compatible("number", "duration")
    assert type_compatible("int", "int64")
    assert type_compatible("bool", "boolean")
    assert type_compatible("bool", "int")
    assert type_compatible("choice", "flags")
    assert type_compatible("choice", "int")
    assert type_compatible("string", "color")
    assert type_compatible("string", "image_size")
    assert type_compatible("string", "duration")
    assert type_compatible("flagset", "flags")
    assert not type_compatible("int", "double")
    assert not type_compatible("number", "string")
    assert not type_compatible("string", "int")


# ------------------------------------------------- drift default extraction

def test_registry_default_nested_parens():
    # M11.4: showvolume's c default is an EXPRESSION that itself contains
    # ')' — a first-paren stop truncated it and fabricated drift rows
    from rag.gaps import registry_default
    assert registry_default(
        {"description": ("set volume color expression; may use VOLUME, PEAK "
                         "and CHANNEL "
                         "(default PEAK*255+floor((1-PEAK)*255)*256+0xff000000)"
                         )}) == "PEAK*255+floor((1-PEAK)*255)*256+0xff000000"
    # a note AFTER the default parens (acompressor level_sc style) must not
    # leak into the captured value
    assert registry_default(
        {"description": ("set sidechain gain (default 1) — the AVOptions "
                         "block is shared with sidechaincompress")}) == "1"
    assert registry_default({"description": "set q"}) is None
    # the real registry entry survives the extraction end-to-end
    from fass.registry import REGISTRY
    c = REGISTRY["filters"]["audio"]["showvolume"]["options"]["c"]
    assert registry_default(c) == \
        "PEAK*255+floor((1-PEAK)*255)*256+0xff000000"


def test_drift_all_equals_expanded_flag_sum(tmp_path):
    # M11.4: the registry documents a flagset default as 'all' while the
    # build help prints the expanded flag sum — same value, not drift
    from rag.gaps import compute_drift
    root = tmp_path / "corpus-root"
    bf = root / "ffmpeg-9.0.1" / "build-facts"
    bf.mkdir(parents=True)
    (bf / "filter-aspectralstats.txt").write_text(
        "Filter aspectralstats\n"
        "aspectralstats AVOptions:\n"
        "   measure             <int>          ..A.... "
        "select the parameters which are measured "
        "(default all+mean+variance+centroid)\n"
        "Exiting with exit code 0\n", encoding="utf-8")
    entries = {"aspectralstats": {"options": {
        "measure": {"kind": "flagset", "values": ["mean", "variance"],
                    "description": "select the parameters (default all)"}}}}
    assert compute_drift(root, ["ffmpeg-9.0.1"], entries) == []
    # a NARROWER registry default is real drift
    entries["aspectralstats"]["options"]["measure"]["description"] = \
        "select the parameters (default mean)"
    drift = compute_drift(root, ["ffmpeg-9.0.1"], entries)
    assert [(d["kind"], d["option"]) for d in drift] == \
        [("default", "measure")]
