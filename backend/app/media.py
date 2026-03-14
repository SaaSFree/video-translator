from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path


def _invoke_progress(progress_callback, completed: float, total: float, message: str | None = None) -> None:
    if not progress_callback:
        return
    try:
        if message is None:
            progress_callback(completed, total)
        else:
            progress_callback(completed, total, message)
    except TypeError:
        progress_callback(completed, total)


def _parse_ffmpeg_time_seconds(line: str) -> float | None:
    key, _, value = line.partition("=")
    value = value.strip()
    if not value:
        return None
    if key in {"out_time_us", "out_time_ms"}:
        try:
            return float(value) / 1_000_000.0
        except ValueError:
            return None
    if key == "out_time":
        parts = value.split(":")
        if len(parts) != 3:
            return None
        try:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
        except ValueError:
            return None
        return hours * 3600.0 + minutes * 60.0 + seconds
    return None


def _run_ffmpeg_with_progress(
    args: list[str],
    *,
    duration_seconds: float | None = None,
    progress_callback=None,
    progress_message: str | None = None,
) -> None:
    command = list(args)
    if not progress_callback or duration_seconds is None or duration_seconds <= 0 or not command or Path(command[0]).name != "ffmpeg":
        run_command(command)
        return

    ffmpeg_command = [
        command[0],
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        *command[1:],
    ]
    stderr_parts: list[str] = []
    process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        if process.stdout:
            for raw_line in process.stdout:
                line = raw_line.strip()
                seconds = _parse_ffmpeg_time_seconds(line)
                if seconds is not None:
                    current = min(max(seconds, 0.0), duration_seconds)
                    detail = (
                        f"{progress_message} ({current:.1f}/{duration_seconds:.1f}s)"
                        if progress_message
                        else None
                    )
                    _invoke_progress(progress_callback, current, duration_seconds, detail)
        if process.stderr:
            stderr_parts.append(process.stderr.read())
        return_code = process.wait()
    except Exception:
        process.kill()
        raise
    if return_code != 0:
        details = "\n".join(part.strip() for part in stderr_parts if part and part.strip()).strip()
        raise RuntimeError(details or f"Command failed: {' '.join(ffmpeg_command)}")
    final_message = (
        f"{progress_message} ({duration_seconds:.1f}/{duration_seconds:.1f}s)"
        if progress_message
        else None
    )
    _invoke_progress(progress_callback, duration_seconds, duration_seconds, final_message)


def run_command(args: list[str]) -> None:
    command = list(args)
    if command and Path(command[0]).name == "ffmpeg":
        command = [command[0], "-hide_banner", "-loglevel", "error", "-nostats", *command[1:]]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        details = "\n".join(part for part in [exc.stdout.strip(), exc.stderr.strip()] if part).strip()
        raise RuntimeError(details or f"Command failed: {' '.join(command)}") from exc


def ffprobe_duration(path: Path) -> float:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return max(float(output or "0"), 0.0)


def _build_atempo_chain(speed: float) -> str:
    remaining = max(speed, 0.01)
    filters: list[str] = []
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.5f}")
    return ",".join(filters)


def extract_audio(video_path: Path, audio_path: Path, *, progress_callback=None, progress_message: str | None = None) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            str(audio_path),
        ],
        duration_seconds=ffprobe_duration(video_path),
        progress_callback=progress_callback,
        progress_message=progress_message,
    )


def concat_audio(inputs: list[Path], output_path: Path, *, progress_callback=None, progress_message: str | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = output_path.parent / "concat.txt"
    manifest.write_text("\n".join(f"file '{path.as_posix()}'" for path in inputs), encoding="utf-8")
    total_duration = sum(ffprobe_duration(path) for path in inputs) if inputs else 0.0
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-c",
            "copy",
            str(output_path),
        ],
        duration_seconds=total_duration,
        progress_callback=progress_callback,
        progress_message=progress_message,
    )


