from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Segment, TranscriptDocument, TranscriptItem


TERMINAL_PUNCTUATION = "。！？!?；;."
SOFT_PUNCTUATION = "，,、：:"
BREAK_PUNCTUATION = TERMINAL_PUNCTUATION + SOFT_PUNCTUATION
INTRAWORD_CONNECTORS = "'’-_‑"
SPACE_BOUNDARY_TRAILING_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "but",
    "by",
    "can",
    "could",
    "de",
    "del",
    "der",
    "des",
    "die",
    "du",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "i've",
    "if",
    "in",
    "into",
    "is",
    "it",
    "it's",
    "la",
    "las",
    "le",
    "les",
    "let's",
    "my",
    "of",
    "on",
    "or",
    "our",
    "para",
    "por",
    "run",
    "that",
    "the",
    "their",
    "this",
    "to",
    "un",
    "una",
    "und",
    "une",
    "was",
    "were",
    "with",
    "your",
    "you're",
    "we're",
    "they're",
    "i'm",
    "he's",
    "she's",
    "that's",
    "there's",
    "who's",
    "what's",
}
SPACE_BOUNDARY_LEADING_WORDS = SPACE_BOUNDARY_TRAILING_WORDS | {
    "about",
    "after",
    "app",
    "apps",
    "astronomy",
    "because",
    "before",
    "completed",
    "does",
    "of",
    "supervise",
    "works",
}


@dataclass(frozen=True)
class TranscriptUnit:
    id: str
    index: int
    text: str
    start: float
    end: float
    start_item: int
    end_item: int


def normalize_anchor_text(text: str) -> str:
    output: list[str] = []
    for char in str(text):
        if char == "'":
            output.append(char)
            continue
        if char.isalnum():
            output.append(char)
            continue
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            output.append(char)
    return "".join(output)


def _is_wordish(char: str) -> bool:
    return char.isalnum() or char in INTRAWORD_CONNECTORS


def _looks_space_delimited(text: str) -> bool:
    value = str(text or "")
    if not value or not any(char.isspace() for char in value):
        return False
    tokens = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", value, flags=re.UNICODE)
    long_tokens = [token for token in tokens if len(token) >= 2]
    return len(long_tokens) >= 2


def _normalize_space_delimited_punctuation_spacing(text: str) -> str:
    value = str(text or "")
    output: list[str] = []
    for index, char in enumerate(value):
        output.append(char)
        if not _is_terminal_punctuation_char(value, index):
            continue
        next_char = value[index + 1] if index + 1 < len(value) else ""
        if next_char and not next_char.isspace():
            output.append(" ")
    return "".join(output)


def _split_space_delimited_text(text: str, *, soft_limit: int, hard_limit: int) -> list[str]:
    normalized_text = _normalize_space_delimited_punctuation_spacing(text)
    tokens = re.findall(r"\S+\s*", normalized_text)
    if not tokens:
        return []

    chunks: list[str] = []
    current_tokens: list[str] = []
    index = 0
    overshoot_limit = max(8, int(hard_limit * 0.35))

    def flush(batch: list[str]) -> None:
        value = "".join(batch).strip()
        if value and _normalized_length(value):
            chunks.append(value)

    while index < len(tokens):
        current_tokens.append(tokens[index])
        index += 1
        current_value = "".join(current_tokens).strip()
        normalized_length = _normalized_length(current_value)
        last_token = current_tokens[-1].rstrip()
        last_char = last_token[-1] if last_token else ""
        if last_char in TERMINAL_PUNCTUATION:
            flush(current_tokens)
            current_tokens = []
            continue
        emergency_limit = hard_limit + overshoot_limit
        if normalized_length <= hard_limit:
            continue
        if normalized_length <= emergency_limit:
            continue

        lookahead_tokens = list(current_tokens)
        lookahead_index = index
        while lookahead_index < len(tokens) and lookahead_index < index + 3:
            lookahead_tokens.append(tokens[lookahead_index])
            lookahead_value = "".join(lookahead_tokens).strip()
            lookahead_length = _normalized_length(lookahead_value)
            if lookahead_length > hard_limit + overshoot_limit:
                break
            token_value = tokens[lookahead_index].rstrip()
            terminal = token_value[-1] if token_value else ""
            if terminal in TERMINAL_PUNCTUATION:
                current_tokens = lookahead_tokens
                index = lookahead_index + 1
                flush(current_tokens)
                current_tokens = []
                break
            lookahead_index += 1
        else:
            split_index = _best_space_split_index(current_tokens, soft_limit=soft_limit, hard_limit=hard_limit)
            head = current_tokens[:split_index]
            tail = current_tokens[split_index:]
            flush(head)
            current_tokens = tail

    if current_tokens:
        flush(current_tokens)
    return chunks


