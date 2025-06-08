#!/usr/bin/env bash
###############################################################################
# run_all.sh  (v1.2 – 2025-06-08)
#
# ① YouTube から動画を DL（download.sh）               ── -u/--url
# ② 既存の mp4/mp3 を直接処理                         ── -f/--file
# ③ 指定言語で字幕生成（generate_srt.sh）             ── -l/--locale
# ④ GPT で日本語に翻訳（translate_srt.sh）
#
# 追加仕様:
# 使い方:
#   ./run_all.sh -u <YouTube URL> [-o <dir>] [-l <locale>] [--clip S E] [--audio] [-N <number>]
#
#   -u|--url       : ダウンロードしたい YouTube URL               (必須)
#   -o|--outdir    : 出力ディレクトリ (既定: カレント)
#   -l|--locale    : 字幕生成言語     (既定: en-US   → SpeakerX_en-US.srt)
#   -n <N>            : 話者数を N に固定               （-m/-N と排他）
#   -m <MIN>          : 最小話者数
#   -N/-M <MAX>       : 最大話者数
#   --clip S E        : hh:mm:ss-hh:mm:ss で切り抜き DL
#   --audio           : 音声のみ DL（download.sh -w）
#
# 使い方例:
# ▸ URL から取得して話者数を 2–4 人として処理
#     ./run_all.sh -u https://youtu.be/abc -o work -m 2 -N 4
#
# ▸ 既存ファイルを 1 人話者で字幕→翻訳
#     ./run_all.sh -f lecture.mp4 -n 1
#
# ▸ DLを省略して既存ファイルを入力に指定
#     ./run_all.sh -f work/video.mp4 -o work
#
###############################################################################
set -Eeuo pipefail

# ────────────── デフォルト
URL=""               # YouTube URL
MEDIA_FILE=""        # 既存ファイル
OUTDIR="."
LOCALE="en-US"
START="" END=""
AUDIO_ONLY=false

FIX_SPK=""           # -n
MIN_SPK=""           # -m
MAX_SPK=""           # -N/-M

# ────────────── ヘルプ
show_help() {
  sed -n '5,40p' "$0"
}

# ────────────── 引数パース
while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--url)   URL="$2";       shift 2 ;;
    -f|--file)  MEDIA_FILE="$2";shift 2 ;;
    -o|--outdir)OUTDIR="$2";    shift 2 ;;
    -l|--locale)LOCALE="$2";    shift 2 ;;
    --clip)     START="$2"; END="$3"; shift 3 ;;
    --audio)    AUDIO_ONLY=true;shift ;;
    -n|--spk)   FIX_SPK="$2";   shift 2 ;;
    -m|--min)   MIN_SPK="$2";   shift 2 ;;
    -N|--max|-M)MAX_SPK="$2";   shift 2 ;;
    -h|--help)  show_help; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ────────────── 相互排他チェック
if [[ -n $URL && -n $MEDIA_FILE ]]; then
  echo "Error: -u と -f は同時に指定できません" >&2; exit 1
fi
if [[ -z $URL && -z $MEDIA_FILE ]]; then
  echo "Error: -u か -f のどちらかを指定してください" >&2; exit 1
fi
if [[ -n $FIX_SPK && ( -n $MIN_SPK || -n $MAX_SPK ) ]]; then
  echo "Error: -n は -m/-N と同時に使えません" >&2; exit 1
fi

mkdir -p "$OUTDIR"

###############################################################################
# 1. ダウンロード（-u の場合）
###############################################################################
MEDIA_PATH=""
if [[ -n $URL ]]; then
  # download.sh の “安全なタイトル → ファイル名” ロジックを踏襲:contentReference[oaicite:0]{index=0}
  get_safe_basename() {
    local raw; raw=$(yt-dlp --get-title "$1")
    echo "${raw//[^a-zA-Z0-9._-]/_}"
  }
  BASENAME=$(get_safe_basename "$URL")

  DL_ARGS=()
  [[ -n $START && -n $END ]] && DL_ARGS+=(-s "$START" -e "$END")
  $AUDIO_ONLY && DL_ARGS+=(-w)

  echo "▶ 1/3 download.sh"
  ./download.sh "$URL" "${DL_ARGS[@]}" "$OUTDIR" "$BASENAME"  # :contentReference[oaicite:1]{index=1}

  EXT=$($AUDIO_ONLY && echo "mp3" || echo "mp4")
  MEDIA_PATH="${OUTDIR%/}/${BASENAME}.${EXT}"
else
  # 既存ファイルを使用
  MEDIA_PATH="$MEDIA_FILE"
  echo "▶ 1/3 既存メディア使用: $MEDIA_PATH"
fi

[[ -f $MEDIA_PATH ]] || { echo "Error: ファイルがありません: $MEDIA_PATH"; exit 2; }

###############################################################################
# 2. 字幕生成
###############################################################################
echo "▶ 2/3 generate_srt.sh ($LOCALE)"
GEN_ARGS=()
[[ -n $FIX_SPK ]] && GEN_ARGS+=( -n "$FIX_SPK" )
[[ -n $MIN_SPK ]] && GEN_ARGS+=( -m "$MIN_SPK" )
[[ -n $MAX_SPK ]] && GEN_ARGS+=( -M "$MAX_SPK" )
./generate_srt.sh "${GEN_ARGS[@]}" "$MEDIA_PATH" "$LOCALE"       # :contentReference[oaicite:2]{index=2}

# 生成された SRT 一覧
SRT_PATTERN="Speaker*_${LOCALE}.srt"
shopt -s nullglob
SRT_FILES=("$OUTDIR"/$SRT_PATTERN)
shopt -u nullglob
[[ ${#SRT_FILES[@]} -gt 0 ]] || { echo "Error: SRT が見つかりません"; exit 3; }

###############################################################################
# 3. 翻訳
###############################################################################
echo "▶ 3/3 translate_srt.sh → ja-*.srt"
for srt in "${SRT_FILES[@]}"; do
  ./translate_srt.sh -i "$srt" -o "$OUTDIR"                     # :contentReference[oaicite:3]{index=3}
done

echo "✅ 完了: 出力先 → $OUTDIR"
exit 0