def enforce_audio_duration(
    input_path: Path,
    output_path: Path,
    duration_seconds: float,
    *,
    progress_callback=None,
    progress_message: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_duration = max(duration_seconds, 0.3)
    destination = output_path
    temp_output = None
    if input_path.resolve() == output_path.resolve():
        temp_output = output_path.with_name(f"{output_path.stem}.duration-fixed{output_path.suffix}")
        destination = temp_output
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            "apad",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-t",
            f"{target_duration:.3f}",
            str(destination),
        ],
        duration_seconds=target_duration,
        progress_callback=progress_callback,
        progress_message=progress_message,
    )
    if temp_output is not None:
        shutil.move(str(temp_output), str(output_path))


def clip_video(
    input_video: Path,
    output_video: Path,
    start_seconds: float = 0.0,
    duration_seconds: float = 180.0,
    *,
    progress_callback=None,
    progress_message: str | None = None,
) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(start_seconds, 0.0):.3f}",
            "-t",
            f"{max(duration_seconds, 1.0):.3f}",
            "-i",
            str(input_video),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_video),
        ],
        duration_seconds=max(duration_seconds, 1.0),
        progress_callback=progress_callback,
        progress_message=progress_message,
    )


def mux_video_with_audio(
    source_video: Path,
    audio_track: Path,
    output_video: Path,
    *,
    progress_callback=None,
    progress_message: str | None = None,
) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_video),
            "-i",
            str(audio_track),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_video),
        ],
        duration_seconds=ffprobe_duration(source_video),
        progress_callback=progress_callback,
        progress_message=progress_message,
    )


def slot_audio(
    input_path: Path,
    output_path: Path,
    duration: float,
    *,
    max_slowdown_ratio: float = 1.0,
    progress_callback=None,
    progress_message: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_duration = max(duration, 0.3)
    source_duration = ffprobe_duration(input_path)
    audio_filters: list[str] = []
    if source_duration > target_duration + 0.03:
        audio_filters.append(_build_atempo_chain(source_duration / target_duration))
    elif target_duration > source_duration + 0.03 and max_slowdown_ratio > 1.0:
        slowdown_target = min(target_duration, source_duration * max(max_slowdown_ratio, 1.0))
        if slowdown_target > source_duration + 0.03:
            audio_filters.append(_build_atempo_chain(source_duration / slowdown_target))
    audio_filters.append("apad")
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            ",".join(audio_filters),
            "-ar",
            "24000",
            "-ac",
            "1",
            "-t",
            f"{target_duration:.3f}",
            str(output_path),
        ],
        duration_seconds=target_duration,
        progress_callback=progress_callback,
        progress_message=progress_message,
    )


def extract_audio_clip(
    input_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    sample_rate: int = 16000,
    progress_callback=None,
    progress_message: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_with_progress(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(start_seconds, 0.0):.3f}",
            "-t",
            f"{max(duration_seconds, 0.3):.3f}",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            str(output_path),
        ],
        duration_seconds=max(duration_seconds, 0.3),
        progress_callback=progress_callback,
        progress_message=progress_message,
    )


def make_silence(output_path: Path, duration: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{max(duration, 0.01):.3f}",
            str(output_path),
        ]
    )


def detect_silence_spans(audio_path: Path, noise: str = "-30dB", min_silence: float = 0.45) -> list[tuple[float, float]]:
    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(audio_path),
            "-af",
            f"silencedetect=noise={noise}:d={min_silence}",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    output = f"{process.stdout}\n{process.stderr}"
    start_re = re.compile(r"silence_start:\s*([0-9.]+)")
    end_re = re.compile(r"silence_end:\s*([0-9.]+)")
    pending_start: float | None = None
    spans: list[tuple[float, float]] = []
    for line in output.splitlines():
        if pending_start is None:
            match = start_re.search(line)
            if match:
                pending_start = float(match.group(1))
            continue
        match = end_re.search(line)
        if match:
            spans.append((pending_start, float(match.group(1))))
            pending_start = None
    return spans


