#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests


def _load_state(repo: Path, project_id: str) -> dict:
    path = repo / "projects" / project_id / "jobs" / "state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _start_target(base_url: str, project_id: str) -> None:
    response = requests.post(f"{base_url}/api/projects/{project_id}/process-target", timeout=30)
    response.raise_for_status()


def _wait_for_completion(repo: Path, project_id: str, poll_seconds: float) -> int:
    last = None
    while True:
        state = _load_state(repo, project_id)
        snapshot = (
            bool(state.get("running")),
            str(state.get("stage") or ""),
            int(state.get("progress") or 0),
            str(state.get("message") or ""),
        )
        if snapshot != last:
            print(f"{project_id}: {snapshot}", flush=True)
            last = snapshot
        if not state.get("running"):
            stage = str(state.get("stage") or "")
            if stage == "complete":
                return 0
            return 1
        time.sleep(max(poll_seconds, 2.0))


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequential target regression runner")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    base_url = str(args.base_url).rstrip("/")

    for project_id in args.project:
        state = _load_state(repo, project_id)
        if state.get("running"):
            print(f"{project_id}: already running, attaching watcher", flush=True)
        else:
            print(f"{project_id}: starting process-target", flush=True)
            _start_target(base_url, project_id)
        result = _wait_for_completion(repo, project_id, args.poll_seconds)
        if result != 0:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
