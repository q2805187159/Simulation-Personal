"""Project the evidence in a LifeGraph into case-scoped entities and relationships."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json


SCHEMA = "case-entities/1"
UNKNOWN_TIME = {"start": None, "end": None, "precision": None}
VERSION_FIELDS = {"version", "version_id", "previous_version_id", "status"}
ORGANIZATION_CONTEXT = {
    "education": "education",
    "school-path": "education",
    "work": "employment",
    "employment-entry": "employment",
}


def _id(kind: str, *parts: object) -> str:
    content = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{kind}-{sha256(content.encode()).hexdigest()[:24]}"


def _window(node: dict) -> dict:
    return {field: node.get(field) for field in UNKNOWN_TIME}


def _evidence_ids(node: dict) -> list[str]:
    return sorted({row["id"] for row in node.get("evidence", []) if row.get("id")})


def _participant_rows(
    node: dict, people: dict[str, str], primary_id: str, issues: set[str]
) -> list[dict]:
    event_id = node["event_id"]
    participants = node.get("participants", [])
    for person_id, occurrences in Counter(participants).items():
        if occurrences > 1:
            issues.add(f"duplicate event participant: {person_id} in {event_id}")
        if person_id not in people:
            issues.add(f"unknown case participant: {person_id} in {event_id}")

    effects: dict[str, dict[str, dict]] = {}
    for item in node.get("participant_deltas", []):
        person_id, role, delta = item["person_id"], item["role"], item["delta"]
        if person_id not in participants:
            issues.add(f"unknown participant for effect: {person_id} in {event_id}")
        fields = effects.setdefault(person_id, {}).setdefault(role, {})
        for field, value in delta.items():
            if field in fields:
                issues.add(f"duplicate participant effect: {person_id}/{role}/{field}")
            fields[field] = deepcopy(value)

    state_delta = node.get("state_delta", {})
    subject_delta = {}
    if state_delta.get("exposure_status") != "candidate":
        accounted = {
            field
            for roles in effects.values()
            for delta in roles.values()
            for field in delta
        }
        subject_delta = {
            field: deepcopy(value)
            for field, value in state_delta.items()
            if field not in accounted
        }
    rows = []
    for person_id in participants:
        declared_roles = effects.get(person_id, {})
        roles = dict(declared_roles)
        if person_id == primary_id and subject_delta:
            roles["subject"] = {**roles.get("subject", {}), **subject_delta}
        if not roles:
            roles = {"subject" if person_id == primary_id else "participant": {}}
        for role, delta in roles.items():
            role_status = "declared" if role in declared_roles else "unspecified"
            if role == "subject" and role not in declared_roles:
                role_status = "derived"
            rows.append(
                {
                    "event_id": event_id,
                    "entity_id": people.get(person_id),
                    "person_id": person_id,
                    "role": role,
                    "role_status": role_status,
                    "delta": delta,
                    "valid_time": _window(node),
                    "known_at": node.get("known_at", {}).get(person_id),
                    "evidence_ids": _evidence_ids(node),
                }
            )
    return rows


def _version_relationships(
    relationships: dict[str, dict], parent: dict
) -> tuple[list[dict], list[dict]]:
    previous_rows = {
        row["relationship_id"]: row
        for group in ("relationships", "withdrawn_relationships")
        for row in parent.get(group, [])
    }
    active, withdrawn = [], []
    for relationship_id in sorted(relationships.keys() | previous_rows.keys()):
        current = relationships.get(relationship_id)
        previous = previous_rows.get(relationship_id)
        if current is None and previous["status"] == "withdrawn":
            withdrawn.append(deepcopy(previous))
            continue
        if current is None:
            current = {
                **{
                    key: previous[key]
                    for key in (
                        "relationship_id",
                        "kind",
                        "source_entity_id",
                        "target_entity_id",
                    )
                },
                "valid_time": dict(UNKNOWN_TIME),
                "source_event_ids": [],
                "evidence_ids": [],
            }
            status = "withdrawn"
            destination = withdrawn
        else:
            status = "recorded"
            destination = active
        if (
            previous
            and status == previous["status"]
            and current
            == {
                key: value
                for key, value in previous.items()
                if key not in VERSION_FIELDS
            }
        ):
            destination.append(deepcopy(previous))
            continue
        previous_id = previous["version_id"] if previous else None
        destination.append(
            {
                **current,
                "status": status,
                "version": previous["version"] + 1 if previous else 1,
                "version_id": _id("relationship-version", current, status, previous_id),
                "previous_version_id": previous_id,
            }
        )
    return active, withdrawn


def project_case_entities(graph: dict, *, parent: dict | None = None) -> dict:
    """Build a checked projection from the supplied graph and optional parent."""
    person = graph["person"]
    primary_id = person["person_id"]
    previous = (parent or {}).get("case_entities") or {}
    if parent and not previous:
        previous = project_case_entities(parent)
    case_id = _id("case", primary_id)
    issues: set[str] = set()
    if previous and previous["case_id"] != case_id:
        issues.add("relationship parent belongs to another case")
        previous = {}
    entities: dict[str, dict] = {}
    relationships: dict[str, dict] = {}

    def entity(kind, source_id, name=None, *, evidence_ref=None, known=True, **fields):
        entity_id = _id(kind, case_id, source_id)
        row = entities.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "kind": kind,
                "source_id": source_id,
                "name": name,
                "identity_status": "recorded" if known else "unknown",
                "evidence_refs": [],
                **fields,
            },
        )
        if name and row["name"] is None:
            row["name"] = name
        if name and row["name"] != name:
            issues.add(f"inconsistent {kind} identity: {source_id}")
        if evidence_ref and evidence_ref not in row["evidence_refs"]:
            row["evidence_refs"].append(evidence_ref)
        return entity_id

    def relate(
        kind,
        source_entity_id,
        target_entity_id,
        *,
        node=None,
        ref=None,
        role=None,
        distinguish_event=False,
        ongoing=False,
    ):
        event_id = node["event_id"] if node else None
        relationship_id = _id(
            "relationship",
            case_id,
            kind,
            source_entity_id,
            target_entity_id,
            event_id if distinguish_event else None,
        )
        valid_time = _window(node) if node else dict(UNKNOWN_TIME)
        if ongoing:
            valid_time["end"] = None
        relationships[relationship_id] = {
            "relationship_id": relationship_id,
            "kind": kind,
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "role": role,
            "valid_time": valid_time,
            "evidence_window": _window(node) if node else dict(UNKNOWN_TIME),
            "source_event_ids": [event_id] if event_id else [],
            "evidence_ids": _evidence_ids(node) if node else [],
            "evidence_refs": [ref] if ref else [],
        }

    primary = entity(
        "person", primary_id, person.get("name"), evidence_ref="person.person_id"
    )
    people = {primary_id: primary}
    for relative in person.get("family", []):
        relative_id = relative["id"]
        if relative_id in people:
            issues.add(f"duplicate case person_id: {relative_id}")
            continue
        ref = f"person.family.{relative_id}"
        relative_entity = entity(
            "person", relative_id, relative.get("name"), evidence_ref=ref
        )
        people[relative_id] = relative_entity
        relate(
            "kinship", primary, relative_entity, ref=ref, role=relative.get("relation")
        )

    household = entity(
        "household",
        "primary-arrangement",
        known=False,
        coverage="family" if person.get("family") else "individual",
    )
    birth_region = entity(
        "region",
        person.get("region_id") or person.get("region") or "unknown",
        person.get("region"),
        evidence_ref="person.region",
        known=bool(person.get("region_id") or person.get("region")),
        region_id=person.get("region_id"),
    )

    participant_ledger, event_context = {}, {}
    for node in sorted(graph["nodes"], key=lambda item: item["event_id"]):
        event_id = node["event_id"]
        if event_id in participant_ledger:
            issues.add(f"duplicate event_id: {event_id}")
            continue
        rows = _participant_rows(node, people, primary_id, issues)
        participant_ledger[event_id] = rows
        context: dict[str, str] = {}
        location = node.get("location")
        if location and location != person.get("region"):
            context["region_entity_id"] = entity(
                "region", location, location, evidence_ref=f"{event_id}.location"
            )
        elif location:
            context["region_entity_id"] = birth_region
        if (
            event_id == "birth"
            and entities[birth_region]["identity_status"] == "recorded"
        ):
            relate("birthplace", primary, birth_region, node=node, ref="person.region")
        if event_id == "household":
            entities[household]["evidence_refs"].append(event_id)
            context["household_entity_id"] = household
            for participant in node.get("participants", []):
                if participant in people:
                    relate(
                        "household_arrangement",
                        people[participant],
                        household,
                        node=node,
                    )

        if event_id == "shared-care":
            recipients = {
                row["entity_id"] for row in rows if row["role"] == "recipient"
            }
            supporters = {
                row["entity_id"] for row in rows if row["role"] == "supporter"
            }
            if (
                recipients != {primary}
                or len(supporters) != 1
                or supporters & {None, primary}
            ):
                issues.add(f"inconsistent shared-care participant roles: {event_id}")
            else:
                relate("care_support", primary, next(iter(supporters)), node=node)

        delta = node.get("state_delta", {})
        if event_id == "partnership" and delta.get("partnership_status"):
            partner = entity(
                "person", "partner-slot", known=False, evidence_ref=event_id
            )
            relate("partnership", primary, partner, node=node, ongoing=True)

        organization_id = delta.get("organization_id")
        if isinstance(organization_id, str) and organization_id.strip():
            organization = entity(
                "organization",
                organization_id,
                name=delta.get("organization_name"),
                evidence_ref=f"{event_id}.organization_id",
            )
            context["organization_entity_id"] = organization
            relate(
                "organization_affiliation",
                primary,
                organization,
                node=node,
                distinguish_event=True,
            )
        elif event_id in ORGANIZATION_CONTEXT:
            kind = (
                "education"
                if delta.get("entry_kind") == "education"
                else ORGANIZATION_CONTEXT[event_id]
            )
            organization = entity(
                "organization",
                f"unspecified:{event_id}",
                known=False,
                evidence_ref=event_id,
                context=kind,
            )
            context["organization_entity_id"] = organization
        event_context[event_id] = context

    active, withdrawn = _version_relationships(relationships, previous)
    return {
        "schema": SCHEMA,
        "case_id": case_id,
        "entities": sorted(entities.values(), key=lambda row: row["entity_id"]),
        "relationships": active,
        "withdrawn_relationships": withdrawn,
        "participant_ledger": participant_ledger,
        "event_context": event_context,
        "consistency": {"valid": not issues, "issues": sorted(issues)},
    }


def check_case_entity_consistency(graph: dict) -> dict:
    """Check that an attached ledger represents this exact graph."""
    expected = project_case_entities(graph)
    issues = expected["consistency"]["issues"].copy()
    stored = graph.get("case_entities")
    if stored is None:
        issues.append("missing case entity projection")
    else:
        for field in (
            "schema",
            "case_id",
            "entities",
            "participant_ledger",
            "event_context",
        ):
            if stored.get(field) != expected[field]:
                issues.append(f"case projection mismatch: {field}")
        relationships = []
        for group, status in (
            ("relationships", "recorded"),
            ("withdrawn_relationships", "withdrawn"),
        ):
            for row in stored.get(group, []):
                payload = {
                    key: value
                    for key, value in row.items()
                    if key not in VERSION_FIELDS
                }
                previous_id = row.get("previous_version_id")
                version = row.get("version")
                if (
                    row.get("status") != status
                    or type(version) is not int
                    or version < 1
                    or (version > 1) != bool(previous_id)
                    or row.get("version_id")
                    != _id("relationship-version", payload, status, previous_id)
                ):
                    issues.append(
                        f"invalid relationship version: {row.get('relationship_id')}"
                    )
                if status == "recorded":
                    relationships.append(payload)
        current = [
            {key: value for key, value in row.items() if key not in VERSION_FIELDS}
            for row in expected["relationships"]
        ]
        if relationships != current:
            issues.append("case projection mismatch: relationships")
    return {"valid": not issues, "issues": issues}


def attach_case_entities(graph: dict, *, parent: dict | None = None) -> dict:
    """Attach the checked case projection to the graph and return the ledger."""
    projection = project_case_entities(graph, parent=parent)
    if issues := projection["consistency"]["issues"]:
        ordered = sorted(
            issues,
            key=lambda issue: (
                0 if issue.startswith("unknown case participant") else 1,
                issue,
            ),
        )
        raise ValueError("；".join(ordered))
    graph["case_entities"] = projection
    return projection