def _leading_artifact_trim_offset_from_spans(
    duration: float,
    silence_spans: list[tuple[float, float]],
    *,
    min_initial_sound: float = 0.04,
    max_initial_sound: float = 0.22,
    max_scan_end: float = 0.42,
    min_silence_gap: float = 0.05,
    min_remaining_audio: float = 0.25,
) -> float:
    for silence_start, silence_end in silence_spans:
        silence_duration = silence_end - silence_start
        if silence_start < min_initial_sound:
            continue
        if silence_start > max_initial_sound:
            break
        if silence_end > max_scan_end or silence_duration < min_silence_gap:
            continue
        if duration - silence_end < min_remaining_audio:
            continue
        return round(silence_end, 3)
    return 0.0


def detect_leading_artifact_offset(
    audio_path: Path,
    *,
    noise: str = "-38dB",
    min_silence: float = 0.05,
) -> float:
    duration = ffprobe_duration(audio_path)
    silence_spans = detect_silence_spans(audio_path, noise=noise, min_silence=min_silence)
    return _leading_artifact_trim_offset_from_spans(duration, silence_spans)


def trim_segment_boundary_artifacts(
    input_path: Path,
    output_path: Path,
    *,
    noise: str = "-38dB",
    min_silence: float = 0.05,
) -> float:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trim_start = detect_leading_artifact_offset(
        input_path,
        noise=noise,
        min_silence=min_silence,
    )
    if trim_start <= 0.0:
        if input_path != output_path:
            shutil.copyfile(input_path, output_path)
        return 0.0

    duration = ffprobe_duration(input_path)
    clipped_duration = max(duration - trim_start, 0.1)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{trim_start:.3f}",
            "-i",
            str(input_path),
            "-t",
            f"{clipped_duration:.3f}",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(output_path),
        ]
    )
    return trim_start


def detect_outer_silence_offsets(
    audio_path: Path,
    *,
    frame_ms: float = 10.0,
    threshold_ratio: float = 0.05,
    min_threshold: float = 0.0025,
) -> tuple[float, float]:
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return 0.0, 0.0

    samples, sample_rate = sf.read(audio_path, dtype="float32")
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    if len(samples) == 0 or sample_rate <= 0:
        return 0.0, 0.0

    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if peak <= 0.0:
        return 0.0, 0.0

    frame_size = max(int(sample_rate * (frame_ms / 1000.0)), 1)
    frame_count = math.ceil(len(samples) / frame_size)
    padded = np.pad(samples, (0, frame_count * frame_size - len(samples)))
    rms = np.sqrt(np.mean(np.square(padded.reshape(frame_count, frame_size)), axis=1))
    threshold = max(min_threshold, peak * threshold_ratio)
    active_frames = np.flatnonzero(rms >= threshold)
    if active_frames.size == 0:
        return 0.0, 0.0

    first_frame = int(active_frames[0])
    last_frame = int(active_frames[-1])
    leading_silence = (first_frame * frame_size) / float(sample_rate)
    trailing_samples = max(len(samples) - ((last_frame + 1) * frame_size), 0)
    trailing_silence = trailing_samples / float(sample_rate)
    return round(leading_silence, 3), round(trailing_silence, 3)


def trim_audio_edges(
    input_path: Path,
    output_path: Path,
    *,
    trim_start: float = 0.0,
    trim_end: float = 0.0,
) -> tuple[float, float]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(input_path)
    applied_trim_start = max(trim_start, 0.0)
    applied_trim_end = max(trim_end, 0.0)
    clipped_duration = max(duration - applied_trim_start - applied_trim_end, 0.1)
    if applied_trim_start <= 0.0 and applied_trim_end <= 0.0:
        if input_path != output_path:
            shutil.copyfile(input_path, output_path)
        return 0.0, 0.0

    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{applied_trim_start:.3f}",
            "-i",
            str(input_path),
            "-t",
            f"{clipped_duration:.3f}",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(output_path),
        ]
    )
    return round(applied_trim_start, 3), round(applied_trim_end, 3)


