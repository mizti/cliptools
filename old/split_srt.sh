#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  cat <<USAGE >&2
Usage: $0 <tmp.json> <outdir>
  <tmp.json> : Azure バッチ転写でマージ済みの JSON ファイル
  <outdir>   : 出力先ディレクトリ
USAGE
  exit 1
fi

TMPJSON="$1"
OUTDIR="$2"

if [[ ! -f "$TMPJSON" ]]; then
  echo "Error: File not found: $TMPJSON" >&2
  exit 1
fi

mkdir -p "$OUTDIR"

# スピーカー番号をユニークに取得
SPKS=$(jq -r '.[].speaker // empty' "$TMPJSON" | sort -nu)
echo $SPKS
for sp in $SPKS; do
  OUTFILE="$OUTDIR/Speaker${sp}.srt"
  echo ">>> Generating for speaker $sp → $OUTFILE" >&2

  # jq で “秒単位” の TSV を吐き出し、awk で SRT フォーマットに変換
  jq -r --arg sp "$sp" '
    # ISO8601 期間 → 秒 に変換
    def tosec:
      capture("PT((?<h>[0-9.]+)H)?((?<m>[0-9.]+)M)?((?<s>[0-9.]+)S)?")
      | ((.h//"0"|tonumber)*3600)
      + ((.m//"0"|tonumber)*60)
      + (.s//"0"|tonumber);

    [ .[]
      | select(.speaker == ($sp|tonumber))
      | (.offset   | tosec)   as $start
      | (.duration | tosec)   as $dur
      | {start:$start, end:($start+$dur), text:.nBest[0].display}
    ]
    | sort_by(.start)
    | .[]
    | "\(.start)\t\(.end)\t\(.text)"
  ' "$TMPJSON" \
  | awk -F'\t' '
      function ts(t){
        h=int(t/3600); m=int((t-h*3600)/60);
        s=int(t-h*3600-m*60);
        ms=int((t-int(t))*1000);
        return sprintf("%02d:%02d:%02d,%03d", h, m, s, ms)
      }
      {
        printf("%d\n%s --> %s\n%s\n\n", NR, ts($1), ts($2), $3)
      }
  ' > "$OUTFILE"

  echo "Generated: $OUTFILE"
done

