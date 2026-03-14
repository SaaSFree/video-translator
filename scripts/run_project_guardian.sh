#!/bin/zsh
set -euo pipefail

REPO="/Volumes/8TR0/codex/codex_chat_app/data/test"
mkdir -p "$REPO/output/guardian"

exec /opt/anaconda3/bin/python3 "$REPO/scripts/project_guardian.py" \
  --repo "$REPO" \
  --project "introducing-the-codex-app-d6c3685b" \
  --project "api-openclaw-ai-a4944120" \
  --port 8010 \
  --interval 5 \
  --stale-seconds 45
