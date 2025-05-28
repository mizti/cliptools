#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# generate_srt.sh
#  - v3.3  2025-05-28
#    • tmp.json → tmp_script.json
#    • 経過時間と最終出力ファイル名を表示
#    • DEBUG=true なら詳細ログ＋一時ファイル保持
#      DEBUG!=true なら “...” だけ
#    • -n で min/max を同値一括指定（未指定時は 1/1）
###############################################################################

START_TIME=$(date +%s)
DEBUG=${DEBUG:-false}

usage() {
  cat <<USG >&2
Usage: $0 [-m MIN -M MAX | -n N] <audio.wav> [en-US|ja-JP]
  -m N   話者数の最小値 (default 1)
  -M N   話者数の最大値 (default 1)
  -n N   最小・最大とも N として指定（-m/-M より前に書くと上書きされる）
USG
  exit 1
}

# --- option parse ------------------------------------------------------------
MIN_SPK=""; MAX_SPK=""; BOTH_SPK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) BOTH_SPK="$2";       shift 2 ;;
    -m) MIN_SPK="$2";        shift 2 ;;
    -M) MAX_SPK="$2";        shift 2 ;;
    --) shift; break ;;
    -*) usage ;;
    *)  break ;;
  esac
done

[[ $# -ge 1 && $# -le 2 ]] || usage
AUDIO_FILE="$1"
LOCALE="${2:-en-US}"
[[ "$LOCALE" =~ ^(en-US|ja-JP)$ ]] || { echo "locale は en-US/ja-JP で指定してください" >&2; exit 1; }

# -n があれば min/max を両方とも上書き
if [[ -n $BOTH_SPK ]]; then
  MIN_SPK=$BOTH_SPK
  MAX_SPK=$BOTH_SPK
fi
# デフォルト
: "${MIN_SPK:=1}"
: "${MAX_SPK:=1}"

BASENAME=$(basename "$AUDIO_FILE" .wav)
DIRNAME=$(dirname  "$AUDIO_FILE")

# --- helper ------------------------------------------------------------------
dlog () { $DEBUG && echo "DEBUG: $*" >&2; }

# --- .env --------------------------------------------------------------------
[[ -f .env ]] || { echo ".env ファイルが見つかりません" >&2; exit 1; }
# shellcheck disable=SC1091
source .env

# --- 4) wav → mono -----------------------------------------------------------
dlog "converting to mono …"
MONO_FILE="${DIRNAME}/${BASENAME}_mono.wav"
ffmpeg -y -i "$AUDIO_FILE" -ac 1 "$MONO_FILE" ${DEBUG:+ 2>/dev/null}

# --- 5) upload ---------------------------------------------------------------
dlog "uploading to Blob …"
az storage blob upload --only-show-errors --auth-mode login \
  --account-name "$STORAGE_ACCOUNT_NAME" --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" --file "$MONO_FILE" --overwrite true

# --- 6) SAS ------------------------------------------------------------------
dlog "generating SAS …"
KEY=$(az storage account keys list -g "$RESOURCE_GROUP_NAME" \
     -n "$STORAGE_ACCOUNT_NAME" --query '[0].value' -o tsv)
EXP=$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%MZ' 2>/dev/null \
      || date -u -v+1H '+%Y-%m-%dT%H:%MZ')
FILE_SAS=$(az storage blob generate-sas -o tsv \
  --account-name "$STORAGE_ACCOUNT_NAME" --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" --permissions r --https-only \
  --expiry "$EXP" --account-key "$KEY")
FILE_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/${BASENAME}.wav?${FILE_SAS}"
CONT_SAS=$(az storage container generate-sas -o tsv \
  --account-name "$STORAGE_ACCOUNT_NAME" --name "$CONTAINER_NAME" \
  --permissions rl --https-only --expiry "$EXP" --account-key "$KEY")
CONT_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/?${CONT_SAS}"

dlog "FILE_URL = $FILE_URL"
dlog "CONT_URL = $CONT_URL"

# --- 7) transcription job ----------------------------------------------------
ENDP="https://${SPEECH_REGION}.api.cognitive.microsoft.com"
API="speechtotext/v3.2/transcriptions"
BODY=$(mktemp)
cat >"$BODY" <<JSON
{
  "displayName":"$BASENAME",
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

REST_OPTS=(--method post --uri "$ENDP/$API" \
  --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
  --body @"$BODY")
$DEBUG || REST_OPTS+=(--only-show-errors)
$DEBUG && REST_OPTS+=(--debug)

dlog "creating transcription job …"
CREATE=$(az rest "${REST_OPTS[@]}")
rm -f "$BODY"
dlog "CREATE response:"
dlog "$(echo "$CREATE" | jq .)"

# --- 8) polling --------------------------------------------------------------
JOB_URL=$(echo "$CREATE" | jq -r .self)
STATUS_URL="${JOB_URL}?api-version=3.2-preview.2"

$DEBUG && echo -n "Processing" >&2
while :; do
  STATUS=$(az rest --only-show-errors --method get \
    --uri "$STATUS_URL" \
    --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
    --query status -o tsv)
  echo -n "." >&2
  [[ $STATUS == Succeeded ]] && { $DEBUG && echo " done" >&2; break; }
  if [[ $STATUS == Failed ]]; then
    $DEBUG && { echo " failed" >&2; \
      az rest --method get --uri "$STATUS_URL" \
        --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
        | jq .properties.error >&2; }
    exit 3
  fi
  sleep 5
done
$DEBUG || echo >&2   # 改行を入れておく

# --- 9) segment list ---------------------------------------------------------
FILES_URL="${JOB_URL}/files?api-version=3.2-preview.2"
dlog "FILES_URL = $FILES_URL"
FILES_JSON=$(az rest --only-show-errors --method get \
  --uri "$FILES_URL" \
  --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY")
dlog "$(echo "$FILES_JSON" | jq .)"

# --- 10) download segments ---------------------------------------------------
TMP_DIR=$(mktemp -d)
idx=0
echo "$FILES_JSON" \
  | jq -r '.values[] | select(.kind=="Transcription") | .links.contentUrl' \
  | while IFS= read -r url; do
      dlog "downloading segment[$idx]: $url"
      curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" \
        "$url" -o "$TMP_DIR/seg_${idx}.json"
      idx=$((idx+1))
    done

# --- 11) merge ---------------------------------------------------------------
TMP_JSON="tmp_script.json"
dlog "merging segments into $TMP_JSON"
jq -s '[ .[] .recognizedPhrases[] ]' "$TMP_DIR"/seg_*.json > "$TMP_JSON"

# --- 12) generate SRT --------------------------------------------------------
SPKS=$(jq -r '.[].speaker // empty' "$TMP_JSON" | sort -nu)
LAST_OUT=""
for sp in $SPKS; do
  OUTFILE="${DIRNAME}/${BASENAME}_Speaker${sp}_${LOCALE}.srt"
  LAST_OUT="$OUTFILE"
  dlog "generating SRT for speaker $sp -> $OUTFILE"

  jq -r --arg sp "$sp" '
    def tosec:
      capture("PT((?<h>[0-9.]+)H)?((?<m>[0-9.]+)M)?((?<s>[0-9.]+)S)?")
      | ((.h//"0"|tonumber)*3600)
      + ((.m//"0"|tonumber)*60)
      + (.s//"0"|tonumber);

    [ .[]
      | select(.speaker == ($sp|tonumber))
      | (.offset|tosec)   as $start
      | (.duration|tosec) as $dur
      | {start:$start, end:($start+$dur), text:.nBest[0].display}
    ]
    | sort_by(.start)
    | .[]
    | "\(.start)\t\(.end)\t\(.text)"
  ' "$TMP_JSON" |
  awk -F'\t' '
      function ts(t){h=int(t/3600);m=int((t-h*3600)/60);
        s=int(t-h*3600-m*60);ms=int((t-int(t))*1000);
        return sprintf("%02d:%02d:%02d,%03d",h,m,s,ms)}
      {printf("%d\n%s --> %s\n%s\n\n", NR, ts($1), ts($2), $3)}
  ' > "$OUTFILE"

  $DEBUG && echo "Generated: $OUTFILE" >&2
done

# --- 13) cleanup & report ----------------------------------------------------
$DEBUG || rm -f "$TMP_JSON"

ELAPSED=$(( $(date +%s) - START_TIME ))
echo "Elapsed time: ${ELAPSED}s"
echo "Output file : ${LAST_OUT:-<none>}"

