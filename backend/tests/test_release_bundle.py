from fastapi.testclient import TestClient

from app.main import create_app
from app.provider import MockProvider


def test_release_bundle_pins_published_graph_detail_and_review(tmp_path):
    app = create_app(
        f"sqlite+aiosqlite:///{tmp_path / 'bundle.db'}",
        provider_override=MockProvider(),
    )
    with TestClient(app) as client:
        options = client.get("/api/catalog").json()["defaults"]
        job = client.post("/api/generations", json=options).json()
        graph_id = job["graph_revision_id"]
        detail = client.post(f"/api/graphs/{graph_id}/continue-flesh").json()
        release = detail["release_id"]
        url = f"/api/archive/releases/{release}"
        assert client.get(url).status_code == 404
        assert client.post(f"/api/reviews/{release}/run").is_success
        assert client.post(f"/api/releases/{release}/publish").is_success

        response = client.get(url)
        assert response.status_code == 200
        bundle = response.json()
        assert bundle["release_id"] == release
        assert bundle["graph"]["graph_revision_id"] == graph_id
        assert bundle["detail"]["graph_revision_id"] == graph_id
        assert bundle["review"]["release_id"] == release
        assert bundle["snapshot_id"]
        assert bundle["synthetic"]
