#!/usr/bin/env bash
set -euo pipefail
DEBUG=${DEBUG:-false}

# -----------------------------------------------------------------------------
# generate_srt.sh
#   - 通常: REST 2024-11-15 で .wav → .srt
#   - 話者分離: v3.2-preview.2 で .wav → モノラル変換 → 複数 JSON セグメント取得 → 話者ごとに .srt
#
# Usage: DEBUG=true ./generate_srt.sh [-m MIN -M MAX] <audio.wav> [en-US|ja-JP]
# -----------------------------------------------------------------------------

usage(){
  cat <<USG >&2
Usage: $0 [-m MIN -M MAX] <audio.wav> [en-US|ja-JP]
  -m N   最小話者数 (diarization)
  -M N   最大話者数 (diarization)
  (両方指定しない場合は通常モード)
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
[[ "$LOCALE" =~ ^(en-US|ja-JP)$ ]] || { echo "locale は en-US/ja-JP" >&2; exit 1; }
DIAR=false
if [[ -n $MIN_SPK || -n $MAX_SPK ]]; then
  [[ -n $MIN_SPK && -n $MAX_SPK ]] || { echo "-m と -M を両方指定してください" >&2; exit 1; }
  DIAR=true
fi

BASENAME=$(basename "$AUDIO_FILE" .wav)
DIRNAME=$(dirname "$AUDIO_FILE")

# 3) .env 読み込み
[[ -f .env ]] || { echo ".env がありません" >&2; exit 1; }
source .env

# 4) Blob アップロード (ステレオ→モノラル変換 for diarization)
EXP=$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%MZ' 2>/dev/null || date -u -v+1H '+%Y-%m-%dT%H:%MZ')
if $DIAR; then
  echo "DEBUG: converting stereo → mono for diarization…"
  MONO_FILE="${BASENAME}_mono.wav"
  ffmpeg -y -i "$AUDIO_FILE" -ac 1 "$MONO_FILE"
  UPLOAD_SRC="$MONO_FILE"
else
  UPLOAD_SRC="$AUDIO_FILE"
fi

az storage blob upload --only-show-errors --auth-mode login \
  --account-name "$STORAGE_ACCOUNT_NAME" --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" --file "$UPLOAD_SRC" --overwrite true

# 5) SAS 発行
KEY=$(az storage account keys list -g "$RESOURCE_GROUP_NAME" \
     -n "$STORAGE_ACCOUNT_NAME" --query '[0].value' -o tsv)

# 通常モード用ファイル SAS
FILE_SAS=$(az storage blob generate-sas -o tsv \
  --account-name "$STORAGE_ACCOUNT_NAME" --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" --permissions r --https-only \
  --expiry "$EXP" --account-key "$KEY")
FILE_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/${BASENAME}.wav?${FILE_SAS}"

# 話者分離モード用コンテナ SAS（末尾スラッシュ付き）
CONT_SAS=$(az storage container generate-sas -o tsv \
  --account-name "$STORAGE_ACCOUNT_NAME" --name "$CONTAINER_NAME" \
  --permissions rl --https-only --expiry "$EXP" --account-key "$KEY")
CONT_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/?${CONT_SAS}"

if $DEBUG; then
  echo "DEBUG: FILE_URL = $FILE_URL"
  echo "DEBUG: CONT_URL = $CONT_URL"
fi

ENDP="https://${SPEECH_REGION}.api.cognitive.microsoft.com"

# 6) ジョブ作成
if $DIAR; then
  API="speechtotext/v3.2-preview.2/transcriptions"
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
    "channels":[0]$([[ $MIN_SPK -gt 2 || $MAX_SPK -gt 2 ]] && \
      printf ',\n    "diarization":{"speakers":{"minCount":%s,"maxCount":%s}}' \
             "$MIN_SPK" "$MAX_SPK")
  }
}
JSON

  CREATE=$(az rest $([ "$DEBUG" = true ]&&echo --debug) --only-show-errors \
    --method post --uri "$ENDP/$API" \
    --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
    --body @"$BODY")
  rm "$BODY"
else
  API="speechtotext/transcriptions:submit?api-version=2024-11-15"
  BODY=$(mktemp)
  cat >"$BODY" <<JSON
{
  "displayName":"$BASENAME",
  "locale":"$LOCALE",
  "contentUrls":["$FILE_URL"],
  "properties":{"captionFormats":["Srt"],"timeToLiveHours":48}
}
JSON

  CREATE=$(az rest $([ "$DEBUG" = true ]&&echo --debug) --only-show-errors \
    --method post --uri "$ENDP/$API" \
    --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
    --body @"$BODY")
  rm "$BODY"
