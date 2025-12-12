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

# pyenv environments often expose Python as `python` (not `python3`).
PYTHON_BIN=""
if command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

# ROUTE A / ROUTE B (high level)
#
# This script has two separate “Route A/B” decisions:
#
# 1) Pre-download (format selection) — implemented in select_format_ids():
#    - ROUTE A: If a *Premiere-safe* stream exists at/above PREFERRED_HEIGHT,
#      download that stream directly (no forced re-encode intent).
#      "Premiere-safe" here means: H.264 (avc1/h264) video in MP4 container + AAC audio.
#    - ROUTE B: Otherwise, download the highest-resolution stream available (optionally
#      preferring VP9/AV1 when FORCE_NON_PREMIERE_SAFE=1), then we may re-encode locally.
#
# 2) Post-download (local compatibility check) — implemented via is_premiere_safe():
#    - If the downloaded file is already (h264 + aac + yuv420p), we keep it as-is.
#    - Otherwise we re-encode to Premiere-safe H.264 + AAC.
#      On macOS, we use h264_videotoolbox when available, else fall back to libx264.

# Format selection (previously delegated to sandbox/select_format_ids.sh).
select_format_ids() {
  local video_url="$1"

  # Env vars for ROUTE A/B (format selection):
  #   PREFERRED_HEIGHT           : Route A minimum height threshold (default 1440)
  #   FORCE_ROUTE_B=1            : skip Route A entirely (always pick Route B)
  #   FORCE_NON_PREMIERE_SAFE=1  : in Route B, prefer VP9/AV1 (to force re-encode)

  # Default changed from 1080 -> 1440.
  local preferred_height="${PREFERRED_HEIGHT:-1440}"
  local force_route_b="${FORCE_ROUTE_B:-0}"
  local force_non_premiere="${FORCE_NON_PREMIERE_SAFE:-0}"

  # Probe formats as JSON (avoid set -e killing the script on failure)
  set +e
  local json
  json=$(yt-dlp -J --skip-download "$video_url" 2>/dev/null)
  local status=$?
  set -e

  if [[ $status -ne 0 || -z "$json" ]]; then
    echo "[download.sh] Failed to probe formats, falling back to defaults" >&2
    echo ""
    return 0
  fi

  if ! command -v jq >/dev/null 2>&1; then
    echo "[download.sh] jq not found; cannot parse format JSON; falling back to defaults" >&2
    echo ""
    return 0
  fi

  # jq-based selector. Keep behavior compatible with the previous Python parser.
  # Output: "<video_format_id>+<audio_format_id>" or empty line to trigger fallback.
  printf '%s' "$json" | jq -r \
    --argjson preferred_h "$preferred_height" \
    --argjson force_route_b "$force_route_b" \
    --argjson force_non_premiere "$force_non_premiere" \
    '
def is_video: (.vcodec? // "none") != "none";
def is_audio: (.acodec? // "none") != "none";
def height_i: (.height? // -1) | (if type=="number" then . else (try tonumber catch -1) end);
def has_h264: ((.vcodec? // "") | test("(avc1|h264)"));
def has_aac: ((.acodec? // "") | test("(mp4a|aac)"));
def is_non_premiere_video: ((.vcodec? // "") | test("(vp09|av01|vp9|av1)"));

def audio_pool: (.formats // []) | map(select(is_audio));
def best_audio:
  (audio_pool | map(select(has_aac)) | sort_by(.abr? // 0) | last)
  // (audio_pool | sort_by(.abr? // 0) | last);

def route_a_video:
  (.formats // [])
  | map(select(is_video))
  | map(select(height_i >= $preferred_h))
  | map(select(has_h264 and (.ext? == "mp4")))
  | sort_by(height_i)
  | last;

def route_b_video_any:
  (.formats // []) | map(select(is_video));

def route_b_video_pool:
  if $force_non_premiere == 1 then
    (route_b_video_any | map(select(is_non_premiere_video)) | if length>0 then . else route_b_video_any end)
  else
    route_b_video_any
  end;

def best_video(pool): (pool | sort_by(height_i) | last);

def out_pair(v; a):
  if (v == null) or (a == null) then empty else "\(v.format_id)+\(a.format_id)" end;

if $force_route_b == 1 then
  out_pair(best_video(route_b_video_pool); best_audio)
else
  (out_pair(route_a_video; best_audio))
  // (out_pair(best_video(route_b_video_pool); best_audio))
end
    ' 2>/dev/null || true
}

# Function to download video + subtitles
download_video() {
  local video_url="$1"
  local output_path="$2"
  local fmt
  fmt="$(select_format_ids "$video_url" | tr -d '\r\n' || true)"

  if [[ -n "$fmt" ]]; then
    echo "[download.sh] Using probed format ids: $fmt" >&2
    yt-dlp -U && \
      yt-dlp \
      -f "$fmt" \
      --merge-output-format mp4 \
      --cookies-from-browser chrome \
      --remote-components ejs:github \
      --output "$output_path" \
      "$video_url"
  else
    echo "[download.sh] No probed format ids; using yt-dlp defaults (-S/-f bv*+ba/b)" >&2
    yt-dlp -U && \
      yt-dlp \
      -S "res,ext" \
      -f "bv*+ba/b" \
      --merge-output-format mp4 \
      --cookies-from-browser chrome \
      --remote-components ejs:github \
      --output "$output_path" \
      "$video_url"
  fi
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

# Function to re-encode video to Premiere-safe format using VideoToolbox (Apple Silicon).
# Falls back to libx264 if VideoToolbox isn't available.
reencode_to_premiere_videotoolbox() {
  local input_file="$1"
  local output_file="$2"

  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "h264_videotoolbox"; then
    echo "Re-encoding to Premiere-safe format using VideoToolbox (h264_videotoolbox)..."
    ffmpeg -i "$input_file" \
      -c:v h264_videotoolbox -b:v 12000k -pix_fmt yuv420p \
      -vsync cfr \
      -video_track_timescale 30000 \
      -c:a aac -b:a 256k \
      -movflags +faststart \
      -loglevel error -stats \
      -y "$output_file"
    echo ""
  else
    echo "[download.sh] h264_videotoolbox not available; falling back to libx264" >&2
    reencode_to_premiere "$input_file" "$output_file"
  fi
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
      reencode_to_premiere_videotoolbox "$TEMP_CLIP" "$FINAL_FILE"
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
        if [[ -z "$PYTHON_BIN" ]]; then
          echo "[download.sh] python not found; cannot adjust SRT timestamps" >&2
          continue
        fi
        "$PYTHON_BIN" "$(dirname "$0")/utils/adjust_srt_timestamp.py" \
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
      reencode_to_premiere_videotoolbox "$TEMP_FULL" "$FINAL_FILE"
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

