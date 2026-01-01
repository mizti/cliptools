#!/usr/bin/env bash
###############################################################################
# generate_srt.sh  (diagnostic-based minimal edition 2025-05-28 rev-3-locale-branch)
#
# 追加／変更点
#   • --skip-authorization-header を az rest 全呼び出しに付与   → ①警告解消
#   • STEP 2 でコンテナ内ファイルを一括削除                     → ②
#   • すべての資格情報を環境変数で参照（az login 不要）        → ③
#   • trap を関数 err_trap() に分離して syntax error を回避
#   • STEP 10 で ja / en を分岐（ロジックは同一、将来変更に備え）
###############################################################################
set -Eeuo pipefail
[[ ${TRACE:-0} -eq 1 ]] && set -x            # TRACE=1 で bash -x
DEBUG=${DEBUG:-false}

# ---- ステップ管理 ------------------------------------------------------------
step_no=0 CURRENT_STEP=""
step() { step_no=$((step_no+1)); CURRENT_STEP="$*";
        printf '▶ STEP %-2d %s\n' "$step_no" "$CURRENT_STEP" >&2; }

# ---- 軽量プログレス表示 ------------------------------------------------------
# 長時間処理(Whisper等)の「ログがうるさい問題」を避けるため、デフォルトでは
# 子プロセスの出力を抑制しつつスピナーだけ表示する。
run_with_progress() {
  local log_file="$1"; shift

  # child process writes to a temp file; we both filter to stderr and save full log
  local tmp_out
  tmp_out=$(mktemp)

  _render_percent_bar() {
    local percent="$1"
    [[ -z "$percent" ]] && return 0
    # clamp 0-100
    (( percent < 0 )) && percent=0
    (( percent > 100 )) && percent=100
    local width=30
    local filled=$(( percent * width / 100 ))
    local empty=$(( width - filled ))
    printf '\r[%.*s%*s] %3d%%' "$filled" "##############################" "$empty" "" "$percent" >&2
  }

  # Run the command capturing all output.
  "$@" >"$tmp_out" 2>&1 &
  local pid=$!

  # If attached to a terminal, follow progress and paint a stable percent bar.
  # Depending on whisper.cpp version, progress output may look like:
  #   - "whisper_print_progress_callback: progress =  42%"
  #   - "Translating:  9%|..." (tqdm style)
  local tail_pid=""
  if [[ -t 2 ]]; then
    (
      tail -n 0 -f "$tmp_out" 2>/dev/null | while IFS= read -r line; do
        if [[ "$line" =~ whisper_print_progress_callback:.*progress[[:space:]]*=[[:space:]]*([0-9]{1,3})% ]]; then
          _render_percent_bar "${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ([0-9]{1,3})%\| ]]; then
          _render_percent_bar "${BASH_REMATCH[1]}"
        fi
      done
    ) &
    tail_pid=$!
  fi

  local ec=0
  if ! wait "$pid"; then
    ec=$?
  fi

  if [[ -n "$tail_pid" ]]; then
    # The tail pipeline may already have exited (e.g., SIGPIPE). Don't surface it.
    kill "$tail_pid" 2>/dev/null || true
    wait "$tail_pid" 2>/dev/null || true
    # finish line
    printf '\r' >&2
    printf '\n' >&2
  fi

  mkdir -p "$(dirname "$log_file")"
  cat "$tmp_out" >"$log_file"
  rm -f "$tmp_out"
  return "$ec"
}
err_trap() {
  local ec=$?
  printf '\e[31m✖ STEP %-2d  %s  (exit %d)\e[0m\n' \
          "$step_no" "$CURRENT_STEP" "$ec" >&2
}
trap err_trap ERR

# ---- 処理関数（共通 → 分岐 → 共通 を見通しよくする） -------------------------
# NOTE: 既存の挙動を維持するため、変数は基本的にグローバルに扱う。

ffmpeg_mono_convert() {
  step "FFmpeg mono convert"
  if [[ $DEBUG == true ]]; then
    ffmpeg -y -i "$AUDIO" -ac 1 "$MONO"
  else
    ffmpeg -y -loglevel error -i "$AUDIO" -ac 1 "$MONO"
  fi
}

