"""Validate researcher observations and project them on a child LifeGraph."""

from copy import deepcopy
from datetime import UTC, date, datetime
from hashlib import sha256
from json import dumps
from urllib.parse import urlsplit

from .historical_mechanisms import (
    EXPOSURE_STATUSES,
    GATE_STATUSES,
    HISTORY_MECHANISMS,
    assess_history,
)
from .world_rules import MAINLAND_REGION_IDS, REGION_IDS, SCENARIOS, WORLD_VERSION


def _day(value: object) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("日期必须采用 YYYY-MM-DD。")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期必须采用 YYYY-MM-DD。") from exc
    if result.isoformat() != value:
        raise ValueError("日期必须采用 YYYY-MM-DD。")
    return result


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"必须提供{label}。")
    return value.strip()


def _optional_text(request: dict, key: str) -> str | None:
    value = request.get(key)
    return None if value is None else _required_text(value, key)


def _source_key(reference: str) -> str:
    try:
        parts = urlsplit(reference)
    except ValueError:
        return reference
    if parts.scheme in {"http", "https"}:
        return f"{parts.netloc.casefold()}{parts.path.rstrip('/')}"
    return reference


PUBLIC_SOURCES = frozenset(
    _source_key(source["url"])
    for scenario in SCENARIOS.values()
    for source in scenario["sources"]
)


def _cutoff(person: dict) -> date:
    return _day(person.get("death_date") or person["as_of"])


def _applicable_regions(rule: dict, mechanism: dict) -> set[str]:
    if rule["scope"] == "mainland":
        return set(MAINLAND_REGION_IDS)
    if rule["scope"] == "external":
        return set(mechanism.get("jurisdictions", ()))
    return set(rule["regions"])


def _validate_request(graph: dict, node: dict, request: dict) -> dict:
    rule_id = node["event_id"]
    rule = SCENARIOS[rule_id]
    mechanism = HISTORY_MECHANISMS[rule_id]
    kind, key, status = request.get("kind"), request.get("key"), request.get("status")
    if kind == "gate":
        if not any(key in group for group in mechanism["gates"].values()):
            raise ValueError("此历史节点没有该制度门槛。")
        if status not in GATE_STATUSES:
            raise ValueError("制度门槛状态无效。")
        selector = "gate"
    elif kind == "exposure":
        if key not in mechanism["channels"]:
            raise ValueError("此历史节点没有该暴露渠道。")
        if status not in EXPOSURE_STATUSES:
            raise ValueError("暴露状态无效。")
        selector = "channel"
    else:
        raise ValueError("断言类型须为制度门槛或暴露。")

    source_ref = _required_text(request.get("source_ref"), "独立来源编号")
    source_note = _required_text(request.get("source_note"), "来源说明")
    if _source_key(source_ref) in PUBLIC_SOURCES or source_ref in SCENARIOS:
        raise ValueError("公共资料无法作为个体经历的来源，请提供独立记录。")
    graph_evidence_ids = {
        entry["id"]
        for event in graph["nodes"]
        for entry in event.get("evidence", ())
        if entry.get("id")
    }
    if source_ref in graph_evidence_ids:
        raise ValueError("请补录独立来源编号，既有骨架资料仅作为背景。")

    person = graph["person"]
    subject = _optional_text(request, "subject_ref") or person["person_id"]
    participants = {
        person["person_id"],
        *(member["id"] for member in person.get("family", [])),
    }
    if subject not in participants:
        raise ValueError("断言主体必须是本骨架的参与者。")

    occurred = _day(request.get("date"))
    if occurred > _cutoff(person):
        raise ValueError("观察日期超过档案截止日。")
    if subject == person["person_id"] and occurred.year < person["birth_year"]:
        raise ValueError("观察日期早于人物出生年份。")
    start = _day(rule["valid_time"]["start"])
    end = _day(rule["valid_time"]["end"]) if rule["valid_time"]["end"] else None
    if occurred < start or (key == "direct" and end and occurred > end):
        raise ValueError("观察日期超出历史节点的发生时窗。")
    known = _day(request["known_at"]) if request.get("known_at") is not None else None
    if known and known < occurred:
        raise ValueError("人物获知日不能早于观察发生日。")

    region = _optional_text(request, "region")
    location = _optional_text(request, "location_ref")
    applicable = _applicable_regions(rule, mechanism)
    if region is not None and region not in REGION_IDS and region not in applicable:
        raise ValueError("观察地区编号无效。")
    if kind == "gate":
        context_region = node.get("historical_assessment", {}).get(
            "opportunity", {}
        ).get("spatial_match", {}).get("region") or person.get("region_id")
        if region is not None and region != context_region:
            raise ValueError("观察地区须与该历史节点的适用地区一致。")
    if key == "direct" and status in {"confirmed", "probable", "potential"}:
        if status in {"confirmed", "probable"} and (not region or not location):
            raise ValueError("直接暴露需要明确的发生地和在场位置来源。")
        if region is not None and region not in applicable:
            raise ValueError("直接暴露的发生地超出规则适用地区。")

    record = {
        "rule_id": rule_id,
        "rule_version": rule["version"],
        "world_version": WORLD_VERSION,
        "kind": kind,
        selector: key,
        "status": status,
        "date": occurred.isoformat(),
        "subject_ref": subject,
        "source_ref": source_ref,
        "source_note": source_note,
        "origin": "researcher_supplied",
    }
    if known:
        record["known_at"] = known.isoformat()
    for field, value in (
        ("region", region),
        ("location_ref", location),
        ("relationship_ref", _optional_text(request, "relationship_ref")),
        ("contact_event_id", _optional_text(request, "contact_event_id")),
    ):
        if value is not None:
            record[field] = value
    return record


