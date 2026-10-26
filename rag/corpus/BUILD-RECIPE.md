# RAG corpus build recipe

`rag/corpus/` holds the two pinned-release FFmpeg corpora that the RAG service
indexes (PRD §5.1–5.2). Each corpus is a version-pinned identity —
`ffmpeg-6.1.1` and `ffmpeg-9.0.1` — built from an exact release source, never
mixed with a nightly snapshot, and never labeled as a release unless it is one.
Per corpus directory:

- `docs/` — the eleven individual manuals as HTML (PRD §5.1); `ffmpeg-all.html`
  is excluded on purpose so its overlapping content does not duplicate them.
- `build-facts/` — the build-help side corpus for *that specific build*
  (`source_kind: build_help`).
- `manifest.json` / `build-facts/manifest.json` — per-file SHA-256, source
  identity, build timestamp. **Committed.**
- `_build/` — scratch downloads and extracted source. **Gitignored.**

Every SHA-256 below is copied verbatim from the corresponding `manifest.json`.

## ffmpeg-6.1.1

Source: the Ubuntu distro package **`ffmpeg-doc` 6.1.1-3ubuntu5** — the
version-matched distro route that PRD §5.1 allows as a pinned-release source.
The host build is `6.1.1-3ubuntu5`, so the manuals match the host binary the
assistant probes.

- deb filename: `ffmpeg-doc_7%3a6.1.1-3ubuntu5_all.deb`
- SHA-256: `7f5f0ed49880be05860b8ba82b49e1fdd9423b6a88a8b436f686f82995ceb4e1`
- apt source: `ubuntu noble (universe)`

Non-root assembly: `apt-get download ffmpeg-doc` + `dpkg -x` into the scratch
dir. The 11 manuals are taken from
`usr/share/doc/ffmpeg/manual/` inside the extracted deb.

## ffmpeg-9.0.1

Source: `https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz`, docs built from the
release source with `make doc`.

- tarball filename: `ffmpeg-9.0.1.tar.xz`
- tarball SHA-256: `cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635`
- `.asc` SHA-256: `b613a00005232a1245ace7080088781ac23a916119d3e5b0d6c042368eee0177`
- configure flags: `--disable-autodetect`
- doc toolchain, as recorded in the manifest:
  - makeinfo: `texi2any (GNU texinfo) 7.1`
  - gcc: `gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
  - perl: `REF(0x579ae36a7280)` — verbatim from the manifest; the build script's
    `perl -e 'print $^V'` capture emits a reference string rather than a version
    number, so this field is a capture artifact, not a version.

Non-root assembly of the doc toolchain: `apt-get download texinfo
libtext-unidecode-perl` + `dpkg -x` each into the scratch dir, then
`PATH=$BUILD/tools/usr/bin` and `PERL5LIB=$BUILD/perl/usr/share/perl5`.
`libtext-unidecode-perl` is **required**: it is a runtime dependency of
makeinfo (`Texinfo::Convert::NodeNameNormalization`) and `dpkg -x` does not
pull it, so without it every HTML page dies with
`Can't locate Text/Unidecode.pm`.

## build-facts

Captured by `rag/tools/build_facts.sh` from the build that answers for each
version:

- **6.1.1** — from the **host `ffmpeg` binary** (`6.1.1-3ubuntu5`).
- **9.0.1** — inside docker image **`fass:m104-verify`** (image ID
  `25d79c8508de` on this machine), built from the `jrottenberg/ffmpeg:9.0-nvidia2404`
  base.

Contents per corpus: `ffmpeg -version`, `ffmpeg -h full`, `-filters`,
`-encoders`, `-muxers`, `-bsfs`, plus `-h filter=<name>` for every filter the
build itself reports (the filter list's `->` argument column is the
discriminator). Captures keep stderr too, so the corpus is authoritative for
that build, quirks included. Current counts: 562 files for 6.1.1 (555
filters), 516 for 9.0.1 (509 filters).

## Rebuild

```
bash rag/tools/build_corpus.sh 6.1.1
bash rag/tools/build_corpus.sh 9.0.1
```

Re-running wipes and rebuilds the corpus from scratch; the same source artifact
must yield the same document bytes, so drift shows up as a manifest diff. The
corpus data (`docs/`, `build-facts/*.txt`, `_build/`) is gitignored and
deterministically rebuilt; the four manifests and this recipe are committed.
Keep this file and `rag/tools/build_corpus.sh` in sync when either changes.
