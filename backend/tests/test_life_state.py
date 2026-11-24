from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.life_state import project_life_state
from app.main import create_app
from app.provider import MockProvider


def _graph(nodes=None):
    return {
        "person": {
            "person_id": "primary",
            "birth_year": 1980,
            "family": [{"id": "sibling", "name": "Sibling"}],
        },
        "nodes": nodes or [],
    }


def _fact(participant, field, projection):
    person = next(
        item for item in projection["participants"] if item["person_id"] == participant
    )
    return next(item for item in person["facts"] if item["field"] == field)


def test_projection_filters_future_valid_time_and_later_known_at():
    graph = _graph(
        [
            {
                "event_id": "work",
                "category": "work",
                "start": "2020-01-01",
                "end": None,
                "precision": "day",
                "participants": ["primary"],
                "state_delta": {"work_status": "working"},
                "known_at": {"primary": "2020-01-03"},
                "recorded_at": "2020-01-04T00:00:00+00:00",
                "evidence": [{"id": "work-record"}],
            },
            {
                "event_id": "promotion",
                "category": "work",
                "start": "2020-01-05",
                "end": None,
                "precision": "day",
                "participants": ["primary"],
                "state_delta": {"rank": 2},
            },
        ]
    )

    early = project_life_state(graph, "2020-01-02", required_fields=["work_status"])
    assert early["participants"][0]["status"] == "incomplete"
    assert _fact("primary", "work_status", early)["status"] == "incomplete"

    known = project_life_state(graph, "2020-01-03")
    fact = _fact("primary", "work_status", known)
    assert fact["value"] == "working"
    assert fact["value_type"] == "string"
    assert fact["provenance"] == [
        {
            "event_id": "work",
            "role": "subject",
            "valid_time": {
                "start": "2020-01-01",
                "end": None,
                "precision": "day",
            },
            "known_at": "2020-01-03",
            "recorded_at": "2020-01-04T00:00:00+00:00",
            "evidence_ids": ["work-record"],
        }
    ]
    assert not any(
        item["field"] == "rank" for item in known["participants"][0]["facts"]
    )


def test_projection_routes_participant_deltas_and_preserves_types_and_provenance():
    graph = _graph(
        [
            {
                "event_id": "shared-care",
                "category": "household",
                "start": "2022-04-01",
                "end": "2022-04-01",
                "precision": "day",
                "participants": ["primary", "sibling"],
                "state_delta": {"care": True, "pressure": 1},
                "participant_deltas": [
                    {
                        "person_id": "sibling",
                        "role": "supporter",
                        "delta": {"minutes": 15},
                    }
                ],
                "known_at": {"sibling": "2022-04-03"},
                "evidence": [{"id": "care-note"}],
            }
        ]
    )

    result = project_life_state(graph, "2022-04-02")

    assert _fact("primary", "care", result)["value_type"] == "boolean"
    assert _fact("primary", "pressure", result)["value_type"] == "integer"
    assert not any(
        item["field"] == "minutes"
        for item in next(
            p for p in result["participants"] if p["person_id"] == "sibling"
        )["facts"]
    )

    after_known = project_life_state(graph, "2022-04-03")
    supporter_fact = _fact("sibling", "minutes", after_known)
    assert supporter_fact["value"] == 15
    assert supporter_fact["provenance"][0]["role"] == "supporter"
    assert supporter_fact["provenance"][0]["event_id"] == "shared-care"


def test_projection_excludes_world_summary_and_decomposition_anchor_nodes():
    graph = _graph(
        [
            {
                "event_id": "world-context",
                "category": "history",
                "start": "2008-05-12",
                "participants": ["primary"],
                "state_delta": {"exposure": "candidate"},
            },
            {
                "event_id": "summary",
                "category": "work",
                "start": "2010-01-01",
                "summary_node": True,
                "participants": ["primary"],
                "state_delta": {"work": "summarized"},
            },
            {
                "event_id": "anchor",
                "category": "work",
                "start": "2011-01-01",
                "summary_node": True,
                "participants": ["primary"],
                "state_delta": {},
                "decomposition_state_delta": {"work": "decomposed"},
            },
            {
                "event_id": "personal-work",
                "category": "work",
                "start": "2012-01-01",
                "participants": ["primary"],
                "state_delta": {"work": "recorded"},
            },
        ]
    )

    result = project_life_state(graph, "2012-12-31")

    assert [
        row["event_id"] for row in _fact("primary", "work", result)["provenance"]
    ] == ["personal-work"]
    assert not any(
        fact["field"] == "exposure" for fact in result["participants"][0]["facts"]
    )


