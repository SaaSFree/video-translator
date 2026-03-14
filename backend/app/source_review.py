from __future__ import annotations

from .models import (
    SegmentDocument,
    SourceCorrectionChange,
    SourceCorrectionReviewDocument,
    SourceCorrectionSuggestion,
)
from .providers import get_reviewer
from .segmentation import normalize_anchor_text
from .storage import (
    clear_target_outputs,
    load_manifest,
    load_source_correction_review,
    load_source_segments,
    project_paths,
    save_manifest,
    save_source_correction_review,
    save_source_segments,
)
from .subtitles import to_srt
from .utils import atomic_write_text, now_iso


REVIEW_READY_NOTE = "Codex 纠错建议已生成，请确认高亮的 source 行。"


def pending_source_correction_count(review: SourceCorrectionReviewDocument | None) -> int:
    if review is None:
        return 0
    return sum(1 for item in review.suggestions if item.status == "pending")


def _review_seed_document(source_document: SegmentDocument) -> SourceCorrectionReviewDocument:
    timestamp = now_iso()
    return SourceCorrectionReviewDocument(
        created_at=timestamp,
        updated_at=timestamp,
        status="running",
        total_segments=len(source_document.segments),
        completed_segments=0,
        suggestions=[
            SourceCorrectionSuggestion(
                segment_id=segment.id,
                segment_index=segment.index,
                original_text=segment.text,
                suggested_text=segment.text,
                status="queued",
                updated_at=timestamp,
            )
            for segment in source_document.segments
        ],
    )


def _load_latest_review_state(
    project_id: str,
    fallback: SourceCorrectionReviewDocument,
) -> SourceCorrectionReviewDocument:
    latest = load_source_correction_review(project_id)
    if latest is None:
        return fallback
    if len(latest.suggestions) != len(fallback.suggestions):
        return fallback
    return latest


def _sync_manifest_review_state(project_id: str, review: SourceCorrectionReviewDocument, *, source_changed: bool = False) -> None:
    manifest = load_manifest(project_id)
    source_document = load_source_segments(project_id)
    pending_count = pending_source_correction_count(review)
    if review.status == "running" or pending_count > 0:
        manifest.status = "source_review"
        manifest.quality_note = REVIEW_READY_NOTE
    elif source_document.segments:
        manifest.status = "edited" if source_changed else "source_ready"
        if manifest.quality_note == REVIEW_READY_NOTE:
            manifest.quality_note = None
    else:
        manifest.status = "idle"
        manifest.quality_note = None
    save_manifest(manifest)


def _persist_source_text_updates(project_id: str, updates: dict[str, str]) -> tuple[SegmentDocument, bool]:
    source_document = load_source_segments(project_id)
    changed = False
    updated_segments = []
    for segment in source_document.segments:
        next_text = updates.get(segment.id)
        if next_text is None:
            updated_segments.append(segment)
            continue
        normalized_next = str(next_text).strip()
        if normalized_next != segment.text:
            changed = True
            updated_segments.append(segment.model_copy(update={"text": normalized_next, "status": "edited"}))
        else:
            updated_segments.append(segment)
    updated_document = SegmentDocument(version=source_document.version, segments=updated_segments)
    save_source_segments(project_id, updated_document)
    atomic_write_text(project_paths(project_id)["source_srt"], to_srt(updated_document))
    if changed:
        clear_target_outputs(project_id)
    return updated_document, changed


