#!/usr/bin/env bash
###############################################################################
# generate_srt.sh  (diagnostic-based minimal edition 2025-05-28 rev-2-fix)
#
# 追加／変更点
#   • --skip-authorization-header を az rest 全呼び出しに付与   → ①警告解消
#   • STEP 2 でコンテナ内ファイルを一括削除                     → ②
#   • すべての資格情報を環境変数で参照（az login 不要）        → ③
#   • trap を関数 err_trap() に分離して syntax error を回避
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

# ---- 引数処理 ---------------------------------------------------------------
usage(){ cat <<USG >&2
Usage: $0 [-m MIN -M MAX | -n N] <audio.wav|audio.mp4> [en-US|ja-JP]
USG
  exit 1; }
MIN_SPK=""; MAX_SPK=""; BOTH_SPK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) BOTH_SPK="$2"; shift 2 ;;
    -m) MIN_SPK="$2";  shift 2 ;;
    -M) MAX_SPK="$2";  shift 2 ;;
    --) shift; break ;;
    -*) usage ;;
    *)  break ;;
  esac
done
[[ $# -ge 1 && $# -le 2 ]] || usage
AUDIO="$1"; LOCALE="${2:-en-US}"
[[ "$LOCALE" =~ ^(en-US|ja-JP)$ ]] || { echo "locale は en-US/ja-JP で指定" >&2; exit 1; }
if [[ -n $BOTH_SPK ]]; then MIN_SPK=$BOTH_SPK; MAX_SPK=$BOTH_SPK; fi
: "${MIN_SPK:=1}"; : "${MAX_SPK:=1}"

BASE=$(basename "$AUDIO" | sed -E 's/\.(wav|mp4|m4a|flac|aac)$//')
DIR=$(dirname "$AUDIO")
MONO="${DIR}/${BASE}_mono.wav"
TMP_JSON="tmp_script.json"
start_ts=$(date +%s)

# ---- 必須環境変数 -----------------------------------------------------------
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
while :; do
  STATUS=$(az rest --skip-authorization-header --method get --uri "$STATUS_URL" \
            --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
            --query status -o tsv)
  echo -n "." >&2
  [[ $STATUS == Succeeded ]] && { echo " ok" >&2; break; }
  [[ $STATUS == Failed    ]] && { echo " failed" >&2; exit 3; }
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

# ---- 10 SRT -----------------------------------------------------------------
step "Generate SRT"
SPKS=$(jq -r '.[].speaker // empty' "$TMP_JSON" | sort -nu)
LAST_OUT=""
for sp in $SPKS; do
  OUT="${DIR}/${BASE}_Speaker${sp}_${LOCALE}.srt"
  LAST_OUT="$OUT"
  jq -r --arg sp "$sp" '
    def tosec: capture("PT((?<h>[0-9.]+)H)?((?<m>[0-9.]+)M)?((?<s>[0-9.]+)S)?")
      | ((.h//"0"|tonumber)*3600)+((.m//"0"|tonumber)*60)+(.s//"0"|tonumber);
    [ .[] | select(.speaker == ($sp|tonumber))
      | (.offset|tosec) as $st | (.duration|tosec) as $du
      | {start:$st,end:($st+$du),text:.nBest[0].display} ]
      | sort_by(.start)[] | "\(.start)\t\(.end)\t\(.text)"
  ' "$TMP_JSON" | awk -F'\t' '
      function ts(t){h=int(t/3600);m=int((t-h*3600)/60);
        s=int(t-h*3600-m*60);ms=int((t-int(t))*1000);
        return sprintf("%02d:%02d:%02d,%03d",h,m,s,ms)}
      {printf("%d\n%s --> %s\n%s\n\n", NR, ts($1), ts($2), $3)}
  ' > "$OUT"
done

# ---- 11 Cleanup & Report ----------------------------------------------------
step "Cleanup"
rm -rf "$TMP_DIR"      # tmp_script.json は残す
ELAPSED=$(( $(date +%s) - start_ts ))
printf '\e[32m✔ DONE  (%ds)  Last SRT: %s\e[0m\n' "$ELAPSED" "${LAST_OUT:-<none>}" >&2

