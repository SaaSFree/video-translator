#!/opt/anaconda3/bin/python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _candidate_hosts() -> list[str]:
    hosts = ["127.0.0.1", "localhost"]
    for interface in ("en0", "en1"):
        try:
            value = subprocess.check_output(["ipconfig", "getifaddr", interface], text=True).strip()
        except Exception:
            continue
        if value and value not in hosts:
            hosts.append(value)
    return hosts


def port_open(port: int, host: str | None = None) -> bool:
    hosts = [host] if host else _candidate_hosts()
    for candidate in hosts:
        if not candidate:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                if sock.connect_ex((candidate, port)) == 0:
                    return True
        except Exception:
            continue
    return False


def notify(title: str, message: str) -> None:
    safe_title = title.replace('"', "'")
    safe_message = message.replace('"', "'")
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{safe_message}" with title "{safe_title}"',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        pass


@dataclass
class ProjectStatus:
    project_id: str
    title: str
    running: bool
    stage: str
    progress: int
    message: str
    updated_at: str | None
    error: str | None


def load_project_status(project_dir: Path) -> ProjectStatus | None:
    manifest_path = project_dir / "project.json"
    state_path = project_dir / "jobs" / "state.json"
    if not manifest_path.exists() or not state_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
        state = json.loads(state_path.read_text())
    except Exception:
        return None
    return ProjectStatus(
        project_id=project_dir.name,
        title=str(manifest.get("title") or manifest.get("name") or project_dir.name),
        running=bool(state.get("running")),
        stage=str(state.get("stage") or ""),
        progress=int(state.get("progress") or 0),
        message=str(state.get("message") or ""),
        updated_at=state.get("updated_at"),
        error=state.get("error"),
    )


def log_line(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = now_utc().isoformat()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--stale-seconds", type=float, default=45.0)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    projects_root = repo / "projects"
    log_path = repo / "output" / "guardian" / "project-guardian.log"
    state_cache: dict[str, tuple[bool, str, int, str]] = {}
    alerted_errors: set[str] = set()
    alerted_complete: set[str] = set()
    stale_alerts: set[str] = set()
    server_up = None

    log_line(log_path, f"Guardian started for: {', '.join(args.project)}")

    while True:
        try:
            is_up = port_open(args.port, host=str(args.host or "").strip() or None)
            if is_up != server_up:
                state = "up" if is_up else "down"
                log_line(log_path, f"Backend port {args.port} is {state}.")
                if not is_up:
                    notify("Video Translator Guardian", f"Backend port {args.port} is down.")
                server_up = is_up

            for project_id in args.project:
                status = load_project_status(projects_root / project_id)
                if status is None:
                    continue
                snapshot = (status.running, status.stage, status.progress, status.message)
                if state_cache.get(project_id) != snapshot:
                    log_line(
                        log_path,
                        f"{project_id}: running={status.running} stage={status.stage} progress={status.progress} message={status.message}",
                    )
                    state_cache[project_id] = snapshot

                updated_at = parse_iso(status.updated_at)
                age_seconds = (now_utc() - updated_at).total_seconds() if updated_at else None
                stale_key = f"{project_id}:{status.stage}"
                if status.running and age_seconds is not None and age_seconds > args.stale_seconds:
                    if stale_key not in stale_alerts:
                        log_line(log_path, f"{project_id}: stalled for {age_seconds:.0f}s at {status.stage}.")
                        notify("Video Translator Guardian", f"{status.title} 卡住了：{status.stage}")
                        stale_alerts.add(stale_key)
                else:
                    stale_alerts.discard(stale_key)

                if status.stage == "error":
                    if project_id not in alerted_errors:
                        error_text = (status.error or status.message or "任务失败").splitlines()[0]
                        log_line(log_path, f"{project_id}: ERROR {error_text}")
                        notify("Video Translator Guardian", f"{status.title} 失败：{error_text}")
                        alerted_errors.add(project_id)
                else:
                    alerted_errors.discard(project_id)

                if not status.running and status.stage == "complete":
                    if project_id not in alerted_complete:
                        log_line(log_path, f"{project_id}: COMPLETE")
                        notify("Video Translator Guardian", f"{status.title} 已完成。")
                        alerted_complete.add(project_id)
                else:
                    alerted_complete.discard(project_id)
        except Exception as exc:
            log_line(log_path, f"Guardian loop error: {exc}")

        time.sleep(max(args.interval, 2.0))


if __name__ == "__main__":
    raise SystemExit(main())
