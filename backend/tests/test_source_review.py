from backend.app.models import Segment, SegmentDocument
from backend.app.source_review import (
    apply_all_source_correction_actions,
    apply_source_correction_action,
    pending_source_correction_count,
    run_source_correction_review,
    stop_running_source_correction_review,
)
from backend.app.storage import (
    create_project,
    delete_project,
    load_manifest,
    load_source_correction_review,
    load_source_segments,
    save_source_correction_review,
    save_source_segments,
)
from backend.app.utils import now_iso
from backend.app.models import SourceCorrectionReviewDocument, SourceCorrectionSuggestion


def _segment(index: int, text: str, start: float, end: float, *, status: str = "ready") -> Segment:
    return Segment(
        id=f"seg-{index + 1:04}",
        index=index,
        start=start,
        end=end,
        text=text,
        status=status,
    )


class _StubReviewer:
    def review_source_segment_correction(self, segment: Segment, *, source_language: str = "Auto"):
        _ = source_language
        if "Open Cloud" in segment.text:
            return (
                segment.text.replace("Open Cloud", "OpenClaw"),
                [{"from": "Open Cloud", "to": "OpenClaw"}],
            )
        return segment.text, []


def test_run_source_correction_review_marks_pending_segments(monkeypatch) -> None:
    manifest = create_project("source review pending test")
    project_id = manifest.id
    try:
        save_source_segments(
            project_id,
            SegmentDocument(
                segments=[
                    _segment(0, "Open Cloud 的功能很强。", 0.0, 1.2),
                    _segment(1, "这一句本身没问题。", 1.2, 2.4),
                ],
            ),
        )
        monkeypatch.setattr("backend.app.source_review.get_reviewer", lambda: _StubReviewer())

        review = run_source_correction_review(project_id, source_language="Chinese")

        assert review.status == "complete"
        assert review.completed_segments == 2
        assert pending_source_correction_count(review) == 1
        assert review.suggestions[0].status == "pending"
        assert review.suggestions[0].suggested_text == "OpenClaw 的功能很强。"
        assert review.suggestions[1].status == "unchanged"
        assert load_manifest(project_id).status == "source_review"
    finally:
        delete_project(project_id)


def test_accept_all_source_corrections_updates_source_text(monkeypatch) -> None:
    manifest = create_project("source review accept test")
    project_id = manifest.id
    try:
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "Open Cloud 的功能很强。", 0.0, 1.2)]),
        )
        monkeypatch.setattr("backend.app.source_review.get_reviewer", lambda: _StubReviewer())

        run_source_correction_review(project_id, source_language="Chinese")
        source_document, review = apply_all_source_correction_actions(project_id, action="accept")

        assert source_document.segments[0].text == "OpenClaw 的功能很强。"
        assert source_document.segments[0].status == "edited"
        assert review.suggestions[0].status == "accepted"
        assert load_manifest(project_id).status == "edited"
    finally:
        delete_project(project_id)


def test_custom_source_correction_action_updates_source_text(monkeypatch) -> None:
    manifest = create_project("source review custom test")
    project_id = manifest.id
    try:
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "Open Cloud 的功能很强。", 0.0, 1.2)]),
        )
        monkeypatch.setattr("backend.app.source_review.get_reviewer", lambda: _StubReviewer())

        run_source_correction_review(project_id, source_language="Chinese")
        source_document, review = apply_source_correction_action(
            project_id,
            "seg-0001",
            action="custom",
            custom_text="OpenClaw 的能力非常强。",
        )

        assert source_document.segments[0].text == "OpenClaw 的能力非常强。"
        assert source_document.segments[0].status == "edited"
        assert review.suggestions[0].status == "customized"
        assert review.suggestions[0].reviewed_text == "OpenClaw 的能力非常强。"
        assert load_manifest(project_id).status == "edited"
    finally:
        delete_project(project_id)


def test_reject_all_source_corrections_keeps_original_source_text(monkeypatch) -> None:
    manifest = create_project("source review reject all test")
    project_id = manifest.id
    try:
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "Open Cloud 的功能很强。", 0.0, 1.2)]),
        )
        monkeypatch.setattr("backend.app.source_review.get_reviewer", lambda: _StubReviewer())

        run_source_correction_review(project_id, source_language="Chinese")
        source_document, review = apply_all_source_correction_actions(project_id, action="reject")

        assert source_document.segments[0].text == "Open Cloud 的功能很强。"
        assert review.suggestions[0].status == "rejected"
        assert review.suggestions[0].reviewed_text == "Open Cloud 的功能很强。"
        assert load_manifest(project_id).status == "source_ready"
    finally:
        delete_project(project_id)


def test_stop_running_source_correction_review_marks_review_error() -> None:
    manifest = create_project("stop running review")
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


def test_apply_source_correction_action_requires_non_empty_custom_text(monkeypatch) -> None:
    manifest = create_project("source review empty custom")
    project_id = manifest.id
    try:
        save_source_segments(
            project_id,
            SegmentDocument(segments=[_segment(0, "Open Cloud", 0.0, 1.0)]),
        )
        monkeypatch.setattr("backend.app.source_review.get_reviewer", lambda: _StubReviewer())
        run_source_correction_review(project_id, source_language="Chinese")

        try:
            apply_source_correction_action(project_id, "seg-0001", action="custom", custom_text=" ")
            raise AssertionError("Expected ValueError for empty custom text")
        except ValueError as exc:
            assert "cannot be empty" in str(exc)

        assert load_source_segments(project_id).segments[0].text == "Open Cloud"
    finally:
        delete_project(project_id)