def _edge_word(text: str, *, from_end: bool) -> str:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿ]+)?", text)
    if not tokens:
        return ""
    return tokens[-1] if from_end else tokens[0]


def _best_space_split_index(tokens: list[str], *, soft_limit: int, hard_limit: int) -> int:
    if len(tokens) <= 1:
        return 1
    ideal_length = min(max(soft_limit, int(hard_limit * 0.8)), hard_limit)
    candidates: list[tuple[float, int]] = []
    for boundary in range(1, len(tokens)):
        left = "".join(tokens[:boundary]).strip()
        right = "".join(tokens[boundary:]).strip()
        left_length = _normalized_length(left)
        right_length = _normalized_length(right)
        if not left_length or not right_length:
            continue
        left_word = _edge_word(left, from_end=True).lower()
        right_word = _edge_word(right, from_end=False).lower()
        score = abs(left_length - ideal_length)
        left_char = left[-1] if left else ""
        if left_char in TERMINAL_PUNCTUATION:
            score -= 20
        elif left_char in SOFT_PUNCTUATION:
            score -= 12
        if left_word in SPACE_BOUNDARY_TRAILING_WORDS:
            score += 45
        if right_word in SPACE_BOUNDARY_LEADING_WORDS:
            score += 45
        if left_word and right_word and left_word[0].isupper() and right_word[0].islower() and len(right_word) <= 6:
            score += 22
        if left_length < max(8, int(soft_limit * 0.45)):
            score += 18
        if right_length < max(6, int(soft_limit * 0.35)):
            score += 18
        candidates.append((score, boundary))
    if not candidates:
        return max(1, min(len(tokens) - 1, len(tokens) // 2))
    candidates.sort(key=lambda item: (item[0], -item[1]))
    return candidates[0][1]


def split_transcript_text(text: str, *, soft_limit: int = 18, hard_limit: int = 26) -> list[str]:
    if _looks_space_delimited(text):
        return _split_space_delimited_text(text, soft_limit=soft_limit, hard_limit=hard_limit)
    chunks: list[str] = []
    current = ""
    for index, char in enumerate(text):
        current += char
        normalized_length = len(normalize_anchor_text(current))
        if _is_terminal_punctuation_char(text, index):
            chunks.append(current.strip())
            current = ""
            continue
        emergency_limit = hard_limit + max(8, int(soft_limit * 0.6))
        if normalized_length >= emergency_limit:
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if normalize_anchor_text(chunk)]


def _normalized_length(text: str) -> int:
    return len(normalize_anchor_text(text))


def _alignment_normalize(text: str) -> str:
    return normalize_anchor_text(text)


def _merge_tokens(tokens: list[str]) -> str:
    merged: list[str] = []
    for token in tokens:
        value = token.strip()
        if not value:
            continue
        if not merged:
            merged.append(value)
            continue
        previous = merged[-1]
        if _needs_space(previous, value):
            merged.append(f" {value}")
        else:
            merged.append(value)
    return "".join(merged).strip()


def _needs_space(previous: str, current: str) -> bool:
    prev_char = previous[-1]
    curr_char = current[0]
    if prev_char in BREAK_PUNCTUATION or curr_char in BREAK_PUNCTUATION:
        return False
    if _is_cjk(prev_char) or _is_cjk(curr_char):
        return False
    return prev_char.isalnum() and curr_char.isalnum()


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF


def _is_decimal_period(text: str, index: int) -> bool:
    value = str(text or "")
    if index < 0 or index >= len(value) or value[index] != ".":
        return False
    previous_char = value[index - 1] if index - 1 >= 0 else ""
    next_char = value[index + 1] if index + 1 < len(value) else ""
    return previous_char.isdigit() and next_char.isdigit()


def _is_terminal_punctuation_char(text: str, index: int) -> bool:
    value = str(text or "")
    if index < 0 or index >= len(value):
        return False
    char = value[index]
    if char not in TERMINAL_PUNCTUATION:
        return False
    if char == "." and _is_decimal_period(value, index):
        return False
    return True


def _soft_break_count(text: str) -> int:
    return sum(1 for char in str(text or "") if char in SOFT_PUNCTUATION)


def _has_terminal_punctuation(text: str) -> bool:
    value = str(text or "")
    return any(_is_terminal_punctuation_char(value, index) for index in range(len(value)))


def _should_split_soft_boundary(
    text: str,
    *,
    soft_limit: int,
    hard_limit: int,
    duration: float | None = None,
) -> bool:
    return False


def _split_punctuated_text(text: str, *, soft_limit: int = 22, hard_limit: int = 36) -> list[str]:
    if _looks_space_delimited(text):
        return _split_space_delimited_text(text, soft_limit=soft_limit, hard_limit=hard_limit)
    chunks: list[str] = []
    current = ""
    for index, char in enumerate(text):
        current += char
        normalized_length = _normalized_length(current)
        if _is_terminal_punctuation_char(text, index):
            if current.strip():
                chunks.append(current.strip())
            current = ""
            continue
        if normalized_length >= hard_limit:
            emergency_limit = hard_limit + max(14, int(hard_limit * 0.35))
            if normalized_length >= emergency_limit and current.strip():
                chunks.append(current.strip())
                current = ""
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if _normalized_length(chunk)]


