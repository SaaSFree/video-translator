from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .utils import atomic_write_json, read_json

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
PROJECTS_DIR = ROOT_DIR / "projects"
TMP_DIR = ROOT_DIR / "tmp"
ASSETS_DIR = ROOT_DIR / "assets"
DEFAULTS_DIR = ASSETS_DIR / "defaults"
DEFAULT_TEST_VIDEO_PATH = DEFAULTS_DIR / "default_test_video.mp4"


def _default_mlx_asr_python_bin() -> str:
    candidates = [
        ROOT_DIR / ".venvs" / "mlx-audio" / "bin" / "python",
        ROOT_DIR.parent / "codex_chat_app" / "data" / "test" / ".venvs" / "mlx-audio" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def _default_mlx_tts_python_bin() -> str:
    return _default_mlx_asr_python_bin()


MLX_ASR_MODEL = os.getenv("MLX_ASR_MODEL", "mlx-community/Qwen3-ASR-1.7B-8bit")
MLX_ALIGNER_MODEL = os.getenv("MLX_ALIGNER_MODEL", "mlx-community/Qwen3-ForcedAligner-0.6B-8bit")
MLX_ASR_SERVICE_PORT = int(os.getenv("MLX_ASR_SERVICE_PORT", "50003"))
MLX_ASR_PYTHON_BIN = os.getenv(
    "MLX_ASR_PYTHON_BIN",
    _default_mlx_asr_python_bin(),
)
MLX_TTS_SERVICE_PORT = int(os.getenv("MLX_TTS_SERVICE_PORT", "50002"))
MLX_TTS_PYTHON_BIN = os.getenv(
    "MLX_TTS_PYTHON_BIN",
    _default_mlx_tts_python_bin(),
)
MLX_TTS_VOICE = os.getenv("MLX_TTS_VOICE", "")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.4")

ASR_CHUNK_TARGET_SECONDS = float(os.getenv("ASR_CHUNK_TARGET_SECONDS", "24"))
ASR_CHUNK_MAX_SECONDS = float(os.getenv("ASR_CHUNK_MAX_SECONDS", "28"))
ASR_CHUNK_MIN_SECONDS = float(os.getenv("ASR_CHUNK_MIN_SECONDS", "8"))
ASR_CHUNK_MIN_SILENCE_SECONDS = float(os.getenv("ASR_CHUNK_MIN_SILENCE_SECONDS", "0.5"))
ASR_CHUNK_SILENCE_NOISE = os.getenv("ASR_CHUNK_SILENCE_NOISE", "-32dB")

RUNTIME_SETTINGS_PATH = TMP_DIR / "runtime_settings.json"
SIDEBAR_STATE_PATH = TMP_DIR / "sidebar_state.json"

ASR_MODEL_OPTIONS = [
    {"value": "mlx-community/Qwen3-ASR-0.6B-8bit", "label": "MLX Qwen ASR / 0.6B-8bit"},
    {"value": "mlx-community/Qwen3-ASR-1.7B-8bit", "label": "MLX Qwen ASR / 1.7B-8bit"},
]

TTS_MODEL_OPTIONS = [
    {"value": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", "label": "MLX Qwen TTS / 0.6B-Base-8bit"},
    {"value": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit", "label": "MLX Qwen TTS / 1.7B-Base-8bit"},
]

REVIEW_BACKEND_OPTIONS = [
    {"value": "codex-none", "label": f"Codex / {CODEX_MODEL} / none"},
    {"value": "codex-low", "label": f"Codex / {CODEX_MODEL} / low"},
    {"value": "codex-medium", "label": f"Codex / {CODEX_MODEL} / medium"},
    {"value": "codex-high", "label": f"Codex / {CODEX_MODEL} / high"},
    {"value": "codex-xhigh", "label": f"Codex / {CODEX_MODEL} / xhigh"},
]

SOURCE_LANGUAGE_OPTIONS = [
    {"value": "Auto", "label": "自动"},
    {"value": "Arabic", "label": "阿拉伯语"},
    {"value": "Cantonese", "label": "粤语"},
    {"value": "Chinese", "label": "中文"},
    {"value": "Dutch", "label": "荷兰语"},
    {"value": "English", "label": "英语"},
    {"value": "French", "label": "法语"},
    {"value": "German", "label": "德语"},
    {"value": "Hindi", "label": "印地语"},
    {"value": "Indonesian", "label": "印尼语"},
    {"value": "Italian", "label": "意大利语"},
    {"value": "Japanese", "label": "日语"},
    {"value": "Korean", "label": "韩语"},
    {"value": "Malay", "label": "马来语"},
    {"value": "Portuguese", "label": "葡萄牙语"},
    {"value": "Russian", "label": "俄语"},
    {"value": "Spanish", "label": "西班牙语"},
    {"value": "Swedish", "label": "瑞典语"},
    {"value": "Thai", "label": "泰语"},
    {"value": "Turkish", "label": "土耳其语"},
    {"value": "Vietnamese", "label": "越南语"},
]

TARGET_LANGUAGE_OPTIONS = [
    {"value": "Chinese", "label": "中文"},
    {"value": "English", "label": "英语"},
    {"value": "French", "label": "法语"},
    {"value": "German", "label": "德语"},
    {"value": "Italian", "label": "意大利语"},
    {"value": "Japanese", "label": "日语"},
    {"value": "Korean", "label": "韩语"},
    {"value": "Portuguese", "label": "葡萄牙语"},
    {"value": "Russian", "label": "俄语"},
    {"value": "Spanish", "label": "西班牙语"},
]

DEFAULT_RUNTIME_SETTINGS = {
    "asr_model": "mlx-community/Qwen3-ASR-1.7B-8bit",
    "tts_model": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
    "review_backend": "codex-medium",
}


def ensure_base_dirs() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULTS_DIR.mkdir(parents=True, exist_ok=True)


def runtime_setting_options() -> dict[str, list[dict[str, str]]]:
    return {
        "asr_model": ASR_MODEL_OPTIONS,
        "tts_model": TTS_MODEL_OPTIONS,
        "review_backend": REVIEW_BACKEND_OPTIONS,
    }


def project_language_options() -> dict[str, list[dict[str, str]]]:
    return {
        "source_language": SOURCE_LANGUAGE_OPTIONS,
        "target_language": TARGET_LANGUAGE_OPTIONS,
    }


def _valid_values() -> dict[str, set[str]]:
    return {
        key: {item["value"] for item in items}
        for key, items in runtime_setting_options().items()
    }


def load_runtime_settings() -> dict[str, str]:
    ensure_base_dirs()
    payload = read_json(RUNTIME_SETTINGS_PATH, {})
    migrated = False
    if payload.get("review_backend") == "codex-minimal":
        payload["review_backend"] = "codex-none"
        migrated = True
    settings = DEFAULT_RUNTIME_SETTINGS.copy()
    valid_values = _valid_values()
    for key, default_value in settings.items():
        value = payload.get(key, default_value)
        settings[key] = value if value in valid_values[key] else default_value
    if migrated:
        atomic_write_json(RUNTIME_SETTINGS_PATH, settings)
    return settings


def save_runtime_settings(updates: dict[str, Any]) -> dict[str, str]:
    settings = load_runtime_settings()
    valid_values = _valid_values()
    for key, value in updates.items():
        if key not in settings or not isinstance(value, str):
            continue
        if value not in valid_values[key]:
            raise ValueError(f"Unsupported value for {key}: {value}")
        settings[key] = value
    atomic_write_json(RUNTIME_SETTINGS_PATH, settings)
    return settings
