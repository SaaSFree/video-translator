from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import shutil
import tempfile
import traceback
from pathlib import Path

from .media import (
    attenuate_audio_if_clipped,
    concat_audio,
    extract_audio,
    extract_audio_clip,
    ffprobe_duration,
    make_silence,
    mux_video_with_audio,
    slot_audio,
    smooth_segment_edges,
    trim_outer_silence,
    trim_segment_boundary_artifacts,
)
from .models import JobState, Segment, SegmentDocument, TranscriptDocument
from .providers import get_reviewer, get_synthesizer, get_transcriber, get_translator
from .segmentation import fallback_source_segments
from .source_review import accept_all_source_corrections, pending_source_correction_count, run_source_correction_review
from .storage import (
    _load_raw_state,
    append_event,
    clear_runtime,
    clear_target_outputs,
    load_manifest,
    load_merged_target_segments,
    load_runtime,
    load_source_correction_review,
    load_source_segments,
    mark_job_interrupted,
    project_dir,
    project_paths,
    save_manifest,
    save_source_segments,
    save_source_target_snapshot,
    save_state,
    save_target_aligned_segments,
    save_target_draft_segments,
    touch_runtime,
)
from .subtitles import to_srt
from .utils import atomic_write_text, now_iso

_PREVIEW_SUBTITLE_PROGRESS: dict[str, int] = {}


def _clamp_ratio(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(float(value), 1.0))


