from __future__ import annotations

import subprocess
import sys
import shutil
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR, ROOT_DIR, ensure_base_dirs, load_runtime_settings, project_language_options, save_runtime_settings
from .media import align_cut_time_to_pause, clip_video, extract_audio, ffprobe_duration
from .providers import get_system_status
from .seed import ensure_demo_project
from .source_review import (
    apply_all_source_correction_actions,
    apply_source_correction_action,
    stop_running_source_correction_review,
)
from .storage import (
    ALLOWED_MEDIA_ROOTS,
    create_project,
    delete_project,
    load_manifest,
    list_projects,
    load_project_detail,
    load_sidebar_state,
    load_state,
    project_dir,
    project_paths,
    reset_project_outputs,
    save_sidebar_state,
    serialize_manifest,
    stop_project_job,
    update_project_settings,
    update_source_segment_text,
    update_target_segment_text,
)
from .utils import now_iso


_CLIP_TASK_LOCK = threading.Lock()
_CLIP_TASK: dict[str, object] | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_base_dirs()
    ensure_demo_project()
    yield


app = FastAPI(title="Video Translater Source Workbench", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def _build_export_bundle(project_id: str) -> Path:
    manifest = load_manifest(project_id)
    base_dir = project_dir(project_id)
    export_dir = base_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = export_dir / f"{project_id}-project.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export_manifest.json", manifest.model_dump_json(indent=2))
        for path in sorted(base_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == bundle_path.resolve():
                continue
            archive.write(path, path.relative_to(base_dir).as_posix())
    return bundle_path


def _clip_task_snapshot() -> dict[str, object] | None:
    with _CLIP_TASK_LOCK:
        if _CLIP_TASK is None:
            return None
        return dict(_CLIP_TASK)


def _set_clip_task(task: dict[str, object] | None) -> dict[str, object] | None:
    global _CLIP_TASK
    with _CLIP_TASK_LOCK:
        _CLIP_TASK = dict(task) if task is not None else None
        return None if _CLIP_TASK is None else dict(_CLIP_TASK)


def _update_clip_task(**patch: object) -> dict[str, object] | None:
    global _CLIP_TASK
    with _CLIP_TASK_LOCK:
        if _CLIP_TASK is None:
            return None
        _CLIP_TASK.update(patch)
        _CLIP_TASK["updated_at"] = now_iso()
        return dict(_CLIP_TASK)


def _clip_task_payload(task: dict[str, object] | None) -> dict[str, object] | None:
    if task is None:
        return None
    total = max(float(task.get("total_seconds") or 0.0), 0.0)
    completed = max(0.0, min(float(task.get("completed_seconds") or 0.0), total or float(task.get("completed_seconds") or 0.0)))
    ratio = 0.0 if total <= 0 else min(max(completed / total, 0.0), 1.0)
    payload = dict(task)
    payload["completed_seconds"] = round(completed, 3)
    payload["total_seconds"] = round(total, 3)
    payload["progress_ratio"] = ratio
    payload["progress_percent"] = round(ratio * 100, 1)
    return payload


def _clip_task_running() -> bool:
    task = _clip_task_snapshot()
    return bool(task and task.get("status") in {"queued", "running"})


def _raise_if_clip_task_running() -> None:
    if _clip_task_running():
        raise HTTPException(status_code=409, detail="A clip task is already running.")


def _start_clip_task(project_id: str, start_seconds: float, duration_seconds: float) -> dict[str, object]:
    current = _clip_task_snapshot()
    if current and current.get("status") in {"queued", "running"}:
        raise RuntimeError("Another clip task is already running.")
    if load_state(project_id).running:
        raise RuntimeError("Project is already running.")
    try:
        detail = load_project_detail(project_id)
    except FileNotFoundError as exc:
        raise FileNotFoundError("Project not found.") from exc
    source_video = project_paths(project_id)["source_video"]
    source_audio = project_paths(project_id)["source_audio"]
    if not source_video.exists():
        raise FileNotFoundError("Source video not found.")
    source_duration = max(float(duration_seconds), 1.0)
    task = {
        "id": f"clip-{uuid4().hex[:10]}",
        "source_project_id": project_id,
        "source_project_name": detail.manifest.name,
        "status": "queued",
        "stage": "queued",
        "message": "等待开始截取片段。",
        "requested_start_seconds": round(start_seconds, 3),
        "requested_duration_seconds": round(duration_seconds, 3),
        "requested_end_seconds": round(start_seconds + duration_seconds, 3),
        "adjusted_end_seconds": round(start_seconds + duration_seconds, 3),
        "completed_seconds": 0.0,
        "total_seconds": source_duration,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "error": None,
        "result_project_id": None,
        "result_project_name": None,
    }
    _set_clip_task(task)

    def _run() -> None:
        temp_audio_path: Path | None = None
        temp_clip_path: Path | None = None
        created_project_id: str | None = None
        extract_duration = 0.0
        resolved_duration = source_duration
        try:
            _update_clip_task(status="running", stage="preparing", message="正在准备截取参数。")
            analysis_audio = source_audio
            if not analysis_audio.exists():
                with NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                    temp_audio_path = Path(handle.name)
                extract_duration = ffprobe_duration(source_video)
                total_work = extract_duration + source_duration

                def _extract_progress(current: float, _total: float, detail_message: str | None = None) -> None:
                    _update_clip_task(
                        stage="extracting_audio",
                        message=detail_message or "正在提取分析音频。",
                        completed_seconds=current,
                        total_seconds=total_work,
                    )

                extract_audio(
                    source_video,
                    temp_audio_path,
                    progress_callback=_extract_progress,
                    progress_message="正在提取分析音频。",
                )
                analysis_audio = temp_audio_path

            _update_clip_task(
                stage="aligning_cut",
                message="正在对齐片段结束点。",
                completed_seconds=extract_duration,
                total_seconds=extract_duration + source_duration,
            )
            requested_end = start_seconds + duration_seconds
            adjusted_end = align_cut_time_to_pause(analysis_audio, requested_end)
            resolved_duration = max(adjusted_end - start_seconds, 1.0)
            total_work = extract_duration + resolved_duration
            _update_clip_task(
                adjusted_end_seconds=round(adjusted_end, 3),
                total_seconds=total_work,
                completed_seconds=extract_duration,
            )

            with NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
                temp_clip_path = Path(handle.name)

            def _clip_progress(current: float, _total: float, detail_message: str | None = None) -> None:
                _update_clip_task(
                    stage="clipping_video",
                    message=detail_message or "正在截取视频片段。",
                    completed_seconds=extract_duration + current,
                    total_seconds=total_work,
                )

            clip_video(
                source_video,
                temp_clip_path,
                start_seconds=start_seconds,
                duration_seconds=resolved_duration,
                progress_callback=_clip_progress,
                progress_message="正在截取视频片段。",
            )

            _update_clip_task(
                stage="finalizing",
                message="正在写入新项目。",
                completed_seconds=total_work,
                total_seconds=total_work,
            )
            manifest = create_project(f"{detail.manifest.name} 片段", source_language=detail.manifest.source_language)
            created_project_id = manifest.id
            destination = project_paths(manifest.id)["source_video"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_clip_path), str(destination))
            temp_clip_path = None
            _update_clip_task(
                status="completed",
                stage="completed",
                message="片段截取完成。",
                completed_seconds=total_work,
                total_seconds=total_work,
                result_project_id=manifest.id,
                result_project_name=manifest.name,
            )
        except Exception as exc:  # noqa: BLE001
            if created_project_id:
                try:
                    delete_project(created_project_id)
                except FileNotFoundError:
                    pass
            _update_clip_task(
                status="failed",
                stage="failed",
                message="片段截取失败。",
                error=str(exc),
            )
        finally:
            if temp_audio_path:
                temp_audio_path.unlink(missing_ok=True)
            if temp_clip_path:
                temp_clip_path.unlink(missing_ok=True)

    threading.Thread(target=_run, name=f"clip-task-{task['id']}", daemon=True).start()
    return _clip_task_payload(task) or task


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/projects")
async def api_list_projects():
    return {"projects": [serialize_manifest(item) for item in list_projects()]}


