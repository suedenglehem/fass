#!/bin/sh
# Capture the build-facts corpus (source_kind: build_help) from an ffmpeg
# binary into $OUTDIR. Used by build_corpus.sh for both versions:
#   - 6.1.1: host binary (FF=ffmpeg)
#   - 9.0.1: inside the verification docker image (FF=ffmpeg in-container)
#
# Env: OUTDIR (required), FF (ffmpeg command prefix, default "ffmpeg").
# Every capture is `cmd 2>&1 > file` so stderr diagnostics are kept too —
# the build-facts corpus is authoritative for THAT build, quirks included.
set -e

OUTDIR="${OUTDIR:?OUTDIR not set}"
FF="${FF:-ffmpeg}"

mkdir -p "$OUTDIR"
"$FF" -version > "$OUTDIR/version.txt" 2>&1
"$FF" -h full > "$OUTDIR/h-full.txt" 2>&1
"$FF" -filters > "$OUTDIR/filters.txt" 2>&1
"$FF" -encoders > "$OUTDIR/encoders.txt" 2>&1
"$FF" -muxers > "$OUTDIR/muxers.txt" 2>&1
"$FF" -bsfs > "$OUTDIR/bsfs.txt" 2>&1

# One help capture per filter the build itself reports. The -filters list
# format is "<flags> <name> <args> <desc>" where <args> always contains "->"
# (e.g. V->V, A->A, V|A->V|A) — that column is the discriminator.
awk 'NF>=3 && $3 ~ /->/ {print $2}' "$OUTDIR/filters.txt" > "$OUTDIR/filter-names.txt"
while IFS= read -r f; do
    [ -n "$f" ] || continue
    "$FF" -h filter="$f" > "$OUTDIR/filter-$f.txt" 2>&1 || true
done < "$OUTDIR/filter-names.txt"

echo "build-facts: $(ls "$OUTDIR" | wc -l) files ($(wc -l < "$OUTDIR/filter-names.txt") filters)"