def _elapsed_seconds(started_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        timestamp = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return max((datetime.now(timezone.utc) - timestamp).total_seconds(), 0.0)


def update_job(
    project_id: str,
    *,
    stage: str,
    progress: int | None = None,
    message: str,
    running: bool = True,
    error: str | None = None,
    error_detail: str | None = None,
    overall_ratio: float | None = None,
    stage_ratio: float | None = None,
    step_index: int | None = None,
    step_total: int | None = None,
    step_label: str | None = None,
    items_completed: float | None = None,
    items_total: float | None = None,
) -> None:
    previous_state = _load_raw_state(project_id)
    runtime = touch_runtime(project_id, worker_pid=os.getpid()) if running else load_runtime(project_id)
    overall = _clamp_ratio(overall_ratio if overall_ratio is not None else ((progress or 0) / 100.0))
    stage_progress = _clamp_ratio(stage_ratio if stage_ratio is not None else overall)
    progress_value = max(0, min(progress if progress is not None else round(overall * 100), 100))
    elapsed = _elapsed_seconds(runtime.started_at)
    eta = None
    if running and elapsed is not None and 0.0 < overall < 1.0:
        eta = max((elapsed / max(overall, 0.001)) - elapsed, 0.0)
    elif overall >= 1.0:
        eta = 0.0

    save_state(
        project_id,
        JobState(
            running=running,
            stage=stage,
            progress=progress_value,
            overall_ratio=round(overall, 4),
            stage_ratio=round(stage_progress, 4),
            message=message,
            error=error,
            error_detail=error_detail,
            updated_at=now_iso(),
            elapsed_seconds=round(elapsed, 1) if elapsed is not None else None,
            eta_seconds=round(eta, 1) if eta is not None else None,
            step_index=step_index,
            step_total=step_total,
            step_label=step_label,
            items_completed=(
                round(float(items_completed), 3)
                if items_completed is not None
                else previous_state.items_completed if not running else None
            ),
            items_total=(
                round(float(items_total), 3)
                if items_total is not None
                else previous_state.items_total if not running else None
            ),
            worker_pid=runtime.worker_pid if running else None,
            heartbeat_at=runtime.heartbeat_at if running else None,
        ),
    )
    if not running:
        clear_runtime(project_id)
    append_event(
        project_id,
        {
            "at": now_iso(),
            "stage": stage,
            "progress": progress_value,
            "overall_ratio": round(overall, 4),
            "stage_ratio": round(stage_progress, 4),
            "message": message,
            "step_index": step_index or 0,
            "step_total": step_total or 0,
            "step_label": step_label or "",
        },
    )


class PipelineProgressReporter:
    def __init__(self, project_id: str, steps: list[tuple[str, str]]) -> None:
        self.project_id = project_id
        self.steps = list(steps)
        self.step_total = max(len(self.steps), 1)
        self.step_index_by_stage = {stage: index for index, (stage, _label) in enumerate(self.steps)}
        self.last_overall_ratio = 0.0

    def _step_position(self, stage: str) -> tuple[int, str]:
        index = self.step_index_by_stage.get(stage, 0)
        label = self.steps[index][1] if index < len(self.steps) else stage
        return index, label

    def _overall_ratio(self, stage: str, stage_ratio: float) -> float:
        index, _ = self._step_position(stage)
        overall = (index + _clamp_ratio(stage_ratio)) / max(self.step_total, 1)
        self.last_overall_ratio = max(self.last_overall_ratio, overall)
        return overall

    def start(self, stage: str, message: str, *, step_label: str | None = None) -> None:
        index, default_label = self._step_position(stage)
        update_job(
            self.project_id,
            stage=stage,
            message=message,
            overall_ratio=index / max(self.step_total, 1),
            stage_ratio=0.0,
            step_index=index + 1,
            step_total=self.step_total,
            step_label=step_label or default_label,
            items_completed=0.0,
        )

    def update(self, stage: str, completed: float, total: float, message: str, *, step_label: str | None = None) -> None:
        index, default_label = self._step_position(stage)
        ratio = 1.0 if total <= 0 else _clamp_ratio(completed / total)
        update_job(
            self.project_id,
            stage=stage,
            message=message,
            overall_ratio=self._overall_ratio(stage, ratio),
            stage_ratio=ratio,
            step_index=index + 1,
            step_total=self.step_total,
            step_label=step_label or default_label,
            items_completed=completed,
            items_total=total,
        )

    def complete(self, stage: str, message: str, *, step_label: str | None = None) -> None:
        self.update(stage, 1.0, 1.0, message, step_label=step_label)

    def finish(
        self,
        *,
        message: str,
        success: bool,
        error: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        update_job(
            self.project_id,
            stage="complete" if success else "error",
            message=message,
            running=False,
            error=error,
            error_detail=error_detail,
            overall_ratio=1.0 if success else max(self.last_overall_ratio, 0.01),
            stage_ratio=1.0,
            step_index=self.step_total,
            step_total=self.step_total,
            step_label=self.steps[-1][1] if self.steps else None,
        )


def _make_stage_progress_updater(
    reporter: PipelineProgressReporter,
    *,
    stage: str,
    message: str,
    step_label: str | None = None,
    substep_index: int = 0,
    substep_total: int = 1,
):
    last_ratio = -1.0
    last_message: str | None = None

    def callback(completed: int | float, total: int | float, message_override: str | None = None) -> None:
        nonlocal last_ratio, last_message
        ratio = 1.0 if total <= 0 else _clamp_ratio(float(completed) / float(total))
        stage_completed = min(max(substep_index + ratio, 0.0), max(float(substep_total), 1.0))
        next_message = message_override or message
        if abs(stage_completed - last_ratio) < 1e-6 and next_message == last_message:
            return
        last_ratio = stage_completed
        last_message = next_message
        reporter.update(stage, stage_completed, float(max(substep_total, 1)), next_message, step_label=step_label)

    return callback


def _normalize_segments(segments: list[Segment]) -> list[Segment]:
    return [
        segment.model_copy(update={"id": f"seg-{index + 1:04}", "index": index})
        for index, segment in enumerate(segments)
    ]


def _persist_segment_preview(project_id: str, segments: list[Segment], *, force_subtitles: bool = False) -> None:
    normalized = _normalize_segments(segments)
    document = SegmentDocument(segments=normalized)
    save_source_segments(project_id, document)
    ready_count = sum(1 for segment in normalized if str(segment.status or "").strip().lower() == "ready")
    previous_ready_count = _PREVIEW_SUBTITLE_PROGRESS.get(project_id, -1)
    should_write_subtitles = force_subtitles or ready_count <= 3 or ready_count == len(normalized)
    if not should_write_subtitles and ready_count > previous_ready_count and ready_count % 5 == 0:
        should_write_subtitles = True
    if not should_write_subtitles:
        return
    _PREVIEW_SUBTITLE_PROGRESS[project_id] = ready_count
    atomic_write_text(project_paths(project_id)["source_srt"], to_srt(document))


def _validate_source_timing_integrity(source_segments: list[Segment], *, context: str) -> None:
    previous_end = 0.0
    for segment in source_segments:
        if segment.end <= segment.start:
            raise RuntimeError(f"{context} produced invalid segment duration: {segment.id}")
        if segment.start < previous_end - 0.02:
            raise RuntimeError(f"{context} produced overlapping source timing around {segment.id}")
        previous_end = segment.end


def _source_clip_extraction_window(
    source_segments: list[Segment],
    *,
    index: int,
    total_duration: float,
    leading_context: float = 0.12,
    trailing_context: float = 0.18,
) -> tuple[float, float]:
    segment = source_segments[index]
    previous_end = source_segments[index - 1].end if index > 0 else 0.0
    next_start = source_segments[index + 1].start if index + 1 < len(source_segments) else total_duration
    available_leading = max(segment.start - previous_end, 0.0)
    available_trailing = max(next_start - segment.end, 0.0)
    clip_start = max(previous_end, segment.start - min(leading_context, available_leading))
    clip_end = min(next_start, segment.end + min(trailing_context, available_trailing))
    if clip_end - clip_start < max(segment.end - segment.start, 0.3):
        clip_start = max(0.0, segment.start)
        clip_end = min(total_duration, max(segment.end, clip_start + 0.3))
    return round(clip_start, 3), round(clip_end, 3)


def _materialize_source_segment_audio(
    *,
    base_dir: Path,
    source_audio: Path,
    source_segments: list[Segment],
    progress_callback=None,
    save_callback=None,
) -> list[Segment]:
    playback_dir = base_dir / "voices" / "source-segments"
    reference_dir = base_dir / "voices" / "source-reference-segments"
    for directory in (playback_dir, reference_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    total_duration = ffprobe_duration(source_audio)
    materialized: list[Segment] = []
    total_segments = len(source_segments)
    for index, segment in enumerate(source_segments):
        clip_path = playback_dir / f"{segment.id}.wav"
        extract_audio_clip(
            source_audio,
            clip_path,
            start_seconds=max(segment.start, 0.0),
            duration_seconds=max(segment.end - segment.start, 0.12),
            sample_rate=16000,
        )
        reference_path = reference_dir / f"{segment.id}.wav"
        temp_reference_path = reference_dir / f"{segment.id}.raw.wav"
        trimmed_reference_path = reference_dir / f"{segment.id}.trimmed.wav"
        clip_start, clip_end = _source_clip_extraction_window(
            source_segments,
            index=index,
            total_duration=total_duration,
        )
        extract_audio_clip(
            source_audio,
            temp_reference_path,
            start_seconds=clip_start,
            duration_seconds=max(clip_end - clip_start, 0.3),
            sample_rate=16000,
        )
        trim_outer_silence(
            temp_reference_path,
            trimmed_reference_path,
            leading_padding=0.01,
            trailing_padding=0.03,
            min_leading_trim=0.012,
            min_trailing_trim=0.05,
        )
        smooth_segment_edges(
            trimmed_reference_path,
            reference_path,
            fade_in=0.014,
            fade_out=0.022,
        )
        temp_reference_path.unlink(missing_ok=True)
        trimmed_reference_path.unlink(missing_ok=True)
        materialized.append(
            segment.model_copy(
                update={
                    "audio_path": clip_path.relative_to(base_dir).as_posix(),
                    "reference_audio_path": reference_path.relative_to(base_dir).as_posix(),
                }
            )
        )
        if save_callback:
            save_callback(materialized + source_segments[index + 1 :])
        if progress_callback:
            progress_callback(index + 1, total_segments, f"Preparing source clips ({index + 1}/{total_segments}).")
    return materialized


def _run_source_review_stage(
    *,
    project_id: str,
    manifest,
    source_document: SegmentDocument,
    reporter: PipelineProgressReporter | None = None,
) -> tuple[object, int]:
    progress_callback = None
    if reporter:
        reporter.start("reviewing", "Reviewing source subtitles with Codex.", step_label="Reviewing source subtitles with Codex.")
        progress_callback = _make_stage_progress_updater(
            reporter,
            stage="reviewing",
            message="Reviewing source subtitles with Codex.",
            step_label="Reviewing source subtitles with Codex.",
        )
    review = run_source_correction_review(
        project_id,
        source_document=source_document,
        source_language=manifest.source_language,
        progress_callback=progress_callback,
    )
    pending_count = pending_source_correction_count(review)
    if reporter:
        final_message = (
            f"Source corrections are ready for review ({pending_count} pending)."
            if pending_count > 0
            else "Source correction review finished."
        )
        reporter.complete("reviewing", final_message, step_label="Reviewing source subtitles with Codex.")
    return review, pending_count


def _persist_target_preview(project_id: str, segments: list[Segment], *, aligned: bool) -> None:
    document = SegmentDocument(segments=segments)
    if aligned:
        save_target_aligned_segments(project_id, document)
        atomic_write_text(project_paths(project_id)["target_srt"], to_srt(document))
    else:
        save_target_draft_segments(project_id, document)
        atomic_write_text(project_paths(project_id)["target_draft_srt"], to_srt(document))


def _full_source_text(document: SegmentDocument) -> str:
    return "\n".join(segment.text.strip() for segment in document.segments if segment.text.strip()).strip()


def _normalize_target_text_for_synthesis(text: str, *, language: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    if language.strip().lower() == "english":
        value = re.sub(r"\b24\s*/\s*7\b", "twenty-four seven", value)
        value = re.sub(r"\bAI\b", "A I", value)
        value = re.sub(r"\bAPI\b", "A P I", value)
    return value


def _postprocess_target_draft_audio(raw_output_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir = output_path.parent / f".{output_path.stem}.postprocess"
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    current_path = raw_output_path
    boundary_trimmed_path = scratch_dir / "boundary-trimmed.wav"
    trim_segment_boundary_artifacts(current_path, boundary_trimmed_path)
    current_path = boundary_trimmed_path

    silence_trimmed_path = scratch_dir / "silence-trimmed.wav"
    trim_outer_silence(
        current_path,
        silence_trimmed_path,
        leading_padding=0.01,
        trailing_padding=0.075,
        min_leading_trim=0.025,
        min_trailing_trim=0.12,
        threshold_ratio=0.018,
        min_threshold=0.0012,
    )
    current_path = silence_trimmed_path

    smoothed_path = scratch_dir / "smoothed.wav"
    smooth_segment_edges(current_path, smoothed_path, fade_in=0.008, fade_out=0.012)
    current_path = smoothed_path

    clipped_path = scratch_dir / "attenuated.wav"
    if attenuate_audio_if_clipped(current_path, clipped_path):
        current_path = clipped_path

    shutil.move(str(current_path), str(output_path))
    shutil.rmtree(scratch_dir, ignore_errors=True)
    raw_output_path.unlink(missing_ok=True)


def _build_target_reference_document(*, base_dir: Path, source_document: SegmentDocument) -> SegmentDocument:
    reference_segments: list[Segment] = []
    for source_segment in source_document.segments:
        reference_text = source_segment.text.strip()
        if not reference_text:
            raise RuntimeError(f"Missing corrected source text for {source_segment.id}.")
        reference_audio_path = source_segment.reference_audio_path or source_segment.audio_path
        if not reference_audio_path:
            raise RuntimeError(f"Missing source segment audio for {source_segment.id}.")
        reference_audio = base_dir / reference_audio_path
        if not reference_audio.exists():
            raise RuntimeError(f"Missing source segment audio file for {source_segment.id}: {reference_audio}")
        reference_segments.append(
            source_segment.model_copy(
                update={
                    "text": reference_text,
                    "audio_path": reference_audio_path,
                    "reference_audio_path": reference_audio_path,
                    "status": "reference",
                }
            )
        )
    return SegmentDocument(version=source_document.version, segments=reference_segments)


def _recover_gap_segments(
    *,
    source_audio: Path,
    left_segment: Segment,
    right_segment: Segment,
    transcriber,
    language: str,
    window_lead: float = 0.8,
    window_trail: float = 0.8,
    min_gap_seconds: float = 1.6,
    max_gap_seconds: float = 14.0,
    boundary_margin: float = 0.1,
) -> list[Segment]:
    gap_duration = max(float(right_segment.start) - float(left_segment.end), 0.0)
    if gap_duration < min_gap_seconds or gap_duration > max_gap_seconds:
        return []

    window_start = max(0.0, float(left_segment.end) - window_lead)
    window_end = max(window_start + 0.6, float(right_segment.start) + window_trail)
    window_duration = max(window_end - window_start, 0.6)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        clip_path = Path(handle.name)
    try:
        extract_audio_clip(
            source_audio,
            clip_path,
            start_seconds=window_start,
            duration_seconds=window_duration,
            sample_rate=16000,
        )
        transcript = transcriber.transcribe_full(
            clip_path,
            window_duration,
            language=language,
        )
    finally:
        clip_path.unlink(missing_ok=True)

    gap_items = []
    gap_start = float(left_segment.end) + boundary_margin
    gap_end = float(right_segment.start) - boundary_margin
    for item in transcript.items:
        absolute_start = round(float(item.start) + window_start, 3)
        absolute_end = round(float(item.end) + window_start, 3)
        if absolute_end <= gap_start or absolute_start >= gap_end:
            continue
        absolute_start = max(absolute_start, gap_start)
        absolute_end = min(absolute_end, gap_end)
        if absolute_end <= absolute_start:
            continue
        gap_items.append(
            item.model_copy(
                update={
                    "index": len(gap_items),
                    "start": absolute_start,
                    "end": absolute_end,
                }
            )
        )
    if not gap_items:
        return []

    recovered = fallback_source_segments(TranscriptDocument(language=transcript.language, text="", items=gap_items))
    repaired: list[Segment] = []
    for segment in recovered:
        text = str(segment.text or "").strip()
        if not text:
            continue
        if segment.end - segment.start < 0.25:
            continue
        repaired.append(
            segment.model_copy(
                update={
                    "text": text,
                    "status": "recovered",
                }
            )
        )
    return repaired


def _repair_source_gap_segments(
    *,
    source_audio: Path,
    source_segments: list[Segment],
    transcriber,
    language: str,
) -> list[Segment]:
    if len(source_segments) < 2:
        return source_segments
    repaired: list[Segment] = []
    for index, segment in enumerate(source_segments[:-1]):
        repaired.append(segment)
        next_segment = source_segments[index + 1]
        recovered = _recover_gap_segments(
            source_audio=source_audio,
            left_segment=segment,
            right_segment=next_segment,
            transcriber=transcriber,
            language=language,
        )
        if recovered:
            repaired.extend(recovered)
    repaired.append(source_segments[-1])
    return _normalize_segments(repaired)


def _translate_and_synthesize_target_segment(
    *,
    base_dir: Path,
    source_segment: Segment,
    reference_segment: Segment,
    source_language: str,
    target_language: str,
    full_source_text: str,
    translator,
    synthesizer,
    output_dir: Path,
    target_text_override: str | None = None,
    allow_retime: bool = True,
) -> Segment:
    if target_text_override is None:
        target_text = translator.translate_segment(
            source_segment,
            source_language=source_language,
            target_language=target_language,
            full_transcript_text=full_source_text,
        )
    else:
        target_text = target_text_override
    source_duration = max(float(source_segment.end) - float(source_segment.start), 0.3)
    if not reference_segment.audio_path:
        raise RuntimeError(f"Missing reference audio path for {reference_segment.id}.")
    reference_audio = base_dir / reference_segment.audio_path
    if not reference_audio.exists():
        raise RuntimeError(f"Missing reference audio file for {reference_segment.id}: {reference_audio}")
    output_path = output_dir / f"{source_segment.id}.wav"
    raw_output_path = output_dir / f"{source_segment.id}.raw.wav"
    synthesis_text = _normalize_target_text_for_synthesis(target_text, language=target_language)
    synthesizer.synthesize(
        text=synthesis_text,
        output_path=raw_output_path,
        reference_audio=reference_audio,
        reference_text=reference_segment.text,
        language=target_language,
    )
    _postprocess_target_draft_audio(raw_output_path, output_path)
    synthesized_duration = ffprobe_duration(output_path)
    if synthesized_duration > source_duration * 1.15 and allow_retime and translator is not None:
        for attempt_index in range(2):
            target_text = translator.retime_translation(
                source_segment,
                source_language=source_language,
                target_language=target_language,
                full_transcript_text=full_source_text,
                current_translation=target_text,
                slot_duration=source_duration,
                synthesized_duration=synthesized_duration,
                attempt_index=attempt_index,
            )
            synthesizer.synthesize(
                text=_normalize_target_text_for_synthesis(target_text, language=target_language),
                output_path=raw_output_path,
                reference_audio=reference_audio,
                reference_text=reference_segment.text,
                language=target_language,
            )
            _postprocess_target_draft_audio(raw_output_path, output_path)
            synthesized_duration = ffprobe_duration(output_path)
            if synthesized_duration <= source_duration * 1.15:
                break
    return source_segment.model_copy(
        update={
            "text": target_text,
            "source_text": source_segment.text,
            "status": "ready",
            "audio_path": output_path.relative_to(base_dir).as_posix(),
        }
    )


def _resolve_existing_target_texts(project_id: str, source_document: SegmentDocument) -> dict[str, str]:
    current_target = load_merged_target_segments(project_id)
    if not current_target.segments:
        raise RuntimeError("No target text is available yet.")
    target_by_id = {segment.id: segment for segment in current_target.segments}
    resolved: dict[str, str] = {}
    for source_segment in source_document.segments:
        candidate = target_by_id.get(source_segment.id)
        if not candidate or not candidate.text.strip():
            raise RuntimeError(f"Missing target text for {source_segment.id}.")
        resolved[source_segment.id] = candidate.text.strip()
    return resolved


def _build_target_track(
    *,
    base_dir: Path,
    segments: list[Segment],
    total_duration: float,
) -> Path:
    output_path = base_dir / "voices" / "target-track.v1.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir = base_dir / "voices" / ".target-track-build"
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    cursor = 0.0
    for segment in segments:
        if segment.start > cursor + 0.01:
            silence_path = scratch_dir / f"gap-{len(parts):04}.wav"
            make_silence(silence_path, max(segment.start - cursor, 0.01))
            parts.append(silence_path)
        if not segment.audio_path:
            continue
        parts.append(base_dir / segment.audio_path)
        cursor = float(segment.end)
    if total_duration > cursor + 0.01:
        silence_path = scratch_dir / f"tail-{len(parts):04}.wav"
        make_silence(silence_path, max(total_duration - cursor, 0.01))
        parts.append(silence_path)
    if not parts:
        make_silence(output_path, total_duration)
    else:
        concat_audio(parts, output_path)
    shutil.rmtree(scratch_dir, ignore_errors=True)
    return output_path


def _solve_bounded_isotonic_offsets(
    preferred: list[float],
    lower_bounds: list[float],
    upper_bounds: list[float],
    weights: list[float],
) -> list[float]:
    if not (len(preferred) == len(lower_bounds) == len(upper_bounds) == len(weights)):
        raise RuntimeError("Bounded isotonic inputs must have matching lengths.")
    if not preferred:
        return []

    blocks: list[dict[str, float | int]] = []
    for index, (target, lower, upper, weight) in enumerate(zip(preferred, lower_bounds, upper_bounds, weights, strict=True)):
        lower_value = float(lower)
        upper_value = float(upper)
        if lower_value > upper_value + 1e-6:
            raise RuntimeError("Target timing constraints are infeasible.")
        effective_weight = max(float(weight), 0.001)
        blocks.append(
            {
                "start": index,
                "end": index,
                "weight": effective_weight,
                "weighted_target": float(target) * effective_weight,
                "lower": lower_value,
                "upper": upper_value,
                "value": min(max(float(target), lower_value), upper_value),
            }
        )
        while len(blocks) >= 2 and float(blocks[-2]["value"]) > float(blocks[-1]["value"]) + 1e-9:
            right = blocks.pop()
            left = blocks.pop()
            merged_lower = max(float(left["lower"]), float(right["lower"]))
            merged_upper = min(float(left["upper"]), float(right["upper"]))
            if merged_lower > merged_upper + 1e-6:
                raise RuntimeError("Unable to distribute target timing across the full timeline.")
            merged_weight = float(left["weight"]) + float(right["weight"])
            merged_weighted_target = float(left["weighted_target"]) + float(right["weighted_target"])
            merged_target = merged_weighted_target / merged_weight
            blocks.append(
                {
                    "start": int(left["start"]),
                    "end": int(right["end"]),
                    "weight": merged_weight,
                    "weighted_target": merged_weighted_target,
                    "lower": merged_lower,
                    "upper": merged_upper,
                    "value": min(max(merged_target, merged_lower), merged_upper),
                }
            )

    solved = [0.0 for _ in preferred]
    for block in blocks:
        value = float(block["value"])
        for index in range(int(block["start"]), int(block["end"]) + 1):
            solved[index] = value
    return solved


def _terminal_pause_marker(text: str) -> str:
    value = re.sub(r"[\s\"'”’)\]】》」』]+$", "", str(text or "").strip())
    return value[-1:] if value else ""


def _classify_target_boundary(left_segment: Segment, right_segment: Segment) -> str:
    source_gap = max(float(right_segment.start) - float(left_segment.end), 0.0)
    terminal = _terminal_pause_marker(left_segment.text)
    if terminal in {".", "!", "?", "。", "！", "？", "…"} or source_gap >= 0.58:
        return "hard_pause"
    if terminal in {",", ";", ":", "，", "；", "：", "、"} or source_gap >= 0.18:
        return "soft_pause"
    return "tight"


def _target_gap_slots(
    source_segments: list[Segment],
    *,
    total_duration: float,
) -> list[dict[str, float | str]]:
    if not source_segments:
        return []

    leading_gap = max(float(source_segments[0].start), 0.0)
    trailing_gap = max(total_duration - float(source_segments[-1].end), 0.0)
    slots: list[dict[str, float | str]] = [
        {
            "kind": "leading",
            "minimum": 0.0,
            "preferred": min(max(leading_gap, 0.0), 0.12),
            "maximum": max(min(max(leading_gap, 0.0), 0.24), 0.08),
            "preferred_priority": 0.2,
            "overflow_priority": 0.05,
        }
    ]

    for left_segment, right_segment in zip(source_segments, source_segments[1:]):
        source_gap = max(float(right_segment.start) - float(left_segment.end), 0.0)
        boundary_kind = _classify_target_boundary(left_segment, right_segment)
        if boundary_kind == "tight":
            slots.append(
                {
                    "kind": boundary_kind,
                    "minimum": 0.0,
                    "preferred": min(max(source_gap, 0.02), 0.08),
                    "maximum": min(max(source_gap, 0.08), 0.16),
                    "preferred_priority": 0.15,
                    "overflow_priority": 0.03,
                }
            )
        elif boundary_kind == "soft_pause":
            slots.append(
                {
                    "kind": boundary_kind,
                    "minimum": 0.02,
                    "preferred": min(max(source_gap, 0.10), 0.24),
                    "maximum": min(max(source_gap, 0.24), 0.42),
                    "preferred_priority": 1.0,
                    "overflow_priority": 0.35,
                }
            )
        else:
            slots.append(
                {
                    "kind": boundary_kind,
                    "minimum": 0.05,
                    "preferred": min(max(source_gap, 0.22), 0.6),
                    "maximum": min(max(source_gap, 0.45), 1.2),
                    "preferred_priority": 1.45,
                    "overflow_priority": 1.0,
                }
            )

    slots.append(
        {
            "kind": "trailing",
            "minimum": 0.0,
            "preferred": min(max(trailing_gap, 0.0), 0.14),
            "maximum": max(min(max(trailing_gap, 0.0), 0.28), 0.08),
            "preferred_priority": 0.2,
            "overflow_priority": 0.2,
        }
    )
    return slots


def _allocate_target_gap_budget(
    total_gap_budget: float,
    slots: list[dict[str, float | str]],
) -> list[float]:
    if not slots:
        return []
    allocated = [float(slot["minimum"]) for slot in slots]
    remaining = max(float(total_gap_budget) - sum(allocated), 0.0)

    def _fill_to(target_key: str, priority_key: str) -> None:
        nonlocal remaining
        for _ in range(8):
            if remaining <= 1e-6:
                return
            weighted_capacities: list[tuple[int, float, float]] = []
            weighted_total = 0.0
            for index, slot in enumerate(slots):
                capacity = max(float(slot[target_key]) - allocated[index], 0.0)
                priority = max(float(slot[priority_key]), 0.0)
                weight = capacity * priority
                if capacity <= 1e-6 or weight <= 1e-6:
                    continue
                weighted_capacities.append((index, capacity, weight))
                weighted_total += weight
            if not weighted_capacities or weighted_total <= 1e-6:
                return

            spent = 0.0
            for index, capacity, weight in weighted_capacities:
                share = remaining * (weight / weighted_total)
                delta = min(capacity, share)
                if delta <= 1e-6:
                    continue
                allocated[index] += delta
                spent += delta
            if spent <= 1e-6:
                return
            remaining = max(remaining - spent, 0.0)

    _fill_to("preferred", "preferred_priority")
    _fill_to("maximum", "overflow_priority")
    if remaining > 1e-6:
        allocated[-1] += remaining
    return [round(value, 3) for value in allocated]


def _plan_target_alignment_windows(
    source_segments: list[Segment],
    draft_durations: list[float],
    *,
    total_duration: float,
    preferred_scale: float = 1.0,
    min_scale: float = 0.35,
    max_scale: float = 1.15,
    overlap_epsilon: float = 0.04,
) -> tuple[list[tuple[float, float]], float]:
    if len(source_segments) != len(draft_durations):
        raise RuntimeError("Draft duration count does not match source segment count.")
    if not source_segments:
        return [], preferred_scale

    minimum_slot = max(overlap_epsilon * 2.0, 0.12)
    gap_slots = _target_gap_slots(source_segments, total_duration=total_duration)

    def _build(scale: float) -> list[tuple[float, float]] | None:
        slot_durations = [max(float(duration) * scale, minimum_slot) for duration in draft_durations]
        total_slot_duration = sum(slot_durations)
        minimum_gap_budget = sum(float(slot["minimum"]) for slot in gap_slots)
        if total_slot_duration + minimum_gap_budget > total_duration + 1e-6:
            return None

        global_slack = max(total_duration - total_slot_duration, 0.0)
        allocated_gaps = _allocate_target_gap_budget(global_slack, gap_slots)
        prefix_durations: list[float] = []
        consumed = 0.0
        for slot_duration in slot_durations:
            prefix_durations.append(consumed)
            consumed += slot_duration

        lower_offsets: list[float] = []
        upper_offsets: list[float] = []
        preferred_offsets: list[float] = []
        weights: list[float] = []
        accumulated_gap = allocated_gaps[0] if allocated_gaps else 0.0
        for segment, slot_duration, prefix in zip(source_segments, slot_durations, prefix_durations, strict=True):
            lower_start = max(0.0, float(segment.start) - slot_duration + overlap_epsilon)
            upper_start = min(max(0.0, float(segment.end) - overlap_epsilon), total_duration - slot_duration)
            gap_driven_start = prefix + accumulated_gap
            source_center_start = ((float(segment.start) + float(segment.end)) / 2.0) - (slot_duration / 2.0)
            preferred_start = (gap_driven_start * 0.72) + (source_center_start * 0.28)
            preferred_start = min(max(preferred_start, lower_start), upper_start)

            lower_offset = max(0.0, lower_start - prefix)
            upper_offset = min(global_slack, upper_start - prefix)
            if lower_offset > upper_offset + 1e-6:
                return None

            lower_offsets.append(lower_offset)
            upper_offsets.append(upper_offset)
            preferred_offsets.append(min(max(preferred_start - prefix, lower_offset), upper_offset))
            weights.append(max(slot_duration, 0.12))
            next_gap_index = len(preferred_offsets)
            if next_gap_index < len(allocated_gaps) - 1:
                accumulated_gap += allocated_gaps[next_gap_index]

        try:
            solved_offsets = _solve_bounded_isotonic_offsets(
                preferred_offsets,
                lower_offsets,
                upper_offsets,
                weights,
            )
        except RuntimeError:
            return None

        planned: list[tuple[float, float]] = []
        previous_end = 0.0
        for prefix, slot_duration, offset in zip(prefix_durations, slot_durations, solved_offsets, strict=True):
            start = prefix + offset
            end = start + slot_duration
            if start + 1e-6 < previous_end:
                return None
            planned.append((round(start, 3), round(end, 3)))
            previous_end = end
        if planned[-1][1] > total_duration + 1e-6:
            return None
        return planned

    total_draft_duration = max(sum(max(float(duration), minimum_slot) for duration in draft_durations), 0.001)
    if total_duration >= total_draft_duration:
        capped_preferred_scale = min(max_scale, max(float(preferred_scale), total_duration / total_draft_duration))
    else:
        capped_preferred_scale = min(1.0, max(float(preferred_scale), min_scale))
    capped_preferred_scale = max(min(capped_preferred_scale, max_scale), min_scale)
    preferred_plan = _build(capped_preferred_scale)
    if preferred_plan is not None:
        return preferred_plan, capped_preferred_scale

    low = max(min_scale, 0.05)
    high = capped_preferred_scale
    best_scale = low
    best_plan = _build(low)
    if best_plan is None:
        raise RuntimeError("Unable to find a feasible target timing allocation.")
    for _ in range(32):
        candidate = (low + high) / 2.0
        candidate_plan = _build(candidate)
        if candidate_plan is None:
            high = candidate
        else:
            best_scale = candidate
            best_plan = candidate_plan
            low = candidate
    return best_plan, round(best_scale, 4)


def _render_target_video(
    *,
    project_id: str,
    source_video: Path,
    target_track: Path,
    progress_callback=None,
) -> Path:
    output_path = project_paths(project_id)["target_video"]
    mux_video_with_audio(
        source_video,
        target_track,
        output_path,
        progress_callback=progress_callback,
        progress_message="Rendering target video.",
    )
    return output_path


def _align_target_segments(
    *,
    project_id: str,
    base_dir: Path,
    source_audio: Path,
    draft_segments: list[Segment],
    progress_callback=None,
) -> list[Segment]:
    aligned_dir = base_dir / "voices" / "target-aligned"
    if aligned_dir.exists():
        shutil.rmtree(aligned_dir)
    aligned_dir.mkdir(parents=True, exist_ok=True)
    draft_durations: list[float] = []
    for segment in draft_segments:
        if not segment.audio_path:
            raise RuntimeError(f"Missing draft audio for {segment.id}.")
        draft_durations.append(ffprobe_duration(base_dir / segment.audio_path))
    total_duration = ffprobe_duration(source_audio)
    planned_windows, selected_scale = _plan_target_alignment_windows(
        draft_segments,
        draft_durations,
        total_duration=total_duration,
    )
    aligned_segments: list[Segment] = []
    total_segments = len(draft_segments)
    for index, (segment, (planned_start, planned_end), draft_duration) in enumerate(
        zip(draft_segments, planned_windows, draft_durations, strict=True)
    ):
        slot_path = aligned_dir / f"{segment.id}.wav"
        raw_slot_path = aligned_dir / f"{segment.id}.raw.wav"
        slot_duration = max(planned_end - planned_start, 0.3)
        slot_audio(
            base_dir / segment.audio_path,
            raw_slot_path,
            slot_duration,
            max_slowdown_ratio=1.15,
        )
        smooth_segment_edges(raw_slot_path, slot_path, fade_in=0.008, fade_out=0.012)
        raw_slot_path.unlink(missing_ok=True)
        aligned_segments.append(
            segment.model_copy(
                update={
                    "source_start": segment.source_start if segment.source_start is not None else segment.start,
                    "source_end": segment.source_end if segment.source_end is not None else segment.end,
                    "start": planned_start,
                    "end": planned_end,
                    "status": "aligned",
                    "audio_path": slot_path.relative_to(base_dir).as_posix(),
                }
            )
        )
        _persist_target_preview(project_id, aligned_segments, aligned=True)
        if progress_callback:
            progress_callback(index + 1, total_segments, f"Aligning target timing ({index + 1}/{total_segments} segments).")
    _build_target_track(base_dir=base_dir, segments=aligned_segments, total_duration=total_duration)
    append_event(
        project_id,
        {
            "type": "target_alignment",
            "selected_scale": selected_scale,
            "total_duration": round(total_duration, 3),
        },
    )
    return aligned_segments


def run_target_pipeline(project_id: str, *, reuse_existing_target_text: bool = False) -> None:
    manifest = load_manifest(project_id)
    paths = project_paths(project_id)
    base_dir = project_dir(project_id)
    source_video = paths["source_video"]
    source_audio = paths["source_audio"]
    source_document = load_source_segments(project_id)
    review = load_source_correction_review(project_id)
    reporter = PipelineProgressReporter(
        project_id,
        [
            ("translating_target", "Translating and synthesizing target audio."),
            ("synthesizing_target", "Re-synthesizing target audio."),
            ("aligning_target", "Aligning target timing."),
            ("rendering_target_video", "Rendering target video."),
        ],
    )
    try:
        if not source_document.segments:
            raise RuntimeError("No source subtitles are available yet.")
        if not source_video.exists():
            raise RuntimeError("Source video not found.")
        if not source_audio.exists():
            raise RuntimeError("Source audio not found.")
        pending_count = pending_source_correction_count(review)
        if pending_count > 0:
            raise RuntimeError(f"Please finish {pending_count} pending source corrections before running target synthesis.")

        existing_translator_provider = manifest.translator_provider
        existing_target_texts = _resolve_existing_target_texts(project_id, source_document) if reuse_existing_target_text else {}

        clear_target_outputs(project_id)
        reference_document = _build_target_reference_document(base_dir=base_dir, source_document=source_document)
        save_source_target_snapshot(project_id, reference_document)
        reference_by_id = {segment.id: segment for segment in reference_document.segments}

        translator = None if reuse_existing_target_text else get_translator()
        synthesizer = get_synthesizer()
        full_source_text = _full_source_text(source_document)
        draft_dir = base_dir / "voices" / "target-draft"
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        draft_dir.mkdir(parents=True, exist_ok=True)

        manifest.translator_provider = translator.name if translator else existing_translator_provider
        manifest.synthesizer_provider = synthesizer.name
        save_manifest(manifest)

        translating_stage = "synthesizing_target" if reuse_existing_target_text else "translating_target"
        translating_message = "Re-synthesizing target audio." if reuse_existing_target_text else "Translating and synthesizing target audio."
        reporter.start(
            translating_stage,
            translating_message,
            step_label=translating_message,
        )
        draft_segments: list[Segment] = []
        total_segments = len(source_document.segments)
        for index, source_segment in enumerate(source_document.segments):
            reference_segment = reference_by_id.get(source_segment.id)
            if reference_segment is None:
                raise RuntimeError(f"Missing reference segment for {source_segment.id}.")
            draft_segment = _translate_and_synthesize_target_segment(
                base_dir=base_dir,
                source_segment=source_segment,
                reference_segment=reference_segment,
                source_language=manifest.source_language,
                target_language=manifest.target_language,
                full_source_text=full_source_text,
                translator=translator,
                synthesizer=synthesizer,
                output_dir=draft_dir,
                target_text_override=existing_target_texts.get(source_segment.id),
                allow_retime=not reuse_existing_target_text,
            )
            draft_segments.append(draft_segment)
            _persist_target_preview(project_id, draft_segments, aligned=False)
            reporter.update(
                translating_stage,
                index + 1,
                total_segments,
                (
                    f"Re-synthesizing target segments ({index + 1}/{total_segments} segments)."
                    if reuse_existing_target_text
                    else f"Translating and synthesizing target segments ({index + 1}/{total_segments} segments)."
                ),
                step_label=translating_message,
            )

        reporter.start("aligning_target", "Aligning target timing.", step_label="Aligning target timing.")
        aligned_segments = _align_target_segments(
            project_id=project_id,
            base_dir=base_dir,
            source_audio=source_audio,
            draft_segments=draft_segments,
            progress_callback=_make_stage_progress_updater(
                reporter,
                stage="aligning_target",
                message="Aligning target timing.",
                step_label="Aligning target timing.",
            ),
        )
        _persist_target_preview(project_id, aligned_segments, aligned=True)

        reporter.start("rendering_target_video", "Rendering target video.", step_label="Rendering target video.")
        _render_target_video(
            project_id=project_id,
            source_video=source_video,
            target_track=paths["target_track"],
            progress_callback=_make_stage_progress_updater(
                reporter,
                stage="rendering_target_video",
                message="Rendering target video.",
                step_label="Rendering target video.",
            ),
        )
        reporter.complete("rendering_target_video", "Target video rendering finished.", step_label="Rendering target video.")

        manifest.status = "target_ready"
        save_manifest(manifest)
        reporter.finish(message="Target translation and synthesis finished.", success=True)
    except Exception as exc:  # noqa: BLE001
        manifest.status = "error"
        save_manifest(manifest)
        reporter.finish(
            message="Target translation and synthesis failed.",
            success=False,
            error=str(exc),
            error_detail=traceback.format_exc(),
        )


def run_source_pipeline(project_id: str) -> None:
    manifest = load_manifest(project_id)
    paths = project_paths(project_id)
    base_dir = project_dir(project_id)
    source_video = paths["source_video"]
    source_audio = paths["source_audio"]
    source_srt = paths["source_srt"]
    reporter = PipelineProgressReporter(
        project_id,
        [
            ("extracting_audio", "Extracting source audio."),
            ("transcribing", "Generating source subtitles."),
            ("reviewing", "Reviewing source subtitles with Codex."),
        ],
    )
    try:
        if not source_video.exists():
            raise RuntimeError("Source video not found.")

        reporter.start("extracting_audio", "Extracting source audio.", step_label="Extracting source audio.")
        extract_audio(
            source_video,
            source_audio,
            progress_callback=_make_stage_progress_updater(
                reporter,
                stage="extracting_audio",
                message="Extracting source audio.",
                step_label="Extracting source audio.",
            ),
            progress_message="Extracting source audio.",
        )

        duration = ffprobe_duration(source_audio)
        reporter.start("transcribing", "Generating source subtitles.", step_label="Generating source subtitles.")
        transcriber = get_transcriber()
        transcript = transcriber.transcribe_full(
            source_audio,
            duration,
            language=manifest.source_language,
            progress_callback=_make_stage_progress_updater(
                reporter,
                stage="transcribing",
                message="Generating source subtitles.",
                step_label="Generating source subtitles.",
                substep_index=0,
                substep_total=2,
            ),
        )

        source_segments = _normalize_segments(fallback_source_segments(transcript))
        source_segments = _repair_source_gap_segments(
            source_audio=source_audio,
            source_segments=source_segments,
            transcriber=transcriber,
            language=transcript.language if transcript.language and transcript.language != "Auto" else manifest.source_language,
        )
        _validate_source_timing_integrity(source_segments, context="Raw source segmentation")
        _persist_segment_preview(project_id, source_segments, force_subtitles=True)
        source_segments = _materialize_source_segment_audio(
            base_dir=base_dir,
            source_audio=source_audio,
            source_segments=source_segments,
            progress_callback=_make_stage_progress_updater(
                reporter,
                stage="transcribing",
                message="Preparing source clips.",
                step_label="Preparing source clips.",
                substep_index=1,
                substep_total=2,
            ),
            save_callback=lambda segments: _persist_segment_preview(project_id, segments),
        )
        source_segments = _normalize_segments(source_segments)
        source_document = SegmentDocument(segments=source_segments)
        save_source_segments(project_id, source_document)
        atomic_write_text(source_srt, to_srt(source_document))

        reviewer = get_reviewer()
        manifest.source_audio = source_audio.relative_to(base_dir).as_posix()
        manifest.transcriber_provider = transcriber.name
        manifest.reviewer_provider = reviewer.name
        manifest.status = "source_ready"
        manifest.quality_note = None
        save_manifest(manifest)

        _review_document, pending_count = _run_source_review_stage(
            project_id=project_id,
            manifest=manifest,
            source_document=source_document,
            reporter=reporter,
        )
        final_message = (
            f"Source subtitles finished. {pending_count} Codex correction suggestions are ready."
            if pending_count > 0
            else "Source subtitles finished."
        )
        reporter.finish(message=final_message, success=True)
    except Exception as exc:  # noqa: BLE001
        manifest.status = "error"
        save_manifest(manifest)
        reporter.finish(
            message="Source subtitle extraction failed.",
            success=False,
            error=str(exc),
            error_detail=traceback.format_exc(),
        )


def run_source_correction_pipeline(project_id: str) -> None:
    manifest = load_manifest(project_id)
    source_document = load_source_segments(project_id)
    reporter = PipelineProgressReporter(
        project_id,
        [("reviewing", "Reviewing source subtitles with Codex.")],
    )
    try:
        if not source_document.segments:
            raise RuntimeError("No source subtitles are available yet.")
        _review_document, pending_count = _run_source_review_stage(
            project_id=project_id,
            manifest=manifest,
            source_document=source_document,
            reporter=reporter,
        )
        final_message = (
            f"Source corrections are ready for review ({pending_count} pending)."
            if pending_count > 0
            else "Source correction review finished."
        )
        reporter.finish(message=final_message, success=True)
    except Exception as exc:  # noqa: BLE001
        reporter.finish(
            message="Source correction review failed.",
            success=False,
            error=str(exc),
            error_detail=traceback.format_exc(),
        )


def run_full_pipeline(project_id: str) -> None:
    run_source_pipeline(project_id)
    manifest = load_manifest(project_id)
    if manifest.status == "error":
        return
    review = load_source_correction_review(project_id)
    if pending_source_correction_count(review) > 0:
        accept_all_source_corrections(project_id)
    run_target_pipeline(project_id)


def stop_interrupted_job(project_id: str, *, message: str | None = None) -> JobState:
    return mark_job_interrupted(project_id, message=message or "Task stopped because the worker exited unexpectedly.")