def split_review_text(text: str, *, soft_limit: int = 22, hard_limit: int = 36, min_chunk_chars: int = 10) -> list[str]:
    chunks = _split_punctuated_text(text, soft_limit=soft_limit, hard_limit=hard_limit)
    if len(chunks) <= 1:
        return chunks
    merged: list[str] = []
    for chunk in chunks:
        value = chunk.strip()
        if not value:
            continue
        if not merged:
            merged.append(value)
            continue
        if _normalized_length(merged[-1]) < min_chunk_chars:
            merged[-1] = _join_unit_texts([merged[-1], value])
            continue
        merged.append(value)
    while len(merged) > 1 and _normalized_length(merged[-1]) < max(min_chunk_chars - 2, 6):
        merged[-2] = _join_unit_texts([merged[-2], merged[-1]])
        merged.pop()
    return [chunk for chunk in merged if _normalized_length(chunk)]


def _build_units_from_punctuated_text(transcript: TranscriptDocument) -> list[TranscriptUnit]:
    if not transcript.text.strip() or not transcript.items:
        return []
    items = transcript.items
    item_char_positions: list[int] = []
    item_stream_parts: list[str] = []
    for list_index, item in enumerate(items):
        normalized = _alignment_normalize(item.text)
        if not normalized:
            continue
        item_stream_parts.append(normalized)
        item_char_positions.extend([list_index] * len(normalized))
    item_stream = "".join(item_stream_parts)
    full_chunks = _split_punctuated_text(transcript.text)
    if not item_stream or not full_chunks:
        return []

    full_stream = "".join(_alignment_normalize(chunk) for chunk in full_chunks)
    if full_stream != item_stream:
        return []

    units: list[TranscriptUnit] = []
    cursor = 0
    for chunk in full_chunks:
        normalized = _alignment_normalize(chunk)
        if not normalized:
            continue
        start_pos = cursor
        end_pos = cursor + len(normalized) - 1
        start_list_index = item_char_positions[start_pos]
        end_list_index = item_char_positions[end_pos]
        start_item = items[start_list_index]
        end_item = items[end_list_index]
        units.append(
            TranscriptUnit(
                id=f"u{len(units) + 1:04}",
                index=len(units),
                text=chunk.strip(),
                start=round(start_item.start, 3),
                end=round(end_item.end, 3),
                start_item=start_item.index,
                end_item=end_item.index,
            )
        )
        cursor = end_pos + 1
    if cursor != len(item_stream):
        return []
    return units