@app.get("/api/system/status")
async def api_system_status():
    return get_system_status()


@app.get("/api/system/settings")
async def api_system_settings():
    status = get_system_status()
    return {
        "settings": status["settings"],
        "options": {
            **status["options"],
            **project_language_options(),
        },
    }


@app.put("/api/system/settings")
async def api_update_system_settings(payload: dict[str, str]):
    try:
        save_runtime_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_system_status()


@app.get("/api/ui/sidebar-state")
async def api_sidebar_state():
    return load_sidebar_state()


@app.put("/api/ui/sidebar-state")
async def api_update_sidebar_state(payload: dict[str, object | None]):
    return save_sidebar_state(payload)


@app.get("/api/clip-task")
async def api_clip_task():
    return {"task": _clip_task_payload(_clip_task_snapshot())}


@app.delete("/api/clip-task")
async def api_clear_clip_task():
    _set_clip_task(None)
    return {"ok": True}


@app.post("/api/projects/import")
async def api_import_project(request: Request, filename: str, name: str | None = None):
    _raise_if_clip_task_running()
    if not filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Only mp4 uploads are supported.")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Upload body is empty.")
    manifest = create_project(name or Path(filename).stem)
    destination = project_paths(manifest.id)["source_video"]
    destination.write_bytes(body)
    return {"project": manifest.model_dump()}


