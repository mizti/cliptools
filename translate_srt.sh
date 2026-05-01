#!/usr/bin/env bash
set -euo pipefail

show_help() {
  echo "Usage: $0 -i <input.srt> [-o <output_dir>] [-s <src_locale>] [-t <dst_locale>]" >&2
  exit 1
}

#--- 引数パース --------------------------------------------------------------
INPUT=""
OUTDIR=""
SRC_LOCALE=""
DST_LOCALE=""

while getopts ":i:o:s:t:" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    s) SRC_LOCALE="$OPTARG" ;;
    t) DST_LOCALE="$OPTARG" ;;
    *) show_help ;;
  esac
done

[[ -z "$INPUT" ]] && show_help
[[ ! -f "$INPUT" ]] && { echo "Input file not found: $INPUT" >&2; exit 1; }

# -o が指定されていなかった場合、-i のディレクトリを使う
OUTDIR="${OUTDIR:-$(dirname "$INPUT")}"

#--- Python スクリプトを実行 -------------------------------------------------
ARGS=( -i "$INPUT" -o "$OUTDIR" )
[[ -n "$SRC_LOCALE" ]] && ARGS+=( --src-locale "$SRC_LOCALE" )
[[ -n "$DST_LOCALE" ]] && ARGS+=( --dst-locale "$DST_LOCALE" )
python translate_srt_gpt.py "${ARGS[@]}"
