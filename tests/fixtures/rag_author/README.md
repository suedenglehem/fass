# M11.3 authoring-pipeline fixtures

Trimmed REAL corpus data for the offline `rag.author` / `rag.gaps` tests
(the full corpora under `rag/corpus/` are gitignored and absent in clean
worktrees, so tests must run against this tree, never against
`rag/corpus/`).

Layout mirrors `rag/corpus/<corpus>/`:

- `<corpus>/docs/ffmpeg-filters.html` — three sections, each cut
  verbatim from the real manual (from its `<h3 class="section">`
  heading to the next `<h3>`): `ebur128`, `blackframe`, `signalstats`.
  Heading anchors: `#ebur128-1` and `#signalstats-1` in both corpora,
  but `#blackframe` (no `-1` suffix) — 6.1.1 carries anchors on a
  `copiable-link` `&para;` span, 9.0.1 on an `a.anchor` `#`/`TOC` span.
  Options are documented in the INLINE form: a paragraph ending "The
  filter accepts the following options:" followed by a `<dl>` — the
  other real form is an `<h4>Options</h4>` heading + `<dl>`, so the
  parser must handle both.
- `<corpus>/build-facts/filter-{ebur128,blackframe,signalstats}.txt` —
  verbatim `ffmpeg -h filter=<name>` captures from that build; options
  live under the `<name> AVOptions:` line as
  `   <name> <type> <flags> <desc> (from X to Y)? (default Z)` with
  indented sub-lines for enum values.

Edge cases these fixtures exercise (all real markup):

- **ebur128** — the options `<dl>` contains NESTED `<dl class="table">`
  value lists inside the `framelog` and `peak` `<dd>`s (6 quoted
  enum-value dts total). A parser that stops at the first `</dl>`
  truncates to 5 options; one that walks every `<dt>` gets 23 names
  (including quoted enum values). Correct top-level count: 17 dt/dd
  pairs, 1:1.
- **signalstats** — GROUPED dts: `<dt>stat</dt><dt>out</dt>` are
  followed by ONE combined `<dd>` describing both; a nested values dl
  sits inside that dd. Build-facts carry 4 top-level options (`stat`,
  `out`, plus the alias pair `c`/`color`), so the docs-only options
  list is 2.
- **blackframe** — NO options dl at all: a third, prose form ("It
  accepts the following parameters: … it defaults to 98 . … it
  defaults to 32 .") including an in-prose alias ("threshold,
  thresh"). Build-facts carry 3 options (`amount`, `threshold`,
  `thresh` alias).

`ebur128`, `signalstats` and `blackframe` are the M11.3 pilot filters
(diagnostic domain, all present in both builds — the `verified_both`
case, with the docs-vs-build option gaps documented above). Conflict /
`present_in` / `docs_only` / `undocumented` branches must be exercised
in tests with SYNTHETIC corpus roots built in `tmp_path`, not by
mutating these fixtures.
