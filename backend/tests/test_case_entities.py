from copy import deepcopy

import pytest

from app.case_entities import attach_case_entities, check_case_entity_consistency
from app.domain import _node, compile_spec, generate_graph


def _graph(person_id="case-primary", household="family"):
    spec = compile_spec({"birth_year": 1984, "household": household})["spec"]
    return generate_graph(spec, person_id=person_id)


def _event(graph, event_id):
    return next(node for node in graph["nodes"] if node["event_id"] == event_id)


def _relationship(ledger, kind):
    return next(row for row in ledger["relationships"] if row["kind"] == kind)


def test_case_entities_are_scoped_stable_and_preserve_unknowns():
    graph = _graph()
    attach_case_entities(graph)
    ledger = graph["case_entities"]
    assert ledger["consistency"] == {"valid": True, "issues": []}
    assert {entity["kind"] for entity in ledger["entities"]} == {
        "person",
        "household",
        "region",
        "organization",
    }
    assert all(
        entity["identity_status"] == "unknown" and entity["name"] is None
        for entity in ledger["entities"]
        if entity["kind"] == "organization"
    )
    assert all(row["kind"] != "co_resident" for row in ledger["relationships"])
    assert _relationship(ledger, "kinship")["valid_time"]["start"] is None
    assert _relationship(ledger, "birthplace")["valid_time"]["precision"] == "year"
    attach_case_entities(graph)
    assert graph["case_entities"] == ledger

    other = _graph("other-case")
    attach_case_entities(other)
    assert not {row["entity_id"] for row in ledger["entities"]} & {
        row["entity_id"] for row in other["case_entities"]["entities"]
    }


def test_participant_ledger_uses_the_shared_event_and_preserves_role_effects():
    graph = _graph()
    roles = [
        {"person_id": "case-primary", "role": "recipient", "delta": {"care": True}},
        {"person_id": "family-elder", "role": "supporter", "delta": {"minutes": 15}},
        {"person_id": "family-elder", "role": "supporter", "delta": {"cost": 2}},
    ]
    graph["nodes"].append(
        _node(
            "shared-care",
            "照护",
            "household",
            "2021-04-01",
            "2021-04-01",
            "day",
            "四川",
            participants=["case-primary", "family-elder"],
            state_delta={"care": True},
            participant_deltas=roles,
        )
    )
    attach_case_entities(graph)
    ledger = graph["case_entities"]
    rows = ledger["participant_ledger"]["shared-care"]
    assert [(row["person_id"], row["role"], row["delta"]) for row in rows] == [
        ("case-primary", "recipient", {"care": True}),
        ("family-elder", "supporter", {"minutes": 15, "cost": 2}),
    ]
    assert rows[0]["entity_id"] != rows[1]["entity_id"]
    assert _relationship(ledger, "care_support")["source_event_ids"] == ["shared-care"]
    assert _relationship(ledger, "care_support")["valid_time"] == {
        "start": "2021-04-01",
        "end": "2021-04-01",
        "precision": "day",
    }


def test_relationship_versions_follow_evidence_changes_and_withdrawals():
    graph = _graph()
    attach_case_entities(graph)
    initial = graph["case_entities"]
    child = deepcopy(graph)
    _event(child, "household")["start"] = "2011-01-01"
    attach_case_entities(child, parent=graph)
    latest = child["case_entities"]
    before = _relationship(initial, "household_arrangement")
    after = next(
        row
        for row in latest["relationships"]
        if row["relationship_id"] == before["relationship_id"]
    )
    assert after["version"] == before["version"] + 1
    assert after["previous_version_id"] == before["version_id"]
    assert _relationship(latest, "birthplace") == _relationship(initial, "birthplace")
    assert _relationship(graph["case_entities"], "household_arrangement") == before

    removed = deepcopy(child)
    removed["nodes"].remove(_event(removed, "household"))
    attach_case_entities(removed, parent=child)
    withdrawn = next(
        row
        for row in removed["case_entities"]["withdrawn_relationships"]
        if row["relationship_id"] == before["relationship_id"]
    )
    assert withdrawn["previous_version_id"] == after["version_id"]

    restored = deepcopy(child)
    attach_case_entities(restored, parent=removed)
    restored_row = next(
        row
        for row in restored["case_entities"]["relationships"]
        if row["relationship_id"] == before["relationship_id"]
    )
    assert restored_row["previous_version_id"] == withdrawn["version_id"]
    assert restored_row["version"] == withdrawn["version"] + 1


