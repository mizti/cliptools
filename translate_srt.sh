#!/usr/bin/env bash
set -euo pipefail

show_help() {
  echo "Usage: $0 -i <input.srt> [-o <output_dir>]"
  exit 1
}

#--- 引数パース --------------------------------------------------------------
INPUT=""
OUTDIR=""

while getopts ":i:o:" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    *) show_help ;;
  esac
done

[[ -z "$INPUT" ]] && show_help
[[ ! -f "$INPUT" ]] && { echo "Input file not found: $INPUT" >&2; exit 1; }

# -o が指定されていなかった場合、-i のディレクトリを使う
OUTDIR="${OUTDIR:-$(dirname "$INPUT")}"

#--- Python スクリプトを実行 -------------------------------------------------
python translate_srt_gpt.py -i "$INPUT" -o "$OUTDIR"
