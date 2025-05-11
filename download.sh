#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 3 ]; then
  echo "Usage: $0 <YouTube URL> [output_dir] [filename_without_ext]"
  exit 1
fi

URL="$1"
OUTPUT_DIR="${2:-.}"
USER_BASENAME="${3:-}"
mkdir -p "$OUTPUT_DIR"

# Download + Merge video/audio + subs
download_video() {
  local video_url="$1"
  local output_path="$2"
  yt-dlp \
    -f 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4' \
    --merge-output-format mp4 \
    --write-subs \
    --write-auto-sub \
    --sub-lang en,ja \
    --convert-subs srt \
    --output "$output_path" \
    "$video_url"
}

# Resolve safe file basename (from yt-dlp title or user input)
get_safe_basename() {
  local url="$1"
  local label="$2"
  local raw_title=$(yt-dlp --get-title "$url")
  local safe_title="${raw_title//[^a-zA-Z0-9._-]/_}"
  echo "${safe_title}${label}"
}

if [[ "$URL" =~ youtube\.com/clip/ ]]; then
  echo "Detected YouTube Clip URL. Extracting original video and time range..."

  HTML=$(curl -sL "$URL")

  # Extract real video ID
  VIDEO_ID=$(echo "$HTML" | grep -o '"videoId":"[^"]\{11\}"' | head -n1 | sed 's/.*"videoId":"\([^"]*\)".*/\1/')
  if [ -z "$VIDEO_ID" ]; then
    VIDEO_ID=$(echo "$HTML" | grep -o '<link[^>]*rel="canonical"[^>]*>' | grep -o 'watch?v=[^"&]\{11\}' | head -n1 | cut -d= -f2)
  fi
  if [ -z "$VIDEO_ID" ]; then
    echo "Failed to extract video ID from clip page."
    exit 1
  fi

  echo "Found original video ID: $VIDEO_ID"

  # Extract time info
  CLIP_CONFIG=$(echo "$HTML" | sed -n 's/.*"clipConfig":\s*\({[^}]*}\).*/\1/p')
  START_MS=$(echo "$CLIP_CONFIG" | jq -r '.startTimeMs')
  END_MS=$(echo "$CLIP_CONFIG" | jq -r '.endTimeMs')

  if [[ -z "$START_MS" || -z "$END_MS" ]]; then
    echo "Failed to extract start/end times."
    exit 1
  fi

  START=$(awk "BEGIN { printf \"%.3f\", $START_MS / 1000 }")
  DURATION=$(awk "BEGIN { printf \"%.3f\", ($END_MS - $START_MS) / 1000 }")
  echo "Start: $START s, Duration: $DURATION s"

  # File naming
  if [[ -n "$USER_BASENAME" ]]; then
    BASENAME="$USER_BASENAME"
  else
    BASENAME=$(get_safe_basename "https://youtube.com/watch?v=$VIDEO_ID" "_clip")
  fi

  TEMP_TEMPLATE="${OUTPUT_DIR%/}/__ytclip_temp.%(ext)s"
  FINAL_FILE="${OUTPUT_DIR%/}/${BASENAME}.mp4"

  download_video "https://youtube.com/watch?v=$VIDEO_ID" "$TEMP_TEMPLATE"
  TEMP_FILE=$(ls "${TEMP_TEMPLATE//%(ext)s/mp4}")

  ffmpeg -i "$TEMP_FILE" -ss "$START" -t "$DURATION" -c copy "$FINAL_FILE"
  rm -f "$TEMP_FILE"

  echo "Clip extracted to: $FINAL_FILE"
  # ---- 字幕トリミング処理 ----
  for lang in en ja; do
    SRC="${OUTPUT_DIR%/}/__ytclip_temp.${lang}.srt"
    DEST="${OUTPUT_DIR%/}/${VIDEO_ID}_clip.${lang}.srt"
    if [[ -f "$SRC" ]]; then
      echo "Adjusting SRT ($lang)..."
      python3 "$(dirname "$0")/utils/adjust_srt_timestamp.py" \
        "$SRC" "$DEST" "$START" "$(awk "BEGIN { print $START + $DURATION }")"
      rm "$SRC"
    else
      echo "No SRT found for $lang"
    fi
  done


else
  # Regular video
  if [[ -n "$USER_BASENAME" ]]; then
    BASENAME="$USER_BASENAME"
  else
    BASENAME=$(get_safe_basename "$URL" "")
  fi
  OUTPUT_TEMPLATE="${OUTPUT_DIR%/}/${BASENAME}.%(ext)s"
  download_video "$URL" "$OUTPUT_TEMPLATE"
  echo "Full video downloaded."
fi

