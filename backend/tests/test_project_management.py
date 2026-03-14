import signal
import zipfile
import json
from pathlib import Path

from backend.app.main import _build_export_bundle
from backend.app.models import JobState, Segment, SegmentDocument, SourceCorrectionReviewDocument, SourceCorrectionSuggestion
from backend.app.source_review import stop_running_source_correction_review
from backend.app.storage import (
    create_project,
    delete_project,
    load_manifest,
    load_sidebar_state,
    load_source_correction_review,
    load_source_segments,
    load_state,
    load_target_aligned_segments,
    load_target_draft_segments,
    project_dir,
    project_paths,
    reset_project_outputs,
    save_manifest,
    save_sidebar_state,
    save_source_correction_review,
    save_source_segments,
    save_state,
    stop_project_job,
    touch_runtime,
    update_project_settings,
)
from backend.app.utils import now_iso


def _segment(index: int, text: str, start: float, end: float) -> Segment:
    return Segment(
        id=f"seg-{index + 1:04}",
        index=index,
        start=start,
        end=end,
        text=text,
    )


def test_reset_project_outputs_preserves_source_video_and_project_config() -> None:
    manifest = create_project("clear project outputs", source_language="Chinese")
    project_id = manifest.id
    try:
        paths = project_paths(project_id)
        base_dir = project_dir(project_id)
        paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_video"].write_bytes(b"source-video")
        paths["source_audio"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_audio"].write_bytes(b"source-audio")
        paths["source_srt"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_srt"].write_text("source srt", encoding="utf-8")
        (base_dir / "voices" / "source-segments").mkdir(parents=True, exist_ok=True)
        (base_dir / "voices" / "source-segments" / "seg-0001.wav").write_bytes(b"voice")
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "原文一。", 0.0, 1.0)]),
        )
        save_source_correction_review(
            project_id,
            SourceCorrectionReviewDocument(
                created_at=now_iso(),
                updated_at=now_iso(),
                status="complete",
                total_segments=1,
                completed_segments=1,
                suggestions=[
                    SourceCorrectionSuggestion(
                        segment_id="seg-0001",
                        segment_index=0,
                        original_text="Open Cloud",
                        suggested_text="OpenClaw",
                        status="accepted",
                        updated_at=now_iso(),
                    )
                ],
            ),
        )

        reset_project_outputs(project_id, preserve_source_stage=False, preserve_source_review=False)

        refreshed_manifest = load_manifest(project_id)
        assert paths["source_video"].exists()
        assert paths["source_video"].read_bytes() == b"source-video"
        assert not paths["source_audio"].exists()
        assert not paths["source_srt"].exists()
        assert load_source_segments(project_id).segments == []
        assert load_source_correction_review(project_id) is None
        assert not any((base_dir / "voices").rglob("*"))
        assert refreshed_manifest.source_language == "Chinese"
        assert refreshed_manifest.source_video == "source/original.mp4"
        assert refreshed_manifest.status == "idle"
    finally:
        delete_project(project_id)


def test_sidebar_state_round_trips_review_visibility_map(monkeypatch, tmp_path: Path) -> None:
    sidebar_path = tmp_path / "sidebar_state.json"
    monkeypatch.setattr("backend.app.storage.SIDEBAR_STATE_PATH", sidebar_path)

    save_sidebar_state(
        {
            "selected_project_id": "project-1",
            "project_list_scroll_top": 18,
            "source_review_visibility_by_project": {
                "project-1": False,
                "project-2": True,
            },
        }
    )

    loaded = load_sidebar_state()

    assert loaded["selected_project_id"] == "project-1"
    assert loaded["project_list_scroll_top"] == 18
    assert loaded["source_review_visibility_by_project"] == {
        "project-1": False,
        "project-2": True,
    }


def test_stop_project_job_marks_state_stopped_and_terminates_worker(monkeypatch) -> None:
    manifest = create_project("stop project job")
    project_id = manifest.id
    killed: list[tuple[int, signal.Signals]] = []
    try:
        save_state(
            project_id,
            JobState(
                running=True,
                stage="transcribing",
                progress=42,
                message="Running",
                updated_at=now_iso(),
            ),
        )
        touch_runtime(project_id, worker_pid=43210)

        def fake_kill(pid: int, sig: signal.Signals) -> None:
            killed.append((pid, sig))

        monkeypatch.setattr("backend.app.storage.os.kill", fake_kill)

        job_state = stop_project_job(project_id)

        assert killed[-1] == (43210, signal.SIGTERM)
        assert job_state.running is False
        assert job_state.stage == "idle"
        assert job_state.message == "Task stopped by user."
        assert load_state(project_id).running is False
    finally:
        delete_project(project_id)


