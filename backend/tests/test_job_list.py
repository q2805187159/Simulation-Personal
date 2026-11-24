from fastapi.testclient import TestClient

from app.main import create_app
from app.provider import MockProvider


def test_recent_jobs_return_resumable_draft_and_respect_limit(tmp_path):
    app = create_app(
        f"sqlite+aiosqlite:///{tmp_path / 'recent.db'}",
        provider_override=MockProvider(),
    )
    with TestClient(app) as client:
        options = client.get("/api/catalog").json()["defaults"]
        jobs = [
            client.post("/api/generations", json={**options, "seed": seed}).json()
            for seed in (101, 102)
        ]
        response = client.get("/api/jobs?limit=1")
        assert response.status_code == 200
        assert len(response.json()["jobs"]) == 1
        item = response.json()["jobs"][0]
        latest = jobs[-1]
        assert item["job_id"] == latest["job_id"]
        assert item["graph_revision_id"] == latest["graph_revision_id"]
        assert item["person"]["name"] == latest["person"]["name"]
        assert item["status"] == "skeleton_review"
        assert item["created_at"]