def trim_outer_silence(
    input_path: Path,
    output_path: Path,
    *,
    leading_padding: float = 0.015,
    trailing_padding: float = 0.05,
    min_leading_trim: float = 0.03,
    min_trailing_trim: float = 0.16,
    threshold_ratio: float = 0.05,
    min_threshold: float = 0.0025,
) -> tuple[float, float]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(input_path)
    leading_silence, trailing_silence = detect_outer_silence_offsets(
        input_path,
        threshold_ratio=threshold_ratio,
        min_threshold=min_threshold,
    )
    trim_start = max(leading_silence - leading_padding, 0.0) if leading_silence >= min_leading_trim else 0.0
    trim_end = max(trailing_silence - trailing_padding, 0.0) if trailing_silence >= min_trailing_trim else 0.0
    _ = duration
    return trim_audio_edges(
        input_path,
        output_path,
        trim_start=trim_start,
        trim_end=trim_end,
    )


def smooth_segment_edges(
    input_path: Path,
    output_path: Path,
    *,
    fade_in: float = 0.012,
    fade_out: float = 0.016,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(input_path)
    if duration <= 0.06:
        if input_path != output_path:
            shutil.copyfile(input_path, output_path)
        return
    applied_fade_in = max(0.0, min(fade_in, duration / 3))
    applied_fade_out = max(0.0, min(fade_out, duration / 3))
    filters: list[str] = []
    if applied_fade_in > 0.0:
        filters.append(f"afade=t=in:st=0:d={applied_fade_in:.3f}")
    if applied_fade_out > 0.0 and duration - applied_fade_out > 0.0:
        filters.append(f"afade=t=out:st={max(duration - applied_fade_out, 0.0):.3f}:d={applied_fade_out:.3f}")
    if not filters:
        if input_path != output_path:
            shutil.copyfile(input_path, output_path)
        return
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-af",
            ",".join(filters),
            "-ar",
            "24000",
            "-ac",
            "1",
            str(output_path),
        ]
    )


def detect_boundary_attack_metrics(
    audio_path: Path,
    *,
    early_window: float = 0.03,
    head_window: float = 0.12,
) -> tuple[float, float]:
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return 0.0, 0.0

    samples, sample_rate = sf.read(audio_path)
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    if len(samples) == 0 or sample_rate <= 0:
        return 0.0, 0.0
    early_count = min(len(samples), max(int(sample_rate * early_window), 1))
    head_count = min(len(samples), max(int(sample_rate * head_window), 1))
    early_peak = float(np.max(np.abs(samples[:early_count]))) if early_count else 0.0
    head_rms = float(np.sqrt(np.mean(np.square(samples[:head_count])))) if head_count else 0.0
    return early_peak, head_rms


def detect_audio_distortion_metrics(
    audio_path: Path,
    *,
    clip_threshold: float = 0.995,
    burst_window: float = 0.05,
) -> dict[str, float]:
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return {
            "peak_abs": 0.0,
            "clipped_ratio": 0.0,
            "burst_rms": 0.0,
        }

    samples, sample_rate = sf.read(audio_path)
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    if len(samples) == 0 or sample_rate <= 0:
        return {
            "peak_abs": 0.0,
            "clipped_ratio": 0.0,
            "burst_rms": 0.0,
        }

    samples = np.asarray(samples, dtype=np.float32)
    peak_abs = float(np.max(np.abs(samples)))
    clipped_ratio = float(np.mean(np.abs(samples) >= clip_threshold))
    burst_size = min(len(samples), max(int(sample_rate * burst_window), 1))
    burst_rms = 0.0
    if burst_size > 0:
        for offset in range(0, len(samples), burst_size):
            window = samples[offset : offset + burst_size]
            if len(window) == 0:
                continue
            rms = float(np.sqrt(np.mean(np.square(window))))
            burst_rms = max(burst_rms, rms)
    return {
        "peak_abs": peak_abs,
        "clipped_ratio": clipped_ratio,
        "burst_rms": burst_rms,
    }


