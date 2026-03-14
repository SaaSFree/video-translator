from pathlib import Path

from backend.app.models import Segment, SegmentDocument, SourceCorrectionReviewDocument, SourceCorrectionSuggestion
from backend.app.pipeline import _plan_target_alignment_windows, run_full_pipeline, run_target_pipeline
from backend.app.storage import (
    create_project,
    delete_project,
    load_manifest,
    load_merged_target_segments,
    project_dir,
    load_target_aligned_segments,
    load_target_draft_segments,
    project_paths,
    save_source_segments,
    save_target_aligned_segments,
    source_target_snapshot_file,
    update_target_segment_text,
)


def _segment(index: int, text: str, start: float, end: float) -> Segment:
    return Segment(
        id=f"seg-{index + 1:04}",
        index=index,
        start=start,
        end=end,
        text=text,
        audio_path=f"voices/source-segments/seg-{index + 1:04}.wav",
    )


class _StubTranslator:
    name = "stub-translator"

    def translate_segment(self, source_segment: Segment, **_kwargs) -> str:
        return f"EN {source_segment.text}"

    def retime_translation(self, source_segment: Segment, *, attempt_index: int = 0, **_kwargs) -> str:
        return f"Short EN {attempt_index} {source_segment.text}"


class _StubSynthesizer:
    name = "stub-synthesizer"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(self, *, text: str, output_path: Path, **_kwargs) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(f"audio:{text}".encode("utf-8"))
        self.calls.append({"text": text, **_kwargs})


def _patch_audio_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.pipeline._postprocess_target_draft_audio",
        lambda raw_output_path, output_path: Path(output_path).write_bytes(Path(raw_output_path).read_bytes()),
    )
    monkeypatch.setattr(
        "backend.app.pipeline.smooth_segment_edges",
        lambda input_path, output_path, **kwargs: Path(output_path).write_bytes(Path(input_path).read_bytes()),
    )


def test_plan_target_alignment_windows_keeps_total_and_overlap() -> None:
    source_segments = [
        Segment(id="seg-0001", index=0, start=0.4, end=1.8, text="A"),
        Segment(id="seg-0002", index=1, start=2.0, end=3.4, text="B"),
        Segment(id="seg-0003", index=2, start=3.7, end=5.0, text="C"),
    ]

    windows, scale = _plan_target_alignment_windows(
        source_segments,
        [1.2, 1.3, 1.1],
        total_duration=5.6,
    )

    assert scale == 1.0
    assert windows[-1][1] <= 5.6
    for source_segment, (start, end) in zip(source_segments, windows, strict=True):
        assert end > source_segment.start
        assert start < source_segment.end
        center = (start + end) / 2.0
        assert source_segment.start <= center <= source_segment.end


def test_plan_target_alignment_windows_uses_global_compression_when_needed() -> None:
    source_segments = [
        Segment(id="seg-0001", index=0, start=0.0, end=1.0, text="A"),
        Segment(id="seg-0002", index=1, start=1.05, end=2.05, text="B"),
        Segment(id="seg-0003", index=2, start=2.1, end=3.1, text="C"),
    ]

    windows, scale = _plan_target_alignment_windows(
        source_segments,
        [1.3, 1.3, 1.3],
        total_duration=3.2,
    )

    assert scale < 1.0
    assert windows[-1][1] <= 3.2
    for source_segment, (start, end) in zip(source_segments, windows, strict=True):
        assert end > source_segment.start
        assert start < source_segment.end


