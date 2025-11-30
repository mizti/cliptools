#!/usr/bin/env bash
set -euo pipefail

# Usage: ./download.sh <YouTube URL> [-s start_time] [-e end_time] [-w] [output_dir] [filename_without_ext]

# Parse arguments
START_TIME=""
END_TIME=""
AUDIO_ONLY=false
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
    -w|--audio-only)
      AUDIO_ONLY=true
      shift
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
  echo "Usage: $0 <YouTube URL> [-s start_time] [-e end_time] [-w] [output_dir] [filename_without_ext]"
  exit 1
fi

URL="$1"
OUTPUT_DIR="${2:-.}"
USER_BASENAME="${3:-}"

mkdir -p "$OUTPUT_DIR"

# Function to download video + subtitles
download_video() {
  local video_url="$1"
  local output_path="$2"
yt-dlp -U && \
    yt-dlp \
    -S "vcodec:h264,acodec:m4a" \
    -f "bv*+ba/b" \
    --merge-output-format mp4 \
    --cookies-from-browser chrome \
    --remote-components ejs:github \
    --output "$output_path" \
    "$video_url"
    #--convert-subs srt \
    #--write-auto-sub \
    #--write-subs \
    #--sub-lang en \
    #--extractor-args "youtube:player_client=ios" \
    #--sub-lang en,ja \
}

# Function to download audio only
download_audio() {
  local audio_url="$1"
  local output_path="${2%.*}.mp3"
yt-dlp -U && \
    yt-dlp \
    -f bestaudio \
    --extract-audio \
    --cookies-from-browser chrome \
    --sleep-requests 2 --min-sleep-interval 1 --max-sleep-interval 5 \
    --audio-format mp3 \
    --output "$output_path" \
    "$audio_url"
    #--extractor-args "youtube:player_client=ios" \
}

# Function to get safe basename
get_safe_basename() {
  # $1 = output directory (= OUTDIR)
  local outdir="$1"
  local raw="downloaded_clip"  # default basename when -o is omitted

  if [[ -n "$outdir" && "$outdir" != "." ]]; then
    raw=$(basename "$outdir")  # use the last path component as basename
  fi
  echo "${raw//[^a-zA-Z0-9._-]/_}"  # sanitize to a safe filename
}

# Main logic
if [[ -n "$START_TIME" && -n "$END_TIME" ]]; then
  echo "Processing clip from $START_TIME to $END_TIME"

  BASENAME="${USER_BASENAME:-$(get_safe_basename "$URL")}"
  TEMP_TEMPLATE="${OUTPUT_DIR%/}/__ytclip_temp.%(ext)s"
  if [[ "$AUDIO_ONLY" == true ]]; then
    download_audio "$URL" "$TEMP_TEMPLATE"
    TEMP_FILE=$(ls "${TEMP_TEMPLATE//%(ext)s/mp3}")
    FINAL_FILE="${OUTPUT_DIR%/}/${BASENAME}.mp3"
    ffmpeg -i "$TEMP_FILE" -ss "$START_TIME" -to "$END_TIME" -c copy "$FINAL_FILE"
    rm -f "$TEMP_FILE"
    echo "Audio clip extracted to: $FINAL_FILE"
  else
    download_video "$URL" "$TEMP_TEMPLATE"
    TEMP_FILE=$(ls "${TEMP_TEMPLATE//%(ext)s/mp4}")
    FINAL_FILE="${OUTPUT_DIR%/}/${BASENAME}.mp4"
    ffmpeg -i "$TEMP_FILE" -ss "$START_TIME" -to "$END_TIME" -c copy "$FINAL_FILE"
    rm -f "$TEMP_FILE"
    echo "Video clip extracted to: $FINAL_FILE"

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
  fi

else
  # No clipping; full download
  BASENAME="${USER_BASENAME:-$(get_safe_basename "$URL" "")}"
  if [[ "$AUDIO_ONLY" == true ]]; then
    download_audio "$URL" "${OUTPUT_DIR%/}/${BASENAME}.%(ext)s"
    echo "Audio downloaded to: ${OUTPUT_DIR%/}/${BASENAME}.mp3"
  else
    OUTPUT_TEMPLATE="${OUTPUT_DIR%/}/${BASENAME}.%(ext)s"
    download_video "$URL" "$OUTPUT_TEMPLATE"
    echo "Full video downloaded."
  fi
fi