def attenuate_audio_if_clipped(
    audio_path: Path,
    output_path: Path,
    *,
    trigger_peak: float = 0.992,
    trigger_ratio: float = 0.00005,
    target_peak: float = 0.97,
) -> bool:
    metrics = detect_audio_distortion_metrics(audio_path)
    peak_abs = float(metrics.get("peak_abs") or 0.0)
    clipped_ratio = float(metrics.get("clipped_ratio") or 0.0)
    if peak_abs < trigger_peak and clipped_ratio < trigger_ratio:
        output_path.unlink(missing_ok=True)
        return False

    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        output_path.unlink(missing_ok=True)
        return False

    samples, sample_rate = sf.read(audio_path, dtype="float32")
    if len(samples) == 0 or sample_rate <= 0 or peak_abs <= 0.0:
        output_path.unlink(missing_ok=True)
        return False

    gain = min(float(target_peak) / peak_abs, 1.0)
    if gain >= 0.999:
        output_path.unlink(missing_ok=True)
        return False

    adjusted = np.asarray(samples, dtype=np.float32) * gain
    sf.write(output_path, adjusted, sample_rate)
    return True


def detect_gap_context_artifact(
    audio_path: Path,
    *,
    leading_gap: float = 0.0,
    trailing_gap: float = 0.0,
    early_peak_threshold: float = 0.08,
    head_rms_threshold: float = 0.03,
) -> bool:
    if leading_gap < 0.8 and trailing_gap < 0.8:
        return False
    early_peak, head_rms = detect_boundary_attack_metrics(audio_path)
    return early_peak > early_peak_threshold or head_rms > head_rms_threshold


def _contextual_fade_duration(gap: float, *, base: float, medium: float, large: float, extreme: float) -> float:
    if gap >= 1.2:
        return extreme
    if gap >= 1.0:
        return large
    if gap >= 0.8:
        return medium
    return base


def smooth_segment_edges_for_gap_context(
    input_path: Path,
    output_path: Path,
    *,
    leading_gap: float = 0.0,
    trailing_gap: float = 0.0,
) -> tuple[float, float]:
    duration = ffprobe_duration(input_path)
    if duration <= 0.06:
        if input_path != output_path:
            shutil.copyfile(input_path, output_path)
        return 0.0, 0.0

    fade_in = _contextual_fade_duration(
        leading_gap,
        base=0.012,
        medium=0.09,
        large=0.12,
        extreme=0.18,
    )
    fade_out = _contextual_fade_duration(
        trailing_gap,
        base=0.016,
        medium=0.07,
        large=0.10,
        extreme=0.12,
    )
    smooth_segment_edges(
        input_path,
        output_path,
        fade_in=fade_in,
        fade_out=fade_out,
    )
    return fade_in, fade_out


def stabilize_gap_context_boundary(
    input_path: Path,
    output_path: Path,
    *,
    leading_gap: float = 0.0,
    trailing_gap: float = 0.0,
    second_pass_head_rms_threshold: float = 0.12,
) -> tuple[float, float, bool]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    first_pass = output_path.with_name(f"{output_path.stem}.pass1.wav")
    fade_in, fade_out = smooth_segment_edges_for_gap_context(
        input_path,
        first_pass,
        leading_gap=leading_gap,
        trailing_gap=trailing_gap,
    )
    _, head_rms = detect_boundary_attack_metrics(first_pass)
    if leading_gap >= 0.8 and head_rms > second_pass_head_rms_threshold:
        second_pass_fade = 0.24 if leading_gap >= 1.0 else 0.18
        smooth_segment_edges(
            first_pass,
            output_path,
            fade_in=second_pass_fade,
            fade_out=max(fade_out, 0.016),
        )
        first_pass.unlink(missing_ok=True)
        return fade_in, max(fade_out, 0.016), True

    if first_pass != output_path:
        shutil.move(str(first_pass), str(output_path))
    return fade_in, fade_out, False