run_azure_stt_and_merge_to_tmp_json() {
  # ---- 必須環境変数 -------------------------------------------------------------
  step "env var check (azure)"
  for v in STORAGE_ACCOUNT_NAME STORAGE_ACCOUNT_KEY CONTAINER_NAME \
           SPEECH_REGION SPEECH_KEY; do
    [[ -n ${!v:-} ]] || { echo "環境変数 $v が未設定" >&2; exit 1; }
  done

  # ---- 2 Container cleanup ----------------------------------------------------
  step "Clear container"
  az storage blob delete-batch \
    --account-name "$STORAGE_ACCOUNT_NAME" \
    --account-key  "$STORAGE_ACCOUNT_KEY" \
    -s "$CONTAINER_NAME" >/dev/null

  # ---- 3 Blob upload ----------------------------------------------------------
  step "Blob upload"
  az storage blob upload \
    --account-name "$STORAGE_ACCOUNT_NAME" \
    --account-key  "$STORAGE_ACCOUNT_KEY" \
    --container-name "$CONTAINER_NAME" \
    --name "${BASE}.wav" --file "$MONO" --overwrite true

  # ---- 4 SAS ------------------------------------------------------------------
  step "SAS generate"
  EXP=$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%MZ' 2>/dev/null || \
        date -u -v+1H '+%Y-%m-%dT%H:%MZ')
  FILE_SAS=$(az storage blob generate-sas -o tsv \
    --account-name "$STORAGE_ACCOUNT_NAME" --container-name "$CONTAINER_NAME" \
    --name "${BASE}.wav" --permissions r --https-only --expiry "$EXP" \
    --account-key "$STORAGE_ACCOUNT_KEY")
  CONT_SAS=$(az storage container generate-sas -o tsv \
    --account-name "$STORAGE_ACCOUNT_NAME" --name "$CONTAINER_NAME" \
    --permissions rl --https-only --expiry "$EXP" \
    --account-key "$STORAGE_ACCOUNT_KEY")
  FILE_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/${BASE}.wav?${FILE_SAS}"
  CONT_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/?${CONT_SAS}"

  # ---- 5 Transcription job ----------------------------------------------------
  step "Create transcription"
  ENDP="https://${SPEECH_REGION}.api.cognitive.microsoft.com"
  API="speechtotext/v3.2/transcriptions"
  BODY=$(mktemp)
  if [[ -n ${SPEECH_CUSTOM_MODEL_SELF:-} ]]; then
  cat >"$BODY" <<JSON
{
  "displayName":"$BASE",
  "locale":"$LOCALE",
  "contentContainerUrl":"$CONT_URL",
  "model":{ "self":"$SPEECH_CUSTOM_MODEL_SELF" },
  "properties":{
    "diarizationEnabled":true,
    "wordLevelTimestampsEnabled":true,
    "punctuationMode":"DictatedAndAutomatic",
    "profanityFilterMode":"Masked",
    "channels":[0],
    "diarization":{"speakers":{"minCount":$MIN_SPK,"maxCount":$MAX_SPK}}
  }
}
JSON
  else
  cat >"$BODY" <<JSON
{
  "displayName":"$BASE",
  "locale":"$LOCALE",
  "contentContainerUrl":"$CONT_URL",
  "properties":{
    "diarizationEnabled":true,
    "wordLevelTimestampsEnabled":true,
    "punctuationMode":"DictatedAndAutomatic",
    "profanityFilterMode":"Masked",
    "channels":[0],
    "diarization":{"speakers":{"minCount":$MIN_SPK,"maxCount":$MAX_SPK}}
  }
}
JSON
  fi
  REST_OPTS=(--skip-authorization-header --method post --uri "$ENDP/$API" \
    --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" --body @"$BODY")
  [[ $DEBUG == true ]] && REST_OPTS+=(--debug)
  CREATE=$(az rest "${REST_OPTS[@]}")
  rm -f "$BODY"
  JOB_URL=$(echo "$CREATE" | jq -r .self)
  STATUS_URL="${JOB_URL}?api-version=3.2-preview.2"

  # ---- 6 Polling --------------------------------------------------------------
  step "Polling job status"
  echo -n "Processing" >&2
  # 軽量リトライ: az rest が一時的に失敗しても数回までは許容する
  FAIL_COUNT=0
  MAX_FAIL=3
  while :; do
    if STATUS=$(az rest --skip-authorization-header --method get --uri "$STATUS_URL" \
              --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
              --query status -o tsv 2>/dev/null); then
      echo -n "." >&2
      [[ $STATUS == Succeeded ]] && { echo " ok" >&2; break; }
      [[ $STATUS == Failed    ]] && { echo " failed" >&2; exit 3; }
      FAIL_COUNT=0   # 通ったらカウンタリセット
    else
      FAIL_COUNT=$((FAIL_COUNT+1))
      echo -n "x" >&2
      if (( FAIL_COUNT >= MAX_FAIL )); then
        echo " polling API failed ${FAIL_COUNT} times" >&2
        exit 2
      fi
    fi
    sleep 5
  done

  # ---- 7 Segment list ---------------------------------------------------------
  step "Get segment list"
  FILES_URL="${JOB_URL}/files?api-version=3.2-preview.2"
  FILES_JSON=$(az rest --skip-authorization-header --method get --uri "$FILES_URL" \
                --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY")

  # ---- 8 Download segments ----------------------------------------------------
  step "Download segments"
  TMP_DIR=$(mktemp -d)
  idx=0
  echo "$FILES_JSON" | jq -r \
    '.values[] | select(.kind=="Transcription") | .links.contentUrl' |
  while read -r url; do
    curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" \
         "$url" -o "$TMP_DIR/seg_${idx}.json"
    idx=$((idx+1))
  done

  # ---- 9 Merge ---------------------------------------------------------------
  step "Merge → $TMP_JSON"
  jq -s '[ .[] .recognizedPhrases[] ]' "$TMP_DIR"/seg_*.json > "$TMP_JSON"
}

run_whisperx_and_convert_to_tmp_json() {
  # ---- whisperx STT --------------------------------------------------------
  step "WhisperX STT (word-level aligned)"
  python -c "import whisperx" >/dev/null 2>&1 || { echo "Python package 'whisperx' が見つかりません (pip install -r requirements.txt)" >&2; exit 1; }

  # locale -> whisper language
  WHISPER_LANG="auto"
  [[ "$LOCALE" == "en-US" ]] && WHISPER_LANG="en"
  [[ "$LOCALE" == "ja-JP" ]] && WHISPER_LANG="ja"

  LOG_DIR="${OUTDIR_ABS}/logs"
  mkdir -p "$LOG_DIR"
  WHISPERX_LOG="${LOG_DIR}/${BASE}_whisperx.log"
  rm -f "$WHISPERX_LOG"

  WHISPERX_OUT_DIR="${OUTDIR_ABS}/whisperx"
  mkdir -p "$WHISPERX_OUT_DIR"

  # whisperx writes <basename>.json into output_dir with --output_format json
  WHISPERX_BASE="${BASE}_mono"
  WHISPERX_JSON="${WHISPERX_OUT_DIR}/${WHISPERX_BASE}.json"

  # Note: whisperx does NOT support MPS/Metal for faster-whisper backend on macOS.
  # Keep device/compute_type configurable via env vars.
  WHISPERX_ARGS=(
    -m whisperx "$MONO"
    --model "$WHISPERX_MODEL"
    --language "$WHISPER_LANG"
    --device "$WHISPERX_DEVICE"
    --compute_type "$WHISPERX_COMPUTE_TYPE"
    --vad_method "$WHISPERX_VAD_METHOD"
    --output_dir "$WHISPERX_OUT_DIR"
    --output_format json
    --print_progress True
  )
  if [[ $DEBUG == true ]]; then
    python "${WHISPERX_ARGS[@]}" 2>&1 | tee "$WHISPERX_LOG"
  else
    run_with_progress "$WHISPERX_LOG" python "${WHISPERX_ARGS[@]}"
  fi

  [[ -f "$WHISPERX_JSON" ]] || { echo "whisperx output JSON not found: $WHISPERX_JSON" >&2; exit 5; }

  step "Convert whisperx JSON → Azure-like JSON ($TMP_JSON)"
  python -m utils.whisperx_json_to_azure_json "$WHISPERX_JSON" "$TMP_JSON"
}

generate_srt_from_tmp_json() {
  # ---- 10 SRT ------------------------------------------------------------------
  step "Generate SRT (sentence-based via JSON parser)"
  SPKS=$(jq -r '.[].speaker // empty' "$TMP_JSON" | sort -nu)
  LAST_OUT=""
  for sp in $SPKS; do
    OUT="${OUTDIR_ABS}/Speaker${sp}_${LOCALE}.srt"
    LAST_OUT="$OUT"

    # spaCy ベースの文単位セグメンテーションを行う JSON パーサをモジュール実行で呼び出す
    # pyenv 環境を前提に、python は python3.x 系を指していることを想定
    python -m utils.json_to_srt_sentences "$TMP_JSON" "$sp" >"$OUT"
  done
}

cleanup_and_report() {
  # ---- 11 Cleanup & Report ----------------------------------------------------
  step "Cleanup"
  if [[ -n ${TMP_DIR:-} ]]; then
    rm -rf "$TMP_DIR"      # tmp_script.json は残す
  fi
  ELAPSED=$(( $(date +%s) - start_ts ))
  printf '\e[32m✔ DONE  (%ds)  Last SRT: %s\e[0m\n' "$ELAPSED" "${LAST_OUT:-<none>}" >&2
}

# ---- 引数処理 -----------------------------------------------------------------
usage(){
  echo "Usage: $0 [--engine azure|whisperx] [-o OUTDIR] [-n NUM] [-m MIN] [-M MAX] <audio.(wav|mp4|m4a|flac|aac)> [en-US|ja-JP]" >&2
  echo "       $0 --from-json <tmp_script.json> [-o OUTDIR] [en-US|ja-JP]" >&2
  exit 1
}
OUTDIR=""   # 明示指定がなければ音声ファイルと同じディレクトリに出力
MIN_SPK=""; MAX_SPK=""; BOTH_SPK=""; FROM_JSON=""
# デフォルトの STT エンジンは whisperx
ENGINE="whisperx"   # azure | whisperx

# whisperx defaults (CPU on macOS; MPS/Metal is not supported by faster-whisper/ctranslate2)
WHISPERX_MODEL="${WHISPERX_MODEL:-large-v3-turbo}"
WHISPERX_VAD_METHOD="${WHISPERX_VAD_METHOD:-silero}"
# CPU-friendly default; tweak if you have CUDA.
WHISPERX_DEVICE="${WHISPERX_DEVICE:-cpu}"
# Default compute type:
# - Prefer float16 when possible (e.g. CUDA)
# - But on CPU (macOS default), float16 is often unsupported/inefficient in ctranslate2.
if [[ -n ${WHISPERX_COMPUTE_TYPE:-} ]]; then
  WHISPERX_COMPUTE_TYPE="$WHISPERX_COMPUTE_TYPE"
else
  if [[ "$WHISPERX_DEVICE" == "cpu" ]]; then
    WHISPERX_COMPUTE_TYPE="int8"
  else
    WHISPERX_COMPUTE_TYPE="float16"
  fi
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine) ENGINE="$2"; shift 2 ;;
    --from-json) FROM_JSON="$2"; shift 2 ;;
    -o|--outdir) OUTDIR="$2"; shift 2 ;;
    -n) BOTH_SPK="$2"; shift 2 ;;
    -m) MIN_SPK="$2";  shift 2 ;;
    -M) MAX_SPK="$2";  shift 2 ;;
    --) shift; break ;;
    -h|--help) usage ;;
    -*) usage ;;
    *)  break ;;
  esac
