from pathlib import Path

import numpy as np

from backend.app.models import Segment
from backend.app.providers import (
    CodexTargetTranslator,
    _codex_reasoning_effort_from_setting,
    _detect_asr_parts,
    _ensure_mlx_asr_service,
    _ensure_mlx_tts_service,
)


def test_detect_asr_parts_uses_speech_windows(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    wav = np.arange(5000, dtype=np.float32)

    monkeypatch.setattr(
        "backend.app.providers.detect_speech_windows",
        lambda *args, **kwargs: [(0.0, 1.0), (1.5, 2.8)],
    )

    parts = _detect_asr_parts(
        audio_path,
        duration=5.0,
        wav=wav,
        sample_rate=1000,
        max_chunk_sec=8.0,
    )

    assert [offset for _chunk, offset in parts] == [0.0, 1.5]
    assert [len(chunk) for chunk, _offset in parts] == [1000, 1300]


def test_detect_asr_parts_falls_back_to_fixed_windows(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    wav = np.arange(7000, dtype=np.float32)

    monkeypatch.setattr(
        "backend.app.providers.detect_speech_windows",
        lambda *args, **kwargs: [],
    )

    parts = _detect_asr_parts(
        audio_path,
        duration=7.0,
        wav=wav,
        sample_rate=1000,
        max_chunk_sec=3.0,
    )

    assert [offset for _chunk, offset in parts] == [0.0, 3.0, 6.0]
    assert [len(chunk) for chunk, _offset in parts] == [3000, 3000, 1000]


def test_codex_reasoning_effort_from_setting_accepts_source_only_options() -> None:
    assert _codex_reasoning_effort_from_setting("codex-minimal") == "none"
    assert _codex_reasoning_effort_from_setting("codex-none") == "none"
    assert _codex_reasoning_effort_from_setting("codex-xhigh") == "xhigh"
    assert _codex_reasoning_effort_from_setting("unknown") == "medium"


def test_ensure_mlx_tts_service_restarts_when_runtime_model_differs(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        "backend.app.providers._mlx_tts_service_health",
        lambda *, port=0: {"ok": True, "model_id": "old-model", "voice": ""},
    )
    monkeypatch.setattr(
        "backend.app.providers._stop_mlx_tts_service",
        lambda *, port=0: events.append(f"stop:{port}"),
    )
    monkeypatch.setattr(
        "backend.app.providers._start_mlx_tts_service",
        lambda model_id, *, port=0, voice=None: (
            events.append(f"start:{model_id}:{voice or ''}:{port}"),
            {"ok": True, "model_id": model_id, "voice": voice or ""},
        )[1],
    )

    health = _ensure_mlx_tts_service("new-model", port=50002, voice="")

    assert health["model_id"] == "new-model"
    assert events == ["stop:50002", "start:new-model::50002"]


def test_ensure_mlx_asr_service_restarts_when_runtime_model_differs(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        "backend.app.providers._mlx_asr_service_health",
        lambda *, port=0: {"ok": True, "model_id": "old-asr", "aligner_id": "old-aligner"},
    )
    monkeypatch.setattr(
        "backend.app.providers._stop_mlx_asr_service",
        lambda *, port=0: events.append(f"stop:{port}"),
    )
    monkeypatch.setattr(
        "backend.app.providers._start_mlx_asr_service",
        lambda model_id, *, aligner_id, port=0: (
            events.append(f"start:{model_id}:{aligner_id}:{port}"),
            {"ok": True, "model_id": model_id, "aligner_id": aligner_id},
        )[1],
    )

    health = _ensure_mlx_asr_service("new-asr", aligner_id="new-aligner", port=50003)

    assert health["model_id"] == "new-asr"
    assert health["aligner_id"] == "new-aligner"
    assert events == ["stop:50003", "start:new-asr:new-aligner:50003"]


def test_translation_prompt_includes_spoken_number_rules_and_adjacent_context() -> None:
    segment = Segment(id="seg-0002", index=1, start=1.0, end=2.4, text="当前行 2.4 以及 24/7。")

    prompt = CodexTargetTranslator._build_translation_prompt(
        segment,
        source_language="Chinese",
        target_language="English",
        full_transcript_text="上一行\n当前行\n下一行",
    )

    assert "2.4 -> two point four" in prompt
    assert "24/7 -> twenty-four seven" in prompt
    assert "Do not reference previous or next subtitle lines." in prompt