fi

echo "DEBUG: CREATE response"
echo "$CREATE" | jq .

# 7) URL 組み立て
JOB_URL=$(echo "$CREATE" | jq -r .self)
if $DIAR; then
  STATUS_URL="$JOB_URL?api-version=3.2-preview.2"
else
  STATUS_URL="$JOB_URL"
fi

echo "DEBUG: JOB_URL    = $JOB_URL"
echo "DEBUG: STATUS_URL = $STATUS_URL"

# 8) ステータス監視
printf 'Processing'
while true; do
  STATUS=$(az rest --only-show-errors --method get --uri "$STATUS_URL" \
    --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
    --query status -o tsv)
  echo " DEBUG: STATUS = $STATUS"
  if [[ $STATUS == Succeeded ]]; then printf ' done\n'; break; fi
  if [[ $STATUS == Failed ]]; then
    printf ' failed\nError details:\n'
    curl -s -H "Ocp-Apim-Subscription-Key=$SPEECH_KEY" "$STATUS_URL" \
      | jq '.properties.error // .properties.errors'
    exit 3
  fi
  printf '.'; sleep 5
done

# 9) 結果取得
FILES_URL=$(echo "$CREATE" | jq -r .links.files)
FILES_URL="${FILES_URL}?api-version=3.2-preview.2"
if $DEBUG; then echo "DEBUG: FETCHING FILES_URL → $FILES_URL"; fi

FILES_JSON=$(az rest --only-show-errors --method get --uri "$FILES_URL" \
  --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY")
if $DEBUG; then echo "DEBUG: FILES_JSON →"; echo "$FILES_JSON" | jq .; fi

# 10-A) 通常モード: direct SRT
if ! $DIAR; then
  SRT_URL=$(echo "$FILES_JSON" | jq -r '.values[]|select(.name|endswith(".srt"))|.links.contentUrl')
  out="${DIRNAME}/gen_${LOCALE}.srt"
  curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" "$SRT_URL" -o "$out"
  echo "SRT saved: $out"
  exit 0
fi

# 10-B) JSON→SRT (複数セグメント統合)
# POSIX互換: Bash3.x では mapfile が使えないため while-readで配列化
JSON_URLS=()
while IFS= read -r url; do
  JSON_URLS+=( "$url" )
done < <(
  echo "$FILES_JSON" \
    | jq -r '.values
      | map(select(.kind=="Transcription"))
      | .[].links.contentUrl'
)
TMP_DIR=$(mktemp -d)
for idx in "${!JSON_URLS[@]}"; do
  url=${JSON_URLS[idx]}
  curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" \
    "$url" -o "$TMP_DIR/seg_${idx}.json"
done

# recognizedPhrases 配列をまとめる
jq -s '[.[]|.recognizedPhrases[]]' "$TMP_DIR"/seg_*.json > tmp.json
if $DEBUG; then echo "DEBUG: tmp.json preview →"; head -n 20 tmp.json; fi

# SRT 生成準備
read -r -d '' toSec <<'EOF'
def tosec:
  capture("PT((?<h>[0-9.]+)H)?((?<m>[0-9.]+)M)?((?<s>[0-9.]+)S)?") as $m
  | (($m.h//0|tonumber)*3600)+(($m.m//0|tonumber)*60)+($m.s//0|tonumber);
EOF

speakers=( $(jq -r '.[]?.speaker // empty' tmp.json | sort -nu) )
if $DEBUG; then echo "DEBUG: speakers → ${speakers[*]:-<none>}"; fi

for sp in "${speakers[@]}"; do
  out="${DIRNAME}/gen_Speaker${sp}_${LOCALE}.srt"
  jq -r "$toSec
    [ .[]
      | select(.speaker==$sp)
      | (.offset|tosec) as \\$s
      | (.duration|tosec) as \\$d
      | {start":\\$s,end:(\\$s+\\$d),text:.nBest[0].display} ]
    | sort_by(.start)
    | .[]
    | [.start,.end,.text]|@tsv
  " tmp.json \
  | awk -F'\t' '
    function ts(t){h=int(t/3600);m=int((t-h*3600)/60);s=t-h*3600-m*60;ms=int((t-int(t))*1000);
      return sprintf("%02d:%02d:%02d,%03d",h,m,s,ms)}
    {printf "%d\n%s --> %s\n%s\n\n",NR,ts(\$1),ts(\$2),\$3}
  ' > "$out"
  echo "Speaker $sp SRT: $out"
done

rm -rf "$TMP_DIR" tmp.json

