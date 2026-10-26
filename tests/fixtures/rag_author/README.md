# M11.3 authoring-pipeline fixtures

Trimmed REAL corpus data for the offline `rag.author` / `rag.gaps` tests
(the full corpora under `rag/corpus/` are gitignored and absent in clean
worktrees, so tests must run against this tree, never against
`rag/corpus/`).

Layout mirrors `rag/corpus/<corpus>/`:

- `<corpus>/docs/ffmpeg-filters.html` — the single `ebur128` section,
  cut verbatim from the real manual (from its `<h3 class="section">`
  heading to the next `<h3>`). Heading anchor: `#ebur128-1` in both
  corpora (6.1.1 carries it on a `copiable-link` `&para;` anchor, 9.0.1
  on an `a.anchor` `#`/`TOC` span). Options are documented in the INLINE
  form: a paragraph ending "The filter accepts the following options:"
  followed by a `<dl>` — the other real form is an `<h4>Options</h4>`
  heading + `<dl>`, so the parser must handle both.
- `<corpus>/build-facts/filter-ebur128.txt` — verbatim
  `ffmpeg -h filter=ebur128` capture from that build; options live under
  the `ebur128 AVOptions:` line as
  `   <name> <type> <flags> <desc> (from X to Y)? (default Z)` with
  indented sub-lines for enum values.

`ebur128` is the M11.3 pilot filter (diagnostic domain, present in both
builds — the `verified_both` case). Conflict / `present_in` /
`docs_only` / `undocumented` branches must be exercised in tests with
SYNTHETIC corpus roots built in `tmp_path`, not by mutating these
fixtures.
