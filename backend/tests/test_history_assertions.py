from datetime import UTC, datetime

import pytest

from app.domain import compile_spec, generate_graph
from app.history_assertions import apply_history_assertion, reproject_history_assertions


RECORDED = datetime(2026, 11, 23, 12, tzinfo=UTC)
RULE = "DIS-WC-2008"


@pytest.fixture
def graph():
    spec = compile_spec(
        {"birth_year": 1984, "household": "family", "as_of": "2010-11-23"}
    )["spec"]
    result = generate_graph(spec, person_id="research-subject")
    result["graph_revision_id"] = "revision-original"
    return result


def node(graph):
    return next(event for event in graph["nodes"] if event["event_id"] == RULE)


def assertion(**overrides):
    return {
        "event_id": RULE,
        "kind": "exposure",
        "key": "direct",
        "status": "confirmed",
        "date": "2008-05-12",
        "known_at": "2008-05-14",
        "region": "51",
        "location_ref": "case-diary-map-3",
        "source_ref": "case-diary-3",
        "source_note": "参与者自述震时活动位置并提供当时日记。",
        **overrides,
    }


def channel(assessment, key="direct"):
    return next(item for item in assessment["channels"] if item["channel"] == key)


def test_late_archive_assertion_is_versioned_and_keeps_source_separate(graph):
    result = apply_history_assertion(graph, assertion(), RECORDED)
    original, revised = node(graph), node(result)
    assert result["parent_revision_id"] == graph["graph_revision_id"]
    assert result["graph_revision_id"] is None
    assert "historical_assertions" not in original
    assert revised["evidence"] == original["evidence"]
    assert revised["state_delta"] == original["state_delta"]
    assert revised["summary_node"]

    raw = revised["historical_assertions"][0]
    assert raw["origin"] == "researcher_supplied"
    assert raw["source_note"] == assertion()["source_note"]
    assert raw["recorded_at"] == RECORDED.isoformat()
    assessment = revised["historical_assessment"]
    assert assessment["archive_as_of"] == "2026-11-23"
    assert channel(assessment)["status"] == "confirmed"
    assert "source_note" not in channel(assessment)["observations"][0]
    assert reproject_history_assertions(revised, person=graph["person"]) == assessment


def test_conflict_is_preserved_and_family_claim_stays_subject_specific(graph):
    first = apply_history_assertion(graph, assertion(), RECORDED)
    conflicting = apply_history_assertion(
        first,
        assertion(status="excluded", region="11", source_ref="case-journey-2"),
        RECORDED,
    )
    assessment = node(conflicting)["historical_assessment"]
    assert channel(assessment)["status"] == "unknown"
    assert channel(assessment)["conflicting_evidence"] is True
    assert len(node(conflicting)["historical_assertions"]) == 2

    relative = graph["person"]["family"][0]["id"]
    relatives = apply_history_assertion(
        graph, assertion(subject_ref=relative), RECORDED
    )
    assessment = node(relatives)["historical_assessment"]
    assert channel(assessment)["status"] == "unknown"
    assert channel(assessment["subject_assessments"][relative])["status"] == "confirmed"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"key": "invalid-channel"}, "渠道"),
        ({"kind": "gate", "key": "imaginary-gate", "status": "met"}, "门槛"),
        ({"source_note": " "}, "来源说明"),
        ({"subject_ref": "other-case-person"}, "参与者"),
        ({"location_ref": None}, "在场"),
        ({"region": "11"}, "适用地区"),
        ({"date": "2008-05-13"}, "发生时窗"),
        ({"date": "2012-01-01"}, "截止"),
        ({"known_at": "2008-05-11"}, "获知"),
        ({"known_at": "wrong"}, "日期"),
    ],
)
def test_rejects_invalid_assertions_before_creating_revision(graph, overrides, message):
    with pytest.raises(ValueError, match=message):
        apply_history_assertion(graph, assertion(**overrides), RECORDED)
    assert "historical_assertions" not in node(graph)


def test_catalog_sources_cannot_confirm_individual_observation(graph):
    sources = [item["url"] for item in node(graph)["evidence"]]
    for source in [RULE, *sources]:
        with pytest.raises(ValueError, match="公共资料"):
            apply_history_assertion(graph, assertion(source_ref=source), RECORDED)