@app.post("/api/projects/{project_id}/transcribe-source")
async def api_transcribe_source(project_id: str):
    _raise_if_clip_task_running()
    state = load_state(project_id)
    if state.running:
        raise HTTPException(status_code=409, detail="Project is already running.")
    try:
        load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    reset_project_outputs(project_id, preserve_source_stage=False, preserve_source_review=False)
    _start_worker(project_id, mode="source")
    return {"ok": True}


@app.post("/api/projects/{project_id}/translate-target")
async def api_translate_target(project_id: str, payload: dict[str, str] | None = Body(default=None)):
    _raise_if_clip_task_running()
    state = load_state(project_id)
    if state.running:
        raise HTTPException(status_code=409, detail="Project is already running.")
    try:
        load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    mode = str((payload or {}).get("mode", "")).strip()
    worker_mode = "target-resynthesize" if mode == "resynthesize" else "target"
    _start_worker(project_id, mode=worker_mode)
    return {"ok": True}


@app.post("/api/projects/{project_id}/run-full")
async def api_run_full(project_id: str):
    _raise_if_clip_task_running()
    state = load_state(project_id)
    if state.running:
        raise HTTPException(status_code=409, detail="Project is already running.")
    try:
        load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    reset_project_outputs(project_id, preserve_source_stage=False, preserve_source_review=False)
    _start_worker(project_id, mode="full")
    return {"ok": True}


@app.post("/api/projects/{project_id}/clip-test")
async def api_clip_test_project(project_id: str, start_seconds: float = 0.0, duration_seconds: float = 180.0):
    if start_seconds < 0:
        raise HTTPException(status_code=400, detail="Start time must be greater than or equal to 0.")
    if duration_seconds <= 0:
        raise HTTPException(status_code=400, detail="Duration must be greater than 0.")
    try:
        task = _start_clip_task(project_id, start_seconds, duration_seconds)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"task": task}


