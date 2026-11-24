from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.provider import MockProvider, ProviderResult


@pytest.fixture
def client(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'workflow.db').as_posix()}"
    with TestClient(create_app(database_url)) as client:
        yield client


def options(seed: int = 7) -> dict:
    return {
        "region": "51",
        "ethnicity": "01",
        "birth_year": 1988,
        "as_of": "2025-01-01",
        "entry": "birth_cohort",
        "household": "family",
        "detail": "rich",
        "seed": seed,
        "life_status": "living",
    }


def test_optional_generation_inputs_resolve_and_persist_with_history_trace(client):
    request = options()
    del request["birth_year"]
    del request["household"]
    validated = client.post("/api/generations/spec/validate", json=request)
    assert validated.status_code == 200
    spec = validated.json()["spec"]
    assert validated.json()["valid"] is True
    assert isinstance(spec["birth_year"], int)
    assert spec["household"] in {"individual", "family"}
    assert spec["sampling"]["birth_year"]["source"] == "sampled"
    assert spec["sampling"]["household"]["source"] == "sampled"
    assert spec["themes"] == [item["id"] for item in spec["history_selection"]]

    headers = {"Idempotency-Key": "automatic-generation"}
    response = client.post("/api/generations", json=request, headers=headers)
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["spec"] == spec
    events = client.get(f"/api/jobs/{job_id}/events").json()["events"]
    lookup = next(
        event["details"] for event in events if event["tool"] == "catalog.lookup"
    )
    assert lookup["birth_year"] == spec["birth_year"]
    assert lookup["household"] == spec["household"]
    assert lookup["sampling"] == spec["sampling"]
    assert lookup["history_selection"] == spec["history_selection"]

    duplicate = client.post(
        "/api/generations",
        json={**request, "birth_year": None, "household": None},
        headers=headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["job_id"] == job_id


@pytest.mark.parametrize(
    "route", ["/api/generations", "/api/generations/spec/validate"]
)
def test_generation_rejects_client_selected_history(client, route):
    response = client.post(route, json={**options(), "themes": ["DIS-WC-2008"]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "themes"]


def test_full_lifecycle_preserves_graph_scope_and_evidence(client):
    response = client.post(
        "/api/generations", json=options(), headers={"Idempotency-Key": "lifecycle-1"}
    )
    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "skeleton_review"
    graph_id = job["graph_revision_id"]

    duplicate = client.post(
        "/api/generations", json=options(), headers={"Idempotency-Key": "lifecycle-1"}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["job_id"] == job["job_id"]

    graph = client.get(f"/api/graphs/{graph_id}").json()
    assert graph["validation"]["valid"] is True
    assert any("地震" in node["title"] for node in graph["nodes"])

    event_id = graph["nodes"][0]["event_id"]
    edited = client.post(
        f"/api/graphs/{graph_id}/edits",
        json={
            "source": "manual",
            "operations": [
                {"op": "update", "event_id": event_id, "notes": "用户补充的可核验注记"}
            ],
        },
    )
    assert edited.status_code == 201
    child = edited.json()
    assert child["graph_revision_id"] != graph_id
    assert child["parent_revision_id"] == graph_id

    flesh = client.post(f"/api/graphs/{child['graph_revision_id']}/continue-flesh")
    assert flesh.status_code == 201
    flesh_job = flesh.json()
    assert flesh_job["detail_revision_id"]
    assert (
        client.post(f"/api/releases/{flesh_job['release_id']}/publish").status_code
        == 409
    )

    review = client.post(f"/api/reviews/{flesh_job['release_id']}/run")
    assert review.status_code == 200
    assert review.json()["status"] == "publish_pending"
    assert review.json()["review_status"] in {"passed", "repaired"}

    published = client.post(f"/api/releases/{flesh_job['release_id']}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "ready"

    archive = client.get("/api/archive/persons")
    assert archive.status_code == 200
    assert archive.json()["persons"][0]["release_id"] == flesh_job["release_id"]

    session = client.post(
        "/api/chat/sessions",
        json={
            "person_id": job["person"]["person_id"],
            "release_id": flesh_job["release_id"],
            "as_of": "2010-12-31",
        },
    )
    assert session.status_code == 201
    turn = client.post(
        f"/api/chat/sessions/{session.json()['session_id']}/turns",
        json={"question": "汶川地震发生时我在哪里？"},
    )
    assert turn.status_code == 201
    payload = turn.json()
    assert payload["evidence"]
    assert payload["evidence"][0]["scope"] == "context"
    assert payload["tools"]
    assert payload["evidence"][0]["start"] <= "2010-12-31"
    assert "没有证据证明我亲历现场" in payload["answer"]
    assert not any(
        claim in payload["answer"]
        for claim in ("我亲眼见到", "我亲身经历了汶川地震", "我当时在汶川")
    )


def test_invalid_edit_does_not_mutate_parent_and_missing_graph_is_404(client):
    created = client.post("/api/generations", json=options(seed=9)).json()
    graph_id = created["graph_revision_id"]
    response = client.post(
        f"/api/graphs/{graph_id}/edits",
        json={
            "source": "manual",
            "operations": [
                {"op": "update", "event_id": "missing-event", "notes": "bad"}
            ],
        },
    )
    assert response.status_code == 422
    assert client.get(f"/api/graphs/{graph_id}").json()["parent_revision_id"] is None
    assert client.get("/api/graphs/not-found").status_code == 404


def test_concurrent_continuation_reuses_the_same_release_candidate(client):
    job = client.post("/api/generations", json=options(seed=11)).json()
    route = f"/api/graphs/{job['graph_revision_id']}/continue-flesh"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(route), range(2)))
    assert sorted(result.status_code for result in responses) == [200, 201]
    assert len({result.json()["release_id"] for result in responses}) == 1


def test_reusing_generation_key_with_different_inputs_returns_conflict(client):
    headers = {"Idempotency-Key": "one-generation"}
    assert (
        client.post("/api/generations", json=options(), headers=headers).status_code
        == 201
    )
    assert (
        client.post(
            "/api/generations", json=options(seed=91), headers=headers
        ).status_code
        == 409
    )


def test_future_events_are_excluded_after_publication(client):
    job = client.post("/api/generations", json=options()).json()
    candidate = client.post(
        f"/api/graphs/{job['graph_revision_id']}/continue-flesh"
    ).json()
    release_id = candidate["release_id"]
    assert client.post(f"/api/reviews/{release_id}/run").status_code == 200
    assert client.post(f"/api/releases/{release_id}/publish").status_code == 200
    response = client.post(
        "/api/chat/sessions",
        json={
            "person_id": job["person"]["person_id"],
            "release_id": release_id,
            "as_of": "2007-12-31",
        },
    )
    assert response.status_code == 201
    scope = response.json()
    turn = client.post(
        f"/api/chat/sessions/{scope['session_id']}/turns",
        json={"question": "汶川地震时你在哪里？"},
    )
    assert turn.status_code == 201
    assert all(item["start"] <= "2007-12-31" for item in turn.json()["evidence"])
    assert "2008" not in turn.json()["answer"]


def test_chat_scope_is_distinct_from_archive_snapshot(client):
    job = client.post("/api/generations", json=options()).json()
    candidate = client.post(
        f"/api/graphs/{job['graph_revision_id']}/continue-flesh"
    ).json()
    release_id = candidate["release_id"]
    assert client.post(f"/api/reviews/{release_id}/run").status_code == 200
    assert client.post(f"/api/releases/{release_id}/publish").status_code == 200
    session = client.post(
        "/api/chat/sessions",
        json={
            "person_id": job["person"]["person_id"],
            "release_id": release_id,
            "as_of": "2010-12-31",
        },
    ).json()
    assert (
        session["snapshot_id"]
        != client.get("/api/archive/persons").json()["persons"][0]["snapshot_id"]
    )
    later = client.post(
        "/api/chat/sessions",
        json={
            "person_id": job["person"]["person_id"],
            "release_id": release_id,
            "as_of": "2015-12-31",
        },
    ).json()
    assert later["snapshot_id"] != session["snapshot_id"]


def test_api_rejects_non_iso_date_and_wrong_chat_role_scope(client):
    assert (
        client.post(
            "/api/generations/spec/validate", json={**options(), "as_of": "2025-01"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/chat/sessions",
            json={
                "person_id": "person",
                "release_id": "release",
                "as_of": "2025-01-01T00:00:00",
                "role_scope": "family",
            },
        ).status_code
        == 422
    )


class HallucinatingProvider(MockProvider):
    async def run(self, role, instruction, context, fallback):
        if role == "persona_qa":
            return ProviderResult(
                {"answer": "我亲眼看见汶川地震发生在眼前。"}, self.name
            )
        return await super().run(role, instruction, context, fallback)


class StubbornReviewer(MockProvider):
    def __init__(self):
        self.review_calls = 0

    async def run(self, role, instruction, context, fallback):
        if role == "reviewer":
            self.review_calls += 1
            if self.review_calls in (1, 2):
                event_id = context["chunks"][0]["event_id"]
                return ProviderResult(
                    {
                        "issues": [
                            {
                                "event_id": event_id,
                                "kind": "narrative",
                                "message": "仍缺少与锚点一致的个人细节",
                            }
                        ]
                    },
                    self.name,
                )
            return ProviderResult({"issues": []}, self.name)
        return await super().run(role, instruction, context, fallback)


class InvalidPlanner(MockProvider):
    async def run(self, role, instruction, context, fallback):
        if role == "spec_orchestrator":
            raise AssertionError("The skeleton generator must not call an LLM planner")
        return await super().run(role, instruction, context, fallback)


def test_model_cannot_turn_unverified_disaster_context_into_personal_memory(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'hallucination.db').as_posix()}"
    with TestClient(create_app(database_url, HallucinatingProvider())) as api_client:
        job = api_client.post("/api/generations", json=options()).json()
        candidate = api_client.post(
            f"/api/graphs/{job['graph_revision_id']}/continue-flesh"
        ).json()
        release_id = candidate["release_id"]
        api_client.post(f"/api/reviews/{release_id}/run")
        api_client.post(f"/api/releases/{release_id}/publish")
        session = api_client.post(
            "/api/chat/sessions",
            json={
                "person_id": job["person"]["person_id"],
                "release_id": release_id,
                "as_of": "2010-12-31",
            },
        ).json()
        turn = api_client.post(
            f"/api/chat/sessions/{session['session_id']}/turns",
            json={"question": "汶川地震发生时你亲眼看见了吗？"},
        ).json()
        assert "我亲眼看见汶川地震发生在眼前" not in turn["answer"]
        assert "没有证据证明我亲历现场" in turn["answer"]


def test_model_cannot_remove_automatically_matched_generation_rules(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'invalid-plan.db').as_posix()}"
    with TestClient(create_app(database_url, InvalidPlanner())) as api_client:
        response = api_client.post("/api/generations", json=options())
        assert response.status_code == 201
        revision = response.json()["graph_revision_id"]
        graph = api_client.get(f"/api/graphs/{revision}").json()
        assert any(node["event_id"] == "DIS-WC-2008" for node in graph["nodes"])
        assert api_client.get("/api/archive/persons").json()["persons"] == []


def test_failed_independent_recheck_blocks_publication(tmp_path):
    reviewer = StubbornReviewer()
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'review-recheck.db').as_posix()}"
    with TestClient(create_app(database_url, reviewer)) as api_client:
        job = api_client.post("/api/generations", json=options()).json()
        candidate = api_client.post(
            f"/api/graphs/{job['graph_revision_id']}/continue-flesh"
        ).json()
        release_id = candidate["release_id"]
        reviewed = api_client.post(f"/api/reviews/{release_id}/run")
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "skeleton_review"
        assert reviewed.json()["review_status"] == "blocked"
        assert reviewer.review_calls == 2
        assert api_client.post(f"/api/releases/{release_id}/publish").status_code == 409