def test_ledger_rejects_unknown_participants_and_duplicate_effects():
    graph = _graph()
    birth = _event(graph, "birth")
    birth["participants"].append("outside-case")
    effect = {"person_id": "family-elder", "role": "supporter", "delta": {"care": True}}
    birth["participant_deltas"] = [effect, effect]
    with pytest.raises(ValueError) as error:
        attach_case_entities(graph)
    assert "unknown case participant: outside-case in birth" in str(error.value)
    assert "duplicate participant effect: family-elder/supporter/care" in str(
        error.value
    )


def test_legacy_parent_supplies_relationship_lineage():
    parent = _graph()
    child = deepcopy(parent)
    _event(child, "household")["start"] = "2011-01-01"
    attach_case_entities(child, parent=parent)
    relation = _relationship(child["case_entities"], "household_arrangement")
    assert "case_entities" not in parent
    assert relation["version"] == 2
    assert relation["previous_version_id"]


def test_editing_evidence_rebuilds_event_and_organization_context():
    graph = _graph()
    old = attach_case_entities(graph)
    work = _event(graph, "work")
    work["location"] = "重庆"
    work["state_delta"].update(
        organization_id="case-org-1", organization_name="实务培训机构"
    )
    work["known_at"] = {"case-primary": work["start"]}
    assert not check_case_entity_consistency(graph)["valid"]

    ledger = attach_case_entities(graph)
    entities = {row["entity_id"]: row for row in ledger["entities"]}
    context = ledger["event_context"]["work"]
    assert entities[context["region_entity_id"]]["name"] == "重庆"
    organization = entities[context["organization_entity_id"]]
    assert organization["name"] == "实务培训机构"
    assert organization["identity_status"] == "recorded"
    assert ledger["participant_ledger"]["work"][0]["known_at"] == work["start"]
    assert check_case_entity_consistency(graph)["valid"]
    assert _relationship(ledger, "birthplace") == _relationship(old, "birthplace")


def test_branch_edits_from_same_parent_have_distinct_relationship_versions():
    parent = _graph()
    attach_case_entities(parent)
    children = [deepcopy(parent), deepcopy(parent)]
    for child, day in zip(children, ("2011-01-01", "2012-01-01"), strict=True):
        _event(child, "household")["start"] = day
        attach_case_entities(child, parent=parent)
    first, second = [
        _relationship(child["case_entities"], "household_arrangement")
        for child in children
    ]
    assert first["relationship_id"] == second["relationship_id"]
    assert first["previous_version_id"] == second["previous_version_id"]
    assert first["version_id"] != second["version_id"]


def test_ledger_preserves_unallocated_subject_effects_alongside_explicit_roles():
    graph = _graph()
    birth = _event(graph, "birth")
    birth["state_delta"] = {"care": True, "pressure": 1}
    birth["participant_deltas"] = [
        {"person_id": "case-primary", "role": "recipient", "delta": {"care": True}},
        {"person_id": "family-elder", "role": "supporter", "delta": {"time": 5}},
    ]
    rows = attach_case_entities(graph)["participant_ledger"]["birth"]
    assert [(row["role"], row["delta"]) for row in rows] == [
        ("recipient", {"care": True}),
        ("subject", {"pressure": 1}),
        ("supporter", {"time": 5}),
    ]


def test_consistency_check_detects_damaged_relationship_version():
    graph = _graph()
    ledger = attach_case_entities(graph)
    relationship = _relationship(ledger, "birthplace")
    relationship["version_id"] = "stale-version"
    assert check_case_entity_consistency(graph) == {
        "valid": False,
        "issues": [f"invalid relationship version: {relationship['relationship_id']}"],
    }
