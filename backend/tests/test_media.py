from pathlib import Path

import numpy as np
import soundfile as sf

from backend.app.media import detect_outer_silence_offsets


def test_detect_outer_silence_offsets_preserves_low_energy_tail_with_sensitive_threshold(tmp_path: Path) -> None:
    sample_rate = 24000
    silence_a = np.zeros(int(sample_rate * 0.05), dtype=np.float32)
    voiced = (0.4 * np.sin(2 * np.pi * 220 * np.arange(int(sample_rate * 0.25)) / sample_rate)).astype(np.float32)
    tail_fricative = (0.015 * np.random.default_rng(0).normal(size=int(sample_rate * 0.08))).astype(np.float32)
    silence_b = np.zeros(int(sample_rate * 0.20), dtype=np.float32)
    audio = np.concatenate([silence_a, voiced, tail_fricative, silence_b])

    audio_path = tmp_path / "tail.wav"
    sf.write(audio_path, audio, sample_rate)

    _leading_default, trailing_default = detect_outer_silence_offsets(audio_path)
    _leading_sensitive, trailing_sensitive = detect_outer_silence_offsets(
        audio_path,
        threshold_ratio=0.018,
        min_threshold=0.0012,
    )

    assert trailing_sensitive < trailing_default
    assert trailing_default - trailing_sensitive >= 0.05
