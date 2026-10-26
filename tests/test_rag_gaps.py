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
