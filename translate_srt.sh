#!/usr/bin/env bash
set -euo pipefail

show_help() {
  echo "Usage: $0 -i <input.srt> -o <output_dir>"
  exit 1
}

#--- 引数パース --------------------------------------------------------------
while getopts ":i:o:" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    *) show_help ;;
  esac
done

[[ -z "${INPUT:-}" || -z "${OUTDIR:-}" ]] && show_help
[[ ! -f "$INPUT" ]] && { echo "Input file not found: $INPUT" >&2; exit 1; }

#--- Python スクリプトを実行 -------------------------------------------------
python translate_srt_gpt.py -i "$INPUT" -o "$OUTDIR"

