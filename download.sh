#!/usr/bin/env bash
set -euo pipefail

# Usage: ./download.sh <YouTube URL> [-s start_time] [-e end_time] [output_dir] [filename_without_ext]

# Parse arguments
START_TIME=""
END_TIME=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case $1 in
    -s|--start)
      START_TIME="$2"
      shift 2
      ;;
    -e|--end)
      END_TIME="$2"
      shift 2
      ;;
    -*|--*)
      echo "Unknown option $1"
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL[@]}"

if [ $# -lt 1 ] || [ $# -gt 3 ]; then
  echo "Usage: $0 <YouTube URL> [-s start_time] [-e end_time] [output_dir] [filename_without_ext]"
  exit 1
fi

URL="$1"
OUTPUT_DIR="${2:-.}"
USER_BASENAME="${3:-}"

mkdir -p "$OUTPUT_DIR"

# Function to download video and subtitles
download_video() {
  local video_url="$1"
  local output_path="$2"
  yt-dlp \
    -f 'bestvideo+bestaudio/best' \
    --merge-output-format mp4 \
    --write-subs \
    --write-auto-sub \
    --sub-lang en,ja \
    --convert-subs srt \
    --output "$output_path" \
    "$video_url"
}

# Function to get safe basename
get_safe_basename() {
  local url="$1"
  local label="$2"
  local raw_title=$(yt-dlp --get-title "$url")
  local safe_title="${raw_title//[^a-zA-Z0-9._-]/_}"
  echo "${safe_title}${label}"
}

# If start and end times are specified, download the full video and then trim
if [[ -n "$START_TIME" && -n "$END_TIME" ]]; then
  echo "Downloading specified time range: $START_TIME to $END_TIME"

  if [[ -n "$USER_BASENAME" ]]; then
    BASENAME="$USER_BASENAME"
  else
    BASENAME=$(get_safe_basename "$URL" "_clip")
  fi

  TEMP_TEMPLATE="${OUTPUT_DIR%/}/__ytclip_temp.%(ext)s"
  FINAL_FILE="${OUTPUT_DIR%/}/${BASENAME}.mp4"

  download_video "$URL" "$TEMP_TEMPLATE"
  TEMP_FILE=$(ls "${TEMP_TEMPLATE//%(ext)s/mp4}")

  ffmpeg -i "$TEMP_FILE" -ss "$START_TIME" -to "$END_TIME" -c copy "$FINAL_FILE"
  rm -f "$TEMP_FILE"

  echo "Clip extracted to: $FINAL_FILE"

  # Adjust subtitles
  for lang in en ja; do
    SRC="${OUTPUT_DIR%/}/__ytclip_temp.${lang}.srt"
    DEST="${OUTPUT_DIR%/}/${BASENAME}.${lang}.srt"
    if [[ -f "$SRC" ]]; then
      echo "Adjusting SRT ($lang)..."
      python3 "$(dirname "$0")/utils/adjust_srt_timestamp.py" \
        "$SRC" "$DEST" "$START_TIME" "$END_TIME"
      rm "$SRC"
    else
      echo "No SRT found for $lang"
    fi
  done

else
  # Download full video
  if [[ -n "$USER_BASENAME" ]]; then
    BASENAME="$USER_BASENAME"
  else
    BASENAME=$(get_safe_basename "$URL" "")
  fi
  OUTPUT_TEMPLATE="${OUTPUT_DIR%/}/${BASENAME}.%(ext)s"
  download_video "$URL" "$OUTPUT_TEMPLATE"
  echo "Full video downloaded."
fi
