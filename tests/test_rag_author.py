"""M11.3 authoring pipeline tests (PRD 7.1): fixture ebur128 run, synthetic
classification branches, registry untouched, determinism.

Real corpora are gitignored/absent in clean worktrees — everything here runs
offline against tests/fixtures/rag_author/ and synthetic tmp_path roots.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "rag_author"
REGISTRY = ROOT / "fass" / "registry" / "registry.json"

CORPORA = ["ffmpeg-6.1.1", "ffmpeg-9.0.1"]


def run_author(component, corpora, corpus_root, out):
    args = ["-m", "rag.author", "--component", component]
    for c in corpora:
        args += ["--corpus", c]
    args += ["--corpus-root", str(corpus_root), "--out", str(out)]
    return subprocess.run([sys.executable, *args], cwd=ROOT,
                          capture_output=True, text=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# real-fixture pilot: ebur128, verified_both
# --------------------------------------------------------------------------

def test_ebur128_fixture_verified_both(tmp_path):
    out = tmp_path / "ebur128.json"
    r = run_author("filter:ebur128", CORPORA, FIXTURES, out)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())

    assert d["filter"] == "ebur128"
    assert d["kind"] == "filter"
    assert d["classification"] == "verified_both"
    for c in CORPORA:
        assert d["docs"][c]["anchor"] == "ebur128-1", c
        assert len(d["docs"][c]["options"]) == 17, c
        assert len(d["builds"][c]["options"]) == 17, c
    assert d["option_diffs"] == []
    assert d["provenance"]["verified_builds"] == ["6.1.1", "9.0.1"]
    assert d["provenance"]["reviewed"] is False
    assert d["review"]["ready_for_registry"] is False
    assert any("description" in n for n in d["review"]["notes"])
    assert any("aliases" in n for n in d["review"]["notes"])
    # review report on stdout
    assert "verified_both" in r.stdout


def test_ebur128_fixture_defaults(tmp_path):
    out = tmp_path / "ebur128.json"
    assert run_author("filter:ebur128", CORPORA, FIXTURES, out).returncode == 0
    d = json.loads(out.read_text())
    docs = d["docs"]["ffmpeg-6.1.1"]["options"]
    builds = d["builds"]["ffmpeg-6.1.1"]["options"]
    assert docs["video"]["default"] == "0"
    assert docs["meter"]["default"] == "9"
    assert docs["metadata"]["default"] == "0"
    assert docs["size"]["default"] is None  # 'Default and minimum...' is not
    assert docs["panlaw"]["default"] is None  # a 'Default is X.' sentence
    assert builds["video"]["default"] == "false"
    assert builds["meter"]["min"] == "9"
    assert builds["meter"]["max"] == "18"
    assert builds["panlaw"]["default"] == "-3.0103"
    assert builds["size"]["type"] == "image_size"


# --------------------------------------------------------------------------
# real-fixture edge cases: grouped <dt>s (signalstats), prose form (blackframe)
# --------------------------------------------------------------------------

def test_signalstats_grouped_dts(tmp_path):
    out = tmp_path / "signalstats.json"
    r = run_author("filter:signalstats", CORPORA, FIXTURES, out)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    assert d["classification"] == "verified_both"
    for c in CORPORA:
        opts = d["docs"][c]["options"]
        # the two <dt>s before one combined <dd> both survive, and the
        # alias group <dt> "color, c" splits into both names
        assert set(opts) == {"stat", "out", "color", "c"}, c
        assert opts["stat"]["description"] == opts["out"]["description"]
        assert opts["stat"]["default"] is None
        assert opts["color"]["description"] == opts["c"]["description"]
        assert set(d["builds"][c]["options"]) == {"stat", "out", "c", "color"}
    # docs and builds line up 1:1 (alias group included) -> no diffs
    assert d["option_diffs"] == []
    assert d["provenance"]["verified_builds"] == ["6.1.1", "9.0.1"]


def test_blackframe_prose_options(tmp_path):
    out = tmp_path / "blackframe.json"
    r = run_author("filter:blackframe", CORPORA, FIXTURES, out)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    assert d["classification"] == "verified_both"
    for c in CORPORA:
        # anchor has no -1 suffix
        assert d["docs"][c]["anchor"] == "blackframe", c
        opts = d["docs"][c]["options"]
        # prose: amount + the in-prose alias pair threshold/thresh
        assert set(opts) == {"amount", "threshold", "thresh"}, c
        assert opts["amount"]["default"] == "98"
        assert opts["threshold"]["default"] == "32"
        assert opts["thresh"]["default"] == "32"
    # prose defaults match the build defaults exactly
    assert d["option_diffs"] == []
    assert any("PROSE" in n for n in d["review"]["notes"])


# --------------------------------------------------------------------------
# synthetic corpora: the other classification branches + form (a)
# --------------------------------------------------------------------------

FORM_A_HTML = """
<h3 class="section"><span>5.1 myfilter<a class="copiable-link" href="#myfilter-1"> &para;</a></span></h3>
<p>My synthetic filter.
</p>
<h4 class="subsection"><span>5.1.1 Options<a class="copiable-link" href="#Options-99"> &para;</a></span></h4>
<dl class="table">
<dt><samp class="option">alpha</samp></dt>
<dd><p>Set alpha. Default is 5.
</p></dd>
<dt><samp class="option">beta</samp></dt>
<dd><p>Enable beta.
</p></dd>
</dl>
<h4 class="subsection"><span>5.1.2 Examples<a class="copiable-link" href="#Examples-99"> &para;</a></span></h4>
<p>ffplay -i x -vf myfilter
</p>
<h3 class="section"><span>5.2 otherfilter<a class="copiable-link" href="#otherfilter-1"> &para;</a></span></h3>
<p>Not the one we author.
</p>
"""

FORM_A_BUILD = """Filter myfilter
  A synthetic filter.
