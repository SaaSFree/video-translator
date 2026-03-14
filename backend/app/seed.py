from __future__ import annotations

import shutil

from .config import DEFAULT_TEST_VIDEO_PATH
from .media import make_demo_video
from .storage import create_project, list_projects, project_paths


def ensure_demo_project() -> None:
    if list_projects():
        return
    manifest = create_project("Demo Review Session")
    destination = project_paths(manifest.id)["source_video"]
    if DEFAULT_TEST_VIDEO_PATH.exists():
        shutil.copyfile(DEFAULT_TEST_VIDEO_PATH, destination)
    else:
        make_demo_video(destination)
