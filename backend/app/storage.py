from __future__ import annotations

import json
import os
import shutil
import signal
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECTS_DIR, SIDEBAR_STATE_PATH, ensure_base_dirs
from .models import (
    JobRuntime,
    JobState,
    ProjectDetail,
    ProjectManifest,
    Segment,
    SegmentDocument,
    SourceCorrectionReviewDocument,
)
from .utils import atomic_write_json, make_project_id, normalize_error_fields, now_iso, read_json


ALLOWED_MEDIA_ROOTS = {"source", "voices", "subtitles", "target"}
JOB_HEARTBEAT_TIMEOUT_SECONDS = 20
INTERRUPTED_JOB_MESSAGE = "Task stopped because the worker exited unexpectedly."
USER_STOPPED_JOB_MESSAGE = "Task stopped by user."


def _normalize_manifest_payload(payload: dict) -> dict:
    normalized = dict(payload or {})
    if not normalized.get("source_language"):
        normalized["source_language"] = "Auto"
    if not normalized.get("target_language"):
        normalized["target_language"] = "English"
    return normalized


def project_dir(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def project_file(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def state_file(project_id: str) -> Path:
    return project_dir(project_id) / "jobs" / "state.json"


def runtime_file(project_id: str) -> Path:
    return project_dir(project_id) / "jobs" / "runtime.json"


def events_file(project_id: str) -> Path:
    return project_dir(project_id) / "logs" / "events.jsonl"


def source_segments_file(project_id: str) -> Path:
    return project_dir(project_id) / "segments" / "source.v1.json"


def source_correction_review_file(project_id: str) -> Path:
    return project_dir(project_id) / "jobs" / "source_correction_review.json"


def target_draft_segments_file(project_id: str) -> Path:
    return project_dir(project_id) / "segments" / "target.draft.v1.json"


def target_aligned_segments_file(project_id: str) -> Path:
    return project_dir(project_id) / "segments" / "target.aligned.v1.json"


def source_target_snapshot_file(project_id: str) -> Path:
    return project_dir(project_id) / "segments" / "source.snapshot.for-target.v1.json"


def project_paths(project_id: str) -> dict[str, Path]:
    base = project_dir(project_id)
    return {
        "source_video": base / "source" / "original.mp4",
        "source_audio": base / "source" / "original.wav",
        "source_srt": base / "subtitles" / "source.v1.srt",
        "target_draft_srt": base / "subtitles" / "target.draft.v1.srt",
        "target_srt": base / "subtitles" / "target.v1.srt",
        "target_track": base / "voices" / "target-track.v1.wav",
        "target_video": base / "target" / "dubbed.v1.mp4",
    }


def ensure_project_layout(project_id: str) -> None:
    base = project_dir(project_id)
    for rel in ["source", "target", "segments", "voices", "subtitles", "jobs", "logs", "exports"]:
        (base / rel).mkdir(parents=True, exist_ok=True)


def _clear_directory_contents(path: Path) -> None:
    if not path.exists():
        return
    for child in list(path.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def reset_project_outputs(
    project_id: str,
    *,
    preserve_source_stage: bool = False,
    preserve_source_review: bool = False,
) -> None:
    ensure_project_layout(project_id)
    base = project_dir(project_id)
    paths = project_paths(project_id)
    manifest = load_manifest(project_id)

    clear_runtime(project_id)
    if not preserve_source_review:
        clear_source_correction_review(project_id)
    events_file(project_id).unlink(missing_ok=True)
    _clear_directory_contents(base / "exports")

    logs_dir = base / "logs"
    if logs_dir.exists():
        for child in list(logs_dir.iterdir()):
            if child.name == "events.jsonl":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)

    if preserve_source_stage:
        manifest.status = "source_ready"
        save_manifest(manifest)
        save_state(project_id, JobState(updated_at=now_iso()))
        return

    paths["source_audio"].unlink(missing_ok=True)
    paths["source_srt"].unlink(missing_ok=True)
    paths["target_video"].unlink(missing_ok=True)
    _clear_directory_contents(base / "voices")
    _clear_directory_contents(base / "target")
    save_source_segments(project_id, SegmentDocument())
    save_target_draft_segments(project_id, SegmentDocument())
    save_target_aligned_segments(project_id, SegmentDocument())
    source_target_snapshot_file(project_id).unlink(missing_ok=True)
    manifest.source_audio = None
    manifest.status = "idle"
    manifest.transcriber_provider = None
    manifest.reviewer_provider = None
    manifest.translator_provider = None
    manifest.synthesizer_provider = None
    manifest.quality_note = None
    save_manifest(manifest)
    save_state(project_id, JobState(updated_at=now_iso()))


def create_project(
    name: str,
    *,
    source_language: str = "Auto",
) -> ProjectManifest:
    ensure_base_dirs()
    project_id = make_project_id(name)
    ensure_project_layout(project_id)
    timestamp = now_iso()
    manifest = ProjectManifest(
        id=project_id,
        name=name,
        created_at=timestamp,
        updated_at=timestamp,
        source_video="source/original.mp4",
        source_language=source_language,
        target_language="English",
    )
    save_manifest(manifest)
    save_state(project_id, JobState(updated_at=timestamp))
    save_source_segments(project_id, SegmentDocument())
    save_target_draft_segments(project_id, SegmentDocument())
    save_target_aligned_segments(project_id, SegmentDocument())
    return manifest


def save_manifest(manifest: ProjectManifest) -> None:
    manifest.updated_at = now_iso()
    atomic_write_json(project_file(manifest.id), manifest.model_dump())


def load_manifest(project_id: str) -> ProjectManifest:
    payload = read_json(project_file(project_id), None)
    if payload is None:
        raise FileNotFoundError(project_id)
    return ProjectManifest.model_validate(_normalize_manifest_payload(payload))


def save_state(project_id: str, state: JobState) -> None:
    summary, detail = normalize_error_fields(state.error, state.error_detail)
    state.error = summary
    state.error_detail = detail
    state.updated_at = now_iso()
    atomic_write_json(state_file(project_id), state.model_dump())


def _load_raw_state(project_id: str) -> JobState:
    payload = read_json(state_file(project_id), None)
    if payload is None:
        return JobState(updated_at=now_iso())
    if isinstance(payload, dict):
        summary, detail = normalize_error_fields(payload.get("error"), payload.get("error_detail"))
        payload = {**payload, "error": summary, "error_detail": detail}
    return JobState.model_validate(payload)


def load_runtime(project_id: str) -> JobRuntime:
    payload = read_json(runtime_file(project_id), None)
    if payload is None:
        return JobRuntime()
    return JobRuntime.model_validate(payload)


def save_runtime(project_id: str, runtime: JobRuntime) -> None:
    atomic_write_json(runtime_file(project_id), runtime.model_dump())


def touch_runtime(project_id: str, *, worker_pid: int | None = None) -> JobRuntime:
    current = load_runtime(project_id)
    timestamp = now_iso()
    runtime = JobRuntime(
        worker_pid=worker_pid if worker_pid is not None else current.worker_pid,
        heartbeat_at=timestamp,
        started_at=current.started_at or timestamp,
    )
    save_runtime(project_id, runtime)
    return runtime


def clear_runtime(project_id: str) -> None:
    runtime_file(project_id).unlink(missing_ok=True)


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds_since(value: str | None) -> float | None:
    timestamp = _parse_iso_timestamp(value)
    if timestamp is None:
        return None
    return max((datetime.now(timezone.utc) - timestamp).total_seconds(), 0.0)


def _pid_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def mark_job_interrupted(project_id: str, *, message: str = INTERRUPTED_JOB_MESSAGE) -> JobState:
    clear_runtime(project_id)
    state = JobState(
        running=False,
        stage="idle",
        progress=0,
        message=message,
        error=None,
        updated_at=now_iso(),
        worker_pid=None,
        heartbeat_at=None,
    )
    save_state(project_id, state)
    append_event(
        project_id,
        {"at": now_iso(), "stage": state.stage, "progress": state.progress, "message": state.message},
    )
    return state


def stop_project_job(project_id: str, *, message: str = USER_STOPPED_JOB_MESSAGE) -> JobState:
    state = load_state(project_id)
    if not state.running:
        raise RuntimeError("Project is not currently running.")
    worker_pid = state.worker_pid or load_runtime(project_id).worker_pid
    if worker_pid and worker_pid > 0:
        try:
            os.kill(worker_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return mark_job_interrupted(project_id, message=message)


def _runtime_is_stale(state: JobState, runtime: JobRuntime) -> bool:
    if runtime.worker_pid:
        return not _pid_exists(runtime.worker_pid)
    heartbeat_age = _seconds_since(runtime.heartbeat_at)
    if heartbeat_age is not None:
        return heartbeat_age > JOB_HEARTBEAT_TIMEOUT_SECONDS
    state_age = _seconds_since(state.updated_at)
    if state_age is None:
        return False
    return state_age > JOB_HEARTBEAT_TIMEOUT_SECONDS


def load_state(project_id: str) -> JobState:
    state = _load_raw_state(project_id)
    runtime = load_runtime(project_id)
    if not state.running:
        if runtime.worker_pid or runtime.heartbeat_at or runtime.started_at:
            clear_runtime(project_id)
        return state.model_copy(update={"worker_pid": None, "heartbeat_at": None})
    if _runtime_is_stale(state, runtime):
        return mark_job_interrupted(project_id)
    return state.model_copy(update={"worker_pid": runtime.worker_pid, "heartbeat_at": runtime.heartbeat_at})


def append_event(project_id: str, event: dict[str, object]) -> None:
    path = events_file(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def save_source_segments(document_project_id: str, document: SegmentDocument) -> None:
    atomic_write_json(source_segments_file(document_project_id), document.model_dump())


def load_source_segments(project_id: str) -> SegmentDocument:
    payload = read_json(source_segments_file(project_id), {"segments": []})
    return SegmentDocument.model_validate(payload)


def save_target_draft_segments(project_id: str, document: SegmentDocument) -> None:
    atomic_write_json(target_draft_segments_file(project_id), document.model_dump())


def load_target_draft_segments(project_id: str) -> SegmentDocument:
    payload = read_json(target_draft_segments_file(project_id), {"segments": []})
    return SegmentDocument.model_validate(payload)


def save_target_aligned_segments(project_id: str, document: SegmentDocument) -> None:
    atomic_write_json(target_aligned_segments_file(project_id), document.model_dump())


def load_target_aligned_segments(project_id: str) -> SegmentDocument:
    payload = read_json(target_aligned_segments_file(project_id), {"segments": []})
    return SegmentDocument.model_validate(payload)


def load_merged_target_segments(project_id: str) -> SegmentDocument:
    source_document = load_source_segments(project_id)
    draft_document = load_target_draft_segments(project_id)
    aligned_document = load_target_aligned_segments(project_id)
    if not source_document.segments:
        if aligned_document.segments:
            return aligned_document
        return draft_document
    draft_by_id = {segment.id: segment for segment in draft_document.segments}
    aligned_by_id = {segment.id: segment for segment in aligned_document.segments}
    merged_segments: list[Segment] = []
    for source_segment in source_document.segments:
        candidate = aligned_by_id.get(source_segment.id) or draft_by_id.get(source_segment.id)
        if candidate:
            merged_segments.append(candidate)
    version = aligned_document.version if aligned_document.segments else draft_document.version
    return SegmentDocument(version=version, segments=merged_segments)


def save_source_target_snapshot(project_id: str, document: SegmentDocument) -> None:
    atomic_write_json(source_target_snapshot_file(project_id), document.model_dump())


def clear_target_outputs(project_id: str) -> None:
    base_dir = project_dir(project_id)
    paths = project_paths(project_id)
    save_target_draft_segments(project_id, SegmentDocument())
    save_target_aligned_segments(project_id, SegmentDocument())
    source_target_snapshot_file(project_id).unlink(missing_ok=True)
    paths["target_draft_srt"].unlink(missing_ok=True)
    paths["target_srt"].unlink(missing_ok=True)
    paths["target_track"].unlink(missing_ok=True)
    paths["target_video"].unlink(missing_ok=True)
    for rel_dir in ["voices/target-draft", "voices/target-normalized", "voices/target-aligned", "voices/source-tts-prompts"]:
        _clear_directory_contents(base_dir / rel_dir)
    manifest = load_manifest(project_id)
    manifest.translator_provider = None
    manifest.synthesizer_provider = None
    if manifest.status == "target_ready":
        manifest.status = "source_ready"
    save_manifest(manifest)


def update_source_segment_text(project_id: str, segment_id: str, text: str) -> SegmentDocument:
    document = load_source_segments(project_id)
    updated_segments: list[Segment] = []
    for segment in document.segments:
        if segment.id == segment_id:
            updated_segments.append(segment.model_copy(update={"text": text, "status": "edited"}))
        else:
            updated_segments.append(segment)
    next_document = SegmentDocument(version=document.version, segments=updated_segments)
    save_source_segments(project_id, next_document)
    clear_target_outputs(project_id)
    manifest = load_manifest(project_id)
    manifest.status = "edited"
    save_manifest(manifest)
    return next_document


def update_target_segment_text(project_id: str, segment_id: str, text: str) -> SegmentDocument:
    merged_document = load_merged_target_segments(project_id)
    if not merged_document.segments:
        raise FileNotFoundError("No target segments are available yet.")
    updated_segments: list[Segment] = []
    found = False
    for segment in merged_document.segments:
        if segment.id == segment_id:
            updated_segments.append(segment.model_copy(update={"text": text, "status": "edited"}))
            found = True
        else:
            updated_segments.append(segment)
    if not found:
        raise FileNotFoundError(segment_id)
    next_document = SegmentDocument(version=merged_document.version, segments=updated_segments)
    save_target_draft_segments(project_id, next_document)
    if load_target_aligned_segments(project_id).segments:
        save_target_aligned_segments(project_id, next_document)
    manifest = load_manifest(project_id)
    manifest.status = "edited"
    save_manifest(manifest)
    return next_document


def save_source_correction_review(project_id: str, document: SourceCorrectionReviewDocument) -> None:
    document.updated_at = now_iso()
    atomic_write_json(source_correction_review_file(project_id), document.model_dump())


def load_source_correction_review(project_id: str) -> SourceCorrectionReviewDocument | None:
    payload = read_json(source_correction_review_file(project_id), None)
    if payload is None:
        return None
    return SourceCorrectionReviewDocument.model_validate(payload)


def clear_source_correction_review(project_id: str) -> None:
    source_correction_review_file(project_id).unlink(missing_ok=True)


def update_project_settings(project_id: str, payload: dict[str, str]) -> ProjectManifest:
    manifest = load_manifest(project_id)
    if "source_language" in payload and payload["source_language"]:
        manifest.source_language = payload["source_language"]
    if "target_language" in payload and payload["target_language"]:
        next_target_language = payload["target_language"]
        if manifest.target_language != next_target_language:
            manifest.target_language = next_target_language
            save_manifest(manifest)
            clear_target_outputs(project_id)
            return load_manifest(project_id)
        manifest.target_language = next_target_language
    save_manifest(manifest)
    return manifest


def list_projects() -> list[ProjectManifest]:
    ensure_base_dirs()
    manifests: list[ProjectManifest] = []
    for path in PROJECTS_DIR.glob("*/project.json"):
        manifests.append(ProjectManifest.model_validate(_normalize_manifest_payload(read_json(path, {}))))
    return sorted(manifests, key=lambda item: item.updated_at, reverse=True)


def effective_project_status(manifest: ProjectManifest, job: JobState) -> str:
    if job.running:
        return job.stage
    return manifest.status


def serialize_manifest(manifest: ProjectManifest, *, job: JobState | None = None) -> dict[str, object | None]:
    resolved_job = job or load_state(manifest.id)
    payload = manifest.model_dump()
    payload.update(
        {
            "effective_status": effective_project_status(manifest, resolved_job),
            "effective_stage": resolved_job.stage if resolved_job.running else None,
            "job_running": resolved_job.running,
            "job_progress": resolved_job.progress,
        }
    )
    return payload


def load_project_detail(project_id: str) -> ProjectDetail:
    manifest = load_manifest(project_id)
    job = load_state(project_id)
    source_segments = load_source_segments(project_id)
    target_segments_draft = load_target_draft_segments(project_id)
    target_segments_aligned = load_target_aligned_segments(project_id)
    source_correction_review = load_source_correction_review(project_id)
    paths = project_paths(project_id)
    return ProjectDetail(
        manifest=manifest,
        job=job,
        source_segments=source_segments,
        target_segments_draft=target_segments_draft,
        target_segments_aligned=target_segments_aligned,
        paths={key: path.relative_to(project_dir(project_id)).as_posix() if path.exists() else None for key, path in paths.items()},
        source_correction_review=source_correction_review,
    )


def delete_project(project_id: str) -> None:
    base_dir = project_dir(project_id)
    if not base_dir.exists():
        raise FileNotFoundError(project_id)
    shutil.rmtree(base_dir)


def load_sidebar_state() -> dict[str, object | None]:
    ensure_base_dirs()
    payload = read_json(SIDEBAR_STATE_PATH, {})
    review_visibility = payload.get("source_review_visibility_by_project")
    if not isinstance(review_visibility, dict):
        review_visibility = {}
    return {
        "selected_project_id": payload.get("selected_project_id"),
        "project_list_scroll_top": payload.get("project_list_scroll_top", 0),
        "source_review_visibility_by_project": review_visibility,
    }


def save_sidebar_state(payload: dict[str, object | None]) -> dict[str, object | None]:
    current = load_sidebar_state()
    current.update(payload)
    atomic_write_json(SIDEBAR_STATE_PATH, current)
    return current
