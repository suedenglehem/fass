"""Structure-preserving parser for makeinfo-generated FFmpeg manual HTML.

The eleven release manuals are built by `make doc` (makeinfo --html,
monolithic), which emits a very regular structure:

    <a name="hflip"></a>
    <h3 class="section">11.117 hflip<span class="pull-right">...#...</span></h3>
    <p>description with <samp>inline</samp> <code>code</code></p>
    <div class="example"><pre class="example-preformatted">ffmpeg ...
    </pre></div>
    <dl class="table">
      <dt><samp class="option">strength</samp></dt>
      <dd><p>option description</p></dd>
    </dl>

Two makeinfo table idioms handled explicitly:
- a run of <dt>s before one <dd> share the description (e.g. w/h ->
  "width and height"): one entry per name, same description blocks
- a <dd> may contain a nested <dl> of accepted values (e.g. eof_action ->
  repeat/endall/pass): the nested table becomes part of the entry's
  description; <dt>s and <dd>s pair per enclosing table (stacked state)

Preserved (RAG PRD 6, ingestion requirements):
- heading hierarchy and section anchors (<a name=...> before the heading)
- component names (heading titles with the section number stripped)
- option terms with their descriptions (definition lists, incl. nested)
- code examples VERBATIM (entities decoded, no whitespace rewriting)
- definition tables with their terms
- internal cross-reference targets (Block.links)

Dropped: the table-of-contents div (duplicates every heading) and the
per-heading anchor/TOC spans. Stdlib only (html.parser).

Two makeinfo generations of this markup appear in the corpora:
- texinfo >= 7 (9.0.1 tarball build): the anchor is a bare
  `<a name="...">` before the heading; the permalink is a
  `<span class="pull-right">` inside the heading (skipped wholesale)
- texinfo 6.x (6.1.1 Ubuntu deb build): the heading text is wrapped in
  a plain `<span>` and the permalink is an
  `<a class="copiable-link" href="#name"> &para;</a>` INSIDE the heading
  with no `<a name>` before it — the link's target is the section
  anchor and its text (a pilcrow permalink) must not leak into the
  heading title
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

_NUM_RX = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}


def collapse(text: str) -> str:
    """Canonical paragraph text: whitespace runs -> single space, trimmed."""
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "example" | "deflist" | "list"
    text: str = ""
    level: int = 0  # heading level (1-4)
    number: str = ""  # "11.117"
    title: str = ""  # heading title with the number stripped
    anchor: str = ""
    entries: list[tuple[str, list["Block"]]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


@dataclass
class Section:
    heading: Block
    blocks: list[Block] = field(default_factory=list)


@dataclass
class _DL:
    """One open <dl>: its entries plus the <dt> terms awaiting a <dd>."""
    entries: list[tuple[str, list[Block]]]
    pending_terms: list[str]


@dataclass
class Document:
    title: str
    source: str
    sections: list[Section] = field(default_factory=list)

    def section_by_anchor(self, anchor: str) -> Section | None:
        for s in self.sections:
            if s.heading.anchor == anchor:
                return s
        return None

    def all_blocks(self):
        for s in self.sections:
            for b in s.blocks:
                yield b


class _ManualParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.sections: list[Section] = []
        # skip machinery (TOC div, heading pull-right spans): one skip at a
        # time, nested same-tag elements counted
        self._skip_tag: str | None = None
        self._skip_depth = 0
        # current section
        self._pending_anchor = ""
        self._section: Section | None = None
        self._heading_level = 0
        self._heading_pieces: list[str] | None = None
        # paragraph context: a <p> anywhere (top level, in <dd>, in <li>)
        self._para: list[str] | None = None
        self._para_links: list[str] = []
        self._pre: list[str] | None = None
        # definition lists: stacks so nested <dl> (value tables inside a
        # <dd>) and shared/continuation <dd> pair correctly (module docstring)
        self._dl_stack: list[_DL] = []
        self._dd_stack: list[list[Block]] = []
        self._dt_pieces: list[str] | None = None
        # ordered list: a stack so nested <ul> survive (items flatten up)
        self._ul_stack: list[list[str]] = []
        self._li_pieces: list[str] | None = None

    def _skipping(self) -> bool:
        return self._skip_tag is not None

    # ------------------------------------------------------- starttags
    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        if self._skipping():
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag == "div" and (a.get("class") == "contents"
                             or a.get("id") == "SEC_Contents"):
            self._skip_tag, self._skip_depth = "div", 0
            return
        if tag == "span" and a.get("class") == "pull-right":
            self._skip_tag, self._skip_depth = "span", 0
            return
        if tag in _HEADINGS:
            self._heading_level = _HEADINGS[tag]
            self._heading_pieces = []
            return
        if tag == "a":
            if a.get("name"):
                self._pending_anchor = a["name"]
            elif a.get("href", "").startswith("#"):
                target = a["href"][1:]
                if self._heading_pieces is not None:
                    # texinfo 6.x copiable-link: the target is the section
                    # anchor (no <a name> precedes the heading) and the
                    # link text is a pilcrow permalink — skip it so it
                    # cannot leak into the heading title
                    if not self._pending_anchor:
                        self._pending_anchor = target
                    self._skip_tag, self._skip_depth = "a", 0
                    return
                if self._para is not None and target not in self._para_links:
                    self._para_links.append(target)
            return
        if tag == "p":
            self._para = []
            self._para_links = []
            return
        if tag == "pre":
            self._pre = []
            return
        if tag == "dl":
            self._dl_stack.append(_DL(entries=[], pending_terms=[]))
            return
        if tag == "dt":
            self._dt_pieces = []
            return
        if tag == "dd":
            self._dd_stack.append([])
            return
        if tag == "ul":
            self._ul_stack.append([])
            return
        if tag == "li":
            self._li_pieces = []
            return
        # samp/code/var/em/strong/... — inline: the text just flows in

    # -------------------------------------------------------- endtags
    def handle_endtag(self, tag) -> None:
        if self._skipping():
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = None
                    self._skip_depth = 0
            return
        if tag in _HEADINGS:
            self._close_heading()
            return
        if tag == "p":
            self._close_paragraph()
            return
        if tag == "pre":
            self._close_example()
            return
        if tag == "dt":
            term = collapse("".join(self._dt_pieces or []))
            if term and self._dl_stack:
                self._dl_stack[-1].pending_terms.append(term)
            self._dt_pieces = None
            return
        if tag == "dd":
            desc = self._dd_stack.pop() if self._dd_stack else []
            if self._dl_stack:
                dl = self._dl_stack[-1]
                terms = dl.pending_terms
                dl.pending_terms = []
                if terms:
                    # a run of <dt>s sharing this <dd>: one entry per name
                    for t in terms:
                        dl.entries.append((t, desc))
                elif dl.entries:
                    # continuation <dd> for the last term (multi-paragraph
                    # description): merge into that entry
                    dl.entries[-1][1].extend(desc)
            return
        if tag == "dl":
            dl = self._dl_stack.pop() if self._dl_stack else None
            if dl:
                # dt with no dd at all: keep the name, empty description
                for t in dl.pending_terms:
                    dl.entries.append((t, []))
                if dl.entries:
                    self._append(Block(kind="deflist", entries=dl.entries))
            return
        if tag == "li":
            if self._li_pieces is not None and self._ul_stack:
                text = collapse("".join(self._li_pieces))
                if text:
                    self._ul_stack[-1].append(text)
            self._li_pieces = None
            return
        if tag == "ul":
            if self._ul_stack:
                items = self._ul_stack.pop()
                if self._ul_stack:  # nested: flatten into the parent's items
                    self._ul_stack[-1].extend(items)
                elif items:
                    self._append(Block(kind="list", items=items))
            return

    # ------------------------------------------------------- data
    def handle_data(self, data: str) -> None:
        if self._skipping():
            return
        if self._pre is not None:
            self._pre.append(data)  # verbatim
            return
        if self._heading_pieces is not None:
            self._heading_pieces.append(data)
            return
        if self._dt_pieces is not None:
            self._dt_pieces.append(data)
            return
        if self._para is not None:
            self._para.append(data)
            return
        if self._li_pieces is not None:
            self._li_pieces.append(data)
            return
        # text outside any block (stray body text): ignored

    # ------------------------------------------------- block closing
    def _append(self, block: Block) -> None:
        if self._dd_stack:
            self._dd_stack[-1].append(block)
            return
        if self._section is not None:
            self._section.blocks.append(block)

    def _close_heading(self) -> None:
        text = collapse("".join(self._heading_pieces or []))
        self._heading_pieces = None
        level = self._heading_level
        self._heading_level = 0
        if not text:
            return
        if level == 1 and not self.sections:
            # the document title heading
            if not self.title:
                self.title = text
            self._pending_anchor = ""
            return
        m = _NUM_RX.match(text)
        number, title = (m.group(1), m.group(2)) if m else ("", text)
        heading = Block(kind="heading", text=text, level=level,
                        number=number, title=title,
                        anchor=self._pending_anchor)
        self._pending_anchor = ""
        self._section = Section(heading=heading)
        self.sections.append(self._section)

    def _close_paragraph(self) -> None:
        text = collapse("".join(self._para or []))
        links, self._para = self._para_links, None
        self._para_links = []
        if not text:
            return
        if self._li_pieces is not None:
            # <li><p>...</p></li>: the item text collects the inner paragraph
            self._li_pieces.append(" " + text)
            return
        self._append(Block(kind="paragraph", text=text, links=links))

    def _close_example(self) -> None:
        raw = "".join(self._pre or "")
        self._pre = None
        if raw:
            self._append(Block(kind="example", text=raw))


def block_to_dict(block: Block) -> dict:
    """Canonical JSON form of a block (parser-fixture round-trip)."""
    d: dict = {"kind": block.kind}
    if block.kind == "deflist":
        d["entries"] = [
            {"term": term, "desc": [block_to_dict(b) for b in desc]}
            for term, desc in block.entries
        ]
        return d
    if block.kind == "list":
        d["items"] = list(block.items)
        return d
    d["text"] = block.text
    if block.kind == "paragraph":
        d["links"] = list(block.links)
    return d


def document_to_dict(doc: Document) -> dict:
    """Canonical JSON form of a Document (parser-fixture round-trip)."""
    return {
        "title": doc.title,
        "sections": [
            {
                "anchor": s.heading.anchor,
                "number": s.heading.number,
                "title": s.heading.title,
                "level": s.heading.level,
                "blocks": [block_to_dict(b) for b in s.blocks],
            }
            for s in doc.sections
        ],
    }


def parse_html(html_text: str, source: str) -> Document:
    p = _ManualParser()
    p.feed(html_text)
    p.close()
    return Document(title=p.title, source=source, sections=p.sections)


def parse_file(path: str | Path) -> Document:
    path = Path(path)
    return parse_html(path.read_text(encoding="utf-8"), source=path.name)