@app.get("/api/projects/{project_id}")
async def api_project_detail(project_id: str):
    try:
        detail = load_project_detail(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    payload = detail.model_dump()
    payload["manifest"] = serialize_manifest(detail.manifest, job=detail.job)
    return payload


@app.put("/api/projects/{project_id}/settings")
async def api_update_project_settings(project_id: str, payload: dict[str, str]):
    source_language_options = {item["value"] for item in project_language_options()["source_language"]}
    target_language_options = {item["value"] for item in project_language_options()["target_language"]}
    if "source_language" in payload and payload["source_language"] not in source_language_options:
        raise HTTPException(status_code=400, detail=f"Unsupported value for source_language: {payload['source_language']}")
    if "target_language" in payload and payload["target_language"] not in target_language_options:
        raise HTTPException(status_code=400, detail=f"Unsupported value for target_language: {payload['target_language']}")
    try:
        manifest = update_project_settings(project_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    return {"project": manifest.model_dump()}


@app.get("/api/projects/{project_id}/export")
async def api_export_project(project_id: str):
    try:
        bundle_path = _build_export_bundle(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    return FileResponse(bundle_path, filename=bundle_path.name, media_type="application/zip")


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str):
    _raise_if_clip_task_running()
    state = load_state(project_id)
    if state.running:
        raise HTTPException(status_code=409, detail="Project is currently running.")
    try:
        delete_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    return {"ok": True}


@app.post("/api/projects/{project_id}/clear")
async def api_clear_project(project_id: str):
    _raise_if_clip_task_running()
    state = load_state(project_id)
    if state.running:
        raise HTTPException(status_code=409, detail="Project is currently running.")
    try:
        load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    reset_project_outputs(project_id, preserve_source_stage=False, preserve_source_review=False)
    return {"project": serialize_manifest(load_manifest(project_id))}


@app.post("/api/projects/{project_id}/stop")
async def api_stop_project(project_id: str):
    try:
        load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    try:
        job = stop_project_job(project_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    stop_running_source_correction_review(project_id)
    return {"ok": True, "job": job.model_dump(), "project": serialize_manifest(load_manifest(project_id), job=job)}


@app.put("/api/projects/{project_id}/segments/{segment_id}")
async def api_update_segment(project_id: str, segment_id: str, payload: dict[str, str]):
    if "text" not in payload:
        raise HTTPException(status_code=400, detail="Missing text.")
    document = update_source_segment_text(project_id, segment_id, payload["text"])
    return {"segments": document.model_dump()}


@app.put("/api/projects/{project_id}/target-segments/{segment_id}")
async def api_update_target_segment(project_id: str, segment_id: str, payload: dict[str, str]):
    if "text" not in payload:
        raise HTTPException(status_code=400, detail="Missing text.")
    try:
        document = update_target_segment_text(project_id, segment_id, payload["text"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"segments": document.model_dump()}


@app.put("/api/projects/{project_id}/source-corrections/{segment_id}")
async def api_update_source_correction(project_id: str, segment_id: str, payload: dict[str, str]):
    action = str(payload.get("action", "")).strip()
    if action not in {"accept", "reject", "custom"}:
        raise HTTPException(status_code=400, detail="Invalid action.")
    try:
        source_document, review = apply_source_correction_action(
            project_id,
            segment_id,
            action=action,
            custom_text=payload.get("text"),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"source_segments": source_document.model_dump(), "source_correction_review": review.model_dump()}


@app.post("/api/projects/{project_id}/source-corrections/bulk")
async def api_bulk_source_corrections(project_id: str, payload: dict[str, str]):
    action = str(payload.get("action", "")).strip()
    if action not in {"accept", "reject"}:
        raise HTTPException(status_code=400, detail="Invalid action.")
    try:
        source_document, review = apply_all_source_correction_actions(project_id, action=action)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"source_segments": source_document.model_dump(), "source_correction_review": review.model_dump()}


@app.get("/media/{project_id}/{root}/{path:path}")
async def api_media(project_id: str, root: str, path: str):
    if root not in ALLOWED_MEDIA_ROOTS:
        raise HTTPException(status_code=404, detail="Invalid media root.")
    file_path = (project_dir(project_id) / root / path).resolve()
    base_root = (project_dir(project_id) / root).resolve()
    if not str(file_path).startswith(str(base_root)) or not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(file_path)


def _start_worker(project_id: str, mode: str = "source") -> None:
    log_path = project_dir(project_id) / "logs" / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_handle:
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "backend.app.worker", project_id, mode],
            cwd=str(ROOT_DIR),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