done
if [[ -n $FROM_JSON ]]; then
  # JSON からの再生成モード: AUDIO は不要
  [[ $# -ge 0 && $# -le 1 ]] || usage
  LOCALE="${1:-en-US}"
else
  [[ $# -ge 1 && $# -le 2 ]] || usage
  AUDIO="$1"; LOCALE="${2:-en-US}"
fi
[[ "$LOCALE" =~ ^(en-US|ja-JP)$ ]] || { echo "locale は en-US/ja-JP で指定" >&2; exit 1; }
[[ "$ENGINE" =~ ^(azure|whisperx)$ ]] || { echo "engine は azure|whisperx で指定" >&2; exit 1; }
if [[ -n $BOTH_SPK ]]; then MIN_SPK=$BOTH_SPK; MAX_SPK=$BOTH_SPK; fi
: "${MIN_SPK:=1}"; : "${MAX_SPK:=1}"

if [[ -n $FROM_JSON ]]; then
  TMP_JSON="$FROM_JSON"
  # OUTDIR が未指定なら JSON と同じディレクトリに出す
  if [[ -n $OUTDIR ]]; then
    mkdir -p "$OUTDIR"
    OUTDIR_ABS="$OUTDIR"
  else
    OUTDIR_ABS="$(dirname "$TMP_JSON")"
  fi
else
  BASE=$(basename "$AUDIO" | sed -E 's/\.(wav|mp4|m4a|flac|aac)$//')
  DIR=$(dirname "$AUDIO")
  if [[ -n $OUTDIR ]]; then
    mkdir -p "$OUTDIR"
    OUTDIR_ABS="$OUTDIR"
  else
    OUTDIR_ABS="$DIR"
  fi
  MONO="${DIR}/${BASE}_mono.wav"
  # STT のマージ結果（内部フォーマット）をクリップごとの出力ディレクトリに永続化
  TMP_JSON="${OUTDIR_ABS}/azure-stt.json"
fi
start_ts=$(date +%s)

if [[ -z $FROM_JSON ]]; then
  # ---- 共通: FFmpeg ------------------------------------------------------------
  ffmpeg_mono_convert

  # ---- 分岐: TMP_JSON 生成 ------------------------------------------------------
  if [[ "$ENGINE" == "azure" ]]; then
    run_azure_stt_and_merge_to_tmp_json
  else
    run_whisperx_and_convert_to_tmp_json
  fi
fi

# ---- 共通: SRT生成 → cleanup ---------------------------------------------------
generate_srt_from_tmp_json
cleanup_and_report

