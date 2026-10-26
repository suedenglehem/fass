"""Structure-first chunking of parsed FFmpeg manuals (PRD 5.3).

Policy (joint PRD 5.3 / RAG PRD 6):
- documentation structure first, token limits second: chunks follow
  sections and option entries, never mid-sentence
- target 350-700 embedding tokens; tokens are approximated by
  whitespace-split words (documented heuristic — re-measured against a
  real embedding tokenizer in M11.1 when the lexical baseline is scored)
- large sections split at option or paragraph boundaries
- child chunks repeat the component name and heading path
- examples are kept with the paragraph that introduces them
- parent_id links a chunk to the first chunk of its parent section
- no silent truncation: a single oversized block becomes its own chunk,
  flagged (stored as a linked evidence block, never cut)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .parser import Block, Document, Section

TOKEN_MIN = 350
TOKEN_MAX = 700

# canonical manual URL per source file (release corpora use the stable
# official URL; the anchor is appended when present)
MANUAL_URLS = {
    "ffmpeg.html": "https://ffmpeg.org/ffmpeg.html",
    "ffprobe.html": "https://ffmpeg.org/ffprobe.html",
    "ffmpeg-filters.html": "https://ffmpeg.org/ffmpeg-filters.html",
    "ffmpeg-codecs.html": "https://ffmpeg.org/ffmpeg-codecs.html",
    "ffmpeg-formats.html": "https://ffmpeg.org/ffmpeg-formats.html",
    "ffmpeg-utils.html": "https://ffmpeg.org/ffmpeg-utils.html",
    "ffmpeg-scaler.html": "https://ffmpeg.org/ffmpeg-scaler.html",
    "ffmpeg-resampler.html": "https://ffmpeg.org/ffmpeg-resampler.html",
    "ffmpeg-bitstream-filters.html": "https://ffmpeg.org/ffmpeg-bitstream-filters.html",
    "ffmpeg-protocols.html": "https://ffmpeg.org/ffmpeg-protocols.html",
    "ffmpeg-devices.html": "https://ffmpeg.org/ffmpeg-devices.html",
}

# component_type from the NEAREST ancestor whose title matches (deepest first)
_COMPONENT_PATTERNS = [
    (re.compile(r"(audio|video)\s+filters?", re.I), "filter"),
    (re.compile(r"(audio|video)\s+encoders?", re.I), "encoder"),
    (re.compile(r"(audio|video)\s+decoders?", re.I), "decoder"),
    (re.compile(r"demuxers?", re.I), "demuxer"),
    (re.compile(r"muxers?", re.I), "muxer"),
    (re.compile(r"protocols?", re.I), "protocol"),
    (re.compile(r"(input|output)\s+devices?", re.I), "device"),
    (re.compile(r"bitstream\s+filters?", re.I), "bitstream_filter"),
]
_NAME_RX = re.compile(r"^[a-z0-9_.\-]+$")  # identifier-like heading title


def tokens(text: str) -> int:
    """Token estimate: whitespace-split words (see module docstring)."""
    return len(text.split())


@dataclass
class Chunk:
    chunk_id: str
    corpus_id: str
    source_url: str
    anchor: str
    heading_path: list[str]
    component_type: str | None
    component_name: str | None
    option_names: list[str]
    text: str
    parent_id: str | None
    content_hash: str
    version_status: str
    source_kind: str
    level: int = 0
    oversized: bool = False

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "corpus_id": self.corpus_id,
            "source_url": self.source_url,
            "anchor": self.anchor,
            "heading_path": self.heading_path,
            "component_type": self.component_type,
            "component_name": self.component_name,
            "option_names": self.option_names,
            "text": self.text,
            "parent_id": self.parent_id,
            "content_hash": self.content_hash,
            "version_status": self.version_status,
            "source_kind": self.source_kind,
            "level": self.level,
            "oversized": self.oversized,
        }


def _component_type_for(path_titles: list[str]) -> str | None:
    for title in reversed(path_titles):  # nearest ancestor first
        for rx, ctype in _COMPONENT_PATTERNS:
            if rx.search(title):
                return ctype
    return None


def _component_name_for(section: Section, path_titles: list[str]) -> str | None:
    # nearest identifier-like title in the path, own section first
    for title in reversed(path_titles):
        if _NAME_RX.match(title):
            return title
    return None


def _desc_text(blocks: list[Block]) -> str:
    """Render an option-description run: paragraphs and lists inline,
    nested value tables as 'term: desc' items. Examples excluded —
    the caller appends them on their own lines."""
    parts: list[str] = []
    for b in blocks:
        if b.kind == "paragraph":
            parts.append(b.text)
        elif b.kind == "list":
            parts.append("\n".join("- " + i for i in b.items))
        elif b.kind == "deflist":
            for term, desc in b.entries:
                parts.append(f"{term}: {_desc_text(desc)}".rstrip(": "))
    return " ".join(parts)


def render_blocks(blocks: list[Block], option_term: str | None = None) -> str:
    """Render a run of blocks to canonical chunk text."""
    out: list[str] = []
    if option_term:
        # option unit: "term: description..."
        desc = _desc_text([b for b in blocks if b.kind != "example"])
        examples = [b.text for b in blocks if b.kind == "example"]
        head = option_term + (": " + desc if desc else "")
        out.append(head)
        for ex in examples:
            out.append(ex.rstrip("\n"))
        return "\n".join(out)
    prev_kind = None
    for b in blocks:
        if b.kind == "paragraph":
            out.append(b.text)
        elif b.kind == "example":
            if prev_kind != "paragraph":
                out.append("Example:")
            out.append(b.text.rstrip("\n"))
        elif b.kind == "list":
            out.append("\n".join("- " + i for i in b.items))
        elif b.kind == "deflist":
            # a deflist reached at render time means a whole table rendered
            # in one unit (small tables): term: desc lines
            for term, desc in b.entries:
                out.append(f"{term}: {_desc_text(desc)}".rstrip(": "))
        prev_kind = b.kind
    return "\n\n".join(out)


def _section_atoms(section: Section) -> list[tuple[str | None, list[Block]]]:
    """Split a section into atoms: (option_term or None, blocks).

    A paragraph followed by an example is one atom (examples stay with the
    text that introduces them). Each deflist entry is its own atom.
    """
    atoms: list[tuple[str | None, list[Block]]] = []
    i = 0
    blocks = section.blocks
    while i < len(blocks):
        b = blocks[i]
        if b.kind == "deflist":
            for term, desc in b.entries:
                atoms.append((term, desc))
        elif b.kind == "example":
            atoms.append((None, [b]))
        else:
            run = [b]
            if b.kind == "paragraph" and i + 1 < len(blocks) \
                    and blocks[i + 1].kind == "example":
                run.append(blocks[i + 1])
                i += 1
            atoms.append((None, run))
        i += 1
    return atoms


def _group_atoms(atoms: list[tuple[str | None, list[Block]]],
                 base_text: str) -> list[tuple[str, bool]]:
    """Group atoms into TOKEN_MIN..TOKEN_MAX chunks. Returns (text, oversized)."""
    rendered = [render_blocks(blocks, term) for term, blocks in atoms]
    groups: list[list[int]] = []
    cur: list[int] = []
    cur_t = 0
    for idx, text in enumerate(rendered):
        t = tokens(text)
        if t > TOKEN_MAX:
            # a single oversized atom: its own chunk, flagged, never cut
            if cur:
                groups.append(cur)
                cur, cur_t = [], 0
            groups.append([idx])
            continue
        if cur and cur_t + t > TOKEN_MAX:
            groups.append(cur)
            cur, cur_t = [], 0
        # merge small adjacent atoms up to the minimum
        if cur and cur_t < TOKEN_MIN and cur_t + t <= TOKEN_MAX:
            cur.append(idx)
            cur_t += t
        else:
            if cur:
                groups.append(cur)
            cur, cur_t = [idx], t
    if cur:
        groups.append(cur)
    out: list[tuple[str, bool]] = []
    for g in groups:
        text = "\n\n".join(rendered[i] for i in g)
        out.append((base_text + ("\n\n" + text if text else ""),
                    tokens(text) > TOKEN_MAX))
    return out


def _make_chunk(corpus_id: str, source: str, heading_path: list[str],
                anchor: str, level: int, ctype: str | None,
                cname: str | None, option_names: list[str], text: str,
                parent_id: str | None, version_status: str,
                oversized: bool = False) -> Chunk:
    base = MANUAL_URLS.get(source, f"https://ffmpeg.org/{source}")
    source_url = base + (f"#{anchor}" if anchor else "")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunk_id = "c" + hashlib.sha256(
        f"{corpus_id}|{source}|{anchor}|{':'.join(heading_path)}|{text}"
        .encode("utf-8")).hexdigest()[:16]
    return Chunk(chunk_id=chunk_id, corpus_id=corpus_id,
                 source_url=source_url, anchor=anchor,
                 heading_path=list(heading_path), component_type=ctype,
                 component_name=cname, option_names=list(option_names),
                 text=text, parent_id=parent_id, content_hash=content_hash,
                 version_status=version_status, source_kind="manual",
                 level=level, oversized=oversized)


def chunk_document(doc: Document, corpus_id: str,
                   version_status: str = "pinned-release") -> list[Chunk]:
    """Chunk a parsed manual into evidence chunks (module docstring policy)."""
    chunks: list[Chunk] = []
    # current ancestor path: (level, first_chunk_id) pairs, shallowest first.
    # The first chunk of a section is the parent_id target of its children.
    stack: list[tuple[int, str]] = []

    for section in doc.sections:
        level = section.heading.level
        while stack and stack[-1][0] >= level:
            stack.pop()
        path_titles = _path_titles(doc.sections, section,
                                   [lv for lv, _ in stack])
        parent_id = stack[-1][1] if stack else None

        ctype = _component_type_for(path_titles[:-1])
        cname = _component_name_for(section, path_titles)
        option_names: list[str] = []
        for b in section.blocks:
            if b.kind == "deflist":
                option_names.extend(t for t, _ in b.entries)

        atoms = _section_atoms(section)
        base_text = _header_line(path_titles)
        groups = _group_atoms(atoms, base_text)
        if not groups:
            # heading-only section (no body): one small chunk so children
            # still have a parent to point at
            groups = [(base_text, False)]
        first_chunk_id: str | None = None
        for i, (text, oversized) in enumerate(groups):
            ch = _make_chunk(corpus_id, doc.source, path_titles,
                             section.heading.anchor, level, ctype, cname,
                             option_names, text, parent_id, version_status,
                             oversized)
            if i == 0:
                first_chunk_id = ch.chunk_id
            chunks.append(ch)
        if first_chunk_id is not None:
            stack.append((level, first_chunk_id))
    return chunks


def _header_line(path_titles: list[str]) -> str:
    """Repeated header: the full heading path (PRD 5.3 — child chunks repeat
    the component name and heading path; for component sections the path
    ends with the component name, so both are present)."""
    return " > ".join(path_titles)


def _path_titles(sections: list[Section], section: Section,
                 stack: list[int]) -> list[str]:
    """Heading-path titles: the ancestor titles on the current stack plus
    the section's own title (recomputed from the flat list).

    `stack` holds the heading levels of the current ancestor path (deepest
    last). Walks the flat list from `section` BACKWARDS toward the document
    start, collecting the title of each level present on the stack, and
    stops at the first section at or above the shallowest stacked level.
    """
    titles_by_level: dict[int, str] = {}
    reached = False
    for s in reversed(sections):
        if s is section:
            reached = True
            continue
        if not reached:
            continue  # sections AFTER `section` in document order
        lv = s.heading.level
        if lv in stack and lv not in titles_by_level:
            titles_by_level[lv] = s.heading.title
        if stack and lv <= min(stack):
            break
    path = [titles_by_level[lv] for lv in sorted(titles_by_level)]
    path.append(section.heading.title)
    return path


# ----------------------------------------------------------------- build_help


def chunk_text_file(path: str | Path, corpus_id: str,
                    version_status: str = "pinned-release",
                    source_kind: str = "build_help") -> list[Chunk]:
    """Chunk a plain build-facts file (source_kind: build_help).

    Split at blank-line paragraph boundaries, merged to the same token
    window as manuals. component_name is the filter name for
    filter-<name>.txt, else the file stem (or None for version.txt).
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    stem = path.stem
    if stem.startswith("filter-"):
        cname, ctype = stem[len("filter-"):], "build_help_filter"
    elif stem == "version":
        cname, ctype = None, "build_help"
    else:
        cname, ctype = stem, "build_help"
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    rendered: list[str] = []
    cur = []
    cur_t = 0
    for p in paragraphs:
        t = tokens(p)
        if cur and cur_t + t > TOKEN_MAX:
            rendered.append("\n\n".join(cur))
            cur, cur_t = [], 0
        if t > TOKEN_MAX:
            if cur:
                rendered.append("\n\n".join(cur))
                cur, cur_t = [], 0
            rendered.append(p)  # oversized, flagged below
            continue
        cur.append(p)
        cur_t += t
        if cur_t >= TOKEN_MIN:
            rendered.append("\n\n".join(cur))
            cur, cur_t = [], 0
    if cur:
        rendered.append("\n\n".join(cur))
    # The raw CLI text is stored sha256-pinned in build-facts/, so citations
    # resolve to stored evidence: build-help://<corpus>/<file>#<stem>-<i>.
    out: list[Chunk] = []
    for i, text in enumerate(rendered, start=1):
        header = f"ffmpeg {cname}" if cname else "ffmpeg"
        full = f"{header}\n\n{text}"
        content_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()
        chunk_id = "c" + hashlib.sha256(
            f"{corpus_id}|{path.name}|||{full}".encode("utf-8")
        ).hexdigest()[:16]
        out.append(Chunk(
            chunk_id=chunk_id, corpus_id=corpus_id,
            source_url=f"build-help://{corpus_id}/{path.name}",
            anchor=f"{stem}-{i}", heading_path=[path.name],
            component_type=ctype, component_name=cname, option_names=[],
            text=full, parent_id=None, content_hash=content_hash,
            version_status=version_status, source_kind=source_kind,
            level=0, oversized=tokens(text) > TOKEN_MAX,
        ))
    return out
