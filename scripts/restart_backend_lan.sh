#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p output/runtime

python3 - <<'PY'
import os
import signal
import subprocess

result = subprocess.run(["lsof", "-ti", ":8010"], capture_output=True, text=True)
for line in result.stdout.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        os.kill(int(line), signal.SIGTERM)
    except ProcessLookupError:
        pass
PY

nohup "$ROOT_DIR/scripts/run_lan.sh" >> "$ROOT_DIR/output/runtime/backend_8010.log" 2>&1 < /dev/null &
echo $! > "$ROOT_DIR/output/runtime/backend_8010.pid"
sleep 2

echo "PID $(cat "$ROOT_DIR/output/runtime/backend_8010.pid")"
echo "LAN URLs:"
python3 - <<'PY'
import subprocess
ips = []
for interface in ("en0", "en1"):
    try:
        value = subprocess.check_output(["ipconfig", "getifaddr", interface], text=True).strip()
    except Exception:
        continue
    if value and value not in ips:
        ips.append(value)
for ip in ips:
    print(f"http://{ip}:8010")
print("http://127.0.0.1:8010")
PY
