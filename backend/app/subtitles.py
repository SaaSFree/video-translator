from __future__ import annotations

from .models import SegmentDocument


def format_timestamp(value: float) -> str:
    milliseconds = int(round(value * 1000))
    hours = milliseconds // 3_600_000
    minutes = (milliseconds % 3_600_000) // 60_000
    seconds = (milliseconds % 60_000) // 1000
    millis = milliseconds % 1000
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def to_srt(document: SegmentDocument) -> str:
    chunks: list[str] = []
    for idx, segment in enumerate(document.segments, start=1):
        chunks.append(
            "\n".join(
                [
                    str(idx),
                    f"{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}",
                    segment.text,
                ]
            )
        )
    return "\n\n".join(chunks) + ("\n" if chunks else "")

