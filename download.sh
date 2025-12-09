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

# Function to check if video is already Premiere-safe (h264 + aac + yuv420p)
is_premiere_safe() {
  local video_file="$1"
  
  # Get video codec
  local vcodec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$video_file" 2>/dev/null)
  # Get audio codec
  local acodec=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 "$video_file" 2>/dev/null)
  # Get pixel format
  local pixfmt=$(ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of default=noprint_wrappers=1:nokey=1 "$video_file" 2>/dev/null)
  
  if [[ "$vcodec" == "h264" && "$acodec" == "aac" && "$pixfmt" == "yuv420p" ]]; then
    return 0  # Safe
  else
    return 1  # Needs re-encode
  fi
}

# Function to re-encode video to Premiere-safe format (H.264 + AAC)
reencode_to_premiere() {
  local input_file="$1"
  local output_file="$2"
  
  echo "Re-encoding to Premiere-safe format (H.264 + AAC, preset=medium, CRF=18)..."
  ffmpeg -i "$input_file" \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
    -vsync cfr \
    -video_track_timescale 30000 \
    -c:a aac -b:a 256k \
    -movflags +faststart \
    -loglevel error -stats \
    -y "$output_file"
  echo ""  # Add newline after progress bar
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
    ffmpeg -i "$TEMP_FILE" -ss "$START_TIME" -to "$END_TIME" -c copy -loglevel error -stats "$FINAL_FILE"
    echo ""  # Add newline after progress
    rm -f "$TEMP_FILE"
    echo "Audio clip extracted to: $FINAL_FILE"
  else
    # Video clipping mode: DL -> clip -> check -> re-encode if needed -> cleanup
    download_video "$URL" "$TEMP_TEMPLATE"
    TEMP_FULL=$(ls "${TEMP_TEMPLATE//%(ext)s/mp4}")
    TEMP_CLIP="${OUTPUT_DIR%/}/__ytclip_clip.tmp.mp4"
    FINAL_FILE="${OUTPUT_DIR%/}/${BASENAME}.mp4"
    
    # Step 1: Clip from full video (fast copy)
    echo "Clipping segment from $START_TIME to $END_TIME..."
    ffmpeg -i "$TEMP_FULL" -ss "$START_TIME" -to "$END_TIME" -c copy -loglevel error -stats -y "$TEMP_CLIP"
    echo ""  # Add newline after progress
    
    # Step 2: Check if already Premiere-safe
    if is_premiere_safe "$TEMP_CLIP"; then
      echo "Video is already Premiere-safe (H.264 + AAC). No re-encoding needed."
      mv "$TEMP_CLIP" "$FINAL_FILE"
    else
      echo "Video needs re-encoding for Premiere compatibility."
      reencode_to_premiere "$TEMP_CLIP" "$FINAL_FILE"
      rm -f "$TEMP_CLIP"
    fi
    
    # Step 3: Cleanup temp files
    rm -f "$TEMP_FULL"
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
    # Full video mode: DL -> check -> re-encode if needed -> cleanup
    TEMP_TEMPLATE="${OUTPUT_DIR%/}/__ytclip_full.%(ext)s"
    download_video "$URL" "$TEMP_TEMPLATE"
    TEMP_FULL=$(ls "${TEMP_TEMPLATE//%(ext)s/mp4}")
    FINAL_FILE="${OUTPUT_DIR%/}/${BASENAME}.mp4"
    
    # Check if already Premiere-safe
    if is_premiere_safe "$TEMP_FULL"; then
      echo "Video is already Premiere-safe (H.264 + AAC). No re-encoding needed."
      mv "$TEMP_FULL" "$FINAL_FILE"
    else
      echo "Video needs re-encoding for Premiere compatibility."
      reencode_to_premiere "$TEMP_FULL" "$FINAL_FILE"
      rm -f "$TEMP_FULL"
    fi
    
    # Download thumbnail after video (highest quality available)
    yt-dlp \
      --skip-download \
      --write-thumbnail \
      --convert-thumbnails jpg \
      --output "${OUTPUT_DIR%/}/${BASENAME}.thumb.%(ext)s" \
      "$URL"
    echo "Full video downloaded to: $FINAL_FILE"
  fi
fi

