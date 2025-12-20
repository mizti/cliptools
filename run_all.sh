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
#   --engine       : STT エンジン (azure|whispercpp) ※省略時は generate_srt.sh のデフォルト(azure)
#   -W|--from-whisper-json : whisper-cli が出力した JSON から開始（内部フォーマットに変換して続行）
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
# ▸ Azure を使って字幕を生成したい場合
#     ./run_all.sh -f lecture.mp4 --engine azure -n 1
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
FROM_JSON=""         # 既存の STT JSON（内部フォーマット: azure-stt.json）から開始
FROM_WHISPER_JSON="" # 既存の whisper-cli JSON から開始（内部フォーマットに変換して続行）
LOCALE="en-US"
ENGINE=""          # azure|whispercpp. 空なら generate_srt.sh 側のデフォルトに任せる
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
    --engine)   ENGINE="$2";    shift 2 ;;
    --clip)     START="$2"; END="$3"; shift 3 ;;
    --audio)    AUDIO_ONLY=true;shift ;;
    -n|--spk)   FIX_SPK="$2";   shift 2 ;;
    -m|--min)   MIN_SPK="$2";   shift 2 ;;
  -N|--max|-M)MAX_SPK="$2";   shift 2 ;;
  -j|--from-json)FROM_JSON="$2"; shift 2 ;;
  -W|--from-whisper-json)FROM_WHISPER_JSON="$2"; shift 2 ;;
    -h|--help)  show_help; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$ENGINE" && ! "$ENGINE" =~ ^(azure|whispercpp)$ ]]; then
  echo "Error: --engine は azure|whispercpp で指定してください" >&2
  exit 1
fi

# ────────────── 相互排他チェック
if [[ -n $FROM_JSON && -n $FROM_WHISPER_JSON ]]; then
  echo "Error: --from-json と --from-whisper-json は同時に指定できません" >&2; exit 1
fi
if [[ -n $FROM_JSON && -n $URL ]]; then
  echo "Error: --from-json と -u は同時に指定できません" >&2; exit 1
fi
if [[ -n $FROM_JSON && -n $MEDIA_FILE ]]; then
  echo "Error: --from-json と -f は同時に指定できません" >&2; exit 1
fi
if [[ -n $FROM_WHISPER_JSON && -n $URL ]]; then
  echo "Error: --from-whisper-json と -u は同時に指定できません" >&2; exit 1
fi
if [[ -n $FROM_WHISPER_JSON && -n $MEDIA_FILE ]]; then
  echo "Error: --from-whisper-json と -f は同時に指定できません" >&2; exit 1
fi
if [[ -n $URL && -n $MEDIA_FILE ]]; then
  echo "Error: -u と -f は同時に指定できません" >&2; exit 1
fi
if [[ -z $URL && -z $MEDIA_FILE && -z $FROM_JSON && -z $FROM_WHISPER_JSON ]]; then
  echo "Error: -u か -f か --from-json か --from-whisper-json のいずれかを指定してください" >&2; exit 1
fi
if [[ -n $FIX_SPK && ( -n $MIN_SPK || -n $MAX_SPK ) ]]; then
  echo "Error: -n は -m/-N と同時に使えません" >&2; exit 1
fi

# -f が指定されていて -o が指定されていない場合は、入力ファイルと同じディレクトリを
# デフォルトの出力ディレクトリとして扱う。
if [[ -n $MEDIA_FILE && "$OUTDIR" == "." ]]; then
  OUTDIR=$(dirname "$MEDIA_FILE")
fi

###############################################################################
# 1. ダウンロード（-u の場合）
###############################################################################
MEDIA_PATH=""
if [[ -n $FROM_WHISPER_JSON ]]; then
  # whisper-cli JSON から内部フォーマット(azure-stt.json)へ変換して続行
  [[ -f $FROM_WHISPER_JSON ]] || { echo "Error: ファイルがありません: $FROM_WHISPER_JSON"; exit 2; }

  # -o が省略された場合は JSON ファイルと同じディレクトリを出力先にする
  if [[ "$OUTDIR" == "." ]]; then
    OUTDIR=$(dirname "$FROM_WHISPER_JSON")
  fi
  mkdir -p "$OUTDIR"

  FROM_JSON="${OUTDIR%/}/azure-stt.json"
  echo "▶ 1/3 whisper JSON → 内部 STT JSON 変換: $FROM_WHISPER_JSON → $FROM_JSON"
  python -m utils.whispercpp_json_to_azure_json "$FROM_WHISPER_JSON" "$FROM_JSON"

elif [[ -n $FROM_JSON ]]; then
  # 既存の STT JSON（内部フォーマット）から開始する場合は download.sh をスキップし、
  # generate_srt.sh の --from-json 経路だけを使う。
  echo "▶ 1/3 既存 STT JSON 使用: $FROM_JSON"
