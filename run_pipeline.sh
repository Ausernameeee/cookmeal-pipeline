#!/usr/bin/env bash
# kaorou-pipeline: YouTube URL -> bilingual (EN+ZH) hardsubbed video.
#
# Usage: ./run_pipeline.sh "<youtube-url>" [name]
#
# Requires: python3 (+ requirements.txt), ffmpeg, yt-dlp,
#           a CJK font (default style uses Noto Sans CJK SC),
#           GEMINI_API_KEY env var (free key: https://aistudio.google.com)
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 \"<youtube-url>\" [name]" >&2
  exit 1
fi
if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "error: GEMINI_API_KEY is not set (free key: https://aistudio.google.com)" >&2
  exit 1
fi

URL="$1"; NAME="${2:-video}"
PY="${PYTHON:-python3}"
FONT="${SUBTITLE_FONT:-Noto Sans CJK SC}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK="$ROOT/work"; mkdir -p "$WORK"; cd "$WORK"
ASR_ARGS=()
if [ -n "${ASR_MODEL:-}" ]; then ASR_ARGS=(--model "$ASR_MODEL"); fi

echo "=== [1/5] download ==="
yt-dlp --no-playlist --extractor-args "youtube:player_client=android" \
  -f "bv*[height<=720]+ba/b[height<=720]/b" --merge-output-format mp4 \
  -o "${NAME}_src.%(ext)s" "$URL"

echo "=== [2/5] extract audio ==="
ffmpeg -y -v error -i "${NAME}_src.mp4" -ar 16000 -ac 1 -c:a pcm_s16le "${NAME}.wav"

echo "=== [3/5] ASR (faster-whisper, CPU int8) ==="
"$PY" "$ROOT/asr.py" "${NAME}.wav" "${NAME}_segments.json" "${ASR_ARGS[@]}"

echo "=== [4/5] translate -> bilingual SRT ==="
"$PY" "$ROOT/translate_srt.py" \
  "${NAME}_segments.json" "${NAME}_segments_zh.json" "${NAME}_bilingual.srt"

echo "=== [5/5] burn in ==="
ffmpeg -y -v error -i "${NAME}_src.mp4" \
  -vf "subtitles=${NAME}_bilingual.srt:force_style='FontName=${FONT},FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1,Shadow=0,MarginV=22'" \
  -c:v libx264 -preset veryfast -crf 23 -c:a copy "${NAME}_final.mp4"

echo "=== done: ${WORK}/${NAME}_final.mp4 ==="
