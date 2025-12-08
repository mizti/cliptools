#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./download.sh -u <YouTube URL> [-o output_dir] [-b basename] [-s start_time] [-e end_time] [-w]

URL=""
OUTPUT_DIR="."
USER_BASENAME=""
START_TIME=""
END_TIME=""
AUDIO_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--url)
      URL="$2"; shift 2 ;;
    -o|--outdir)
      OUTPUT_DIR="$2"; shift 2 ;;
    -b|--basename)
      USER_BASENAME="$2"; shift 2 ;;
    -s|--start)
      START_TIME="$2"; shift 2 ;;
    -e|--end)
      END_TIME="$2"; shift 2 ;;
    -w|--audio-only)
      AUDIO_ONLY=true; shift ;;
    -h|--help)
      echo "Usage: $0 -u <YouTube URL> [-o output_dir] [-b basename] [-s start_time] [-e end_time] [-w]"; exit 0 ;;
    *)
      echo "Unknown option $1" >&2; exit 1 ;;
  esac
done

if [[ -z $URL ]]; then
  echo "Usage: $0 -u <YouTube URL> [-o output_dir] [-b basename] [-s start_time] [-e end_time] [-w]" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Function to download video + subtitles
download_video() {
  local video_url="$1"
  local output_path="$2"
yt-dlp -U && \
    yt-dlp \
    -S "res,ext" \
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

  BASENAME="${USER_BASENAME:-$(get_safe_basename "$OUTPUT_DIR")}"
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
  BASENAME="${USER_BASENAME:-$(get_safe_basename "$OUTPUT_DIR" "")}"
  if [[ "$AUDIO_ONLY" == true ]]; then
    download_audio "$URL" "${OUTPUT_DIR%/}/${BASENAME}.%(ext)s"
    echo "Audio downloaded to: ${OUTPUT_DIR%/}/${BASENAME}.mp3"
  else
    OUTPUT_TEMPLATE="${OUTPUT_DIR%/}/${BASENAME}.%(ext)s"
    download_video "$URL" "$OUTPUT_TEMPLATE"
    echo "Full video downloaded."
  fi
fi

