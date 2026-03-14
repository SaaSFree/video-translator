from __future__ import annotations

from pydantic import BaseModel, Field


class Segment(BaseModel):
    id: str
    index: int
    start: float
    end: float
    source_start: float | None = None
    source_end: float | None = None
    text: str
    source_text: str | None = None
    speaker: str = "speaker_1"
    status: str = "ready"
    audio_path: str | None = None
    reference_audio_path: str | None = None


class TranscriptItem(BaseModel):
    index: int
    text: str
    start: float
    end: float


class TranscriptDocument(BaseModel):
    language: str = ""
    text: str = ""
    items: list[TranscriptItem] = Field(default_factory=list)


class SegmentDocument(BaseModel):
    version: str = "v1"
    segments: list[Segment] = Field(default_factory=list)


class SourceCorrectionChange(BaseModel):
    from_text: str
    to_text: str


class SourceCorrectionSuggestion(BaseModel):
    segment_id: str
    segment_index: int
    original_text: str
    suggested_text: str
    status: str = "queued"
    changes: list[SourceCorrectionChange] = Field(default_factory=list)
    reviewed_text: str | None = None
    error: str | None = None
    updated_at: str | None = None


class SourceCorrectionReviewDocument(BaseModel):
    version: str = "v1"
    created_at: str
    updated_at: str
    status: str = "idle"
    total_segments: int = 0
    completed_segments: int = 0
    suggestions: list[SourceCorrectionSuggestion] = Field(default_factory=list)


class JobState(BaseModel):
    running: bool = False
    stage: str = "idle"
    progress: int = 0
    overall_ratio: float = 0.0
    stage_ratio: float = 0.0
    message: str = "Ready"
    error: str | None = None
    error_detail: str | None = None
    updated_at: str
    elapsed_seconds: float | None = None
    eta_seconds: float | None = None
    step_index: int | None = None
    step_total: int | None = None
    step_label: str | None = None
    items_completed: float | None = None
    items_total: float | None = None
    worker_pid: int | None = None
    heartbeat_at: str | None = None


class JobRuntime(BaseModel):
    worker_pid: int | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None


class ProjectManifest(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    status: str = "idle"
    source_video: str | None = None
    source_audio: str | None = None
    source_segments_version: str = "v1"
    source_language: str = "Auto"
    target_language: str = "English"
    transcriber_provider: str | None = None
    reviewer_provider: str | None = None
    translator_provider: str | None = None
    synthesizer_provider: str | None = None
    quality_note: str | None = None
    notes: str | None = None


class ProjectDetail(BaseModel):
    manifest: ProjectManifest
    job: JobState
    source_segments: SegmentDocument
    target_segments_draft: SegmentDocument
    target_segments_aligned: SegmentDocument
    paths: dict[str, str | None]
    source_correction_review: SourceCorrectionReviewDocument | None = None
