#!/usr/bin/env bash
# Build the pinned-release RAG corpus for one FFmpeg version (PRD 5.1-5.2).
#
# Usage: rag/tools/build_corpus.sh <6.1.1|9.0.1>
#
# Produces under rag/corpus/ffmpeg-<v>/:
#   docs/                    the 11 official manuals (HTML), release-pinned
#   build-facts/             build-help capture (source_kind: build_help)
#   manifest.json            manual corpus manifest (PRD 5.2 fields)
#   build-facts/manifest.json
#   _build/                  scratch: downloads + extracted source (gitignored, disposable)
#
# Non-root: every package install is `apt-get download` + `dpkg -x` into the
# scratch dir. The recorded recipe lives in rag/corpus/BUILD-RECIPE.md —
# keep both in sync when either changes.
#
# Determinism: re-running rebuilds from scratch (rm -rf) and must yield the
# same document bytes for the same source artifact; the manifests hash every
# file, so drift shows up as a manifest diff.
set -euo pipefail

VERSION="${1:?usage: build_corpus.sh <6.1.1|9.0.1>}"
case "$VERSION" in
    6.1.1|9.0.1) ;;
    *) echo "unsupported version: $VERSION (supported: 6.1.1, 9.0.1)" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
CORPUS="$REPO/rag/corpus/ffmpeg-$VERSION"
DOCS="$CORPUS/docs"
BF="$CORPUS/build-facts"
BUILD="$CORPUS/_build"

# The eleven individual manuals (PRD 5.1). ffmpeg-all.html is EXCLUDED on
# purpose — its overlapping content would duplicate the individual manuals.
MANUALS=(ffmpeg ffprobe ffmpeg-filters ffmpeg-codecs ffmpeg-formats
         ffmpeg-utils ffmpeg-scaler ffmpeg-resampler
         ffmpeg-bitstream-filters ffmpeg-protocols ffmpeg-devices)

rm -rf "$BUILD" "$DOCS" "$BF"
mkdir -p "$BUILD" "$DOCS" "$BF"

sha256() { sha256sum "$1" | awk '{print $1}'; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '\n=== %s\n' "$*"; }

SOURCE_JSON="$BUILD/source.json"

# --------------------------------------------------------------- manuals

log "docs: fetching $VERSION manuals"
case "$VERSION" in
6.1.1)
    # Version-matched distro package — PRD 5.1 allows it as a pinned-release
    # source. Ubuntu noble ships ffmpeg-doc 6.1.1-3ubuntu5: the same version
    # as the tr4 host build, so the corpus matches the host binary.
    (cd "$BUILD" && apt-get download ffmpeg-doc)
    DEB="$(ls "$BUILD"/ffmpeg-doc_*.deb)"
    dpkg -x "$DEB" "$BUILD/deb"
    SRCDIR="$BUILD/deb/usr/share/doc/ffmpeg/manual"
    cat > "$SOURCE_JSON" <<EOF
{
  "type": "distro-deb",
  "package": "ffmpeg-doc",
  "version": "6.1.1-3ubuntu5",
  "filename": "$(basename "$DEB")",
  "sha256": "$(sha256 "$DEB")",
  "apt_source": "ubuntu noble (universe)",
  "fetched_at": "$(now)"
}
EOF
    ;;
9.0.1)
    # Exact release tarball from ffmpeg.org, docs built with `make doc`.
    # configure's htmlpages probe wants perl + (makeinfo>=5 | texi2html);
    # texinfo is assembled non-root from the distro debs into the scratch
    # dir. libtext-unidecode-perl is a hard runtime dependency of makeinfo
    # (Texinfo::Convert::NodeNameNormalization) and is NOT pulled by
    # dpkg -x — without it every HTML page dies with "Can't locate
    # Text/Unidecode.pm".
    (cd "$BUILD" && curl -fsSL -O "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz" \
        -O "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz.asc")
    (cd "$BUILD" && tar xf "ffmpeg-$VERSION.tar.xz")
    (cd "$BUILD" && apt-get download texinfo libtext-unidecode-perl \
        && dpkg -x texinfo_*.deb tools \
        && dpkg -x libtext-unidecode-perl_*.deb perl)
    export PATH="$BUILD/tools/usr/bin:$PATH"
    export PERL5LIB="$BUILD/perl/usr/share/perl5"
    (cd "$BUILD/ffmpeg-$VERSION" && ./configure --disable-autodetect > configure.log 2>&1 \
        && make doc > make.log 2>&1)
    SRCDIR="$BUILD/ffmpeg-$VERSION/doc"
    cat > "$SOURCE_JSON" <<EOF
{
  "type": "release-tarball",
  "url": "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz",
  "filename": "ffmpeg-$VERSION.tar.xz",
  "sha256": "$(sha256 "$BUILD/ffmpeg-$VERSION.tar.xz")",
  "asc_sha256": "$(sha256 "$BUILD/ffmpeg-$VERSION.tar.xz.asc")",
  "doc_toolchain": {
    "makeinfo": "$(makeinfo --version 2>&1 | head -1)",
    "gcc": "$(gcc --version | head -1)",
    "perl": "$(perl -e 'print \$^V' 2>/dev/null || true)"
  },
  "configure_flags": "--disable-autodetect",
  "fetched_at": "$(now)"
}
EOF
    ;;
