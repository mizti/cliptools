#!/usr/bin/env bash
# count_livechat.sh
#
# 使い方:
#   DEBUG=true ./count_livechat.sh <YouTube URL> <出力ディレクトリ>
#
#   DEBUG を true にしなければ ./temp 内の中間ファイルは削除されます。
#   生成される SRT は <出力ディレクトリ>/comment_count.srt

set -euo pipefail

###############################################
# 引数チェック
###############################################
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <YouTube URL> <output_dir>"
  exit 1
fi

URL="$1"
OUTDIR="$2"
DEBUG="${DEBUG:-false}"

TMPDIR="./temp"
mkdir -p "$TMPDIR" "$OUTDIR"

###############################################
# 1. ライブチャット JSON を取得
###############################################
# live chat は「字幕」として扱われるため、
#   --write-subs --sub-langs live_chat
# で .live_chat.json が落ちてきます。:contentReference[oaicite:0]{index=0}
yt-dlp \
  --skip-download \
  --write-subs --sub-langs live_chat \
  -o "$TMPDIR/%(id)s.%(ext)s" \
  "$URL"

# 取得した JSON ファイル（複数になることはまず無い想定）
JSON_FILE=$(ls "$TMPDIR"/*.live_chat.json | head -n 1)

###############################################
# 2. 10 秒ごとのコメント数を SRT 形式で集計
###############################################
python3 - <<'PY' "$JSON_FILE" "$OUTDIR/comment_count.srt"
import sys, json, math, re, os, pathlib

json_path, srt_path = sys.argv[1:]
with open(json_path, 'r', encoding='utf-8') as fp:
    data = json.load(fp)

def to_seconds(entry):
    """
    yt-dlp の live_chat.json はメッセージごとに
      • "time_in_seconds"  (float)
      • "time_in_ms" / "timestampUsec" など
    のいずれかを持つ。見つかったものを秒(float)で返す。
    """
    for k in ('time_in_seconds', 'time_in_ms', 'timestampUsec'):
        if k in entry:
            v = entry[k]
            if isinstance(v, str) and v.isdigit():
                v = float(v)
            if 'ms' in k or 'Usec' in k:
                v = v / 1000.0
            return float(v)
    # 最悪 time_text を h:m:s.fff でパース
    if 'time_text' in entry:
        hms = [float(x) for x in re.split('[:.]', entry['time_text'])]
        sec = 0
        for n in hms:
            sec = sec * 60 + n
        return sec
    return None

times = list(filter(None, (to_seconds(e) for e in data)))
max_t = max(times, default=0)
bins = [0] * (math.ceil((max_t + 1) / 10))

for t in times:
    bins[int(t // 10)] += 1

def fmt(ts):
    h = int(ts // 3600)
    m = int(ts % 3600 // 60)
    s = int(ts % 60)
    ms = int(round((ts - int(ts)) * 1000))
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

with open(srt_path, 'w', encoding='utf-8') as fp:
    for idx, cnt in enumerate(bins, 1):
        start = (idx - 1) * 10
        end   = idx * 10
        fp.write(f'{idx}\n{fmt(start)} --> {fmt(end)}\n{cnt}\n\n')

print(f'Wrote {srt_path}')
PY

###############################################
# 3. DEBUG でなければ temp を掃除
###############################################
if [[ "${DEBUG,,}" != "true" ]]; then
  rm -f "$TMPDIR"/*.live_chat.json
  rmdir "$TMPDIR" 2>/dev/null || true
fi

