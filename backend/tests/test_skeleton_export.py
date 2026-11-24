from fastapi.testclient import TestClient

from app.main import create_app
from app.provider import MockProvider


def test_export_downloads_exact_unpublished_skeleton_revision(tmp_path):
    app = create_app(f"sqlite+aiosqlite:///{tmp_path / 'export.db'}", MockProvider())
    with TestClient(app) as client:
        options = client.get("/api/catalog").json()["defaults"]
        job = client.post("/api/generations", json=options).json()
        assert job["status"] == "skeleton_review"
        assert not job["detail_revision_id"]
        graph_id = job["graph_revision_id"]
        url = f"/api/graphs/{graph_id}"
        original = client.get(url).json()
        changed = client.post(
            f"{url}/edits",
            json={
                "source": "manual",
                "operations": [
                    {"op": "update", "event_id": "birth", "notes": "测试修订"}
                ],
            },
        )
        assert changed.status_code == 201
        child_id = changed.json()["graph_revision_id"]
        assert child_id != graph_id

        response = client.get(f"{url}/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert (
            response.headers["content-disposition"]
            == f'attachment; filename="skeleton-{graph_id}.json"'
        )
        assert response.json() == original
        child = client.get(f"/api/graphs/{child_id}/export").json()
        assert child["parent_revision_id"] == graph_id
        assert child["graph_revision_id"] == child_id
        assert client.get("/api/graphs/missing/export").status_code == 404