def align_cut_time_to_pause(
    audio_path: Path,
    desired_seconds: float,
    *,
    search_forward_seconds: float = 8.0,
    noise: str = "-30dB",
    min_silence: float = 0.35,
) -> float:
    duration = ffprobe_duration(audio_path)
    target = max(0.0, min(desired_seconds, duration))
    for silence_start, silence_end in detect_silence_spans(audio_path, noise=noise, min_silence=min_silence):
        if silence_start <= target <= silence_end:
            return round(target, 3)
        if silence_start >= target and silence_start - target <= max(search_forward_seconds, 0.0):
            return round(min(silence_start, duration), 3)
    return round(target, 3)


def detect_speech_windows(
    audio_path: Path,
    duration: float,
    *,
    target_duration: float = 10.0,
    max_duration: float = 18.0,
    min_duration: float = 2.0,
    noise: str = "-30dB",
    min_silence: float = 0.45,
) -> list[tuple[float, float]]:
    silence_spans = detect_silence_spans(audio_path, noise=noise, min_silence=min_silence)
    speech_windows: list[tuple[float, float]] = []
    cursor = 0.0
    for silence_start, silence_end in silence_spans:
        if silence_start - cursor >= min_duration:
            speech_windows.append((cursor, silence_start))
        cursor = max(cursor, silence_end)
    if duration - cursor >= min_duration:
        speech_windows.append((cursor, duration))

    normalized = _normalize_windows(
        speech_windows,
        duration,
        target_duration=target_duration,
        max_duration=max_duration,
        min_duration=min_duration,
    )
    if normalized:
        return normalized
    return _fallback_windows(
        duration,
        target_duration=target_duration,
        max_duration=max_duration,
        min_duration=min_duration,
    )


def _normalize_windows(
    windows: list[tuple[float, float]],
    duration: float,
    *,
    target_duration: float,
    max_duration: float,
    min_duration: float,
) -> list[tuple[float, float]]:
    if not windows:
        return []
    normalized: list[tuple[float, float]] = []
    for start, end in windows:
        window_duration = end - start
        if window_duration < min_duration:
            continue
        if window_duration <= max_duration:
            normalized.append((round(start, 3), round(end, 3)))
            continue
        parts = max(2, math.ceil(window_duration / target_duration))
        step = window_duration / parts
        current = start
        for index in range(parts):
            next_end = end if index == parts - 1 else current + step
            normalized.append((round(current, 3), round(next_end, 3)))
            current = next_end
    if not normalized:
        return []
    normalized.sort(key=lambda item: item[0])
    cleaned: list[tuple[float, float]] = []
    for start, end in normalized:
        start = max(0.0, min(start, duration))
        end = max(start, min(end, duration))
        if end - start < min_duration:
            continue
        if cleaned and start - cleaned[-1][1] < 0.05:
            prev_start, _ = cleaned[-1]
            cleaned[-1] = (prev_start, end)
        else:
            cleaned.append((start, end))
    return cleaned


def _fallback_windows(
    duration: float,
    *,
    target_duration: float,
    max_duration: float,
    min_duration: float,
) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    parts = max(1, math.ceil(duration / target_duration))
    step = min(max_duration, duration / parts if parts else duration)
    windows: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration - 0.05:
        end = min(duration, cursor + step)
        if end - cursor >= min_duration:
            windows.append((round(cursor, 3), round(end, 3)))
        cursor = end
    return windows


def make_demo_video(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000",
            "-t",
            "18",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(output_path),
        ]
    )


def say_available() -> bool:
    return shutil.which("say") is not None