def test_run_full_pipeline_auto_accepts_pending_source_corrections(monkeypatch) -> None:
    events: list[str] = []
    review = SourceCorrectionReviewDocument(
        created_at="2026-03-14T00:00:00+00:00",
        updated_at="2026-03-14T00:00:00+00:00",
        status="complete",
        total_segments=1,
        completed_segments=1,
        suggestions=[
            SourceCorrectionSuggestion(
                segment_id="seg-0001",
                segment_index=0,
                original_text="Open Cloud",
                suggested_text="OpenClaw",
                status="pending",
                updated_at="2026-03-14T00:00:00+00:00",
            )
        ],
    )

    class _Manifest:
        status = "source_ready"

    monkeypatch.setattr("backend.app.pipeline.run_source_pipeline", lambda project_id: events.append(f"source:{project_id}"))
    monkeypatch.setattr("backend.app.pipeline.load_manifest", lambda project_id: _Manifest())
    monkeypatch.setattr("backend.app.pipeline.load_source_correction_review", lambda project_id: review)
    monkeypatch.setattr("backend.app.pipeline.accept_all_source_corrections", lambda project_id: events.append(f"accept:{project_id}"))
    monkeypatch.setattr("backend.app.pipeline.run_target_pipeline", lambda project_id: events.append(f"target:{project_id}"))

    run_full_pipeline("project-1")

    assert events == ["source:project-1", "accept:project-1", "target:project-1"]


