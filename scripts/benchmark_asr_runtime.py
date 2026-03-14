from __future__ import annotations

import argparse
import importlib.util
import json
import os
import resource
import tempfile
import time
import wave
from pathlib import Path
from typing import Any


def _audio_duration_seconds(audio_path: Path) -> float:
    with wave.open(str(audio_path), "rb") as handle:
        frame_rate = handle.getframerate()
        frame_count = handle.getnframes()
    if frame_rate <= 0:
        raise ValueError(f"Invalid frame rate for {audio_path}")
    return frame_count / frame_rate


def _max_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def _resolve_torch_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def _transcribe_qwen(model: Any, audio_path: Path, language: str) -> dict[str, Any]:
    from qwen_asr.inference.utils import (
        MAX_FORCE_ALIGN_INPUT_SECONDS,
        SAMPLE_RATE,
        AudioChunk,
        chunk_list,
        merge_languages,
        normalize_audios,
        normalize_language_name,
        parse_asr_output,
        split_audio_into_chunks,
        validate_language,
    )

    forced_language = None if language == "Auto" else normalize_language_name(language)
    if forced_language is not None:
        validate_language(forced_language)

    wavs = normalize_audios(str(audio_path))
    parts = split_audio_into_chunks(
        wav=wavs[0],
        sr=SAMPLE_RATE,
        max_chunk_sec=MAX_FORCE_ALIGN_INPUT_SECONDS,
    )
    chunks = [
        AudioChunk(orig_index=0, chunk_index=index, wav=chunk_wav, sr=SAMPLE_RATE, offset_sec=offset_sec)
        for index, (chunk_wav, offset_sec) in enumerate(parts)
    ]
    chunk_ctx = [""] * len(chunks)
    chunk_lang = [forced_language] * len(chunks)
    chunk_wavs = [chunk.wav for chunk in chunks]

    batch_size = model.max_inference_batch_size
    if batch_size is None or batch_size < 0:
        batch_size = len(chunk_wavs) or 1
    batch_size = min(batch_size, 4)

    recognize_started = time.perf_counter()
    raw_outputs: list[str] = []
    for offset in range(0, len(chunk_wavs), batch_size):
        raw_outputs.extend(
            model._infer_asr(
                chunk_ctx[offset : offset + batch_size],
                chunk_wavs[offset : offset + batch_size],
                chunk_lang[offset : offset + batch_size],
            )
        )
    recognize_elapsed = time.perf_counter() - recognize_started

    parsed_languages: list[str] = []
    parsed_texts: list[str] = []
    for output, chunk_forced_language in zip(raw_outputs, chunk_lang):
        parsed_language, parsed_text = parse_asr_output(output, user_language=chunk_forced_language)
        parsed_languages.append(parsed_language)
        parsed_texts.append(parsed_text)

    to_align_audio = []
    to_align_text = []
    to_align_lang = []
    for chunk, text, language_pred in zip(chunks, parsed_texts, parsed_languages):
        if not text.strip():
            continue
        to_align_audio.append((chunk.wav, chunk.sr))
        to_align_text.append(text)
        to_align_lang.append(language_pred)

    align_started = time.perf_counter()
    aligned_results = []
    if to_align_audio:
        align_batch_size = model.max_inference_batch_size
        if align_batch_size is None or align_batch_size < 0:
            align_batch_size = len(to_align_audio)
        align_batch_size = min(align_batch_size, 4)
        for a_chunk, t_chunk, l_chunk in zip(
            chunk_list(to_align_audio, align_batch_size),
            chunk_list(to_align_text, align_batch_size),
            chunk_list(to_align_lang, align_batch_size),
        ):
            aligned_results.extend(
                model.forced_aligner.align(audio=a_chunk, text=t_chunk, language=l_chunk)
            )
    align_elapsed = time.perf_counter() - align_started

    merged_text = "".join(text for text in parsed_texts if text is not None).strip()
    merged_language = merge_languages(parsed_languages)
    aligned_segment_count = 0
    if aligned_results:
        merged_align = model._merge_align_results(aligned_results)
        aligned_segment_count = len(merged_align.items)

    return {
        "language": merged_language,
        "text_chars": len(merged_text),
        "chunk_count": len(chunks),
        "aligned_segments": aligned_segment_count,
        "recognize_s": recognize_elapsed,
        "align_s": align_elapsed,
    }


