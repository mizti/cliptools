#!/usr/bin/env bash
set -euo pipefail
# -----------------------------------------------------------------------------
# generate_srt.sh  –  Azure Speech Fast Transcription (REST 2024-11-15)
#                     .wav から .srt を生成し、SRT が無い場合は JSON → SRT
#
# 事前準備 (.env に下記を定義し、az login 済み)
#   RESOURCE_GROUP_NAME   ストレージアカウントの RG
#   STORAGE_ACCOUNT_NAME  ストレージアカウント名
#   CONTAINER_NAME        WAV を置くコンテナ
#   SPEECH_KEY            Speech サブスクリプションキー
#   SPEECH_REGION         Speech リージョン (例: japaneast)
#   ※ jq・curl がインストール済み
# -----------------------------------------------------------------------------

# ────────────────────────────────────────────────────────────────────────────
# 0) .env 読み込み
[[ -f .env ]] || { echo ".env が見つかりません"; exit 1; }
# shellcheck disable=SC1091
source .env

# 1) 引数チェック
[[ $# -ge 1 ]] || { echo "Usage: $0 <audio.wav> [en-US|ja-JP]"; exit 1; }
AUDIO_FILE="$1"; LOCALE="${2:-en-US}"
[[ $LOCALE =~ ^(en-US|ja-JP)$ ]] || { echo "locale は en-US / ja-JP"; exit 2; }

BASENAME=$(basename "$AUDIO_FILE" .wav)
DIRNAME=$(dirname  "$AUDIO_FILE")
OUTPUT_SRT="${DIRNAME}/gen_${LOCALE}.srt"

# ────────────────────────────────────────────────────────────────────────────
# 2) WAV → Blob (上書き可)

EXPIRY=$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%MZ' 2>/dev/null || date -u -v+1H '+%Y-%m-%dT%H:%MZ')

az storage blob upload --only-show-errors --auth-mode login \
  --account-name "$STORAGE_ACCOUNT_NAME" \
  --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" \
  --file "$AUDIO_FILE" \
  --overwrite true

ACCOUNT_KEY=$(az storage account keys list \
  -g "$RESOURCE_GROUP_NAME" -n "$STORAGE_ACCOUNT_NAME" \
  --query '[0].value' -o tsv)

SAS_TOKEN=$(az storage blob generate-sas \
  --account-name "$STORAGE_ACCOUNT_NAME" \
  --container-name "$CONTAINER_NAME" \
  --name "${BASENAME}.wav" \
  --permissions r \
  --https-only \
  --expiry "$EXPIRY" \
  --account-key "$ACCOUNT_KEY" \
  -o tsv)

FILE_URL="https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/${CONTAINER_NAME}/${BASENAME}.wav?${SAS_TOKEN}"

# ────────────────────────────────────────────────────────────────────────────
# 3) ジョブ作成 (REST 2024-11-15)

API_VER="2024-11-15"
ENDPOINT="https://${SPEECH_REGION}.api.cognitive.microsoft.com"

TMP_JSON=$(mktemp)
cat >"$TMP_JSON" <<JSON
{
  "displayName": "$BASENAME",
  "locale": "$LOCALE",
  "contentUrls": ["$FILE_URL"],
  "properties": {
    "captionFormats": ["Srt"],
    "timeToLiveHours": 48
  }
}
JSON

JOB_URL=$(az rest --only-show-errors --resource "" --method post \
  --uri "$ENDPOINT/speechtotext/transcriptions:submit?api-version=$API_VER" \
  --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
  --body @"$TMP_JSON" | jq -r .self)
rm "$TMP_JSON"

# ────────────────────────────────────────────────────────────────────────────
# 4) ステータス監視（静かなドット表示）

printf 'Transcribing'
while true; do
  STATUS=$(az rest --only-show-errors --resource "" --method get \
           --uri "$JOB_URL" \
           --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
           --query status -o tsv)
  [[ $STATUS == Succeeded ]] && { printf ' done\n'; break; }
  [[ $STATUS == Failed    ]] && { echo ' failed'; exit 3; }
  printf '.'; sleep 5
done

# ────────────────────────────────────────────────────────────────────────────
# 5) ファイル一覧取得

FILES_URL=$(az rest --only-show-errors --resource "" --method get \
            --uri "$JOB_URL" \
            --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY" \
            | jq -r '.links.files')

FILES_JSON=$(az rest --only-show-errors --resource "" --method get \
              --uri "$FILES_URL" \
              --headers "Ocp-Apim-Subscription-Key=$SPEECH_KEY")

SRT_URL=$(echo "$FILES_JSON" | \
          jq -r '.values[] | select(.name|endswith(".srt")) | .links.contentUrl')

# ────────────────────────────────────────────────────────────────────────────
# 6-A) SRT が直接取得できる場合

if [[ -n $SRT_URL ]]; then
  curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" \
       "$SRT_URL" -o "$OUTPUT_SRT"
  echo "SRT 保存完了: $OUTPUT_SRT"
  exit 0
fi

# ────────────────────────────────────────────────────────────────────────────
# 6-B) フォールバック: JSON → SRT 変換

echo "SRT が返されなかったため JSON → SRT を変換します"

JSON_URL=$(echo "$FILES_JSON" | \
           jq -r '.values[] | select(.kind=="Transcription") | .links.contentUrl')

curl -s -H "Ocp-Apim-Subscription-Key:$SPEECH_KEY" \
     "$JSON_URL" -o tmp_transcription.json

jq -r '
  .recognizedPhrases[]
  | (.offset   | gsub("^PT|S$";"") | tonumber) as $start
  | (.duration | gsub("^PT|S$";"") | tonumber) as $dur
  | ($start + $dur) as $end
  | [$start,$end, .nBest[0].display] | @tsv
' tmp_transcription.json |
awk -F'\t' '
function ts(sec){
  h=int(sec/3600); m=int((sec-h*3600)/60); s=sec-h*3600-m*60;
  return sprintf("%02d:%02d:%06.3f",h,m,s) }
{ idx=NR; printf "%d\n%s --> %s\n%s\n\n",idx,ts($1),ts($2),$3 }' \
> "$OUTPUT_SRT"

rm -f tmp_transcription.json
echo "SRT 変換完了: $OUTPUT_SRT"

