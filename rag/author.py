"""M11.3 authoring pipeline (PRD 7.1-7.2): docs <-> build diff, draft JSON.

    python -m rag.author --component filter:ebur128 \
        --corpus ffmpeg-6.1.1 --corpus ffmpeg-9.0.1 \
        [--corpus-root rag/corpus] --out drafts/ebur128.json

Per corpus, reads:
    <corpus-root>/<corpus>/docs/ffmpeg-filters.html   (manual section)
    <corpus-root>/<corpus>/build-facts/filter-<name>.txt  (ffmpeg -h capture)

Emits a DRAFT JSON + a review report on stdout. It never writes
registry.json, never bumps versions, never runs tests (PRD 7.1: drafts are
reviewed like code). Offline: stdlib only, no network.

Classification (PRD 7.1 table):
    docs + all builds + no conflict -> verified_both
    present in some corpora         -> present_in + min_version (oldest)
    docs only                       -> docs_only
    builds only                     -> undocumented
    default/range stated on both sides and differing -> conflict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOC_FILENAME = "ffmpeg-filters.html"


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

def clean_text(html_fragment: str) -> str:
    """Strip tags, decode entities (&para; -> ¶), collapse whitespace."""
    text = re.sub(r"<[^>]*>", "", html_fragment)
    import html as _html
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _heading_name_pattern(name: str) -> str:
    return r"(?:[\d.]+\s+)?" + re.escape(name) + r"(?:\s*[¶#])?(?:\s*TOC)?\s*"


def iter_sections(html: str):
    """Yield (heading_html, body_html) for every <h3 class="section">."""
    heads = []
    for m in re.finditer(r"<h3\b[^>]*>", html):
        if "section" not in m.group(0):
            continue
        end = html.find("</h3>", m.end())
        if end == -1:
            continue
        heads.append((m.start(), end + len("</h3>")))
    for i, (_start, body_start) in enumerate(heads):
        heading_html = html[_start:html.find("</h3>", _start) + len("</h3>")]
        next_start = heads[i + 1][0] if i + 1 < len(heads) else len(html)
        yield heading_html, html[body_start:next_start]


def find_section(html: str, name: str):
    """The section whose heading text fullmatches the filter name."""
    pat = re.compile(_heading_name_pattern(name))
    for heading_html, body in iter_sections(html):
        if pat.fullmatch(clean_text(heading_html)):
            return heading_html, body
    return None


def heading_anchor(heading_html: str) -> str | None:
    """href (minus '#') of the FIRST heading <a> with href '#' not 'toc-'."""
    for m in re.finditer(r"<a\b[^>]*href=\"([^\"]*)\"", heading_html):
        href = m.group(1)
        if href.startswith("#") and not href[1:].startswith("toc-"):
            return href[1:]
    return None


def parse_dl(html: str, start: int):
    """Top-level <dt>/<dd> pairs of the <dl> at `start`; nested <dl>
    (enum sub-lists) are skipped; stops at the outer </dl>. A run of
    consecutive <dt>s followed by ONE <dd> (grouped options, e.g.
    signalstats' stat/out) yields one (name, shared description) pair
    per <dt>."""
    items: list[tuple[str | None, str]] = []
    open_end = html.find(">", start) + 1
    depth = 1  # inside the outer <dl>
    mode = None  # 'dt' | 'dd' at top level
    chunk_start = None
    chunks: list[str] = []
    pending: list[str] = []
    for m in re.finditer(r"<(/?)(dl|dt|dd)\b[^>]*>", html[open_end:]):
        closing = m.group(1) == "/"
        tag = m.group(2)
        tok_start = open_end + m.start()
        tok_end = open_end + m.end()
        if tag == "dl":
            if mode == "dd":
                chunks.append(html[chunk_start:tok_start])
            if closing:
                if depth == 1:
                    break
                depth -= 1
                if depth == 1:
                    mode = "dd"
                    chunk_start = tok_end
            else:
                depth += 1
                mode = None
        elif depth == 1:
            if tag == "dt":
                if not closing:
                    mode = "dt"
                    chunk_start = tok_end
                else:
                    pending.append(clean_text(html[chunk_start:tok_start]))
                    mode = None
            else:  # dd
                if not closing:
                    mode = "dd"
                    chunk_start = tok_end
                    chunks = []
                else:
                    chunks.append(html[chunk_start:tok_start])
                    desc = clean_text(" ".join(chunks))
                    for n in (pending or [None]):
                        items.append((n, desc))
                    pending = []
                    mode = None
    return items


PROSE_MARKER = re.compile(r"accepts the following parameters:", re.I)
PROSE_OPTION = re.compile(
    r"(\w+)(?:\s*,\s*(\w+))?\s+(.+?)it defaults to\s*([\w.]+)\s*\.", re.S)


def parse_doc_options(body: str) -> tuple[dict, str | None]:
    """The ONE options list of a section body. Forms:
    (a) <h4>Options</h4> + <dl>,
    (b) paragraph ending 'The filter accepts the following options:' + <dl>,
    (c) prose '...accepts the following parameters: ...' (no dl).
    Returns (options, form) where form is 'options_dl' | 'prose' | None."""
    dl_start = None
    m = re.search(r"The filter accepts the following options:\s*</p>", body)
    if m:
        dl_start = body.find("<dl", m.end())
    else:
        for hm in re.finditer(r"<h4\b[^>]*>(.*?)</h4>", body, re.S):
            if re.fullmatch(r"(?:[\d.]+\s+)?Options(?:\s*[¶#])?(?:\s*TOC)?\s*",
                            clean_text(hm.group(1))):
                dl_start = body.find("<dl", hm.end())
                break
    if dl_start is not None and dl_start >= 0:
        options = {}
        for name, desc in parse_dl(body, dl_start):
            if not name:
                continue
            dm = re.search(r"Default is\s+(.+?)\.", desc)
            default = dm.group(1).strip() if dm else None
            # alias groups arrive as one <dt> ("color, c") — split on commas
            for n in (p.strip() for p in name.split(",")):
                if n:
                    options[n] = {"description": desc, "default": default}
        return options, "options_dl"
    # form (c) — prose only (intro paragraph, before any <h4> subsection)
    h4 = re.search(r"<h4\b", body)
    intro = body[:h4.start()] if h4 else body
    text = clean_text(intro)
    pm = PROSE_MARKER.search(text)
    if pm:
        options = {}
        for opt in PROSE_OPTION.finditer(text[pm.end():]):
            name, alias, desc, default = (g or "" for g in opt.groups())
            name, alias, default = name.strip(), alias.strip(), default.strip()
            desc = desc.strip(" .;,")
            for n in (name, alias) if alias else (name,):
                options[n] = {"description": desc, "default": default}
        if options:
            return options, "prose"
    return {}, None


# --------------------------------------------------------------------------
# build-facts helpers
# --------------------------------------------------------------------------

def parse_build_facts(text: str, name: str) -> dict:
    """Options under the '<name> AVOptions:' line. Top-level option lines
    are indented 3 spaces; 5-space sub-lines are enum values (skipped)."""
    options: dict = {}
    in_block = False
    for line in text.splitlines():
        if line.strip() == f"{name} AVOptions:":
            in_block = True
            continue
        if not in_block:
            continue
        m = re.match(r"^ {3}(\S+)\s+<([^>]+)>\s+(\S+)\s+(.*)$", line)
        if not m:
            if line.startswith("    "):
                continue  # indented enum sub-line — skip
            if line.strip():
                in_block = False  # block ended (e.g. 'Exiting with ...')
            continue
        opt, typ, flags, rest = m.group(1), m.group(2), m.group(3), m.group(4)
        default = None
        dm = re.search(r"\(default (.+)\)\s*$", rest)
        if dm:
            default = dm.group(1).strip()
            rest = rest[:dm.start()].strip()
        mn = mx = None
        fm = re.search(r"\(from (\S+) to (\S+)\)\s*$", rest)
        if fm:
            mn, mx = fm.group(1), fm.group(2)
        options[opt] = {
            "type": typ,
            "flags": flags,
            "min": mn,
            "max": mx,
            "default": default,
        }
    return options


# --------------------------------------------------------------------------
# diff / classification
# --------------------------------------------------------------------------

def normalize_default(value):
    if value is None:
        return None
    s = str(value).strip().strip('"')
    if s == "false":
        s = "0"
    elif s == "true":
        s = "1"
    return s


def compute_option_diffs(corpus: str, docs_opts: dict, build_opts: dict) -> list:
    diffs = []
    for opt in sorted(set(docs_opts) | set(build_opts)):
        d, b = docs_opts.get(opt), build_opts.get(opt)
        if d is not None and b is None:
            diffs.append({"corpus": corpus, "option": opt,
                          "kind": "docs_only_option"})
        elif b is not None and d is None:
            diffs.append({"corpus": corpus, "option": opt,
                          "kind": "undocumented_option"})
        else:
            dd = normalize_default(d.get("default"))
            bd = normalize_default(b.get("default"))
            if dd is not None and bd is not None and dd != bd:
                diffs.append({"corpus": corpus, "option": opt,
                              "kind": "default_conflict",
                              "docs_default": d.get("default"),
                              "build_default": b.get("default")})
    return diffs


def corpus_version_key(corpus: str):
    m = re.search(r"(\d+(?:\.\d+)*)$", corpus)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def corpus_version(corpus: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)*)$", corpus)
    return m.group(1) if m else corpus


# --------------------------------------------------------------------------
# draft assembly
# --------------------------------------------------------------------------

def author_filter(name: str, corpora: list[str], corpus_root: Path) -> dict:
    corpora = sorted(set(corpora), key=corpus_version_key)
    docs: dict = {}
    builds: dict = {}
    prose_corpora: list[str] = []
    for corpus in corpora:
        html_path = corpus_root / corpus / "docs" / DOC_FILENAME
        if html_path.is_file():
            sec = find_section(html_path.read_text(encoding="utf-8",
                                                   errors="replace"), name)
            if sec is not None:
                heading_html, body = sec
                opts, form = parse_doc_options(body)
                docs[corpus] = {
                    "anchor": heading_anchor(heading_html),
                    "options": opts,
                }
                if form == "prose":
                    prose_corpora.append(corpus)
        bf = corpus_root / corpus / "build-facts" / f"filter-{name}.txt"
        if bf.is_file():
            opts = parse_build_facts(bf.read_text(encoding="utf-8",
                                                  errors="replace"), name)
            if opts:
                builds[corpus] = {"options": opts}

    option_diffs: list = []
    for corpus in sorted(set(docs) & set(builds), key=corpus_version_key):
        option_diffs.extend(
            compute_option_diffs(corpus, docs[corpus]["options"],
                                 builds[corpus]["options"]))

    all_c, d_set, b_set = set(corpora), set(docs), set(builds)
    present_in = sorted(d_set | b_set, key=corpus_version_key)
    if any(d["kind"] == "default_conflict" for d in option_diffs):
        classification = "conflict"
    elif d_set == all_c and b_set == all_c:
        classification = "verified_both"
    elif d_set == all_c and not b_set:
        classification = "docs_only"
    elif not d_set and b_set == all_c:
        classification = "undocumented"
    elif present_in:
        classification = "present_in"
    else:
        classification = "not_found"

    notes = [
        f"human TODO: write registry description for {name}",
        f"human TODO: add aliases for {name}",
    ]
    if classification == "present_in":
        notes.append("human TODO: presence gating — present only in "
                     + ", ".join(present_in))
    if classification == "docs_only":
        notes.append("human TODO: docs_only — likely newer release; "
                     "not allowed into registry")
    if classification == "undocumented":
        notes.append("human TODO: undocumented in the manual — needs "
                     "manual justification")
    if classification == "conflict":
        notes.append("human TODO: resolve default/range conflicts before "
                     "registry merge")
    if prose_corpora:
        notes.append("human TODO: options parsed from PROSE form "
                     f"({', '.join(sorted(prose_corpora))}) — best-effort, "
                     "verify names/defaults against the manual")
    for diff in option_diffs:
        notes.append(f"human TODO: {diff['kind']} for option "
                     f"{diff['option']} in {diff['corpus']}")

    draft = {
        "filter": name,
        "kind": "filter",
        "classification": classification,
        "review": {"ready_for_registry": False, "notes": notes},
        "docs": docs,
        "builds": builds,
        "option_diffs": option_diffs,
        "provenance": {
            "corpus_id": corpora[-1] if corpora else None,
            "option_source": ("both" if docs and builds
                              else "docs" if docs else "build_help"),
            "verified_builds": sorted(
                (corpus_version(c) for c in set(docs) & set(builds)),
                key=lambda v: tuple(int(x) for x in v.split("."))),
            "reviewed": False,
        },
    }
    if classification == "present_in":
        draft["present_in"] = present_in
        draft["min_version"] = corpus_version(present_in[0])
    return draft


def review_report(draft: dict) -> str:
    lines = [
        f"rag.author draft: filter:{draft['filter']}",
        f"  classification: {draft['classification']}",
        f"  corpora: docs={sorted(draft['docs'])} "
        f"builds={sorted(draft['builds'])}",
        f"  options: " + ", ".join(
            f"{c}={len(draft['docs'].get(c, {}).get('options', {}))}docs/"
            f"{len(draft['builds'].get(c, {}).get('options', {}))}builds"
            for c in sorted(set(draft['docs']) | set(draft['builds']))),
        f"  option_diffs: {len(draft['option_diffs'])}",
        "  review notes:",
    ]
    lines += [f"    - {n}" for n in draft["review"]["notes"]]
    lines.append("  ready_for_registry: "
                 + str(draft["review"]["ready_for_registry"]).lower())
    lines.append("  draft written; registry.json is NOT touched — "
                 "human review + manual merge required.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="rag.author",
        description="M11.3 draft authoring: docs vs build diff (PRD 7.1).")
    ap.add_argument("--component", required=True,
                    help="component:filter:<name>, e.g. filter:ebur128")
    ap.add_argument("--corpus", action="append", required=True,
                    help="corpus id (repeatable), e.g. ffmpeg-6.1.1")
    ap.add_argument("--corpus-root", default="rag/corpus",
                    help="root holding <corpus>/docs and <corpus>/build-facts")
    ap.add_argument("--out", required=True, help="draft JSON output path")
    args = ap.parse_args(argv)

    if ":" not in args.component:
        print(f"rag.author: bad --component {args.component!r} "
              "(want filter:<name>)", file=sys.stderr)
        return 2
    kind, name = args.component.split(":", 1)
    if kind != "filter" or not name:
        print(f"rag.author: unsupported component {args.component!r}",
              file=sys.stderr)
        return 2

    draft = author_filter(name, args.corpus, Path(args.corpus_root))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    print(review_report(draft))
    return 0


if __name__ == "__main__":
    sys.exit(main())
