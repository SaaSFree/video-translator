from pathlib import Path

from backend.app.models import Segment, TranscriptDocument, TranscriptItem
from backend.app.pipeline import run_source_pipeline
from backend.app.storage import create_project, delete_project, load_manifest, load_source_segments, project_paths


class _StubTranscriber:
    name = "stub-transcriber"

    def transcribe_full(
        self,
        audio_path: Path,
        duration: float,
        *,
        language: str = "Auto",
        progress_callback=None,
    ) -> TranscriptDocument:
        _ = (audio_path, duration, language)
        if progress_callback:
            progress_callback(1, 2, "Running MLX ASR recognition (1/2 chunks).")
            progress_callback(2, 2, "Running MLX ASR recognition (2/2 chunks).")
        return TranscriptDocument(
            language="Chinese",
            text="Open Cloud 来了。\n第二句没改。",
            items=[
                TranscriptItem(index=0, text="Open Cloud 来了。", start=0.0, end=1.1),
                TranscriptItem(index=1, text="第二句没改。", start=1.5, end=2.6),
            ],
        )


class _GapRepairTranscriber:
    name = "gap-repair-transcriber"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe_full(
        self,
        audio_path: Path,
        duration: float,
        *,
        language: str = "Auto",
        progress_callback=None,
    ) -> TranscriptDocument:
        _ = (audio_path, duration, language, progress_callback)
        self.calls += 1
        if self.calls == 1:
            return TranscriptDocument(
                language="Chinese",
                text="第一句。第二句。第四句。",
                items=[
                    TranscriptItem(index=0, text="第一句。", start=0.0, end=1.0),
                    TranscriptItem(index=1, text="第二句。", start=1.1, end=2.0),
                    TranscriptItem(index=2, text="第四句。", start=5.4, end=6.4),
                ],
            )
        return TranscriptDocument(
            language="Chinese",
            text="第二句。第三句。第四句。",
            items=[
                TranscriptItem(index=0, text="第二句。", start=0.0, end=0.8),
                TranscriptItem(index=1, text="第三句。", start=1.2, end=2.3),
                TranscriptItem(index=2, text="第四句。", start=4.4, end=5.0),
            ],
        )


class _StubReviewer:
    name = "stub-reviewer"


def test_run_source_pipeline_keeps_raw_asr_text(monkeypatch) -> None:
    manifest = create_project("raw asr pipeline test")
    project_id = manifest.id
    paths = project_paths(project_id)
    paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
    paths["source_video"].write_bytes(b"fake-video")
    try:
        monkeypatch.setattr("backend.app.pipeline.get_transcriber", lambda: _StubTranscriber())
        monkeypatch.setattr("backend.app.pipeline.get_reviewer", lambda: _StubReviewer())
        monkeypatch.setattr(
            "backend.app.pipeline.extract_audio",
            lambda source_video, source_audio, **kwargs: Path(source_audio).write_bytes(b"fake-wav"),
        )
        monkeypatch.setattr("backend.app.pipeline.ffprobe_duration", lambda path: 2.6)
        monkeypatch.setattr(
            "backend.app.pipeline._materialize_source_segment_audio",
            lambda **kwargs: [
                segment.model_copy(update={"audio_path": f"voices/source-segments/{segment.id}.wav"})
                for segment in kwargs["source_segments"]
            ],
        )
        monkeypatch.setattr(
            "backend.app.pipeline._run_source_review_stage",
            lambda **kwargs: (object(), 0),
        )

        run_source_pipeline(project_id)

        source_document = load_source_segments(project_id)
        assert [segment.text for segment in source_document.segments] == [
            "Open Cloud 来了。",
            "第二句没改。",
        ]
        assert all(segment.audio_path for segment in source_document.segments)

        saved_manifest = load_manifest(project_id)
        assert saved_manifest.status == "source_ready"
        assert saved_manifest.transcriber_provider == "stub-transcriber"
        assert saved_manifest.reviewer_provider == "stub-reviewer"
        assert saved_manifest.source_audio == "source/original.wav"
        assert paths["source_srt"].exists()
    finally:
        delete_project(project_id)


def test_run_source_pipeline_marks_failure_when_source_video_missing() -> None:
    manifest = create_project("missing video pipeline test")
    project_id = manifest.id
    try:
        run_source_pipeline(project_id)
        saved_manifest = load_manifest(project_id)
        assert saved_manifest.status == "error"
    finally:
        delete_project(project_id)


def test_run_source_pipeline_repairs_large_source_gap(monkeypatch) -> None:
    manifest = create_project("source gap repair test")
    project_id = manifest.id
    paths = project_paths(project_id)
    paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
    paths["source_video"].write_bytes(b"fake-video")
    transcriber = _GapRepairTranscriber()
    try:
        monkeypatch.setattr("backend.app.pipeline.get_transcriber", lambda: transcriber)
        monkeypatch.setattr("backend.app.pipeline.get_reviewer", lambda: _StubReviewer())
        monkeypatch.setattr(
            "backend.app.pipeline.extract_audio",
            lambda source_video, source_audio, **kwargs: Path(source_audio).write_bytes(b"fake-wav"),
        )
        monkeypatch.setattr("backend.app.pipeline.ffprobe_duration", lambda path: 6.4)
        monkeypatch.setattr(
            "backend.app.pipeline.extract_audio_clip",
            lambda input_path, output_path, **kwargs: Path(output_path).write_bytes(b"gap-wav"),
        )
        monkeypatch.setattr(
            "backend.app.pipeline._materialize_source_segment_audio",
            lambda **kwargs: [
                segment.model_copy(update={"audio_path": f"voices/source-segments/{segment.id}.wav"})
                for segment in kwargs["source_segments"]
            ],
        )
        monkeypatch.setattr(
            "backend.app.pipeline._run_source_review_stage",
            lambda **kwargs: (object(), 0),
        )

        run_source_pipeline(project_id)

        source_document = load_source_segments(project_id)
        assert [segment.text for segment in source_document.segments] == [
            "第一句。",
            "第二句。",
            "第三句。",
            "第四句。",
        ]
    finally:
        delete_project(project_id)
