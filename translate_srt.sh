#!/usr/bin/env bash
set -euo pipefail

show_help() {
  echo "Usage: $0 -i <input.srt> [-o <output_dir>] [--backend ollama|azure] [--chunk <blocks_per_request>]"
  exit 1
}

#--- 引数パース --------------------------------------------------------------
INPUT=""
OUTDIR=""
BACKEND="${CLIPTOOLS_LLM_BACKEND:-ollama}"
CHUNK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i) INPUT="$2"; shift 2 ;;
    -o) OUTDIR="$2"; shift 2 ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --chunk) CHUNK="$2"; shift 2 ;;
    -h|--help) show_help ;;
    *) echo "Unknown option: $1" >&2; show_help ;;
  esac
done

[[ -z "$INPUT" ]] && show_help
[[ ! -f "$INPUT" ]] && { echo "Input file not found: $INPUT" >&2; exit 1; }

# -o が指定されていなかった場合、-i のディレクトリを使う
OUTDIR="${OUTDIR:-$(dirname "$INPUT")}"

#--- Python スクリプトを実行 -------------------------------------------------
ARGS=( -i "$INPUT" -o "$OUTDIR" --backend "$BACKEND" )
if [[ -n "$CHUNK" ]]; then
  ARGS+=( --chunk "$CHUNK" )
fi
python translate_srt_gpt.py "${ARGS[@]}"
