#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <audio_file> [-o output_dir]" >&2
  exit 1
}

[[ $# -lt 1 ]] && usage

INPUT="$1"; shift
OUTDIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) OUTDIR="$2"; shift 2 ;;
    *)  usage ;;
  esac
done

[[ ! -f "$INPUT" ]] && { echo "[ERROR] File not found: $INPUT" >&2; exit 1; }

OUTDIR="${OUTDIR:-$(dirname "$INPUT")/separated_sound}"
mkdir -p "$OUTDIR"

python -m demucs --two-stems vocals -o "$OUTDIR" "$INPUT"

echo "✅ Done → $OUTDIR"
