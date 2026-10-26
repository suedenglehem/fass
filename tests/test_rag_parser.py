"""Parser tests (PRD 5.2): fixture round-trip + makeinfo table idioms."""

from __future__ import annotations

import json
from pathlib import Path

from rag.parser import document_to_dict, parse_file, parse_html

FIXTURES = Path(__file__).resolve().parent.parent / "rag" / "fixtures"


def _canonical(doc) -> str:
    return json.dumps(document_to_dict(doc), indent=2, sort_keys=True)


def _roundtrip(name: str) -> str:
    """Parse a fixture and assert the canonical form is byte-identical to
    the checked-in expected JSON (PRD 5.2: fixtures round-trip)."""
    actual = _canonical(parse_file(FIXTURES / f"{name}.html"))
    expected = (FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8")
    expected = json.dumps(json.loads(expected), indent=2, sort_keys=True)
    assert actual == expected, f"{name} fixture drifted from parsed structure"
    return actual


# ------------------------------------------------------------- round-trips


def test_filter_options_fixture_roundtrip():
    _roundtrip("filter_options")


def test_nested_headings_fixture_roundtrip():
    _roundtrip("nested_headings")


# -------------------------------------------------------- filter_options


def test_filter_options_structure():
    d = json.loads(_roundtrip("filter_options"))
    assert d["title"] == "FFmpeg Test Manual"
    tflip = d["sections"][2]
    assert [b["kind"] for b in tflip["blocks"]] == \
        ["paragraph", "paragraph", "example", "deflist"]
    # example kept verbatim (entity-decoded, trailing newline intact)
    ex = tflip["blocks"][2]
    assert ex["text"] == 'ffmpeg -i in.avi -vf "tflip" out.avi\n'


def test_continuation_dd_merges_into_last_entry():
    d = json.loads(_roundtrip("filter_options"))
    entries = {e["term"]: e["desc"]
               for e in d["sections"][2]["blocks"][3]["entries"]}
    # "mirror" had two <dd>s: both paragraphs live in ONE entry
    assert list(entries) == ["strength", "mirror"]
    assert [b["kind"] for b in entries["mirror"]] == ["paragraph", "paragraph"]
    assert entries["mirror"][1]["text"] == "Second paragraph for mirror."


# -------------------------------------------------------- nested_headings


def test_toc_div_excluded():
    d = json.loads(_roundtrip("nested_headings"))
    titles = [s["title"] for s in d["sections"]]
    assert "Table of Contents" not in titles
    assert len(d["sections"]) == 4  # Alpha, Beta, Gamma, Delta — not the TOC


def test_heading_hierarchy():
    d = json.loads(_roundtrip("nested_headings"))
    secs = [(s["anchor"], s["number"], s["title"], s["level"])
            for s in d["sections"]]
    assert secs == [
        ("Alpha", "1", "Alpha chapter", 2),
        ("Beta", "1.1", "Beta section", 3),
        ("Gamma", "1.1.1", "Gamma subsection", 4),
        ("Delta", "2", "Delta", 2),
    ]


def test_entity_decoding():
    d = json.loads(_roundtrip("nested_headings"))
    gamma = d["sections"][2]["blocks"][0]
    assert gamma["text"] == "Gamma text with & < > entities."


def test_internal_links_captured():
    d = json.loads(_roundtrip("nested_headings"))
    beta_para = d["sections"][1]["blocks"][0]
    assert beta_para["links"] == ["Gamma"]
    assert beta_para["text"] == "See the gamma subsection for details."


def test_list_items_inline_tags_collapsed():
    d = json.loads(_roundtrip("nested_headings"))
    lst = d["sections"][0]["blocks"][1]
    assert lst["items"] == ["first item", "second item with emphasis"]


def test_heading_only_section_empty_blocks():
    d = json.loads(_roundtrip("nested_headings"))
    assert d["sections"][3]["blocks"] == []


# -------------------------------------------------- makeinfo dl idioms


def _parse_body(html: str):
    return parse_html(
        f"<body><h2>1 X</h2>{html}</body>", source="inline.html")


def test_shared_dd_one_description_per_name():
    # a run of <dt>s before one <dd> share the description (w/h idiom)
    doc = _parse_body(
        '<dl><dt><samp>w</samp></dt><dt><samp>h</samp></dt>'
        '<dd><p>width and height</p></dd></dl>')
    (dl,) = doc.sections[0].blocks
    assert [t for t, _ in dl.entries] == ["w", "h"]
    assert all(d[0].text == "width and height" for _, d in dl.entries)


def test_nested_value_table_becomes_entry_description():
    # eof_action idiom: a <dd> containing a nested <dl> of accepted values
    doc = _parse_body(
        '<dl><dt>eof_action</dt><dd><p>Accepts one of:</p>'
        '<dl><dt>repeat</dt><dd><p>Repeat.</p></dd>'
        '<dt>endall</dt><dd><p>End.</p></dd>'
        '<dt>pass</dt><dd><p>Pass.</p></dd></dl></dd></dl>')
    (outer,) = doc.sections[0].blocks
    (term, desc) = outer.entries[0]
    assert term == "eof_action"
    assert [b.kind for b in desc] == ["paragraph", "deflist"]
    assert [t for t, _ in desc[1].entries] == ["repeat", "endall", "pass"]
    # the outer dl is the only deflist at section level
    assert len(doc.sections[0].blocks) == 1


def test_dt_without_dd_kept_with_empty_description():
    doc = _parse_body('<dl><dt>lone</dt></dl>')
    (dl,) = doc.sections[0].blocks
    assert dl.entries == [("lone", [])]


def test_mixed_named_and_described_entries():
    doc = _parse_body(
        '<dl><dt>a</dt><dd><p>desc a</p></dd><dt>b</dt></dl>')
    (dl,) = doc.sections[0].blocks
    assert [t for t, _ in dl.entries] == ["a", "b"]
    assert dl.entries[0][1][0].text == "desc a"
    assert dl.entries[1][1] == []


def test_nested_ul_flattens_into_items():
    doc = _parse_body(
        '<ul><li>one</li><ul><li>two</li></ul><li>three</li></ul>')
    (lst,) = doc.sections[0].blocks
    assert lst.items == ["one", "two", "three"]


def test_pull_right_span_dropped_from_heading():
    doc = _parse_body(
        '<a name="Y"></a><h3>1.1 Y'
        '<span class="pull-right"><a href="#toc-y">...#...</a></span></h3>'
        '<p>y text</p>')
    s = doc.sections[-1]  # _parse_body's own "1 X" section comes first
    assert s.heading.title == "Y"
    assert s.heading.anchor == "Y"
    assert s.blocks[0].text == "y text"


def test_copiable_link_markup_from_6_1_1_deb():
    # texinfo 6.x (6.1.1 Ubuntu deb): the heading text is wrapped in a
    # plain <span>, there is NO <a name> before the heading, and the
    # permalink is a copiable link INSIDE the heading whose text is a
    # pilcrow (" &para;") — the link target must become the section
    # anchor and the pilcrow must not leak into the title
    doc = parse_html(
        '<body>'
        '<h2 class="chapter"><span>8 Audio Filters'
        '<a class="copiable-link" href="#Audio-Filters"> &para;</a></span></h2>'
        '<p>intro</p>'
        '<h3 class="section"><span>8.1 acompressor'
        '<a class="copiable-link" href="#acompressor"> &para;</a></span></h3>'
        '<p>compressor text</p>'
        '</body>', source="inline.html")
    chap, sec = doc.sections
    assert chap.heading.title == "Audio Filters"
    assert chap.heading.anchor == "Audio-Filters"
    assert sec.heading.title == "acompressor"
    assert sec.heading.anchor == "acompressor"
    assert sec.blocks[0].text == "compressor text"


def test_copiable_link_does_not_override_name_anchor():
    # an explicit <a name> before the heading wins over the in-heading
    # copiable link target
    doc = _parse_body(
        '<a name="Explicit"></a><h3>1.1 Z'
        '<a class="copiable-link" href="#implicit"> &para;</a></h3>'
        '<p>z text</p>')
    s = doc.sections[-1]
    assert s.heading.anchor == "Explicit"
    assert s.heading.title == "Z"
