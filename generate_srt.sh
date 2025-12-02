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
err_trap() {
  local ec=$?
  printf '\e[31m✖ STEP %-2d  %s  (exit %d)\e[0m\n' \
          "$step_no" "$CURRENT_STEP" "$ec" >&2
}
trap err_trap ERR

# ---- 引数処理 -----------------------------------------------------------------
usage(){
  echo "Usage: $0 [-o OUTDIR] [-n NUM] [-m MIN] [-M MAX] <audio.(wav|mp4)> [en-US|ja-JP]" >&2
  echo "       $0 --from-json <tmp_script.json> [-o OUTDIR] [en-US|ja-JP]" >&2
  exit 1
}
OUTDIR=""   # 明示指定がなければ音声ファイルと同じディレクトリに出力
MIN_SPK=""; MAX_SPK=""; BOTH_SPK=""; FROM_JSON=""
while [[ $# -gt 0 ]]; do
  case "$1" in
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
  # Azure STT のマージ結果をクリップごとの出力ディレクトリに永続化
  TMP_JSON="${OUTDIR_ABS}/azure-stt.json"
fi
start_ts=$(date +%s)

if [[ -z $FROM_JSON ]]; then
  # ---- 必須環境変数 -------------------------------------------------------------
  step "env var check"
  for v in STORAGE_ACCOUNT_NAME STORAGE_ACCOUNT_KEY CONTAINER_NAME \
           SPEECH_REGION SPEECH_KEY; do
    [[ -n ${!v:-} ]] || { echo "環境変数 $v が未設定" >&2; exit 1; }
  done

  # ---- 1 FFmpeg ---------------------------------------------------------------
  step "FFmpeg mono convert"
  if [[ $DEBUG == true ]]; then
    ffmpeg -y -i "$AUDIO" -ac 1 "$MONO"
  else
    ffmpeg -y -loglevel error -i "$AUDIO" -ac 1 "$MONO"
  fi

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
fi

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

# ---- 11 Cleanup & Report ----------------------------------------------------
step "Cleanup"
if [[ -n ${TMP_DIR:-} ]]; then
  rm -rf "$TMP_DIR"      # tmp_script.json は残す
fi
ELAPSED=$(( $(date +%s) - start_ts ))
printf '\e[32m✔ DONE  (%ds)  Last SRT: %s\e[0m\n' "$ELAPSED" "${LAST_OUT:-<none>}" >&2

