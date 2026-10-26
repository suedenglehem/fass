"""Chunker tests (PRD 5.3): structure-first chunks, metadata, no truncation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rag.chunker import (TOKEN_MAX, chunk_document, chunk_text_file,
                         render_blocks, tokens)
from rag.parser import Block, parse_file

FIXTURES = Path(__file__).resolve().parent.parent / "rag" / "fixtures"

EXPECTED_TFLIP_TEXT = (
    "Video Filters > tflip\n"
    "\n"
    "Flip the input video vertically.\n"
    "\n"
    "For example, to flip the input video:\n"
    "\n"
    'ffmpeg -i in.avi -vf "tflip" out.avi\n'
    "\n"
    "strength: Strength of the flip, between 0 and 1.\n"
    "\n"
    "mirror: Also mirror the output. Default is no. "
    "Second paragraph for mirror."
)


def _filter_options_chunks():
    doc = parse_file(FIXTURES / "filter_options.html")
    return chunk_document(doc, "test-corpus")


def test_filter_options_three_chunks():
    chunks = _filter_options_chunks()
    assert [c.heading_path for c in chunks] == \
        [["Description"], ["Video Filters"], ["Video Filters", "tflip"]]


def test_tflip_chunk_text_exact():
    (tflip,) = (c for c in _filter_options_chunks()
                if c.component_name == "tflip")
    assert tflip.text == EXPECTED_TFLIP_TEXT


def test_examples_stay_with_introducing_paragraph():
    (tflip,) = (c for c in _filter_options_chunks()
                if c.component_name == "tflip")
    assert "For example, to flip the input video:" in tflip.text
    assert 'ffmpeg -i in.avi -vf "tflip" out.avi' in tflip.text


def test_tflip_metadata():
    (tflip,) = (c for c in _filter_options_chunks()
                if c.component_name == "tflip")
    assert tflip.component_type == "filter"
    assert tflip.option_names == ["strength", "mirror"]
    assert tflip.anchor == "tflip"
    # fixture source is not in MANUAL_URLS -> documented fallback base
    assert tflip.source_url == \
        "https://ffmpeg.org/filter_options.html#tflip"
    assert tflip.level == 3
    assert tflip.version_status == "pinned-release"
    assert tflip.source_kind == "manual"
    assert tflip.oversized is False
    assert tflip.content_hash == \
        hashlib.sha256(tflip.text.encode("utf-8")).hexdigest()


def test_parent_is_first_chunk_of_parent_section():
    chunks = _filter_options_chunks()
    parents = [c for c in chunks if c.heading_path == ["Video Filters"]]
    (vf,) = parents
    (tflip,) = (c for c in chunks if c.component_name == "tflip")
    assert vf.parent_id is None
    assert tflip.parent_id == vf.chunk_id


def test_nested_headings_parent_chain():
    doc = parse_file(FIXTURES / "nested_headings.html")
    chunks = chunk_document(doc, "test-corpus")
    by_path = {tuple(c.heading_path): c for c in chunks}
    alpha = by_path[("Alpha chapter",)]
    beta = by_path[("Alpha chapter", "Beta section")]
    gamma = by_path[("Alpha chapter", "Beta section", "Gamma subsection")]
    # child -> parent section's FIRST chunk
    assert beta.parent_id == alpha.chunk_id
    assert gamma.parent_id == beta.chunk_id
    assert alpha.parent_id is None
    # heading-only section still becomes a chunk (Delta)
    delta = by_path[("Delta",)]
    assert delta.text == "Delta"


def test_all_chunks_within_window_or_flagged():
    for name in ("filter_options", "nested_headings"):
        doc = parse_file(FIXTURES / f"{name}.html")
        for c in chunk_document(doc, "test-corpus"):
            if tokens(c.text) > TOKEN_MAX:
                assert c.oversized, f"over-window chunk not flagged: {c.chunk_id}"


def test_render_blocks_small_deflist_table():
    blocks = [Block(kind="deflist", entries=[
        ("a", [Block(kind="paragraph", text="desc a")]),
        ("b", []),
    ])]
    # empty description: rstrip(": ") leaves just the term
    assert render_blocks(blocks) == "a: desc a\n\nb"


def test_render_blocks_option_with_nested_value_table():
    blocks = [
        Block(kind="paragraph", text="Accepts one of:"),
        Block(kind="deflist", entries=[
            ("repeat", [Block(kind="paragraph", text="Repeat.")]),
            ("endall", [Block(kind="paragraph", text="End.")]),
        ]),
    ]
    out = render_blocks(blocks, option_term="eof_action")
    assert out == ("eof_action: Accepts one of: "
                   "repeat: Repeat. endall: End.")


# ------------------------------------------------------------ build_help


def test_chunk_text_file_oversized_never_cut(tmp_path: Path):
    p = tmp_path / "big.txt"
    p.write_text(" ".join(f"word{i}" for i in range(900)), encoding="utf-8")
    (c,) = chunk_text_file(p, "test-corpus")
    assert c.oversized is True
    # header "big" (2 words) + 900 body words
    assert tokens(c.text) == 902
    assert "word899" in c.text  # tail intact — never truncated


def test_chunk_text_file_filter_name(tmp_path: Path):
    p = tmp_path / "filter-foo.txt"
    para = " ".join(f"tok{i}" for i in range(400))
    # identical paragraphs -> identical chunks (dedup is an ingest concern)
    p.write_text(f"{para}\n\n{para}\n", encoding="utf-8")
    chunks = chunk_text_file(p, "test-corpus")
    assert len(chunks) == 2
    assert {c.content_hash for c in chunks} == \
        {chunks[0].content_hash}
    assert chunks[0].component_name == "foo"
    assert chunks[0].component_type == "build_help_filter"
    assert chunks[0].text.startswith("ffmpeg foo\n\n")
    assert chunks[0].source_url == ""
    assert chunks[0].heading_path == ["filter-foo.txt"]
    assert chunks[0].parent_id is None
