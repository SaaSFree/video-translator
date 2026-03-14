#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="${OPEN_WEBUI_IMAGE:-ghcr.io/open-webui/open-webui:main}"
CONTAINER_NAME="${OPEN_WEBUI_CONTAINER_NAME:-open-webui-qwen3}"
PORT="${OPEN_WEBUI_PORT:-3000}"
DATA_DIR="${OPEN_WEBUI_DATA_DIR:-$ROOT_DIR/tmp/open-webui-data}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
DEFAULT_MODELS="${DEFAULT_MODELS:-qwen3:8b}"
WEBUI_AUTH="${WEBUI_AUTH:-True}"

mkdir -p "$DATA_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH." >&2
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "ollama is required but was not found in PATH." >&2
  exit 1
fi

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama is not reachable at http://127.0.0.1:11434." >&2
  echo "Start Ollama first, then rerun this script." >&2
  exit 1
fi

if ! ollama list | rg -q '^qwen3:8b[[:space:]]'; then
  echo "Model qwen3:8b was not found in Ollama." >&2
  echo "Run: ollama pull qwen3:8b" >&2
  exit 1
fi

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "Pulling $IMAGE ..."
docker pull "$IMAGE" >/dev/null

echo "Starting Open WebUI on port $PORT ..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p "${PORT}:8080" \
  -e OLLAMA_BASE_URL="$OLLAMA_BASE_URL" \
  -e DEFAULT_MODELS="$DEFAULT_MODELS" \
  -e WEBUI_AUTH="$WEBUI_AUTH" \
  -v "$DATA_DIR:/app/backend/data" \
  "$IMAGE" >/dev/null

echo "Waiting for Open WebUI to come up ..."
python3 - "$PORT" <<'PY'
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError

port = int(sys.argv[1])
deadline = time.time() + 180
while time.time() < deadline:
    try:
        with urlopen(f"http://127.0.0.1:{port}", timeout=3) as response:
            if response.status < 500:
                break
    except (URLError, Exception):
        time.sleep(1)
else:
    raise SystemExit("Open WebUI did not become reachable within 180s.")

ips = []
for interface in ("en0", "en1"):
    try:
        value = subprocess.check_output(["ipconfig", "getifaddr", interface], text=True).strip()
    except Exception:
        continue
    if value and value not in ips:
        ips.append(value)

print("Open WebUI is ready.")
for ip in ips:
    print(f"http://{ip}:{port}")
print(f"http://127.0.0.1:{port}")
PY