def test_same_day_disagreement_is_conflict_and_later_value_supersedes_it():
    graph = _graph(
        [
            {
                "event_id": "first",
                "category": "work",
                "start": "2020-06-01",
                "participants": ["primary"],
                "state_delta": {"work_status": "employed"},
            },
            {
                "event_id": "second",
                "category": "work",
                "start": "2020-06-01",
                "participants": ["primary"],
                "state_delta": {"work_status": "self-employed"},
            },
            {
                "event_id": "third",
                "category": "work",
                "start": "2020-06-02",
                "participants": ["primary"],
                "state_delta": {"work_status": "retired"},
            },
        ]
    )

    conflicting = project_life_state(graph, "2020-06-01")
    fact = _fact("primary", "work_status", conflicting)
    assert conflicting["participants"][0]["status"] == "conflict"
    assert fact["status"] == "conflict"
    assert fact["candidate_values"] == ["employed", "self-employed"]
    assert [row["event_id"] for row in fact["provenance"]] == ["first", "second"]

    resolved = project_life_state(graph, "2020-06-02")
    fact = _fact("primary", "work_status", resolved)
    assert resolved["participants"][0]["status"] == "consistent"
    assert fact["value"] == "retired"
    assert fact["provenance"][0]["event_id"] == "third"


def test_incomplete_required_fields_remain_distinct_from_conflicts():
    graph = _graph(
        [
            {
                "event_id": "one",
                "category": "work",
                "start": "2020-06-01",
                "participants": ["primary"],
                "state_delta": {"work_status": "employed"},
            },
            {
                "event_id": "two",
                "category": "work",
                "start": "2020-06-01",
                "participants": ["primary"],
                "state_delta": {"work_status": "self-employed"},
            },
        ]
    )

    result = project_life_state(
        graph,
        "2020-06-01",
        required_fields=("work_status", "work_location"),
    )

    assert result["participants"][0]["status"] == "conflict"
    assert result["participants"][0]["conflict_fields"] == ["work_status"]
    assert result["participants"][0]["missing_fields"] == ["work_location"]
    assert _fact("primary", "work_location", result)["status"] == "incomplete"


def test_projection_is_pure_and_rejects_invalid_as_of():
    graph = _graph(
        [
            {
                "event_id": "one",
                "category": "work",
                "start": "2020-06-01",
                "participants": ["primary"],
                "state_delta": {"work_status": "employed"},
            }
        ]
    )
    original = deepcopy(graph)

    project_life_state(graph, "2020-06-01")

    assert graph == original
    with pytest.raises(ValueError, match="as_of"):
        project_life_state(graph, "06/01/2020")


def test_state_endpoint_uses_requested_parent_or_child_revision(tmp_path):
    app = create_app(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}", MockProvider())
    with TestClient(app) as client:
        options = client.get("/api/catalog").json()["defaults"]
        options.update(birth_year=1980, household="family", life_status="living")
        generated = client.post("/api/generations", json=options)
        assert generated.status_code == 201, generated.text
        job = generated.json()
        parent_id = job["graph_revision_id"]
        person_id = job["person"]["person_id"]
        parent_url = f"/api/graphs/{parent_id}"
        params = {"as_of": "1995-01-01", "participant_id": person_id}
        baseline = client.get(f"{parent_url}/state", params=params)
        assert baseline.status_code == 200, baseline.text
        original = baseline.json()
        assert original["graph_revision_id"] == parent_id
        assert [row["person_id"] for row in original["participants"]] == [person_id]
        assert _fact(person_id, "education_stage", original)["value"] == "基础教育"

        changed = client.post(
            f"{parent_url}/edits",
            json={
                "source": "manual",
                "operations": [
                    {
                        "op": "update",
                        "event_id": "education",
                        "state_delta": {"education_stage": "修订后的教育阶段"},
                        "known_at": {person_id: "1995-02-01"},
                    }
                ],
            },
        )
        assert changed.status_code == 201, changed.text
        child_id = changed.json()["graph_revision_id"]
        child_url = f"/api/graphs/{child_id}/state"
        early_child = client.get(child_url, params=params)
        assert early_child.status_code == 200, early_child.text
        assert all(
            row["field"] != "education_stage"
            for row in early_child.json()["participants"][0]["facts"]
        )
        later_child = client.get(child_url, params={**params, "as_of": "1995-02-01"})
        assert later_child.status_code == 200, later_child.text
        assert later_child.json()["graph_revision_id"] == child_id
        child_fact = _fact(person_id, "education_stage", later_child.json())
        assert child_fact["value"] == "修订后的教育阶段"
        assert child_fact["provenance"][0]["known_at"] == "1995-02-01"
        assert client.get(f"{parent_url}/state", params=params).json() == original

        for date in ("19950101", "1995-02-31", "2120-01-01"):
            assert client.get(child_url, params={"as_of": date}).status_code == 422
        assert client.get(child_url).status_code == 422
        unknown = client.get(child_url, params={**params, "participant_id": "unknown"})
        assert unknown.status_code == 404
        assert client.get("/api/graphs/missing/state", params=params).status_code == 404
