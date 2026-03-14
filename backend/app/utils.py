from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned or "project"


def make_project_id(name: str) -> str:
    return f"{slugify(name)}-{uuid4().hex[:8]}"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def truncate_text(value: str, max_length: int = 160) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max(0, max_length - 1)].rstrip()}…"


def summarize_error_text(raw_text: str | None, *, max_length: int = 160) -> str:
    text = str(raw_text or "").replace("\0", "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        match = re.search(r'"message"\s*:\s*"([^"]+)"', line)
        if match:
            return truncate_text(match.group(1), max_length=max_length)
    for line in reversed(lines):
        if re.match(r"^(RuntimeError|ValueError|TypeError|AssertionError|FileNotFoundError|KeyError|OSError):\s*", line):
            return truncate_text(re.sub(r"^[A-Za-z_][A-Za-z0-9_]*:\s*", "", line), max_length=max_length)
    meaningful: list[str] = []
    for line in lines:
        if re.match(r"^Traceback\b", line):
            break
        if re.match(r'^File ".+", line \d+, in ', line):
            continue
        if re.match(r"^(OpenAI Codex\b|--------$|workdir:|model:|provider:|approval:|sandbox:|reasoning effort:|reasoning summaries:|session id:|user$|mcp startup:)", line):
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}T", line):
            continue
        meaningful.append(re.sub(r"^[A-Za-z_][A-Za-z0-9_]*Error:\s*", "", line))
    summary = meaningful[0] if meaningful else re.sub(r"^[A-Za-z_][A-Za-z0-9_]*Error:\s*", "", lines[0])
    return truncate_text(summary, max_length=max_length)


def normalize_error_fields(
    error: str | None,
    error_detail: str | None = None,
    *,
    summary_max_length: int = 160,
) -> tuple[str | None, str | None]:
    raw_error = str(error or "").replace("\0", "").strip()
    raw_detail = str(error_detail or "").replace("\0", "").strip()
    if raw_detail:
        summary = summarize_error_text(raw_error or raw_detail, max_length=summary_max_length)
        return (summary or None, raw_detail)
    if not raw_error:
        return None, None
    if "\n" in raw_error or len(raw_error) > summary_max_length or "Traceback" in raw_error:
        summary = summarize_error_text(raw_error, max_length=summary_max_length)
        return (summary or None, raw_error)
    return raw_error, None