esac

for m in "${MANUALS[@]}"; do
    [ -s "$SRCDIR/$m.html" ] || { echo "missing or empty manual: $SRCDIR/$m.html" >&2; exit 1; }
    cp "$SRCDIR/$m.html" "$DOCS/"
done
echo "docs: $(ls "$DOCS" | wc -l)/11 manuals copied"

# ----------------------------------------------------------- build-facts

log "build-facts: capturing from the $VERSION build"
case "$VERSION" in
6.1.1)
    # Host binary — must be 6.1.1 (same build as the deb's manuals).
    head -1 <(ffmpeg -version) | grep -q "ffmpeg version 6.1.1" \
        || { echo "host ffmpeg is not 6.1.1: $(ffmpeg -version | head -1)" >&2; exit 1; }
    FF=ffmpeg OUTDIR="$BF" sh "$SCRIPT_DIR/build_facts.sh"
    ;;
9.0.1)
    # The 9.0.1 binary lives in the verification image built from the
    # jrottenberg/ffmpeg:9.0-nvidia2404 base (see BUILD-RECIPE.md for the
    # pinned image ID).
    IMG="${FASS_RAG_901_IMAGE:-fass:m104-verify}"
    docker run --entrypoint sh --rm \
        -e FF=ffmpeg -e OUTDIR=/out \
        -v "$BF":/out -v "$SCRIPT_DIR/build_facts.sh":/build_facts.sh:ro \
        "$IMG" -c 'sh /build_facts.sh'
    ;;
esac

# --------------------------------------------------------------- manifests

log "manifests"
CORPUS="$CORPUS" python3 - "$VERSION" <<'PYEOF'
import datetime
import hashlib
import json
import os
import pathlib
import sys

corpus = pathlib.Path(os.environ["CORPUS"])
version = sys.argv[1]
built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

docs = sorted((corpus / "docs").glob("*.html"))
assert len(docs) == 11, f"expected 11 manuals, found {len(docs)}: {[p.name for p in docs]}"
source = json.loads((corpus / "_build/source.json").read_text())

manifest = {
    "corpus_id": f"ffmpeg-{version}",
    "version_status": "pinned-release",
    "source_kind": "manual",
    "source": source,
    "built_at": built,
    "parser_version": None,   # filled by the ingestion CLI (M11.0 parser)
    "documents": [
        {"name": p.name, "sha256": sha(p), "bytes": p.stat().st_size} for p in docs
    ],
    "total_chunks": None,     # filled by the ingestion CLI
    "index_generation": 1,
}
(corpus / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

bf = sorted((corpus / "build-facts").glob("*.txt"))
build_identity = (corpus / "build-facts/version.txt").read_text().strip().splitlines()[0]
bf_manifest = {
    "corpus_id": f"ffmpeg-{version}-build-help",
    "version_status": "pinned-release",
    "source_kind": "build_help",
    "source": {**source, "build_identity": build_identity},
    "built_at": built,
    "parser_version": None,
    "documents": [
        {"name": p.name, "sha256": sha(p), "bytes": p.stat().st_size} for p in bf
    ],
    "total_chunks": None,
    "index_generation": 1,
}
(corpus / "build-facts/manifest.json").write_text(json.dumps(bf_manifest, indent=2) + "\n")
print(f"manifests: ffmpeg-{version} ({len(docs)} manuals) + "
      f"ffmpeg-{version}-build-help ({len(bf)} files)")
PYEOF

# ---------------------------------------------------------------- verify

log "verify"
test "$(ls "$DOCS" | wc -l)" = "11"
test -s "$CORPUS/manifest.json"
test -s "$BF/manifest.json"
python3 -c 'import json, sys
for f in sys.argv[1:]:
    json.load(open(f))
print("manifests parse OK")' "$CORPUS/manifest.json" "$BF/manifest.json"
du -sh "$DOCS" "$BF" | sed 's/^/  /'
echo "DONE: $CORPUS"