elif [[ -n $URL ]]; then
  # download.sh と同じロジックで安全なベース名を決める
  get_safe_basename() {
    # $1 = 出力ディレクトリ (= OUTDIR)
    local outdir="$1"
    local raw="downloaded_clip"                   # -o 省略時の既定名

    if [[ -n "$outdir" && "$outdir" != "." ]]; then
      raw=$(basename "$outdir")                   # -o があれば末尾名
    fi
    echo "${raw//[^a-zA-Z0-9._-]/_}"              # 安全化して返す
  }
  BASENAME=$(get_safe_basename "$OUTDIR")

  DL_ARGS=()
  [[ -n $START && -n $END ]] && DL_ARGS+=(-s "$START" -e "$END")
  $AUDIO_ONLY && DL_ARGS+=(-w)

  echo "▶ 1/3 download.sh"
  # zsh + set -u だと、空配列の "${DL_ARGS[@]}" 展開が unbound 扱いになることがあるため、
  # 要素数を見て分岐させてから download.sh を呼び出す。
  if [[ ${#DL_ARGS[@]} -gt 0 ]]; then
    ./download.sh -u "$URL" -o "$OUTDIR" -b "$BASENAME" "${DL_ARGS[@]}"
  else
    ./download.sh -u "$URL" -o "$OUTDIR" -b "$BASENAME"
  fi

  ###########################################################################
  # (optional) hype-finder: analyze live chat replay to find hype segments
  # Runs right after download.sh, because it needs the same YouTube URL.
  # Output goes under: $OUTDIR/hype-data/
  ###########################################################################
  if [[ -x ./hype_finder.sh ]]; then
    echo "▶ 1.5/3 hype_finder.sh (live chat → hype segments)"
    # Do not fail the whole pipeline if live_chat is unavailable for the video.
    ./hype_finder.sh -u "$URL" -o "$OUTDIR" || echo "[warn] hype_finder failed; continuing" >&2
  else
    echo "[warn] ./hype_finder.sh not found/executable; skipping hype-finder" >&2
  fi

  EXT=$($AUDIO_ONLY && echo "mp3" || echo "mp4")
  MEDIA_PATH="${OUTDIR%/}/${BASENAME}.${EXT}"
  # -o が指定されていない場合は、ダウンロードされたメディアファイルのディレクトリを
  # デフォルトの出力ディレクトリとして扱う（既存ファイルを処理する -f の挙動と揃える）。
  if [[ "$OUTDIR" == "." ]]; then
    OUTDIR=$(dirname "$MEDIA_PATH")
  fi
else
  # 既存ファイルを使用
  MEDIA_PATH="$MEDIA_FILE"
  echo "▶ 1/3 既存メディア使用: $MEDIA_PATH"
fi

# --from-json で -o が省略された場合も、JSON ファイルと同じディレクトリを
# デフォルトの出力ディレクトリとして扱う。
if [[ -n $FROM_JSON && "$OUTDIR" == "." ]]; then
  OUTDIR=$(dirname "$FROM_JSON")
fi

mkdir -p "$OUTDIR"

if [[ -z $FROM_JSON ]]; then
  [[ -f $MEDIA_PATH ]] || { echo "Error: ファイルがありません: $MEDIA_PATH"; exit 2; }
fi

###############################################################################
# 2. 字幕生成
###############################################################################
echo "▶ 2/3 generate_srt.sh ($LOCALE)"
GEN_ARGS=()
[[ -n $ENGINE ]] && GEN_ARGS+=( --engine "$ENGINE" )
[[ -n $FIX_SPK ]] && GEN_ARGS+=( -n "$FIX_SPK" )
[[ -n $MIN_SPK ]] && GEN_ARGS+=( -m "$MIN_SPK" )
[[ -n $MAX_SPK ]] && GEN_ARGS+=( -M "$MAX_SPK" )

# zsh + set -u だと、空配列の "${GEN_ARGS[@]}" 展開が unbound 扱いになることがあるため、
# 要素数を見て分岐させてから generate_srt.sh を呼び出す。
if [[ -n $FROM_JSON ]]; then
  # 既存 JSON から開始する場合
  if [[ ${#GEN_ARGS[@]} -gt 0 ]]; then
    ./generate_srt.sh --from-json "$FROM_JSON" -o "$OUTDIR" "${GEN_ARGS[@]}" "$LOCALE"
  else
    ./generate_srt.sh --from-json "$FROM_JSON" -o "$OUTDIR" "$LOCALE"
  fi
elif [[ ${#GEN_ARGS[@]} -gt 0 ]]; then
  ./generate_srt.sh -o "$OUTDIR" "${GEN_ARGS[@]}" "$MEDIA_PATH" "$LOCALE"       # :contentReference[oaicite:2]{index=2}
else
  ./generate_srt.sh -o "$OUTDIR" "$MEDIA_PATH" "$LOCALE"       # オプションなし
fi

# 生成された SRT 一覧（OUTDIR 配下）
SRT_PATTERN="Speaker*_${LOCALE}.srt"
shopt -s nullglob
SRT_FILES=("$OUTDIR"/$SRT_PATTERN)
shopt -u nullglob
[[ ${#SRT_FILES[@]} -gt 0 ]] || { echo "Error: SRT が見つかりません"; exit 3; }

###############################################################################
# 3. 固有名詞補正 (英語 SRT → 英語 SRT fixed)
###############################################################################
echo "▶ 3/4 fix_unique_nouns.py (proper nouns in EN SRT)"
FIXED_SRT_FILES=()
for srt in "${SRT_FILES[@]}"; do
  # 出力ファイル名は <元ファイル名>_fixed.srt
  fixed_srt="${srt%.srt}_fixed.srt"
  python fix_unique_nouns.py "$srt" -o "$fixed_srt" || {
    echo "[warn] fix_unique_nouns.py failed for $srt; using original SRT" >&2
    FIXED_SRT_FILES+=("$srt")
    continue
  }
  FIXED_SRT_FILES+=("$fixed_srt")
done

###############################################################################
# 4. 翻訳
###############################################################################
echo "▶ 4/4 translate_srt.sh → *_ja-JP.srt"
for srt in "${FIXED_SRT_FILES[@]}"; do
  ./translate_srt.sh -i "$srt" -o "$OUTDIR"                     # :contentReference[oaicite:3]{index=3}
done

echo "✅ 完了: 出力先 → $OUTDIR"
exit 0