def _run_qwen(model_name: str, audio_paths: list[Path], language: str, repeat: int) -> dict[str, Any]:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from qwen_asr import Qwen3ASRModel

    aligner_name = "Qwen/Qwen3-ForcedAligner-0.6B"
    device = _resolve_torch_device()

    load_started = time.perf_counter()
    model = Qwen3ASRModel.from_pretrained(
        model_name,
        forced_aligner=aligner_name,
        forced_aligner_kwargs={"dtype": "auto", "device_map": device},
        dtype="auto",
        device_map=device,
    )
    load_elapsed = time.perf_counter() - load_started

    runs: list[dict[str, Any]] = []
    for audio_path in audio_paths:
        duration_s = _audio_duration_seconds(audio_path)
        for run_index in range(1, repeat + 1):
            started = time.perf_counter()
            details = _transcribe_qwen(model, audio_path, language)
            elapsed = time.perf_counter() - started
            runs.append(
                {
                    "audio": str(audio_path),
                    "audio_duration_s": duration_s,
                    "run_index": run_index,
                    "elapsed_s": elapsed,
                    "realtime_factor": elapsed / duration_s,
                    "throughput_x": duration_s / elapsed if elapsed > 0 else None,
                    **details,
                }
            )

    return {
        "backend": "qwen",
        "model": model_name,
        "device": device,
        "load_s": load_elapsed,
        "max_rss_mb": _max_rss_mb(),
        "runs": runs,
    }


def _run_mlx(model_name: str, audio_paths: list[Path], language: str | None, repeat: int) -> dict[str, Any]:
    import mlx.core as mx
    from mlx_audio.stt.generate import generate_transcription
    from mlx_audio.stt.utils import load_model

    load_started = time.perf_counter()
    model = load_model(model_name)
    load_elapsed = time.perf_counter() - load_started

    runs: list[dict[str, Any]] = []
    for audio_path in audio_paths:
        duration_s = _audio_duration_seconds(audio_path)
        for run_index in range(1, repeat + 1):
            kwargs: dict[str, Any] = {}
            if language and language != "Auto":
                kwargs["language"] = language
            with tempfile.TemporaryDirectory(prefix="mlx-stt-bench-") as temp_dir:
                output_path = Path(temp_dir) / "transcript"
                started = time.perf_counter()
                result = generate_transcription(
                    model=model,
                    audio=str(audio_path),
                    output_path=str(output_path),
                    format="json",
                    verbose=False,
                    **kwargs,
                )
                elapsed = time.perf_counter() - started
                segment_count = len(getattr(result, "segments", []) or [])
                result_language = getattr(result, "language", None)
                if isinstance(result_language, list):
                    result_language = ",".join(result_language)
                runs.append(
                    {
                        "audio": str(audio_path),
                        "audio_duration_s": duration_s,
                        "run_index": run_index,
                        "elapsed_s": elapsed,
                        "realtime_factor": elapsed / duration_s,
                        "throughput_x": duration_s / elapsed if elapsed > 0 else None,
                        "language": result_language,
                        "text_chars": len(getattr(result, "text", "") or ""),
                        "chunk_count": segment_count,
                        "aligned_segments": segment_count,
                        "recognize_s": elapsed,
                        "align_s": 0.0,
                        "peak_mlx_gb": mx.get_peak_memory() / 1e9,
                    }
                )

    return {
        "backend": "mlx",
        "model": model_name,
        "device": "apple-silicon-gpu",
        "load_s": load_elapsed,
        "max_rss_mb": _max_rss_mb(),
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local ASR runtimes.")
    parser.add_argument("--backend", choices=("qwen", "mlx"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", nargs="+", required=True)
    parser.add_argument("--language", default="Auto")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output-json")
    args = parser.parse_args()

    audio_paths = [Path(item).resolve() for item in args.audio]
    for audio_path in audio_paths:
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if args.backend == "qwen":
        payload = _run_qwen(args.model, audio_paths, args.language, args.repeat)
    else:
        payload = _run_mlx(args.model, audio_paths, args.language, args.repeat)

    payload["python"] = os.sys.version.split()[0]
    payload["cwd"] = str(Path.cwd())

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
