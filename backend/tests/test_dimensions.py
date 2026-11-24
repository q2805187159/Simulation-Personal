from copy import deepcopy

import pytest

from app.domain import apply_edits, compile_spec, generate_graph
from app.skeleton import ReferenceRunner, run_skeleton
from app.world_rules import SCENARIOS


def graph(**overrides):
    return generate_graph(
        compile_spec(
            {
                "birth_year": 1984,
                "region": "51",
                "household": "family",
                "seed": 2026,
                **overrides,
            }
        )["spec"]
    )


def rows(record):
    return {
        entry["dim_id"]: entry
        for group in record["dimensions"]["families"]
        for entry in group["dimensions"]
    }


def test_dimension_ledger_registers_dictionary_and_anchors_substantive_events():
    record = graph()
    ledger = record["dimensions"]
    dimensions = rows(record)
    assert len(ledger["families"]) == ledger["summary"]["family_count"] == 55
    assert len(dimensions) == ledger["summary"]["dimension_count"] == 440
    assert (
        sum(
            ledger["summary"][key]
            for key in ("configured", "resolved", "unknown", "not_applicable")
        )
        == 440
    )
    assert ledger["summary"]["resolved"] >= 35
    assert (
        len(
            {
                row["family_id"]
                for row in dimensions.values()
                if row["value"] is not None
            }
        )
        >= 20
    )
    assert ledger["registry_version"] == "v3.0"
    assert len(ledger["registry_sha256"]) == 64

    by_id = {node["event_id"]: node for node in record["nodes"]}
    assert {
        "work",
        "daily-routine",
        "budget-routine",
        "leisure-routine",
        "digital-adoption",
    } <= by_id.keys()
    assert "或" not in by_id["work"]["title"]
    assert by_id["work"]["state_delta"]["entry_kind"] in {"work", "education"}
    for event in record["nodes"]:
        assert event["dimension_updates"], event["event_id"]
        for update in event["dimension_updates"]:
            row = dimensions[update["dim_id"]]
            assert event["event_id"] in row["event_ids"]
            assert update["valid_time"]["start"] == event["start"]
    assert dimensions["BODY.05"]["status"] == "unresolved"
    assert dimensions["HISTORY.04"]["value"] is None
    assert dimensions["HISTORY.01"]["provenance"]["kind"] == "world_context"
    assert dimensions["TIME.03"]["value"] == {"min": 41, "max": 42, "unit": "year"}
    assert dimensions["DEATH.01"]["status"] == "not_applicable"


def test_cutoff_applies_to_every_dimension_assertion():
    result = graph(birth_year=2025, as_of="2025-05-01")
    dimensions = rows(result)
    assert dimensions["TIME.03"]["value"] == {"min": 0, "max": 0, "unit": "year"}
    assert dimensions["TIME.01"]["value"]["end"] == "2025-05-01"
    assert dimensions["WORK.01"]["status"] == "not_applicable"
    assert dimensions["HISTORY.01"]["status"] == "unresolved"
    deceased = graph(birth_year=1880, life_status="deceased")
    assert deceased["dimensions"]["reference_date"] == deceased["person"]["death_date"]
    assert rows(deceased)["DEATH.01"]["status"] == "event_anchored"
    for dimension in rows(deceased).values():
        if window := dimension["valid_time"]:
            assert window["start"] <= deceased["person"]["death_date"]
            assert (
                window["end"] is None
                or window["end"] <= deceased["person"]["death_date"]
            )


def test_old_graph_edit_projects_ledger_and_removes_deleted_anchors():
    parent = deepcopy(graph())
    parent.pop("dimensions")
    for node in parent["nodes"]:
        node.pop("dimension_updates")
    child = apply_edits(parent, [{"op": "delete", "event_id": "education"}])
    childcare = rows(child)
    assert "dimensions" not in parent
    assert all("education" not in row["event_ids"] for row in childcare.values())

    result = graph()
    child = apply_edits(
        result, [{"op": "update", "event_id": "work", "start": "2005-07-01"}]
    )
    window = next(
        update["valid_time"]
        for node in child["nodes"]
        if node["event_id"] == "work"
        for update in node["dimension_updates"]
        if update["dim_id"] in {"WORK.01", "EDU.01"}
    )
    assert window["start"] == "2005-07-01"
    assert rows(result)["TIME.07"]["value"] != rows(child)["TIME.07"]["value"]


@pytest.mark.parametrize(
    ("seed", "expected"),
    [
        (3, {"job-exit": {"WORK.01", "CAREER.04"}}),
        (
            5,
            {
                "relocation": {"GEO.05"},
                "health-transition": {"HOUSEHOLD.04"},
                "shared-care": {"HOUSEHOLD.05", "HOUSEHOLD.08"},
                "partnership": {"PARTNER.01"},
            },
        ),
    ],
)
def test_engine_events_anchor_only_modeled_state(seed, expected):
    spec = compile_spec(
        {
            "birth_year": 1965,
            "region": "51",
            "household": "family",
            "seed": seed,
            "as_of": "2026-11-23",
        }
    )["spec"]
    record = run_skeleton(spec, ReferenceRunner(), "subject")
    nodes = {node["event_id"]: node for node in record["nodes"]}
    dimensions = rows(record)
    for event_id, dim_ids in expected.items():
        node = nodes[event_id]
        updates = {update["dim_id"]: update for update in node["dimension_updates"]}
        assert dim_ids <= updates.keys()
        for dim_id in dim_ids:
            assert updates[dim_id]["provenance"]["kind"] == "simulation_event"
            assert updates[dim_id]["provenance"]["source_ids"] == [
                node["evidence"][0]["id"]
            ]
            assert event_id in dimensions[dim_id]["event_ids"]

    if "shared-care" in expected:
        care = dimensions["HOUSEHOLD.05"]["value"]
        assert care["recipient_id"] == "subject"
        assert care["provider_id"] == record["person"]["family"][0]["id"]
    assert all(
        dimensions[key]["value"] is None
        for key in ("HEALTH.03", "PARTNER.02", "GEO.04", "WORK.06", "HOUSEHOLD.01")
    )


def test_world_rule_declares_family_and_dimension_relevance_without_personal_values(
    monkeypatch,
):
    rule_id = "POL-AIR-2013"
    monkeypatch.setitem(
        SCENARIOS[rule_id], "affected_dimensions", ["WORK", "HEALTH.03"]
    )
    record = graph()
    node = next(node for node in record["nodes"] if node["event_id"] == rule_id)
    relevance = node["dimension_relevance"]
    assert relevance["family_ids"] == ["WORK", "HEALTH"]
    assert relevance["dimension_ids"] == ["HEALTH.03"]
    assert relevance["scope"] == "background_candidate"
    assert relevance["source_ids"] == [item["id"] for item in node["evidence"]]

    dimensions = rows(record)
    assert dimensions["HEALTH.03"]["rule_relevance"] == [rule_id]
    assert dimensions["HEALTH.03"]["value"] is None
    assert dimensions["HEALTH.03"]["status"] == "unresolved"
    assert dimensions["HEALTH.03"]["event_ids"] == []
    families = {
        family["family_id"]: family for family in record["dimensions"]["families"]
    }
    assert rule_id in families["WORK"]["rule_relevance"]
    assert rule_id in families["HEALTH"]["rule_relevance"]
    assert "WORK.06" not in relevance["dimension_ids"]
    assert dimensions["WORK.06"]["value"] is None
    assert dimensions["HISTORY.04"]["value"] is None
