#!/usr/bin/env bash
set -euo pipefail

# Simple wrapper: given a YouTube URL/ID and output dir, download live_chat.json via yt-dlp
# and run the beta.hype_finder analyzer.

usage() {
  echo "Usage: $0 -u <youtube_url_or_id> -o <outdir>" >&2
  exit 1
}

URL=""
OUTDIR=""

while getopts "u:o:" opt; do
  case "$opt" in
    u) URL="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    *) usage ;;
  esac
done

if [[ -z "$URL" || -z "$OUTDIR" ]]; then
  usage
fi

mkdir -p "$OUTDIR" "$OUTDIR/hype-data"

# Derive a safe video id-ish name from the URL (yt-dlp can print id but we keep it simple here).
VIDEO_ID="${URL##*=}"
if [[ -z "$VIDEO_ID" ]]; then
  VIDEO_ID="video"
fi

LIVE_JSON="$OUTDIR/hype-data/${VIDEO_ID}.live_chat.json"

# Download live chat subtitles only
yt-dlp \
  --skip-download \
  --write-subs \
  --sub-format live_chat \
  --sub-lang live_chat \
  -o "$OUTDIR/hype-data/%(id)s.%(ext)s" \
  "$URL"

# Find the produced live_chat file.
# Note: some videos don't have live chat replay, in which case yt-dlp prints
# "There are no subtitles..." and produces no *.live_chat.json.
# Also, older runs may have only JSONL (already preprocessed).
shopt -s nullglob
json_files=("$OUTDIR"/hype-data/*.live_chat.json)
jsonl_files=("$OUTDIR"/hype-data/*.live_chat.jsonl)
shopt -u nullglob

if [[ ${#json_files[@]} -eq 0 && ${#jsonl_files[@]} -eq 0 ]]; then
  echo "[hype_finder.sh] No live chat replay found for this video. hype-finder: done (skipped)." >&2
  exit 0
fi

# Prefer JSONL if present (faster; avoids re-parsing huge NDJSON).
if [[ ${#jsonl_files[@]} -gt 0 ]]; then
  LIVE_JSONL="${jsonl_files[0]}"
  echo "[hype_finder.sh] Using live chat JSONL: $LIVE_JSONL" >&2
  python -m beta.hype_finder -o "$OUTDIR" --live-chat-jsonl "$LIVE_JSONL"
else
  LIVE_JSON="${json_files[0]}"
  echo "[hype_finder.sh] Using live chat JSON: $LIVE_JSON" >&2
  python -m beta.hype_finder -o "$OUTDIR" --live-chat-json "$LIVE_JSON"
fi
