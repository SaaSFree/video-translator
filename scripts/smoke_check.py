from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.main import app


def main() -> int:
    with TestClient(app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "<title>Local Video Review Workbench</title>" in root.text

        projects = client.get("/api/projects")
        assert projects.status_code == 200
        payload = projects.json()
        assert payload["projects"], "expected at least one seeded project"
        project_id = payload["projects"][0]["id"]

        detail = client.get(f"/api/projects/{project_id}")
        assert detail.status_code == 200
        project_payload = detail.json()
        assert project_payload["manifest"]["status"] in {"ready", "edited"}
        assert project_payload["source_segments"]["segments"]
        assert project_payload["target_segments"]["segments"]

        first_source = project_payload["source_segments"]["segments"][0]
        updated_text = "这里是 smoke check 更新后的原文。"
        update = client.put(
            f"/api/projects/{project_id}/segments/source/{first_source['id']}",
            json={"text": updated_text},
        )
        assert update.status_code == 200

        detail_after = client.get(f"/api/projects/{project_id}").json()
        assert detail_after["source_segments"]["segments"][0]["text"] == updated_text

        target_video = detail_after["paths"]["target_video"]
        root_name, *rest = target_video.split("/")
        media = client.get(f"/media/{project_id}/{root_name}/" + "/".join(rest))
        assert media.status_code == 200
        assert media.headers["content-type"] == "video/mp4"

    print("smoke_check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