def reproject_history_assertions(node: dict, *, person: dict) -> dict:
    """Reassess a world node from its stored, individually scoped observations."""
    records_by_subject: dict[str, list[dict]] = {person["person_id"]: []}
    cutoff = _cutoff(person)
    archive_day = cutoff
    region = person.get("region_id")
    window = _day(node["start"]), _day(node.get("end") or node["start"])
    for record in node.get("historical_assertions", ()):
        registered = datetime.fromisoformat(record["recorded_at"]).date()
        archive_day = max(archive_day, registered)
        fields = {
            key: value
            for key, value in record.items()
            if key not in {"source_note", "source_ref", "recorded_at"}
        }
        fields.update(
            evidence_ref=record["source_ref"], recorded_at=registered.isoformat()
        )
        records_by_subject.setdefault(record["subject_ref"], []).append(fields)

    assessments = {
        subject: assess_history(
            node["event_id"],
            as_of=cutoff,
            region=region,
            window=window,
            archive_as_of=archive_day,
            evidence=records,
        )
        for subject, records in records_by_subject.items()
    }
    main = assessments.pop(person["person_id"])
    if assessments:
        main["subject_assessments"] = assessments
    return main


def apply_history_assertion(graph: dict, request: dict, recorded_at: datetime) -> dict:
    """Create a child with one additional sourced assertion and refreshed assessment."""
    event_id = request.get("event_id")
    source_node = next(
        (node for node in graph["nodes"] if node["event_id"] == event_id), None
    )
    if (
        not source_node
        or source_node.get("category") != "history"
        or event_id not in SCENARIOS
    ):
        raise ValueError("找不到可补录证据的历史背景节点。")
    if recorded_at.tzinfo is None:
        raise ValueError("登记时间需要时区。")

    record = _validate_request(graph, source_node, request)
    identity = sha256(
        dumps(record, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    existing = source_node.get("historical_assertions", [])
    if any(item["assertion_id"] == identity for item in existing):
        raise ValueError("该研究断言已登记。")
    record.update(
        assertion_id=identity, recorded_at=recorded_at.astimezone(UTC).isoformat()
    )

    child = deepcopy(graph)
    child.update(
        graph_revision_id=None,
        parent_revision_id=graph.get("graph_revision_id"),
        branch="edit",
        diff={"added": [], "modified": [event_id], "removed": [], "recomputed": []},
    )
    node = next(item for item in child["nodes"] if item["event_id"] == event_id)
    node.setdefault("historical_assertions", []).append(record)
    node["historical_assessment"] = reproject_history_assertions(
        node, person=child["person"]
    )
    return child