def _build_units_from_items(transcript: TranscriptDocument) -> list[TranscriptUnit]:
    if not transcript.items:
        return []
    items = transcript.items
    units: list[TranscriptUnit] = []
    current_items: list[TranscriptItem] = []

    soft_char_limit = 18
    hard_char_limit = 42
    soft_duration_limit = 3.8
    hard_duration_limit = 6.6
    long_duration_limit = 8.8
    strong_pause = 0.52
    soft_pause = 0.28
    min_split_chars = 8

    def append_unit(batch: list[TranscriptItem]) -> None:
        if not batch:
            return
        text = _merge_tokens([item.text for item in batch]).strip()
        if not _normalized_length(text):
            return
        units.append(
            TranscriptUnit(
                id=f"u{len(units) + 1:04}",
                index=len(units),
                text=text,
                start=round(batch[0].start, 3),
                end=round(batch[-1].end, 3),
                start_item=batch[0].index,
                end_item=batch[-1].index,
            )
        )

    for item in items:
        if not normalize_anchor_text(item.text) and item.text not in BREAK_PUNCTUATION:
            continue

        if current_items:
            previous = current_items[-1]
            gap = max(0.0, item.start - previous.end)
            current_text = _merge_tokens([value.text for value in current_items])
            current_length = _normalized_length(current_text)
            current_duration = max(0.0, current_items[-1].end - current_items[0].start)
            if gap >= strong_pause and current_length >= min_split_chars and (
                _has_terminal_punctuation(current_text)
                or current_duration >= long_duration_limit
            ):
                append_unit(current_items)
                current_items = []
            elif gap >= soft_pause and _has_terminal_punctuation(current_text):
                append_unit(current_items)
                current_items = []

        current_items.append(item)

        text = _merge_tokens([value.text for value in current_items])
        normalized_length = _normalized_length(text)
        duration = max(0.0, current_items[-1].end - current_items[0].start)
        token_text = item.text.strip()

        should_split = False
        if any(_is_terminal_punctuation_char(token_text, index) for index in range(len(token_text))):
            should_split = True
        elif duration >= long_duration_limit and not _has_terminal_punctuation(text):
            should_split = True
        elif normalized_length >= hard_char_limit + 18 and not _has_terminal_punctuation(text):
            should_split = True

        if should_split:
            append_unit(current_items)
            current_items = []

    append_unit(current_items)

    if units:
        return units

    fallback_text = transcript.text.strip() or _merge_tokens([item.text for item in items])
    return [
        TranscriptUnit(
            id="u0001",
            index=0,
            text=fallback_text,
            start=round(items[0].start, 3),
            end=round(items[-1].end, 3),
            start_item=items[0].index,
            end_item=items[-1].index,
        )
    ]


def build_transcript_units(transcript: TranscriptDocument) -> list[TranscriptUnit]:
    punctuated_units = _build_units_from_punctuated_text(transcript)
    if punctuated_units:
        return punctuated_units
    return _build_units_from_items(transcript)


def _normalized_text_with_positions(text: str) -> tuple[str, list[int]]:
    normalized_chars: list[str] = []
    raw_positions: list[int] = []
    for raw_index, char in enumerate(str(text or "")):
        normalized = normalize_anchor_text(char)
        if not normalized:
            continue
        for normalized_char in normalized:
            normalized_chars.append(normalized_char)
            raw_positions.append(raw_index)
    return "".join(normalized_chars), raw_positions


def _boundary_breaks_word(text: str, previous_raw_index: int, next_raw_index: int) -> bool:
    if previous_raw_index < 0 or next_raw_index <= previous_raw_index:
        return False
    previous_char = text[previous_raw_index]
    next_char = text[next_raw_index]
    between = text[previous_raw_index + 1 : next_raw_index]
    if previous_char in BREAK_PUNCTUATION or next_char in BREAK_PUNCTUATION:
        return False
    if any(char.isspace() or char in BREAK_PUNCTUATION for char in between):
        return False
    if between and not all(char in INTRAWORD_CONNECTORS for char in between):
        return False
    return _is_wordish(previous_char) and _is_wordish(next_char)


