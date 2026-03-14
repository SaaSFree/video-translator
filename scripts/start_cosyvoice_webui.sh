#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../third_party/CosyVoice" && pwd)"
PORT="${1:-50000}"
MODEL_DIR="${2:-pretrained_models/Fun-CosyVoice3-0.5B}"

source /opt/anaconda3/etc/profile.d/conda.sh
conda activate cosyvoice

# CosyVoice's FAQ expects Matcha-TTS to be on PYTHONPATH for local scripts.
export PYTHONPATH="${ROOT_DIR}:${ROOT_DIR}/third_party/Matcha-TTS:${PYTHONPATH:-}"

cd "${ROOT_DIR}"
exec python webui.py --port "${PORT}" --model_dir "${MODEL_DIR}"