myfilter AVOptions:
   alpha             <int>        ..FV....... set alpha (from 0 to 10) (default 5)
   beta              <boolean>    ..FV....... enable beta (default false)

Exiting with exit code 0
"""

FORM_B_HTML = """
<h3 class="section">20.1 metered<span class="pull-right"><a class="anchor hidden-xs" href="#metered-1" aria-hidden="true">#</a> <a class="anchor hidden-xs"href="#toc-metered-1" aria-hidden="true">TOC</a></span></h3>
<p>Inline-form filter.
</p>
<p>The filter accepts the following options:
</p>
<dl class="table">
<dt><samp class="option">meter</samp></dt>
<dd><p>Set the meter. Default is 9.
</p></dd>
</dl>
<h3 class="section">20.2 other<span class="pull-right"><a class="anchor hidden-xs" href="#other-1" aria-hidden="true">#</a></span></h3>
<p>Not it.
</p>
"""


def make_corpus(root: Path, corpus: str, html: str | None = None,
                build_facts: str | None = None, name: str = "myfilter"):
    docs = root / corpus / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    if html is not None:
        (docs / "ffmpeg-filters.html").write_text(html, encoding="utf-8")
    if build_facts is not None:
        bf = root / corpus / "build-facts"
        bf.mkdir(exist_ok=True)
        (bf / f"filter-{name}.txt").write_text(build_facts, encoding="utf-8")


def test_form_a_options_heading(tmp_path):
    root = tmp_path / "corpus-root"
    for c in CORPORA:
        make_corpus(root, c, FORM_A_HTML, FORM_A_BUILD)
    out = tmp_path / "draft.json"
    r = run_author("filter:myfilter", CORPORA, root, out)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    assert d["classification"] == "verified_both"
    for c in CORPORA:
        opts = d["docs"][c]["options"]
        assert set(opts) == {"alpha", "beta"}
        assert opts["alpha"]["default"] == "5"
        assert opts["alpha"]["description"].startswith("Set alpha.")
        assert d["builds"][c]["options"]["alpha"]["min"] == "0"
        assert d["builds"][c]["options"]["beta"]["type"] == "boolean"
    assert d["option_diffs"] == []
    assert d["docs"]["ffmpeg-6.1.1"]["anchor"] == "myfilter-1"


def test_present_in(tmp_path):
    root = tmp_path / "corpus-root"
    make_corpus(root, "ffmpeg-6.1.1", FORM_A_HTML, FORM_A_BUILD)
    make_corpus(root, "ffmpeg-9.0.1",
                FORM_A_HTML.replace("myfilter", "notmyfilter"))
    out = tmp_path / "draft.json"
    assert run_author("filter:myfilter", CORPORA, root, out).returncode == 0
    d = json.loads(out.read_text())
    assert d["classification"] == "present_in"
    assert d["present_in"] == ["ffmpeg-6.1.1"]
    assert d["min_version"] == "6.1.1"


def test_docs_only(tmp_path):
    root = tmp_path / "corpus-root"
    for c in CORPORA:
        make_corpus(root, c, FORM_A_HTML)  # no build-facts anywhere
    out = tmp_path / "draft.json"
    assert run_author("filter:myfilter", CORPORA, root, out).returncode == 0
    d = json.loads(out.read_text())
    assert d["classification"] == "docs_only"
    assert d["builds"] == {}
    assert d["provenance"]["verified_builds"] == []


def test_undocumented(tmp_path):
    root = tmp_path / "corpus-root"
    for c in CORPORA:
        make_corpus(root, c,
                    FORM_A_HTML.replace("myfilter", "notmyfilter"),
                    FORM_A_BUILD)
    out = tmp_path / "draft.json"
    assert run_author("filter:myfilter", CORPORA, root, out).returncode == 0
    d = json.loads(out.read_text())
    assert d["classification"] == "undocumented"
    assert d["docs"] == {}
    assert len(d["builds"]) == 2


def test_conflict_default(tmp_path):
    root = tmp_path / "corpus-root"
    build = """Filter metered
