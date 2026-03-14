from __future__ import annotations

import os
import signal
import sys
import threading
import traceback

from .config import ensure_base_dirs
from .pipeline import run_full_pipeline, run_source_correction_pipeline, run_source_pipeline, run_target_pipeline, stop_interrupted_job
from .storage import load_state, touch_runtime


class WorkerHeartbeat:
    def __init__(self, project_id: str, worker_pid: int, *, interval_seconds: float = 3.0) -> None:
        self.project_id = project_id
        self.worker_pid = worker_pid
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"{project_id}-heartbeat", daemon=True)

    def start(self) -> None:
        touch_runtime(self.project_id, worker_pid=self.worker_pid)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=self.interval_seconds + 1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            touch_runtime(self.project_id, worker_pid=self.worker_pid)


def _install_signal_handlers() -> None:
    def _handle_signal(signum: int, _frame) -> None:
        raise SystemExit(signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def _run_mode(project_id: str, mode: str) -> int:
    if mode == "source":
        run_source_pipeline(project_id)
        return 0
    if mode == "review-source":
        run_source_correction_pipeline(project_id)
        return 0
    if mode == "target":
        run_target_pipeline(project_id)
        return 0
    if mode == "target-resynthesize":
        run_target_pipeline(project_id, reuse_existing_target_text=True)
        return 0
    if mode == "full":
        run_full_pipeline(project_id)
        return 0
    print(f"Unsupported mode: {mode}")
    return 1


def main() -> int:
    ensure_base_dirs()
    if len(sys.argv) not in {2, 3}:
        print("Usage: python -m backend.app.worker <project_id> [source|review-source|target|target-resynthesize|full]")
        return 1
    project_id = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) == 3 else "source"
    _install_signal_handlers()
    heartbeat = WorkerHeartbeat(project_id, os.getpid())
    heartbeat.start()
    try:
        return _run_mode(project_id, mode)
    except BaseException as exc:  # noqa: BLE001
        traceback.print_exc()
        heartbeat.stop()
        if load_state(project_id).running:
            stop_interrupted_job(project_id)
        if isinstance(exc, SystemExit):
            return exc.code if isinstance(exc.code, int) else 1
        return 1
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    raise SystemExit(main())
