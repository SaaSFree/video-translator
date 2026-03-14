#!/bin/zsh
set -euo pipefail

CONTAINER_NAME="${OPEN_WEBUI_CONTAINER_NAME:-open-webui-qwen3}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH." >&2
  exit 1
fi

if docker ps -a --format '{{.Names}}' | rg -qx "$CONTAINER_NAME"; then
  docker rm -f "$CONTAINER_NAME" >/dev/null
  echo "Stopped $CONTAINER_NAME"
else
  echo "$CONTAINER_NAME is not running."
fi
