#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p assets/defaults

URL="${1:-https://www.youtube.com/watch?v=4RLI4kQFDck&t=410s}"
OUTPUT_TEMPLATE="assets/defaults/default_test_video.%(ext)s"

yt-dlp \
  -f "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b" \
  -o "$OUTPUT_TEMPLATE" \
  "$URL"

