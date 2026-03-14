from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from .config import (
    ASR_CHUNK_MAX_SECONDS,
    ASR_CHUNK_MIN_SECONDS,
    ASR_CHUNK_MIN_SILENCE_SECONDS,
    ASR_CHUNK_SILENCE_NOISE,
    ASR_CHUNK_TARGET_SECONDS,
    CODEX_MODEL,
    MLX_ALIGNER_MODEL,
    MLX_ASR_PYTHON_BIN,
    MLX_ASR_SERVICE_PORT,
    MLX_TTS_PYTHON_BIN,
    MLX_TTS_SERVICE_PORT,
    MLX_TTS_VOICE,
    load_runtime_settings,
    project_language_options,
    runtime_setting_options,
)
from .media import detect_speech_windows, ffprobe_duration
from .models import Segment, TranscriptDocument, TranscriptItem


_MLX_ASR_SERVICE_STATE: dict[str, object] = {}
_MLX_TTS_SERVICE_STATE: dict[str, object] = {}


def _emit_progress(progress_callback, completed: int, total: int, message: str | None = None) -> None:
    if not progress_callback:
        return
    try:
        if message is None:
            progress_callback(completed, total)
        else:
            progress_callback(completed, total, message)
    except TypeError:
        progress_callback(completed, total)


def _run_with_progress_heartbeat(progress_callback, completed: int, total: int, message: str, work):
    _emit_progress(progress_callback, completed, total, message)
    if not progress_callback:
        return work()
    stop_event = threading.Event()
    started_at = time.monotonic()

    def _heartbeat() -> None:
        while not stop_event.wait(5.0):
            elapsed = int(time.monotonic() - started_at)
            _emit_progress(progress_callback, completed, total, f"{message} 已耗时 {elapsed}s")

    thread = threading.Thread(target=_heartbeat, name="asr-progress-heartbeat", daemon=True)
    thread.start()
    try:
        return work()
    finally:
        stop_event.set()
        thread.join(timeout=0.2)


def _clean_generation_text(text: str) -> str:
    value = text.strip()
    if "</think>" in value:
        value = value.split("</think>", 1)[1].strip()
    return value.strip().strip('"').strip("'").strip()


def _terminate_process_group(process: subprocess.Popen[bytes] | subprocess.Popen[str] | None, *, timeout: float = 10.0) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:  # noqa: BLE001
        try:
            process.terminate()
        except Exception:  # noqa: BLE001
            return
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return