def run_source_correction_review(
    project_id: str,
    *,
    source_document: SegmentDocument | None = None,
    source_language: str = "Auto",
    progress_callback=None,
) -> SourceCorrectionReviewDocument:
    document = source_document or load_source_segments(project_id)
    review = _review_seed_document(document)
    save_source_correction_review(project_id, review)
    if not document.segments:
        review.status = "complete"
        save_source_correction_review(project_id, review)
        _sync_manifest_review_state(project_id, review)
        return review

    reviewer = get_reviewer()
    completed = 0
    for index, segment in enumerate(document.segments):
        review = _load_latest_review_state(project_id, review)
        suggestion = review.suggestions[index]
        if suggestion.status in {"accepted", "rejected", "customized"}:
            completed += 1
            review.completed_segments = max(review.completed_segments, completed)
            review.updated_at = now_iso()
            save_source_correction_review(project_id, review)
            if progress_callback:
                progress_callback(completed, len(document.segments), f"Correcting source subtitles ({completed}/{len(document.segments)} segments).")
            continue

        review.suggestions[index] = suggestion.model_copy(
            update={"status": "processing", "error": None, "updated_at": now_iso()}
        )
        review.updated_at = now_iso()
        save_source_correction_review(project_id, review)
        try:
            suggested_text, raw_changes = reviewer.review_source_segment_correction(
                segment,
                source_language=source_language,
            )
            normalized_original = normalize_anchor_text(segment.text)
            normalized_suggested = normalize_anchor_text(suggested_text)
            changes = [
                SourceCorrectionChange(
                    from_text=str(item.get("from", "")).strip(),
                    to_text=str(item.get("to", "")).strip(),
                )
                for item in raw_changes
                if str(item.get("from", "")).strip() and str(item.get("to", "")).strip()
            ]
            next_status = "pending" if normalized_suggested and normalized_suggested != normalized_original else "unchanged"
            next_text = suggested_text.strip() if next_status == "pending" else segment.text
            review = _load_latest_review_state(project_id, review)
            review.suggestions[index] = suggestion.model_copy(
                update={
                    "suggested_text": next_text,
                    "changes": changes if next_status == "pending" else [],
                    "status": next_status,
                    "error": None,
                    "updated_at": now_iso(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            review = _load_latest_review_state(project_id, review)
            review.suggestions[index] = suggestion.model_copy(
                update={
                    "suggested_text": segment.text,
                    "changes": [],
                    "status": "failed",
                    "error": str(exc),
                    "updated_at": now_iso(),
                }
            )
        completed += 1
        review.completed_segments = completed
        review.updated_at = now_iso()
        save_source_correction_review(project_id, review)
        if progress_callback:
            progress_callback(completed, len(document.segments), f"Correcting source subtitles ({completed}/{len(document.segments)} segments).")

    review = _load_latest_review_state(project_id, review)
    review.status = "complete"
    review.updated_at = now_iso()
    save_source_correction_review(project_id, review)
    _sync_manifest_review_state(project_id, review)
    return review


def stop_running_source_correction_review(project_id: str) -> SourceCorrectionReviewDocument | None:
    review = load_source_correction_review(project_id)
    if review is None or review.status != "running":
        return review
    review.status = "error"
    review.updated_at = now_iso()
    save_source_correction_review(project_id, review)
    _sync_manifest_review_state(project_id, review)
    return review


def apply_source_correction_action(
    project_id: str,
    segment_id: str,
    *,
    action: str,
    custom_text: str | None = None,
) -> tuple[SegmentDocument, SourceCorrectionReviewDocument]:
    review = load_source_correction_review(project_id)
    if review is None:
        raise FileNotFoundError("Source correction review not found.")

    updates: dict[str, str] = {}
    next_suggestions: list[SourceCorrectionSuggestion] = []
    matched = False
    for suggestion in review.suggestions:
        if suggestion.segment_id != segment_id:
            next_suggestions.append(suggestion)
            continue
        matched = True
        if action == "accept":
            next_suggestions.append(
                suggestion.model_copy(
                    update={"status": "accepted", "reviewed_text": suggestion.suggested_text, "updated_at": now_iso()}
                )
            )
            updates[segment_id] = suggestion.suggested_text
        elif action == "reject":
            next_suggestions.append(
                suggestion.model_copy(
                    update={"status": "rejected", "reviewed_text": suggestion.original_text, "updated_at": now_iso()}
                )
            )
            updates[segment_id] = suggestion.original_text
        elif action == "custom":
            value = str(custom_text or "").strip()
            if not value:
                raise ValueError("Custom text cannot be empty.")
            next_suggestions.append(
                suggestion.model_copy(
                    update={"status": "customized", "reviewed_text": value, "updated_at": now_iso()}
                )
            )
            updates[segment_id] = value
        else:
            raise ValueError(f"Unsupported source correction action: {action}")

    if not matched:
        raise FileNotFoundError(f"Source segment not found: {segment_id}")

    source_document, changed = _persist_source_text_updates(project_id, updates)
    review.suggestions = next_suggestions
    review.completed_segments = sum(
        1 for item in review.suggestions if item.status in {"accepted", "rejected", "customized", "unchanged", "failed"}
    )
    review.updated_at = now_iso()
    review.status = "complete"
    save_source_correction_review(project_id, review)
    _sync_manifest_review_state(project_id, review, source_changed=changed)
    return source_document, review


def apply_all_source_correction_actions(
    project_id: str,
    *,
    action: str,
) -> tuple[SegmentDocument, SourceCorrectionReviewDocument]:
    review = load_source_correction_review(project_id)
    if review is None:
        raise FileNotFoundError("Source correction review not found.")
    updates: dict[str, str] = {}
    next_suggestions: list[SourceCorrectionSuggestion] = []
    for suggestion in review.suggestions:
        if suggestion.status != "pending":
            next_suggestions.append(suggestion)
            continue
        if action == "accept":
            updates[suggestion.segment_id] = suggestion.suggested_text
            next_suggestions.append(
                suggestion.model_copy(
                    update={"status": "accepted", "reviewed_text": suggestion.suggested_text, "updated_at": now_iso()}
                )
            )
        elif action == "reject":
            updates[suggestion.segment_id] = suggestion.original_text
            next_suggestions.append(
                suggestion.model_copy(
                    update={"status": "rejected", "reviewed_text": suggestion.original_text, "updated_at": now_iso()}
                )
            )
        else:
            raise ValueError(f"Unsupported bulk action: {action}")
    source_document, changed = _persist_source_text_updates(project_id, updates)
    review.suggestions = next_suggestions
    review.completed_segments = sum(
        1 for item in review.suggestions if item.status in {"accepted", "rejected", "customized", "unchanged", "failed"}
    )
    review.updated_at = now_iso()
    review.status = "complete"
    save_source_correction_review(project_id, review)
    _sync_manifest_review_state(project_id, review, source_changed=changed)
    return source_document, review


def accept_all_source_corrections(project_id: str) -> tuple[SegmentDocument, SourceCorrectionReviewDocument]:
    return apply_all_source_correction_actions(project_id, action="accept")