def test_run_target_pipeline_builds_draft_aligned_outputs(monkeypatch) -> None:
    manifest = create_project("target pipeline test", source_language="Chinese")
    project_id = manifest.id
    base_dir = project_dir(project_id)
    paths = project_paths(project_id)
    source_segments = SegmentDocument(
        segments=[
            _segment(0, "第一句。", 0.0, 1.0),
            _segment(1, "第二句。", 1.0, 2.0),
        ]
    )
    try:
        save_source_segments(project_id, source_segments)
        paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_video"].write_bytes(b"source-video")
        paths["source_audio"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_audio"].write_bytes(b"source-audio")
        for segment in source_segments.segments:
            audio_path = base_dir / (segment.audio_path or "")
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"source-segment")

        def fake_duration(path: Path) -> float:
            name = Path(path).name
            if name == "original.wav":
                return 2.0
            if name == "seg-0001.wav":
                return 0.88
            if name == "seg-0002.wav":
                return 0.92
            if name == "target-track.v1.wav":
                return 2.0
            return 1.0

        synthesizer = _StubSynthesizer()
        monkeypatch.setattr("backend.app.pipeline.get_translator", lambda: _StubTranslator())
        monkeypatch.setattr("backend.app.pipeline.get_synthesizer", lambda: synthesizer)
        monkeypatch.setattr("backend.app.pipeline.ffprobe_duration", fake_duration)
        _patch_audio_pipeline(monkeypatch)
        monkeypatch.setattr(
            "backend.app.pipeline.slot_audio",
            lambda input_path, output_path, duration, **kwargs: Path(output_path).write_bytes(
                f"aligned:{Path(input_path).name}:{duration:.3f}".encode("utf-8")
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.concat_audio",
            lambda inputs, output_path, **kwargs: Path(output_path).write_bytes(
                b"".join(Path(item).read_bytes() for item in inputs)
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.make_silence",
            lambda output_path, duration, **kwargs: Path(output_path).write_bytes(
                f"silence:{duration:.3f}".encode("utf-8")
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.mux_video_with_audio",
            lambda source_video, audio_track, output_video, **kwargs: (
                Path(output_video).parent.mkdir(parents=True, exist_ok=True),
                Path(output_video).write_bytes(b"muxed-video"),
            )[-1],
        )

        run_target_pipeline(project_id)

        draft_document = load_target_draft_segments(project_id)
        aligned_document = load_target_aligned_segments(project_id)
        saved_manifest = load_manifest(project_id)

        assert [segment.text for segment in draft_document.segments] == ["EN 第一句。", "EN 第二句。"]
        assert [segment.status for segment in draft_document.segments] == ["ready", "ready"]
        assert all(segment.audio_path and "voices/target-draft/" in segment.audio_path for segment in draft_document.segments)

        assert [segment.status for segment in aligned_document.segments] == ["aligned", "aligned"]
        assert all(segment.audio_path and "voices/target-aligned/" in segment.audio_path for segment in aligned_document.segments)
        assert paths["target_draft_srt"].exists()
        assert paths["target_srt"].exists()
        assert paths["target_track"].exists()
        assert paths["target_video"].exists()
        assert saved_manifest.status == "target_ready"
        assert saved_manifest.translator_provider == "stub-translator"
        assert saved_manifest.synthesizer_provider == "stub-synthesizer"
        assert synthesizer.calls[0]["reference_audio"] == base_dir / "voices/source-segments/seg-0001.wav"
        assert synthesizer.calls[0]["reference_text"] == "第一句。"
        assert synthesizer.calls[1]["reference_audio"] == base_dir / "voices/source-segments/seg-0002.wav"
        assert synthesizer.calls[1]["reference_text"] == "第二句。"
        snapshot_payload = source_target_snapshot_file(project_id).read_text(encoding="utf-8")
        assert "voices/source-segments/seg-0001.wav" in snapshot_payload
        assert "第一句。" in snapshot_payload
    finally:
        delete_project(project_id)


def test_update_target_segment_text_marks_segment_as_edited() -> None:
    manifest = create_project("target edit test", source_language="Chinese")
    project_id = manifest.id
    source_segments = SegmentDocument(segments=[_segment(0, "第一句。", 0.0, 1.0)])
    target_segments = SegmentDocument(
        segments=[
            Segment(
                id="seg-0001",
                index=0,
                start=0.0,
                end=1.0,
                text="EN first line",
                status="aligned",
                audio_path="voices/target-aligned/seg-0001.wav",
            )
        ]
    )
    try:
        save_source_segments(project_id, source_segments)
        save_target_aligned_segments(project_id, target_segments)

        updated = update_target_segment_text(project_id, "seg-0001", "Edited target line")

        assert updated.segments[0].text == "Edited target line"
        assert updated.segments[0].status == "edited"
        assert load_merged_target_segments(project_id).segments[0].text == "Edited target line"
    finally:
        delete_project(project_id)


def test_run_target_pipeline_resynthesize_reuses_existing_target_text(monkeypatch) -> None:
    manifest = create_project("target resynthesize test", source_language="Chinese")
    project_id = manifest.id
    base_dir = project_dir(project_id)
    paths = project_paths(project_id)
    source_segments = SegmentDocument(
        segments=[
            _segment(0, "第一句。", 0.0, 1.0),
            _segment(1, "第二句。", 1.0, 2.0),
        ]
    )
    edited_target = SegmentDocument(
        segments=[
            Segment(id="seg-0001", index=0, start=0.0, end=1.0, text="Edited EN first", status="edited"),
            Segment(id="seg-0002", index=1, start=1.0, end=2.0, text="Edited EN second", status="edited"),
        ]
    )
    try:
        save_source_segments(project_id, source_segments)
        save_target_aligned_segments(project_id, edited_target)
        paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_video"].write_bytes(b"source-video")
        paths["source_audio"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_audio"].write_bytes(b"source-audio")
        for segment in source_segments.segments:
            audio_path = base_dir / (segment.audio_path or "")
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"source-segment")

        def fake_duration(path: Path) -> float:
            name = Path(path).name
            if name == "original.wav":
                return 2.0
            if name == "seg-0001.wav":
                return 0.9
            if name == "seg-0002.wav":
                return 1.0
            if name == "target-track.v1.wav":
                return 2.0
            return 1.0

        monkeypatch.setattr(
            "backend.app.pipeline.get_translator",
            lambda: (_ for _ in ()).throw(AssertionError("translator should not be used during resynthesis")),
        )
        synthesizer = _StubSynthesizer()
        monkeypatch.setattr("backend.app.pipeline.get_synthesizer", lambda: synthesizer)
        monkeypatch.setattr("backend.app.pipeline.ffprobe_duration", fake_duration)
        _patch_audio_pipeline(monkeypatch)
        monkeypatch.setattr(
            "backend.app.pipeline.slot_audio",
            lambda input_path, output_path, duration, **kwargs: Path(output_path).write_bytes(
                f"aligned:{Path(input_path).name}:{duration:.3f}".encode("utf-8")
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.concat_audio",
            lambda inputs, output_path, **kwargs: Path(output_path).write_bytes(
                b"".join(Path(item).read_bytes() for item in inputs)
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.make_silence",
            lambda output_path, duration, **kwargs: Path(output_path).write_bytes(
                f"silence:{duration:.3f}".encode("utf-8")
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.mux_video_with_audio",
            lambda source_video, audio_track, output_video, **kwargs: (
                Path(output_video).parent.mkdir(parents=True, exist_ok=True),
                Path(output_video).write_bytes(b"muxed-video"),
            )[-1],
        )

        run_target_pipeline(project_id, reuse_existing_target_text=True)

        draft_document = load_target_draft_segments(project_id)
        assert [segment.text for segment in draft_document.segments] == ["Edited EN first", "Edited EN second"]
        assert [segment.status for segment in draft_document.segments] == ["ready", "ready"]
        assert synthesizer.calls[0]["reference_text"] == "第一句。"
        assert synthesizer.calls[1]["reference_text"] == "第二句。"
    finally:
        delete_project(project_id)


def test_run_target_pipeline_normalizes_tts_only_text(monkeypatch) -> None:
    manifest = create_project("target spoken normalization test", source_language="Chinese")
    project_id = manifest.id
    base_dir = project_dir(project_id)
    paths = project_paths(project_id)
    source_segments = SegmentDocument(
        segments=[
            _segment(0, "原文。", 0.0, 1.0),
        ]
    )

    class _NumericTranslator(_StubTranslator):
        def translate_segment(self, source_segment: Segment, **_kwargs) -> str:
            return "Run this 24/7 with API and AI."

    try:
        save_source_segments(project_id, source_segments)
        paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_video"].write_bytes(b"source-video")
        paths["source_audio"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_audio"].write_bytes(b"source-audio")
        for segment in source_segments.segments:
            audio_path = base_dir / (segment.audio_path or "")
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"source-segment")

        synthesizer = _StubSynthesizer()
        monkeypatch.setattr("backend.app.pipeline.get_translator", lambda: _NumericTranslator())
        monkeypatch.setattr("backend.app.pipeline.get_synthesizer", lambda: synthesizer)
        monkeypatch.setattr("backend.app.pipeline.ffprobe_duration", lambda _path: 1.0)
        _patch_audio_pipeline(monkeypatch)
        monkeypatch.setattr(
            "backend.app.pipeline.slot_audio",
            lambda input_path, output_path, duration, **kwargs: Path(output_path).write_bytes(
                f"aligned:{Path(input_path).name}:{duration:.3f}".encode("utf-8")
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.concat_audio",
            lambda inputs, output_path, **kwargs: Path(output_path).write_bytes(
                b"".join(Path(item).read_bytes() for item in inputs)
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.make_silence",
            lambda output_path, duration, **kwargs: Path(output_path).write_bytes(
                f"silence:{duration:.3f}".encode("utf-8")
            ),
        )
        monkeypatch.setattr(
            "backend.app.pipeline.mux_video_with_audio",
            lambda source_video, audio_track, output_video, **kwargs: (
                Path(output_video).parent.mkdir(parents=True, exist_ok=True),
                Path(output_video).write_bytes(b"muxed-video"),
            )[-1],
        )

        run_target_pipeline(project_id)

        draft_document = load_target_draft_segments(project_id)
        assert draft_document.segments[0].text == "Run this 24/7 with API and AI."
        assert synthesizer.calls[0]["text"] == "Run this twenty-four seven with A P I and A I."
    finally:
        delete_project(project_id)
