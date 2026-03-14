from pathlib import Path

from backend.app import config


def test_load_runtime_settings_migrates_codex_minimal_to_none(tmp_path: Path, monkeypatch) -> None:
    runtime_settings_path = tmp_path / "runtime_settings.json"
    runtime_settings_path.write_text(
        '{"asr_model":"mlx-community/Qwen3-ASR-0.6B-8bit","tts_model":"mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit","review_backend":"codex-minimal"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", runtime_settings_path)

    settings = config.load_runtime_settings()

    assert settings["review_backend"] == "codex-none"
    assert '"review_backend": "codex-none"' in runtime_settings_path.read_text(encoding="utf-8")
