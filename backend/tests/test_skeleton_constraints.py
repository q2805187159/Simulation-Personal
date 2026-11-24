import os
from copy import deepcopy
from pathlib import Path

import pytest

from app.domain import compile_spec
from app.schemas import GraphOperation
from app.skeleton import ReferenceRunner, revise_skeleton, run_skeleton
from app.skeleton_constraints import ConstraintEngineError, check_hard_constraints


DAY = "2026-01-05"
NO_CLAIM = object()


def graph_with(*nodes):
    return {
        "person": {
            "person_id": "subject",
            "birth_year": 1970,
            "death_date": None,
            "family": [{"id": "relative"}],
        },
        "as_of": "2026-01-31",
        "nodes": list(nodes),
    }


def booking(
    event_id, start=NO_CLAIM, duration=60, day=DAY, participants=("subject",), **money
):
    delta = (
        {}
        if start is NO_CLAIM
        else {
            "resource_start_minute": start,
            "resource_duration_minutes": duration,
            **{f"resource_{key}_cents": value for key, value in money.items()},
        }
    )
    return {
        "event_id": event_id,
        "start": day,
        "end": day,
        "precision": "day",
        "participants": list(participants),
        "state_delta": delta,
    }


def care_nodes():
    care = booking("shared-care", participants=("subject", "relative"))
    care["participant_deltas"] = [
        {"person_id": "subject", "role": "recipient", "delta": {}},
        {"person_id": "relative", "role": "supporter", "delta": {}},
    ]
    return booking("health-transition"), care


@pytest.fixture(params=["reference", "minizinc"])
def validator(request, monkeypatch):
    if request.param == "reference":
        monkeypatch.delenv("MINIZINC_BIN", raising=False)
        return "python-reference"
    if not os.environ.get("MINIZINC_BIN"):
        pytest.skip("MINIZINC_BIN is required for native constraint checks")
    return "minizinc"


def test_no_hard_constraint_inputs_is_not_applicable(monkeypatch):
    monkeypatch.setenv("MINIZINC_BIN", "broken-minizinc")
    result = check_hard_constraints(graph_with(booking("education")))
    assert result["status"] == "not_applicable"
    assert result["validator"] is None
    assert result["event_ids"] == []


def test_adjacent_bookings_and_distinct_people_can_share_a_day(validator):
    graph = graph_with(
        booking("first", 0),
        booking("next", 60),
        booking("relative-plan", 20, participants=("relative",)),
    )
    result = check_hard_constraints(graph)
    assert result["status"] == "feasible"
    assert result["validator"] == validator
    assert result["issues"] == []
    assert len(result["input_sha256"]) == len(result["model_sha256"]) == 64
    assert result == check_hard_constraints(graph_with(*reversed(graph["nodes"])))


@pytest.mark.parametrize(
    ("nodes", "issue_text"),
    [
        ((booking("first", 0), booking("overlap", 59)), "占用"),
        ((booking("crosses-midnight", 1439, 2),), "时间"),
        (
            (
                booking("paid", 0, cost=800, budget=1000),
                booking("second", 60, cost=201),
            ),
            "预算",
        ),
        ((booking("out-of-window", 0, day="2026-02-01"),), "时间"),
    ],
)
def test_hard_constraints_reject_infeasible_claims(validator, nodes, issue_text):
    result = check_hard_constraints(graph_with(*nodes))
    assert result["status"] == "infeasible"
    assert result["validator"] == validator
    assert any(issue_text in issue for issue in result["issues"])


def test_costs_use_the_cap_for_the_booking_date(validator):
    result = check_hard_constraints(
        graph_with(
            booking("first", 0, cost=1000, budget=1000),
            booking("rest", 0, cost=900, budget=1000, day="2026-01-06"),
        )
    )
    assert result["status"] == "feasible"


def test_shared_care_can_use_a_free_slot_for_both_existing_participants(validator):
    health, care = care_nodes()
    other = booking("relative-plan", 0, 1380, participants=("relative",))
    result = check_hard_constraints(graph_with(health, care, other))
    assert result["status"] == "feasible"
    assert result["event_ids"] == ["relative-plan", "shared-care"]


def test_shared_care_requires_a_contiguous_slot_for_both_participants(validator):
    health, care = care_nodes()
    other = booking("early", 30, 1380, participants=("relative",))
    result = check_hard_constraints(graph_with(health, care, other))
    assert result["status"] == "infeasible"
    assert any("占用" in issue and "shared-care" in issue for issue in result["issues"])


def test_care_requires_a_known_relative_and_matching_roles(validator):
    health, care = care_nodes()
    care["participants"][1] = "unknown"
    care["participant_deltas"][1]["person_id"] = "unknown"
    result = check_hard_constraints(graph_with(health, care))
    assert result["status"] == "infeasible"
    assert any("参与者" in issue for issue in result["issues"])


def test_care_cannot_precede_health_transition(validator):
    health, care = care_nodes()
    health["start"] = "2026-01-06"
    result = check_hard_constraints(graph_with(health, care))
    assert result["status"] == "infeasible"
    assert any("照护" in issue and "时间" in issue for issue in result["issues"])


def test_resource_claim_does_not_coerce_strings_to_integers(validator):
    result = check_hard_constraints(graph_with(booking("bad", "60")))
    assert result["status"] == "infeasible"
    assert any("资源" in issue and "bad" in issue for issue in result["issues"])


def test_skeleton_revisions_reject_overlapping_claims_and_keep_parent(validator):
    spec = compile_spec({"birth_year": 1984, "household": "individual"})["spec"]
    runner = ReferenceRunner()
    parent = run_skeleton(spec, runner, "subject")
    snapshot = deepcopy(parent)
    claim = GraphOperation(
        op="add",
        event_id="appointment",
        title="预约",
        start=DAY,
        state_delta={"resource_start_minute": 420, "resource_duration_minutes": 60},
    ).model_dump(mode="json", exclude_unset=True)

    child = revise_skeleton(parent, spec, [claim], runner)
    assert child["simulation"]["constraints"]["status"] == "feasible"
    assert child["simulation"]["constraints"]["validator"] == validator
    assert child["simulation"]["constraints"]["event_ids"] == ["appointment"]
    child_snapshot = deepcopy(child)

    with pytest.raises(ValueError, match="重复占用"):
        revise_skeleton(child, spec, [{**claim, "event_id": "overlap"}], runner)

    assert parent == snapshot
    assert child == child_snapshot


def test_configured_solver_failure_does_not_fall_back(monkeypatch):
    monkeypatch.setenv(
        "MINIZINC_BIN", str(Path(__file__).with_name("missing-minizinc"))
    )
    with pytest.raises(ConstraintEngineError, match="MiniZinc"):
        check_hard_constraints(graph_with(booking("first", 0)))
