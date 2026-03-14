#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-./tmp/pycache}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8010}"
PYTHON_BIN="${PYTHON_BIN:-/opt/anaconda3/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

exec "$PYTHON_BIN" -m uvicorn server:app --host "$BIND_HOST" --port "$PORT"
