#!/usr/bin/env bash
set -euo pipefail
DEBUG=${DEBUG:-false}

# -----------------------------------------------------------------------------
# generate_srt.sh
#   - v3.2 で常に話者分離 → 話者別 .srt
#     (-m/-M 未指定時は minCount=1,maxCount=1)
#
# Usage: DEBUG=true ./generate_srt.sh [-m MIN -M MAX] <audio.wav> [en-US|ja-JP]
# -----------------------------------------------------------------------------

usage(){
  cat <<USG >&2
Usage: $0 [-m MIN -M MAX] <audio.wav> [en-US|ja-JP]
  -m N   最小話者数 (diarization, default 1)
  -M N   最大話者数 (diarization, default 1)
USG
  exit 1
}

# 1) オプション解析
MIN_SPK=""; MAX_SPK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -m) MIN_SPK="$2"; shift 2 ;;
    -M) MAX_SPK="$2"; shift 2 ;;
    --) shift; break ;;
    -*) usage ;;
    *) break ;;
  esac
done

# 2) 引数チェック
[[ $# -ge 1 && $# -le 2 ]] || usage
AUDIO_FILE="$1"
LOCALE="${2:-en-US}"
[[ "$LOCALE" =~ ^(en-US|ja-JP)$ ]] || { echo "locale は en-US/ja-JP で指定してください" >&2; exit 1; }

# -m/-M のデフォルトを 1 に
if [[ -z $MIN_SPK ]]; then MIN_SPK=1; fi
if [[ -z $MAX_SPK ]]; then MAX_SPK=1; fi

BASENAME=$(basename "$AUDIO_FILE" .wav)
DIRNAME=$(dirname "$AUDIO_FILE")

# 3) .env 読み込み
[[ -f .env ]] || { echo ".env ファイルが見つかりません" >&2; exit 1; }
source .env

# 4) モノラル変換 (話者分離も通常も共通)
echo "DEBUG: converting to mono…" >&2
MONO_FILE="${DIRNAME}/${BASENAME}_mono.wav"
ffmpeg -y -i "$AUDIO_FILE" -ac 1 "$MONO_FILE"

# 5) Blob アップロード
echo "DEBUG: uploading to Blob…" >&2
az storage blob upload --only-show-errors --auth-mode login \
  --account-name "$STORAGE_ACCOUNT_NAME" --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" --file "$MONO_FILE" --overwrite true

# 6) SAS 発行
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

if $DEBUG; then
  echo "DEBUG: FILE_URL = $FILE_URL" >&2
  echo "DEBUG: CONT_URL  = $CONT_URL" >&2
fi

# 7) Transcription ジョブ作成 (常に diarization mode)
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

# REST 呼び出しオプション
REST_OPTS=(--method post --uri "$ENDP/$API" \
  --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
  --body @"$BODY")
if $DEBUG; then
  REST_OPTS=(--debug "${REST_OPTS[@]}")
else
  REST_OPTS=(--only-show-errors "${REST_OPTS[@]}")
fi

echo "DEBUG: creating transcription job…" >&2
CREATE=$(az rest "${REST_OPTS[@]}")
rm "$BODY"

echo "DEBUG: CREATE response" >&2
echo "$CREATE" | jq . >&2

# 8) ステータス監視
JOB_URL=$(echo "$CREATE" | jq -r .self)
STATUS_URL="${JOB_URL}?api-version=3.2-preview.2"

echo -n "Processing" >&2
while :; do
  STATUS=$(az rest --only-show-errors --method get \
    --uri "$STATUS_URL" \
    --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
    --query status -o tsv)
  echo -n "." >&2
  if [[ $STATUS == Succeeded ]]; then
    echo " done" >&2
    break
  fi
  if [[ $STATUS == Failed ]]; then
    echo " failed" >&2
    az rest --method get --uri "$STATUS_URL" \
      --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
      | jq .properties.error >&2
    exit 3
  fi
  sleep 5
done

# 9) セグメント一覧取得
FILES_URL="${JOB_URL}/files?api-version=3.2-preview.2"
echo "DEBUG: FILES_URL = $FILES_URL" >&2
FILES_JSON=$(az rest --only-show-errors --method get \
  --uri "$FILES_URL" \
  --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY")
echo "$FILES_JSON" | jq . >&2

# 10) 各セグメント JSON ダウンロード
SEG_URLS=$(echo "$FILES_JSON" \
  | jq -r '.values[] | select(.kind=="Transcription") | .links.contentUrl')

TMP_DIR=$(mktemp -d)
idx=0
while IFS= read -r url; do
  echo "DEBUG: downloading segment[$idx]: $url" >&2
  curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" \
    "$url" -o "$TMP_DIR/seg_${idx}.json"
  idx=$((idx+1))
done <<<"$SEG_URLS"

# 11) tmp.json マージ
echo "DEBUG: merging segments into tmp.json" >&2
jq -s '[ .[] .recognizedPhrases[] ]' "$TMP_DIR"/seg_*.json > tmp.json

# 12) 話者ごとに SRT 出力
SPKS=$(jq -r '.[].speaker // empty' tmp.json | sort -nu)
for sp in $SPKS; do
  OUTFILE="${DIRNAME}/${BASENAME}_Speaker${sp}_${LOCALE}.srt"
  echo "DEBUG: generating SRT for speaker $sp -> $OUTFILE" >&2

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
  ' tmp.json \
  | awk -F'\t' '
      function ts(t){
        h=int(t/3600); m=int((t-h*3600)/60);
        s=int(t-h*3600-m*60); ms=int((t-int(t))*1000);
        return sprintf("%02d:%02d:%02d,%03d",h,m,s,ms)
      }
      {
        printf("%d\n%s --> %s\n%s\n\n", NR, ts($1), ts($2), $3)
      }
  ' > "$OUTFILE"

  echo "Generated: $OUTFILE"
done

rm tmp.json
rm create.json