def _listener_pids_for_port(port: int) -> list[int]:
    try:
        output = subprocess.check_output(["lsof", "-ti", f"tcp:{port}"], text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    pids: list[int] = []
    for line in output.splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            pid = int(value)
        except ValueError:
            continue
        if pid != os.getpid():
            pids.append(pid)
    return sorted(set(pids))


def _terminate_pid(pid: int, *, timeout: float = 10.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _close_service_log_handle(state: dict[str, object]) -> None:
    handle = state.get("log_handle")
    if handle is None:
        state.clear()
        return
    try:
        handle.close()
    except Exception:  # noqa: BLE001
        pass
    state.clear()


def _detect_asr_parts(
    audio_path: Path,
    duration: float,
    wav: np.ndarray,
    sample_rate: int,
    *,
    max_chunk_sec: float,
) -> list[tuple[np.ndarray, float]]:
    target_duration = min(max(float(ASR_CHUNK_TARGET_SECONDS), 1.0), max_chunk_sec)
    max_duration = min(max(float(ASR_CHUNK_MAX_SECONDS), target_duration), max_chunk_sec)
    min_duration = min(max(float(ASR_CHUNK_MIN_SECONDS), 0.5), target_duration)
    windows = detect_speech_windows(
        audio_path,
        duration,
        target_duration=target_duration,
        max_duration=max_duration,
        min_duration=min_duration,
        noise=ASR_CHUNK_SILENCE_NOISE,
        min_silence=ASR_CHUNK_MIN_SILENCE_SECONDS,
    )
    parts: list[tuple[np.ndarray, float]] = []
    for start, end in windows:
        start_index = max(0, min(int(round(start * sample_rate)), len(wav)))
        end_index = max(start_index, min(int(round(end * sample_rate)), len(wav)))
        if end_index <= start_index:
            continue
        parts.append((wav[start_index:end_index].copy(), round(start, 3)))
    if parts:
        return parts
    if duration <= 0:
        return []
    cursor = 0.0
    while cursor < duration - 0.05:
        end = min(duration, cursor + max_chunk_sec)
        start_index = int(round(cursor * sample_rate))
        end_index = int(round(end * sample_rate))
        if end_index > start_index:
            parts.append((wav[start_index:end_index].copy(), round(cursor, 3)))
        cursor = end
    return parts


def _mlx_asr_service_health(*, port: int = MLX_ASR_SERVICE_PORT) -> dict[str, object] | None:
    try:
        response = requests.get(f"http://127.0.0.1:{port}/health", timeout=3)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001
        return None
    return payload if payload.get("ok") else None


def _start_mlx_asr_service(
    model_id: str,
    *,
    aligner_id: str = MLX_ALIGNER_MODEL,
    port: int = MLX_ASR_SERVICE_PORT,
) -> dict[str, object]:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "mlx_audio_asr_service.py"
    if not script_path.exists():
        raise RuntimeError(f"MLX Audio ASR service script not found: {script_path}")
    python_bin = Path(MLX_ASR_PYTHON_BIN)
    if not python_bin.exists():
        raise RuntimeError(f"MLX Audio python interpreter not found: {python_bin}")
    log_dir = Path(__file__).resolve().parents[2] / "tmp"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mlx_asr_service.log"
    log_handle = open(log_path, "ab")
    command = [
        str(python_bin),
        str(script_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--model",
        str(model_id),
        "--aligner",
        str(aligner_id),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True,
    )
    _MLX_ASR_SERVICE_STATE["process"] = process
    _MLX_ASR_SERVICE_STATE["log_handle"] = log_handle
    _MLX_ASR_SERVICE_STATE["model_id"] = str(model_id)
    _MLX_ASR_SERVICE_STATE["aligner_id"] = str(aligner_id)
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        health = _mlx_asr_service_health(port=port)
        if health:
            return health
        if process.poll() is not None:
            break
        time.sleep(1.0)
    tail = ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="ignore")[-2000:]
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(
        "Failed to start local MLX Audio ASR service."
        + (f" Recent log:\n{tail}" if tail else "")
    )


def _stop_mlx_asr_service(*, port: int = MLX_ASR_SERVICE_PORT) -> None:
    process = _MLX_ASR_SERVICE_STATE.get("process")
    if isinstance(process, subprocess.Popen):
        _terminate_process_group(process)
    _close_service_log_handle(_MLX_ASR_SERVICE_STATE)
    for pid in _listener_pids_for_port(port):
        _terminate_pid(pid)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not _mlx_asr_service_health(port=port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Failed to stop MLX ASR service on port {port}.")


def _ensure_mlx_asr_service(
    model_id: str,
    *,
    aligner_id: str = MLX_ALIGNER_MODEL,
    port: int = MLX_ASR_SERVICE_PORT,
) -> dict[str, object]:
    expected_model = str(model_id)
    expected_aligner = str(aligner_id)
    health = _mlx_asr_service_health(port=port)
    if health:
        active_model = str(health.get("model_id") or "")
        active_aligner = str(health.get("aligner_id") or "")
        if active_model == expected_model and active_aligner == expected_aligner:
            return health
        _stop_mlx_asr_service(port=port)
    return _start_mlx_asr_service(expected_model, aligner_id=expected_aligner, port=port)


class MlxLocalTranscriber:
    name = "mlx-qwen3-asr-local"

    def __init__(self, model_name: str, *, aligner_name: str = MLX_ALIGNER_MODEL) -> None:
        self.model_name = model_name
        self.aligner_name = aligner_name

    def transcribe_full(
        self,
        audio_path: Path,
        duration: float,
        *,
        language: str = "Auto",
        progress_callback=None,
    ) -> TranscriptDocument:
        _ensure_mlx_asr_service(self.model_name, aligner_id=self.aligner_name)
        wav, sample_rate = sf.read(audio_path, always_2d=False)
        wav = np.asarray(wav, dtype=np.float32)
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        max_chunk_sec = max(float(ASR_CHUNK_MAX_SECONDS), 1.0)
        parts = _detect_asr_parts(audio_path, duration, wav, sample_rate, max_chunk_sec=max_chunk_sec)
        if not parts:
            parts = [(wav, 0.0)]

        chunk_count = len(parts)
        total_steps = chunk_count + 2
        _emit_progress(progress_callback, 1, total_steps, f"Preparing MLX ASR transcription ({chunk_count} chunks).")

        all_texts: list[str] = []
        all_languages: list[str] = []
        items: list[TranscriptItem] = []

        for index, (chunk_wav, offset_sec) in enumerate(parts, start=1):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                chunk_path = Path(handle.name)
            try:
                sf.write(chunk_path, chunk_wav, sample_rate)

                def _request():
                    response = requests.post(
                        f"http://127.0.0.1:{MLX_ASR_SERVICE_PORT}/transcribe",
                        json={"audio_path": str(chunk_path), "language": language},
                        timeout=(10, max(120, int(max(len(chunk_wav) / max(sample_rate, 1), 1.0) * 8))),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not payload.get("ok"):
                        raise RuntimeError(str(payload.get("error") or "MLX ASR request failed."))
                    return payload

                payload = _run_with_progress_heartbeat(
                    progress_callback,
                    index,
                    total_steps,
                    f"Running MLX ASR recognition ({index}/{chunk_count} chunks).",
                    _request,
                )
            finally:
                chunk_path.unlink(missing_ok=True)

            text = str(payload.get("text", "") or "").strip()
            if text:
                all_texts.append(text)
            language_name = str(payload.get("language", "") or "").strip()
            if language_name:
                all_languages.append(language_name)
            for item_index, item in enumerate(payload.get("items", []) or []):
                item_text = str(item.get("text", "") or "").strip()
                start = round(float(item.get("start", 0.0) or 0.0) + offset_sec, 3)
                end = round(float(item.get("end", 0.0) or 0.0) + offset_sec, 3)
                if not item_text or end < start:
                    continue
                items.append(
                    TranscriptItem(
                        index=len(items) if item_index >= 0 else len(items),
                        text=item_text,
                        start=start,
                        end=end,
                    )
                )
            _emit_progress(
                progress_callback,
                index + 1,
                total_steps,
                f"Running MLX ASR recognition ({index}/{chunk_count} chunks).",
            )

        merged_language = next((item for item in all_languages if item), language if language != "Auto" else "Auto")
        merged_text = "\n".join(text for text in all_texts if text).strip()
        _emit_progress(progress_callback, total_steps, total_steps, "MLX ASR transcription complete.")
        return TranscriptDocument(language=merged_language, text=merged_text, items=items)


class CodexSourceReviewer:
    name = "codex-gpt-5.4"

    def __init__(self, model_name: str, *, reasoning_effort: str = "medium") -> None:
        self.model_name = model_name
        self.reasoning_effort = self._normalize_reasoning_effort(reasoning_effort)
        self.binary = shutil.which("codex")
        if not self.binary:
            raise RuntimeError("The codex CLI is not installed or not in PATH.")

    @staticmethod
    def _normalize_reasoning_effort(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "minimal":
            return "none"
        if normalized in {"none", "low", "medium", "high", "xhigh"}:
            return normalized
        return "medium"

    @staticmethod
    def _parse_response(raw: str) -> dict[str, object]:
        value = raw.strip()
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise RuntimeError("Codex reviewer did not return JSON.")
        return json.loads(value[start : end + 1])

    @staticmethod
    def _build_source_review_prompt(
        source_segment: Segment,
        *,
        source_language: str,
    ) -> str:
        source_hint = (
            "The source subtitle may be in mixed or auto-detected languages."
            if source_language == "Auto"
            else f"The source subtitle is in {source_language}."
        )
        payload = {"id": source_segment.id, "text": source_segment.text}
        return (
            "You are reviewing one ASR subtitle line and proposing only obvious ASR corrections.\n"
            "Return only valid JSON with this exact shape:\n"
            '{"text":"...","changes":[{"from":"...","to":"..."}]}\n'
            "Rules:\n"
            "- The output text must stay in the original language.\n"
            "- Only fix obvious ASR mistakes.\n"
            "- Keep the original wording, meaning, and sentence structure whenever possible.\n"
            "- Do not paraphrase, summarize, translate, merge, or split the line.\n"
            "- If the line already looks correct, return it unchanged and return an empty changes array.\n"
            "- Only include changes that you actually corrected.\n"
            "- Prioritize correcting product names, brand names, acronyms, technical terms, and mixed-language proper nouns.\n"
            "- Keep punctuation natural, but do not rewrite for style.\n"
            "- Common terms that must be preserved when applicable include: OpenClaw, Codex, API, Anthropic, OpenAI, Claude, Claude Opus, OAuth.\n"
            "- No markdown.\n"
            "- No commentary.\n"
            f"{source_hint}\n"
            "Subtitle line to review:\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    def _run_prompt(self, prompt: str) -> dict[str, object]:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as handle:
            output_path = Path(handle.name)
        try:
            command = [
                self.binary,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--model",
                self.model_name,
                "--output-last-message",
                str(output_path),
                "-",
            ]
            subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                check=True,
            )
            raw_output = output_path.read_text(encoding="utf-8")
            return self._parse_response(raw_output)
        except subprocess.CalledProcessError as exc:
            details = "\n".join(
                part.strip()
                for part in [exc.stdout or "", exc.stderr or ""]
                if part and part.strip()
            ).strip()
            raise RuntimeError(details or "Codex source review command failed.") from exc
        finally:
            output_path.unlink(missing_ok=True)

    def review_source_segment_correction(
        self,
        source_segment: Segment,
        *,
        source_language: str = "Auto",
    ) -> tuple[str, list[dict[str, str]]]:
        payload = self._run_prompt(
            self._build_source_review_prompt(source_segment, source_language=source_language)
        )
        text = _clean_generation_text(str(payload.get("text", "")).strip())
        if not text:
            raise RuntimeError("Codex source review returned an empty text.")
        raw_changes = payload.get("changes")
        if raw_changes is None:
            raw_changes = []
        if not isinstance(raw_changes, list):
            raise RuntimeError("Codex source review returned an invalid changes payload.")
        changes: list[dict[str, str]] = []
        for item in raw_changes[:12]:
            if not isinstance(item, dict):
                continue
            from_text = str(item.get("from", "")).strip()
            to_text = str(item.get("to", "")).strip()
            if not from_text or not to_text or from_text == to_text:
                continue
            changes.append({"from": from_text, "to": to_text})
        return text, changes


def _codex_reasoning_effort_from_setting(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("codex-"):
        normalized = normalized.split("-", 1)[1]
    if normalized == "minimal":
        return "none"
    if normalized in {"none", "low", "medium", "high", "xhigh"}:
        return normalized
    return "medium"


def _mlx_tts_service_health(*, port: int = MLX_TTS_SERVICE_PORT) -> dict[str, object] | None:
    try:
        response = requests.get(f"http://127.0.0.1:{port}/health", timeout=3)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001
        return None
    return payload if payload.get("ok") else None


def _start_mlx_tts_service(
    model_id: str,
    *,
    port: int = MLX_TTS_SERVICE_PORT,
    voice: str | None = MLX_TTS_VOICE,
) -> dict[str, object]:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "mlx_audio_tts_service.py"
    if not script_path.exists():
        raise RuntimeError(f"MLX Audio TTS service script not found: {script_path}")
    python_bin = Path(MLX_TTS_PYTHON_BIN)
    if not python_bin.exists():
        raise RuntimeError(f"MLX Audio TTS python interpreter not found: {python_bin}")
    log_dir = Path(__file__).resolve().parents[2] / "tmp"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mlx_tts_service.log"
    log_handle = open(log_path, "ab")
    command = [
        str(python_bin),
        str(script_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--model",
        str(model_id),
        "--voice",
        str(voice or ""),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True,
    )
    _MLX_TTS_SERVICE_STATE["process"] = process
    _MLX_TTS_SERVICE_STATE["log_handle"] = log_handle
    _MLX_TTS_SERVICE_STATE["model_id"] = str(model_id)
    _MLX_TTS_SERVICE_STATE["voice"] = str(voice or "")
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        health = _mlx_tts_service_health(port=port)
        if health:
            return health
        if process.poll() is not None:
            break
        time.sleep(1.0)
    tail = ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="ignore")[-2000:]
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(
        "Failed to start local MLX Audio TTS service."
        + (f" Recent log:\n{tail}" if tail else "")
    )


def _stop_mlx_tts_service(*, port: int = MLX_TTS_SERVICE_PORT) -> None:
    process = _MLX_TTS_SERVICE_STATE.get("process")
    if isinstance(process, subprocess.Popen):
        _terminate_process_group(process)
    _close_service_log_handle(_MLX_TTS_SERVICE_STATE)
    for pid in _listener_pids_for_port(port):
        _terminate_pid(pid)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not _mlx_tts_service_health(port=port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Failed to stop MLX TTS service on port {port}.")


def _ensure_mlx_tts_service(
    model_id: str,
    *,
    port: int = MLX_TTS_SERVICE_PORT,
    voice: str | None = MLX_TTS_VOICE,
) -> dict[str, object]:
    expected_model = str(model_id)
    expected_voice = str(voice or "").strip()
    health = _mlx_tts_service_health(port=port)
    if health:
        active_model = str(health.get("model_id") or "")
        active_voice = str(health.get("voice") or "").strip()
        if active_model == expected_model and active_voice == expected_voice:
            return health
        _stop_mlx_tts_service(port=port)
    return _start_mlx_tts_service(expected_model, port=port, voice=expected_voice)


class CodexTargetTranslator:
    name = "codex-gpt-5.4"

    def __init__(self, model_name: str, *, reasoning_effort: str = "medium") -> None:
        self.model_name = model_name
        self.reasoning_effort = _codex_reasoning_effort_from_setting(reasoning_effort)
        self.binary = shutil.which("codex")
        if not self.binary:
            raise RuntimeError("The codex CLI is not installed or not in PATH.")

    @staticmethod
    def _parse_response(raw: str) -> dict[str, object]:
        value = raw.strip()
        start = value.find("{")
        end = value.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise RuntimeError("Codex target translator did not return JSON.")
        return json.loads(value[start : end + 1])

    @staticmethod
    def _spoken_rendering_rules(target_language: str) -> str:
        normalized = str(target_language or "").strip().lower()
        if normalized == "english":
            return (
                "- Output the final spoken-reading text, not display text.\n"
                "- Render numerals, decimals, percentages, currencies, dates, times, ratios, ranges, fractions, versions, and shorthand in the way a narrator should literally say them.\n"
                "- Examples: 2.4 -> two point four; 24/7 -> twenty-four seven; 15% -> fifteen percent; $20 -> twenty dollars.\n"
                "- Keep established product and brand names intact, but format embedded numbers in a speakable way when needed.\n"
            )
        return (
            "- Output the final spoken-reading text, not display text.\n"
            "- Convert numerals and symbolic shorthand into the natural spoken form a voice actor should literally read aloud.\n"
        )

    @staticmethod
    def _build_translation_prompt(
        source_segment: Segment,
        *,
        source_language: str,
        target_language: str,
        full_transcript_text: str,
    ) -> str:
        source_hint = (
            "The source subtitle may be in mixed or auto-detected languages."
            if source_language == "Auto"
            else f"The source subtitle is in {source_language}."
        )
        payload = {
            "id": source_segment.id,
            "start": round(float(source_segment.start), 3),
            "end": round(float(source_segment.end), 3),
            "duration": round(max(float(source_segment.end) - float(source_segment.start), 0.0), 3),
            "text": source_segment.text,
        }
        return (
            f"You are translating one subtitle line into natural spoken {target_language} for dubbed video.\n"
            "Return only valid JSON with this exact shape:\n"
            '{"text":"..."}\n'
            "Rules:\n"
            "- Translate exactly one line.\n"
            "- Keep the meaning accurate and spoken.\n"
            "- Keep a strict one-to-one mapping to this source line.\n"
            "- Prefer concise, direct phrasing over literal wording.\n"
            "- Remove filler, repeated emphasis, and anything visually obvious.\n"
            "- Keep important names, brands, product names, and technical terms intact.\n"
            "- Make the spoken length fit the source duration naturally.\n"
            "- Do not reference previous or next subtitle lines.\n"
            f"{CodexTargetTranslator._spoken_rendering_rules(target_language)}"
            "- No markdown.\n"
            "- No commentary.\n"
            f"{source_hint}\n"
            "Full transcript context:\n"
            f"{full_transcript_text.strip()}\n\n"
            "Source subtitle:\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def _build_retime_translation_prompt(
        source_segment: Segment,
        *,
        source_language: str,
        target_language: str,
        full_transcript_text: str,
        current_translation: str,
        slot_duration: float,
        synthesized_duration: float,
        attempt_index: int,
    ) -> str:
        source_hint = (
            "The source subtitle may be in mixed or auto-detected languages."
            if source_language == "Auto"
            else f"The source subtitle is in {source_language}."
        )
        strictness_hint = (
            "Be materially shorter than the current translation while keeping the core meaning intact."
            if attempt_index > 0
            else "Prefer a tighter phrasing if it still sounds natural."
        )
        return (
            f"You are revising one dubbing line into natural spoken {target_language}.\n"
            "Return only valid JSON with this exact shape:\n"
            '{"text":"..."}\n'
            "Rules:\n"
            "- Output exactly one subtitle line.\n"
            "- Keep the same meaning as the source line.\n"
            "- Keep the line concise, natural, and dubbing-ready.\n"
            "- Preserve important names and technical terms.\n"
            "- Avoid filler, repetition, and explanatory expansions.\n"
            f"- Target slot duration is about {slot_duration:.2f} seconds.\n"
            f"- Current synthesized duration is about {synthesized_duration:.2f} seconds.\n"
            f"- {strictness_hint}\n"
            f"{CodexTargetTranslator._spoken_rendering_rules(target_language)}"
            "- No markdown.\n"
            "- No commentary.\n"
            f"{source_hint}\n"
            "Full transcript context:\n"
            f"{full_transcript_text.strip()}\n\n"
            f"Source line:\n{source_segment.text.strip()}\n\n"
            f"Current translation:\n{current_translation.strip()}\n"
        )

    def _run_text_prompt(self, prompt: str) -> str:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as handle:
            output_path = Path(handle.name)
        try:
            command = [
                self.binary,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--model",
                self.model_name,
                "--output-last-message",
                str(output_path),
                "-",
            ]
            subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                check=True,
            )
            raw_output = output_path.read_text(encoding="utf-8")
            payload = self._parse_response(raw_output)
            text = _clean_generation_text(str(payload.get("text", "")).strip())
            if not text:
                raise RuntimeError("Codex target translator returned an empty text.")
            return text
        except subprocess.CalledProcessError as exc:
            details = "\n".join(
                part.strip()
                for part in [exc.stdout or "", exc.stderr or ""]
                if part and part.strip()
            ).strip()
            raise RuntimeError(details or "Codex target translation command failed.") from exc
        finally:
            output_path.unlink(missing_ok=True)

    def translate_segment(
        self,
        source_segment: Segment,
        *,
        source_language: str = "Auto",
        target_language: str = "English",
        full_transcript_text: str,
    ) -> str:
        return self._run_text_prompt(
            self._build_translation_prompt(
                source_segment,
                source_language=source_language,
                target_language=target_language,
                full_transcript_text=full_transcript_text,
            )
        )

    def retime_translation(
        self,
        source_segment: Segment,
        *,
        source_language: str = "Auto",
        target_language: str = "English",
        full_transcript_text: str,
        current_translation: str,
        slot_duration: float,
        synthesized_duration: float,
        attempt_index: int = 0,
    ) -> str:
        return self._run_text_prompt(
            self._build_retime_translation_prompt(
                source_segment,
                source_language=source_language,
                target_language=target_language,
                full_transcript_text=full_transcript_text,
                current_translation=current_translation,
                slot_duration=slot_duration,
                synthesized_duration=synthesized_duration,
                attempt_index=attempt_index,
            )
        )



class MlxLocalSynthesizer:
    name = "mlx-qwen3-tts-local"

    def __init__(self, model_name: str, *, voice: str | None = MLX_TTS_VOICE) -> None:
        self.model_name = model_name
        self.voice = str(voice or "").strip() or None

    def synthesize(
        self,
        *,
        text: str,
        output_path: Path,
        reference_audio: Path,
        reference_text: str | None = None,
        language: str = "English",
        progress_callback=None,
    ) -> None:
        _ensure_mlx_tts_service(self.model_name, voice=self.voice)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        def _request() -> bytes:
            response = requests.post(
                f"http://127.0.0.1:{MLX_TTS_SERVICE_PORT}/tts",
                json={
                    "text": text,
                    "ref_audio": str(reference_audio),
                    "ref_text": str(reference_text or ""),
                    "language": language,
                    "voice": self.voice or "",
                },
                timeout=(10, 600),
            )
            content_type = response.headers.get("content-type", "")
            if not response.ok:
                detail = ""
                try:
                    if "application/json" in content_type:
                        detail = str(response.json().get("error") or "")
                    else:
                        detail = response.text.strip()
                except Exception:  # noqa: BLE001
                    detail = ""
                raise RuntimeError(detail or "MLX TTS request failed.")
            return response.content

        audio_bytes = _run_with_progress_heartbeat(
            progress_callback,
            0,
            1,
            "Generating target speech.",
            _request,
        )
        output_path.write_bytes(audio_bytes)
        if ffprobe_duration(output_path) <= 0.05:
            raise RuntimeError(f"Synthesized audio for {output_path.name} is empty.")


def get_system_status() -> dict[str, object]:
    settings = load_runtime_settings()
    review_setting = settings["review_backend"]
    reasoning_effort = _codex_reasoning_effort_from_setting(review_setting)
    asr_python_available = Path(MLX_ASR_PYTHON_BIN).exists()
    tts_python_available = Path(MLX_TTS_PYTHON_BIN).exists()
    asr_service_script = Path(__file__).resolve().parents[2] / "scripts" / "mlx_audio_asr_service.py"
    tts_service_script = Path(__file__).resolve().parents[2] / "scripts" / "mlx_audio_tts_service.py"
    asr_health = _mlx_asr_service_health()
    tts_health = _mlx_tts_service_health()
    asr_blocked_reasons: list[str] = []
    tts_blocked_reasons: list[str] = []
    if not asr_python_available:
        asr_blocked_reasons.append(f"MLX Audio python not found: {MLX_ASR_PYTHON_BIN}")
    if not asr_service_script.exists():
        asr_blocked_reasons.append(f"service script missing: {asr_service_script}")
    if not tts_python_available:
        tts_blocked_reasons.append(f"MLX Audio python not found: {MLX_TTS_PYTHON_BIN}")
    if not tts_service_script.exists():
        tts_blocked_reasons.append(f"service script missing: {tts_service_script}")

    providers = [
        {
            "role": "transcriber",
            "configured": settings["asr_model"],
            "provider": MlxLocalTranscriber.name,
            "mode": "real" if not asr_blocked_reasons else "blocked",
            "ready": not asr_blocked_reasons,
            "reason": (
                (
                    f"Model: {settings['asr_model']}. Aligner: {MLX_ALIGNER_MODEL}. Runtime port: {MLX_ASR_SERVICE_PORT}."
                    + (f" Service online at 127.0.0.1:{MLX_ASR_SERVICE_PORT}." if asr_health else " Service will auto-start on first transcription request.")
                )
                if not asr_blocked_reasons
                else "; ".join(asr_blocked_reasons) + "."
            ),
        },
        {
            "role": "reviewer",
            "configured": review_setting,
            "provider": CodexTargetTranslator.name,
            "mode": "real" if shutil.which("codex") else "blocked",
            "ready": shutil.which("codex") is not None,
            "reason": (
                f"Model: {CODEX_MODEL}. Reasoning effort: {reasoning_effort}. Codex runs for source ASR correction review and target translation."
                if shutil.which("codex")
                else "The codex CLI is not installed or not in PATH."
            ),
        },
        {
            "role": "synthesizer",
            "configured": settings["tts_model"],
            "provider": MlxLocalSynthesizer.name,
            "mode": "real" if not tts_blocked_reasons else "blocked",
            "ready": not tts_blocked_reasons,
            "reason": (
                (
                    f"Model: {settings['tts_model']}. Runtime port: {MLX_TTS_SERVICE_PORT}."
                    + (f" Service online at 127.0.0.1:{MLX_TTS_SERVICE_PORT}." if tts_health else " Service will auto-start on first synthesis request.")
                )
                if not tts_blocked_reasons
                else "; ".join(tts_blocked_reasons) + "."
            ),
        },
    ]
    modes = {item["mode"] for item in providers}
    overall_mode = "real" if modes == {"real"} else "limited"
    return {
        "mode": overall_mode,
        "summary": "Current workspace handles source transcription, source clip cutting, Codex correction review, and target translation/synthesis.",
        "providers": providers,
        "warnings": [item["reason"] for item in providers if item["mode"] != "real"],
        "settings": settings,
        "options": {
            **runtime_setting_options(),
            **project_language_options(),
        },
    }


def get_transcriber() -> MlxLocalTranscriber:
    settings = load_runtime_settings()
    return MlxLocalTranscriber(settings["asr_model"], aligner_name=MLX_ALIGNER_MODEL)


def get_reviewer() -> CodexSourceReviewer:
    settings = load_runtime_settings()
    return CodexSourceReviewer(
        CODEX_MODEL,
        reasoning_effort=_codex_reasoning_effort_from_setting(settings["review_backend"]),
    )


def get_translator() -> CodexTargetTranslator:
    settings = load_runtime_settings()
    return CodexTargetTranslator(
        CODEX_MODEL,
        reasoning_effort=_codex_reasoning_effort_from_setting(settings["review_backend"]),
    )


def get_synthesizer() -> MlxLocalSynthesizer:
    settings = load_runtime_settings()
    return MlxLocalSynthesizer(settings["tts_model"], voice=MLX_TTS_VOICE)
