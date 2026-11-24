"""Project participant state facts visible at a point in valid and known time."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, NotRequired, TypedDict


SCHEMA = "life-state/1"


class StateProvenance(TypedDict):
    event_id: str
    role: str
    valid_time: dict[str, Any]
    known_at: str | None
    recorded_at: str | None
    evidence_ids: list[str]
    asserted_value: NotRequired[str | bool | int | float | None]


class StateFact(TypedDict, total=False):
    field: str
    value: Any
    value_type: str
    status: str
    valid_from: str | None
    candidate_values: list[Any]
    provenance: list[StateProvenance]


class ParticipantState(TypedDict):
    person_id: str
    status: str
    facts: list[StateFact]
    missing_fields: list[str]
    conflict_fields: list[str]


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return day if day.isoformat() == value else None


def _value_type(value: Any) -> str | None:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _evidence_ids(node: dict) -> list[str]:
    return sorted(
        {
            evidence_id
            for row in node.get("evidence", [])
            if (evidence_id := row.get("id"))
        }
    )


def _is_personal_event(node: dict) -> bool:
    category = str(node.get("category", "")).lower()
    return not (
        node.get("summary_node")
        or "decomposition_state_delta" in node
        or "historical_assessment" in node
        or category in {"history", "world", "historical", "历史", "历史背景", "世界"}
        or node.get("state_delta", {}).get("exposure_status") == "candidate"
    )


def _participant_deltas(node: dict, primary_id: str) -> list[tuple[str, str, dict]]:
    participants = node.get("participants") or [primary_id]
    effects: list[tuple[str, str, dict]] = []
    explicit = node.get("participant_deltas", [])
    accounted = {
        field
        for item in explicit
        if item.get("person_id") in participants
        for field in item.get("delta", {})
    }
    for item in explicit:
        person_id = item.get("person_id")
        delta = item.get("delta")
        if person_id in participants and isinstance(delta, dict):
            effects.append((person_id, str(item.get("role") or "participant"), delta))

    state_delta = node.get("state_delta", {})
    if (
        primary_id in participants
        and isinstance(state_delta, dict)
        and state_delta.get("exposure_status") != "candidate"
    ):
        subject_delta = {
            field: value
            for field, value in state_delta.items()
            if field not in accounted
        }
        if subject_delta:
            effects.append((primary_id, "subject", subject_delta))
    return effects


def project_life_state(
    graph: dict,
    as_of: str | date,
    *,
    participant_id: str | None = None,
    required_fields: tuple[str, ...] | list[str] = (),
) -> dict:
    """Return each participant's latest eligible state facts at ``as_of``.

    ``required_fields`` declares the caller's completeness contract. Fields
    absent from the projection are returned as explicit incomplete facts.
    Without that contract, the projection reports only observed fields.
    """
    as_of_day = _day(as_of)
    if as_of_day is None:
        raise ValueError("as_of must be an ISO date or date value")

    person = graph.get("person", {})
    primary_id = person.get("person_id")
    if not isinstance(primary_id, str) or not primary_id:
        raise ValueError("graph must identify its primary person")

    all_participants = {primary_id}
    all_participants.update(
        row.get("id")
        for row in person.get("family", [])
        if isinstance(row.get("id"), str)
    )
    for node in graph.get("nodes", []):
        all_participants.update(
            person_id
            for person_id in node.get("participants", [])
            if isinstance(person_id, str)
        )
        all_participants.update(
            row.get("person_id")
            for row in node.get("participant_deltas", [])
            if isinstance(row.get("person_id"), str)
        )
    if participant_id is not None and participant_id not in all_participants:
        raise LookupError("找不到该骨架中的参与者。")
    selected = {participant_id} if participant_id is not None else all_participants

    assertions: dict[str, dict[str, list[tuple[date, Any, str, StateProvenance]]]] = {
        person_id: defaultdict(list) for person_id in selected
    }
    issues: list[str] = []
    for node in graph.get("nodes", []):
        if not _is_personal_event(node):
            continue
        event_id = node.get("event_id")
        start = _day(node.get("start"))
        if not isinstance(event_id, str) or start is None:
            issues.append(f"invalid event identity or start date: {event_id}")
            continue
        if start > as_of_day:
            continue

        known_at_map = node.get("known_at", {})
        for person_id, role, delta in _participant_deltas(node, primary_id):
            if person_id not in selected:
                continue
            known_at_value = known_at_map.get(person_id)
            known_at = _day(known_at_value) if known_at_value is not None else None
            if known_at_value is not None and known_at is None:
                issues.append(f"invalid known_at for {person_id} in {event_id}")
                continue
            if known_at is not None and known_at > as_of_day:
                continue

            provenance: StateProvenance = {
                "event_id": event_id,
                "role": role,
                "valid_time": {
                    "start": node.get("start"),
                    "end": node.get("end"),
                    "precision": node.get("precision"),
                },
                "known_at": known_at.isoformat() if known_at else None,
                "recorded_at": node.get("recorded_at"),
                "evidence_ids": _evidence_ids(node),
            }
            for field, value in delta.items():
                value_type = _value_type(value)
                if value_type is None:
                    issues.append(f"unsupported state value: {event_id}.{field}")
                    continue
                assertions[person_id][field].append(
                    (start, value, value_type, provenance)
                )

    participants: list[ParticipantState] = []
    normalized_required = sorted(set(required_fields))
    for person_id in sorted(selected, key=lambda item: (item != primary_id, item)):
        facts: list[StateFact] = []
        fields = assertions[person_id]
        missing_fields: list[str] = []
        conflict_fields: list[str] = []
        for field in sorted(fields):
            latest_day = max(row[0] for row in fields[field])
            latest = [row for row in fields[field] if row[0] == latest_day]
            typed_values = {(row[2], row[1]) for row in latest}
            latest.sort(key=lambda row: (row[3]["event_id"], row[3]["role"]))
            if len(typed_values) > 1:
                conflict_fields.append(field)
                facts.append(
                    {
                        "field": field,
                        "value": None,
                        "value_type": "conflict",
                        "status": "conflict",
                        "valid_from": latest_day.isoformat(),
                        "candidate_values": [
                            value for _, value in sorted(typed_values, key=repr)
                        ],
                        "provenance": [
                            {**source, "asserted_value": value}
                            for _, value, _, source in latest
                        ],
                    }
                )
            else:
                _, value, value_type, _ = latest[0]
                facts.append(
                    {
                        "field": field,
                        "value": value,
                        "value_type": value_type,
                        "status": "known",
                        "valid_from": latest_day.isoformat(),
                        "provenance": [row[3] for row in latest],
                    }
                )

        known_fields = set(fields)
        for field in normalized_required:
            if field in known_fields:
                continue
            missing_fields.append(field)
            facts.append(
                {
                    "field": field,
                    "value": None,
                    "value_type": "unknown",
                    "status": "incomplete",
                    "valid_from": None,
                    "provenance": [],
                }
            )

        status = (
            "conflict"
            if conflict_fields
            else "incomplete"
            if missing_fields or not facts
            else "consistent"
        )
        participants.append(
            {
                "person_id": person_id,
                "status": status,
                "facts": facts,
                "missing_fields": missing_fields,
                "conflict_fields": conflict_fields,
            }
        )

    return {
        "schema": SCHEMA,
        "graph_revision_id": graph.get("graph_revision_id"),
        "as_of": as_of_day.isoformat(),
        "coverage": "observed_fields_only",
        "participants": participants,
        "issues": sorted(set(issues)),
    }