def find_segment_boundary_issues(full_text: str, segments: list[Segment] | list[str]) -> list[dict[str, object]]:
    if not _looks_space_delimited(full_text):
        return []
    normalized_full, raw_positions = _normalized_text_with_positions(full_text)
    if not normalized_full or not raw_positions:
        return []
    segment_texts = [segment.text if isinstance(segment, Segment) else str(segment) for segment in segments]
    cursor = 0
    issues: list[dict[str, object]] = []
    for index, segment_text in enumerate(segment_texts[:-1]):
        normalized_segment = normalize_anchor_text(segment_text)
        if not normalized_segment:
            continue
        next_cursor = cursor + len(normalized_segment)
        if next_cursor > len(normalized_full):
            issues.append(
                {
                    "index": index,
                    "reason": "coverage-overflow",
                    "left": segment_texts[index],
                    "right": segment_texts[index + 1],
                }
            )
            break
        if normalized_full[cursor:next_cursor] != normalized_segment:
            issues.append(
                {
                    "index": index,
                    "reason": "coverage-mismatch",
                    "left": segment_texts[index],
                    "right": segment_texts[index + 1],
                }
            )
            break
        if next_cursor < len(normalized_full):
            previous_raw_index = raw_positions[next_cursor - 1]
            next_raw_index = raw_positions[next_cursor]
            if _boundary_breaks_word(full_text, previous_raw_index, next_raw_index):
                issues.append(
                    {
                        "index": index,
                        "reason": "mid-word-boundary",
                        "left": segment_texts[index],
                        "right": segment_texts[index + 1],
                    }
                )
                cursor = next_cursor
                continue
            if _boundary_breaks_phrase(segment_texts[index], segment_texts[index + 1]):
                issues.append(
                    {
                        "index": index,
                        "reason": "phrase-boundary",
                        "left": segment_texts[index],
                        "right": segment_texts[index + 1],
                    }
                )
        cursor = next_cursor
    return issues


def _boundary_breaks_phrase(left_text: str, right_text: str) -> bool:
    left = str(left_text or "").strip()
    right = str(right_text or "").strip()
    if not left or not right:
        return False
    if left[-1] in TERMINAL_PUNCTUATION:
        return False
    left_word = _edge_word(left, from_end=True)
    right_word = _edge_word(right, from_end=False)
    if not left_word or not right_word:
        return False
    lower_left = left_word.lower()
    lower_right = right_word.lower()
    if lower_left in SPACE_BOUNDARY_TRAILING_WORDS:
        return True
    if lower_right in SPACE_BOUNDARY_LEADING_WORDS:
        return True
    if left_word[0].isupper() and right_word[0].islower() and len(right_word) <= 6:
        return True
    return False


def fallback_source_segments(transcript: TranscriptDocument) -> list[Segment]:
    units = build_transcript_units(transcript)
    return [
        Segment(
            id=f"seg-{index + 1:04}",
            index=index,
            start=unit.start,
            end=unit.end,
            text=unit.text,
            anchor_start=unit.start_item,
            anchor_end=unit.end_item,
        )
        for index, unit in enumerate(units)
    ]


def _join_unit_texts(chunks: list[str]) -> str:
    merged: list[str] = []
    for chunk in chunks:
        value = chunk.strip()
        if not value:
            continue
        if not merged:
            merged.append(value)
            continue
        previous = merged[-1]
        previous_char = previous[-1]
        current_char = value[0]
        if previous_char in BREAK_PUNCTUATION and not _is_cjk(current_char):
            merged.append(f" {value}")
            continue
        if _needs_space(previous, value):
            merged.append(f" {value}")
            continue
        merged.append(value)
    return "".join(merged).strip()


def source_text_from_units(units: list[TranscriptUnit], start_index: int, end_index: int) -> str:
    return _join_unit_texts([unit.text for unit in units[start_index : end_index + 1]])