metered AVOptions:
   meter             <int>        ..FV....... set the meter (from 9 to 18) (default 18)

Exiting with exit code 0
"""
    for c in CORPORA:
        make_corpus(root, c, FORM_B_HTML, build, name="metered")
    out = tmp_path / "draft.json"
    assert run_author("filter:metered", CORPORA, root, out).returncode == 0
    d = json.loads(out.read_text())
    assert d["classification"] == "conflict"
    kinds = {(x["corpus"], x["option"], x["kind"]) for x in d["option_diffs"]}
    assert kinds == {(c, "meter", "default_conflict") for c in CORPORA}
    assert d["review"]["ready_for_registry"] is False
    assert any("conflict" in n.lower() for n in d["review"]["notes"])


def test_marker_without_dl(tmp_path):
    # form (b) marker present but its <dl> is missing: no options, no crash
    html = re.sub(r"<dl.*?</dl>", "", FORM_B_HTML, flags=re.S)
    build = """Filter metered
metered AVOptions:
   meter             <int>        ..FV....... set the meter (default 9)

Exiting with exit code 0
"""
    root = tmp_path / "corpus-root"
    for c in CORPORA:
        make_corpus(root, c, html, build, name="metered")
    out = tmp_path / "draft.json"
    r = run_author("filter:metered", CORPORA, root, out)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    for c in CORPORA:
        assert d["docs"][c]["options"] == {}, c
    kinds = {(x["corpus"], x["option"], x["kind"]) for x in d["option_diffs"]}
    assert kinds == {(c, "meter", "undocumented_option") for c in CORPORA}


# --------------------------------------------------------------------------
# invariants
# --------------------------------------------------------------------------

def test_registry_untouched(tmp_path):
    before = _sha256(REGISTRY)
    out = tmp_path / "ebur128.json"
    r = run_author("filter:ebur128", CORPORA, FIXTURES, out)
    assert r.returncode == 0, r.stderr
    assert _sha256(REGISTRY) == before


def test_determinism(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert run_author("filter:ebur128", CORPORA, FIXTURES, a).returncode == 0
    assert run_author("filter:ebur128", CORPORA, FIXTURES, b).returncode == 0
    assert a.read_bytes() == b.read_bytes()
