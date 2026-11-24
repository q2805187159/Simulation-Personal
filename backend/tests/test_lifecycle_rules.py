from datetime import date

import pytest

from app.domain import compile_spec, generate_graph, validate_graph
from app.lifecycle_rules import (
    LIFECYCLE_SHA256,
    LIFECYCLE_VERSION,
    generate_lifecycle_events,
)


def inputs(**options):
    spec = compile_spec({"birth_year": 1969, **options})["spec"]
    graph = generate_graph(spec, person_id="subject")
    return spec, graph


def test_catalog_builds_deterministic_nodes_with_scoped_provenance():
    spec, graph = inputs()
    cutoff = date(2026, 11, 23)
    nodes = generate_lifecycle_events(spec, graph["nodes"], cutoff)
    assert nodes == generate_lifecycle_events(
        spec, list(reversed(graph["nodes"])), cutoff
    )
    assert len(nodes) >= 7
    assert len({node["event_id"] for node in nodes}) == len(nodes)
    assert len(LIFECYCLE_SHA256) == 64
    assert validate_graph({**graph, "nodes": [*graph["nodes"], *nodes]})["valid"]
    for node in nodes:
        assert node["participants"] == ["subject"]
        assert node["lifecycle_rule"]["version"] == LIFECYCLE_VERSION
        assert node["lifecycle_rule"]["basis"] == "synthetic_rule"
        assert node["dimension_values"]
        assert node["start"] <= node["end"] <= cutoff.isoformat()
        assert "sample_probability" not in node["decision"]


def test_cutoff_and_existing_event_id_are_respected():
    spec, graph = inputs()
    cutoff = date(1972, 12, 31)
    assert generate_lifecycle_events(spec, graph["nodes"], cutoff) == []
    cutoff = date(1973, 5, 10)
    first = generate_lifecycle_events(spec, graph["nodes"], cutoff)
    assert [item["event_id"] for item in first] == ["lifecycle-early-learning"]
    assert first[0]["end"] == cutoff.isoformat()
    assert generate_lifecycle_events(spec, [*graph["nodes"], first[0]], cutoff) == []


def test_missing_dependencies_and_individual_scope_remove_dependent_events():
    spec, family = inputs(household="family")
    cutoff = date(2026, 11, 23)
    events = generate_lifecycle_events(spec, family["nodes"], cutoff)
    indexed = {item["event_id"]: item for item in events}
    household = indexed["lifecycle-family-coordination"]
    assert set(household["participants"]) == {"subject", "family-sibling"}
    assert indexed.get("lifecycle-post-relocation") is None
    assert indexed.get("lifecycle-care-review") is None
    assert all("lifecycle-post-relocation" != item["event_id"] for item in events)
    solo_events = generate_lifecycle_events(
        {**spec, "household": "individual"}, family["nodes"], cutoff
    )
    assert "lifecycle-family-coordination" not in {
        item["event_id"] for item in solo_events
    }


def test_native_anchor_eligibility_and_local_date_are_preserved():
    spec, graph = inputs()
    cutoff = date(2026, 11, 23)
    assert "lifecycle-post-relocation" not in {
        item["event_id"]
        for item in generate_lifecycle_events(spec, graph["nodes"], cutoff)
    }
    anchor = {
        "event_id": "relocation",
        "start": "2024-12-30",
        "participants": ["subject"],
    }
    output = generate_lifecycle_events(spec, [*graph["nodes"], anchor], cutoff)
    node = next(
        item for item in output if item["event_id"] == "lifecycle-post-relocation"
    )
    assert node["depends_on"] == ["relocation"]
    assert node["location"] is None
    assert node["start"] >= "2025-01-01"


def test_catalog_rejects_reversed_age_windows():
    from app.lifecycle_rules import LifecycleRule

    with pytest.raises(ValueError, match="maximum_age"):
        LifecycleRule.model_validate(
            {
                "id": "lifecycle-test",
                "title": "测试",
                "category": "household",
                "minimum_age": 31,
                "maximum_age": 29,
                "anchors": ["birth"],
                "condition": "测试条件",
                "state_field": "example_status",
                "choices": ["测试选项"],
                "dimensions": [{"id": "HOUSEHOLD.06", "value_key": "arrangement"}],
            }
        )