def test_load_state_normalizes_legacy_traceback_error() -> None:
    manifest = create_project("legacy traceback state")
    project_id = manifest.id
    try:
        legacy_error = (
            "RuntimeError: MLX TTS request failed.\n"
            "Traceback (most recent call last):\n"
            '  File "/tmp/example.py", line 10, in <module>\n'
            "    raise RuntimeError('MLX TTS request failed.')\n"
            "RuntimeError: MLX TTS request failed.\n"
        )
        state_path = project_dir(project_id) / "jobs" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "running": False,
                    "stage": "error",
                    "progress": 12,
                    "message": "Target translation and synthesis failed.",
                    "error": legacy_error,
                    "updated_at": now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        loaded = load_state(project_id)

        assert loaded.error == "MLX TTS request failed."
        assert "Traceback (most recent call last):" in (loaded.error_detail or "")
    finally:
        delete_project(project_id)


def test_load_state_extracts_message_from_legacy_codex_error_payload() -> None:
    manifest = create_project("legacy codex state")
    project_id = manifest.id
    try:
        legacy_error = "\n".join(
            [
                "OpenAI Codex v0.108.0-alpha.12 (research preview)",
                "--------",
                "workdir: /Volumes/8TR0/codex/video_translater",
                '    "message": "The following tools cannot be used with reasoning.effort \'minimal\': web_search.",',
                "ERROR: {",
                '  "message": "The following tools cannot be used with reasoning.effort \'minimal\': web_search.",',
                "}",
            ]
        )
        state_path = project_dir(project_id) / "jobs" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "running": False,
                    "stage": "error",
                    "progress": 12,
                    "message": "Target translation and synthesis failed.",
                    "error": legacy_error,
                    "updated_at": now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        loaded = load_state(project_id)

        assert loaded.error == "The following tools cannot be used with reasoning.effort 'minimal': web_search."
        assert "OpenAI Codex v0.108.0-alpha.12" in (loaded.error_detail or "")
    finally:
        delete_project(project_id)


def test_stop_running_source_correction_review_marks_review_non_running() -> None:
    manifest = create_project("stop running review from project management")
    project_id = manifest.id
    try:
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "Open Cloud", 0.0, 1.0)]),
        )
        save_source_correction_review(
            project_id,
            SourceCorrectionReviewDocument(
                created_at=now_iso(),
                updated_at=now_iso(),
                status="running",
                total_segments=1,
                completed_segments=0,
                suggestions=[
                    SourceCorrectionSuggestion(
                        segment_id="seg-0001",
                        segment_index=0,
                        original_text="Open Cloud",
                        suggested_text="OpenClaw",
                        status="processing",
                        updated_at=now_iso(),
                    )
                ],
            ),
        )

        review = stop_running_source_correction_review(project_id)

        assert review is not None
        assert review.status == "error"
        assert load_source_correction_review(project_id).status == "error"
    finally:
        delete_project(project_id)


def test_build_export_bundle_includes_project_files() -> None:
    manifest = create_project("export project bundle")
    project_id = manifest.id
    try:
        paths = project_paths(project_id)
        paths["source_video"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_video"].write_bytes(b"source-video")
        paths["source_audio"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_audio"].write_bytes(b"source-audio")
        paths["source_srt"].parent.mkdir(parents=True, exist_ok=True)
        paths["source_srt"].write_text("source srt", encoding="utf-8")
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "原文一。", 0.0, 1.0)]),
        )

        bundle_path = _build_export_bundle(project_id)

        assert bundle_path.exists()
        with zipfile.ZipFile(bundle_path) as archive:
            names = set(archive.namelist())
        assert "export_manifest.json" in names
        assert "project.json" in names
        assert "source/original.mp4" in names
        assert "source/original.wav" in names
        assert "subtitles/source.v1.srt" in names
        assert "segments/source.v1.json" in names
        assert f"exports/{bundle_path.name}" not in names
    finally:
        delete_project(project_id)


def test_updating_target_language_clears_existing_target_outputs() -> None:
    manifest = create_project("target language reset")
    project_id = manifest.id
    try:
        paths = project_paths(project_id)
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "原文一。", 0.0, 1.0)]),
        )
        (project_dir(project_id) / "voices" / "target-draft").mkdir(parents=True, exist_ok=True)
        (project_dir(project_id) / "voices" / "target-draft" / "seg-0001.wav").write_bytes(b"draft")
        save_state(
            project_id,
            JobState(
                running=False,
                stage="complete",
                progress=100,
                message="Target translation and synthesis finished.",
                updated_at=now_iso(),
            ),
        )
        paths["target_track"].parent.mkdir(parents=True, exist_ok=True)
        paths["target_track"].write_bytes(b"track")
        manifest = load_manifest(project_id)
        manifest.status = "target_ready"
        save_manifest(manifest)

        updated = update_project_settings(project_id, {"target_language": "Japanese"})

        assert updated.target_language == "Japanese"
        assert load_target_draft_segments(project_id).segments == []
        assert load_target_aligned_segments(project_id).segments == []
        assert not paths["target_track"].exists()
        assert load_manifest(project_id).status == "source_ready"
    finally:
        delete_project(project_id)
