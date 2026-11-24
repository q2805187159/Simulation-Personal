"""Historical opportunities, qualification gates, and personal exposure evidence."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

from .world_rules import MAINLAND_REGION_IDS, SCENARIOS, WORLD_VERSION

CATALOG_PATH = Path(__file__).with_name("data") / "history_mechanisms.json"
CHANNELS = frozenset(
    {"direct", "household", "school", "work", "service", "network", "media"}
)
GATE_STAGES = ("eligibility", "supply", "execution")
GATE_STATUSES = frozenset({"met", "not_met", "unknown", "not_applicable"})
EXPOSURE_STATUSES = ("confirmed", "probable", "potential", "unknown", "excluded")


def _load_catalog() -> tuple[str, tuple[str, ...], dict[str, dict[str, Any]]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if (
        catalog["schema_version"] != 1
        or not catalog["mechanism_version"]
        or not catalog["source_documents"]
    ):
        raise ValueError("Unsupported historical mechanism catalog")
    mechanisms = {}
    for rule in catalog["rules"]:
        rule_id = rule["id"]
        channels = rule["channels"]
        gates = rule["gates"]
        identifiers = [gate for group in gates.values() for gate in group]
        if (
            rule_id in mechanisms
            or not channels
            or len(channels) != len(set(channels))
            or not set(channels) <= CHANNELS
            or not set(gates) <= set(GATE_STAGES)
            or not gates.get("eligibility")
            or not gates.get("execution")
            or len(identifiers) != len(set(identifiers))
            or not all(
                rule[field]
                for field in (
                    "trigger",
                    "pathway",
                    "outcome",
                    "negative_control",
                    "record_check",
                )
            )
        ):
            raise ValueError(f"Invalid historical mechanism: {rule_id}")
        mechanisms[rule_id] = rule
    if set(mechanisms) != set(SCENARIOS):
        raise ValueError("Historical mechanism IDs must match the world rules catalog")
    return catalog["mechanism_version"], tuple(catalog["source_documents"]), mechanisms


MECHANISM_VERSION, SOURCE_DOCUMENTS, HISTORY_MECHANISMS = _load_catalog()


def _regions(rule_id: str) -> Iterable[str]:
    rule = SCENARIOS[rule_id]
    if rule["scope"] == "mainland":
        return MAINLAND_REGION_IDS
    if rule["scope"] == "external":
        return HISTORY_MECHANISMS[rule_id].get("jurisdictions", ())
    return rule["regions"]


def _day(value: Any) -> date | None:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _evidence_by_key(
    rule_id: str,
    evidence: Iterable[Mapping[str, Any]],
    as_of: date,
    archive_as_of: date,
    region: str | None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rule = SCENARIOS[rule_id]
    first = date.fromisoformat(rule["valid_time"]["start"])
    last = _day(rule["valid_time"]["end"])
    applicable_regions = _regions(rule_id)
    observations = defaultdict(list)
    for row in evidence:
        at = _day(row.get("date"))
        recorded = _day(row.get("recorded_at")) if "recorded_at" in row else at
        if (
            row.get("rule_id") != rule_id
            or not isinstance(row.get("evidence_ref"), str)
            or not row["evidence_ref"].strip()
            or row.get("rule_version", rule["version"]) != rule["version"]
            or row.get("world_version", WORLD_VERSION) != WORLD_VERSION
            or at is None
            or not first <= at <= as_of
            or recorded is None
            or recorded > archive_as_of
        ):
            continue
        kind = row.get("kind")
        if kind == "gate" and row.get("status") in GATE_STATUSES:
            key = row.get("gate")
            if region is not None and row.get("region", region) != region:
                continue
        elif kind == "exposure" and row.get("status") in EXPOSURE_STATUSES:
            key = row.get("channel")
            if key == "direct":
                if last is not None and at > last:
                    continue
                if (
                    row.get("status") != "excluded"
                    and row.get("region") is not None
                    and row["region"] not in applicable_regions
                ):
                    continue
        else:
            continue
        if not isinstance(key, str):
            continue
        observation = {
            "evidence_ref": row["evidence_ref"],
            "status": row["status"],
            "date": at.isoformat(),
            "recorded_at": recorded.isoformat(),
        }
        for field in (
            "region",
            "origin",
            "subject_ref",
            "location_ref",
            "relationship_ref",
            "contact_event_id",
            "precision",
            "implementation_version",
            "value",
        ):
            if field in row:
                observation[field] = row[field]
        known_at = _day(row.get("known_at"))
        if known_at is not None and known_at <= as_of:
            observation["known_at"] = known_at.isoformat()
        observations[(kind, key)].append(observation)
    return observations


def _observed_state(
    observations: list[dict[str, Any]], *, channel: bool = False
) -> dict[str, Any]:
    records = sorted(
        observations,
        key=lambda row: (row["date"], row["recorded_at"], row["evidence_ref"]),
    )
    assertions = [row for row in records if row["status"] != "unknown"]
    if assertions and not channel:
        latest = assertions[-1]["date"]
        assertions = [row for row in assertions if row["date"] == latest]
    statuses = {row["status"] for row in assertions}
    if not channel:
        status = next(iter(statuses)) if len(statuses) == 1 else "unknown"
        conflict = len(statuses) > 1
    else:
        conflict = "excluded" in statuses and len(statuses) > 1
        status = next(
            (value for value in EXPOSURE_STATUSES if value in statuses), "unknown"
        )
    return {
        "status": "unknown" if conflict else status,
        "evidence_refs": list(dict.fromkeys(row["evidence_ref"] for row in records)),
        "observations": records,
        "conflicting_evidence": conflict,
    }


def _opportunity(
    rule_id: str,
    rule: Mapping[str, Any],
    as_of: date,
    region: str | None,
    window: tuple[date, date] | None,
    location_ref: str | None,
) -> dict[str, Any]:
    validity = rule["valid_time"]
    availability = rule.get("release_and_record_times", {})
    effective = _day(availability.get("effective_at")) or date.fromisoformat(
        validity["start"]
    )
    last = _day(validity["end"])
    published = _day(availability.get("published_at"))
    spatial_status = (
        "unknown"
        if region is None
        else "matched"
        if region in _regions(rule_id)
        else "outside"
    )
    start = max(effective, window[0]) if window is not None else effective
    end = min(
        bound
        for bound in (as_of, last, window[1] if window is not None else None)
        if bound is not None
    )
    overlaps = start <= end
    reason_codes = []
    if spatial_status == "outside":
        reason_codes.append("REGION_OUTSIDE_RULE_SCOPE")
    elif spatial_status == "unknown":
        reason_codes.append("REGION_EVIDENCE_REQUIRED")
    if not overlaps:
        reason_codes.append("DATE_OUTSIDE_EFFECTIVE_WINDOW")
    if published is not None and as_of < published:
        reason_codes.append("NOT_PUBLISHED_AS_OF")
    status = "unknown"
    if spatial_status == "outside" or not overlaps:
        status = "excluded"
    elif spatial_status == "matched" and (published is None or published <= as_of):
        status = "potential"
        reason_codes.append("CONTEXT_SCOPE_MATCH")
    return {
        "status": status,
        "reason_codes": reason_codes,
        "availability": {"effective_at": effective.isoformat(), **availability},
        "spatial_match": {
            "status": spatial_status,
            "region": region,
            "precision": "jurisdiction" if rule["scope"] == "external" else "region",
            "location_ref": location_ref,
        },
        "time_match": {
            "status": "overlap" if overlaps else "outside",
            "start": start.isoformat() if overlaps else None,
            "end": end.isoformat() if overlaps else None,
            "precision": validity["precision"],
        },
        "evidence_refs": [location_ref] if location_ref else [],
    }


def _policy_rates(
    mechanism: Mapping[str, Any],
    opportunity: Mapping[str, Any],
    as_of: date,
    window: tuple[date, date] | None,
) -> list[dict[str, Any]]:
    if opportunity["status"] != "potential":
        return []
    rates = mechanism.get("policy_rates", ())
    start, end = window if window is not None else (as_of, as_of)
    end = min(end, as_of)
    applicable = []
    for index, rate in enumerate(rates):
        period_start = max(start, date.fromisoformat(rate["effective_at"]))
        period_end = end
        if index + 1 < len(rates):
            next_rate = date.fromisoformat(rates[index + 1]["effective_at"])
            period_end = min(period_end, next_rate - timedelta(days=1))
        if period_start <= period_end:
            applicable.append(
                {
                    **rate,
                    "rates": dict(rate["rates"]),
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                }
            )
    return applicable


def assess_history(
    rule_id: str,
    *,
    as_of: date,
    region: str | None = None,
    window: tuple[date, date] | None = None,
    location_ref: str | None = None,
    evidence: Iterable[Mapping[str, Any]] = (),
    archive_as_of: date | None = None,
) -> dict[str, Any]:
    """Evaluate a world context and optional observations scoped to one person.

    A dated observation identifies a rule, a gate or channel, its status, and
    a personal or local evidence reference. A graph context match stays
    potential until a matching personal exposure is observed independently.
    """
    mechanism = HISTORY_MECHANISMS[rule_id]
    rule = SCENARIOS[rule_id]
    archive_as_of = archive_as_of or as_of
    observations = _evidence_by_key(rule_id, evidence, as_of, archive_as_of, region)
    opportunity = _opportunity(rule_id, rule, as_of, region, window, location_ref)
    channels = []
    for channel in mechanism["channels"]:
        assessed = _observed_state(
            observations.get(("exposure", channel), []), channel=True
        )
        known_dates = [
            row["known_at"] for row in assessed["observations"] if "known_at" in row
        ]
        channels.append(
            {"channel": channel, "known_at": min(known_dates, default=None), **assessed}
        )

    gates = [
        {
            "id": gate,
            "stage": stage,
            **_observed_state(observations.get(("gate", gate), [])),
        }
        for stage in GATE_STAGES
        for gate in mechanism["gates"].get(stage, ())
    ]
    required = mechanism["gates"]["eligibility"]
    qualification = {gate["status"] for gate in gates if gate["stage"] == "eligibility"}
    eligibility = "unknown"
    if "not_met" in qualification:
        eligibility = "ineligible"
    elif qualification == {"not_applicable"}:
        eligibility = "not_applicable"
    elif qualification <= {"met", "not_applicable"}:
        eligibility = "eligible"

    supply_gates = [gate for gate in gates if gate["stage"] == "supply"]
    practical_status = "unknown"
    if eligibility == "ineligible" or any(
        gate["status"] == "not_met" for gate in supply_gates
    ):
        practical_status = "blocked"
    elif eligibility == "eligible" and all(
        gate["status"] in {"met", "not_applicable"} for gate in supply_gates
    ):
        practical_status = "available"
    opportunity["practical_status"] = practical_status
    opportunity["evidence_refs"] = list(
        dict.fromkeys(
            [
                *opportunity["evidence_refs"],
                *(
                    ref
                    for gate in gates
                    if gate["stage"] != "execution"
                    for ref in gate["evidence_refs"]
                ),
            ]
        )
    )

    channel_statuses = {channel["status"] for channel in channels}
    status = next(
        (value for value in EXPOSURE_STATUSES if value in channel_statuses), "unknown"
    )
    if status == "unknown" and opportunity["status"] == "potential":
        status = "potential"

    return {
        "rule_id": rule_id,
        "rule_version": rule["version"],
        "world_version": WORLD_VERSION,
        "mechanism_version": MECHANISM_VERSION,
        "as_of": as_of.isoformat(),
        "archive_as_of": archive_as_of.isoformat(),
        "status": status,
        "channels": channels,
        "opportunity": opportunity,
        "eligibility": {"status": eligibility, "required_gates": list(required)},
        "policy_gates": gates,
        "policy_rates": _policy_rates(mechanism, opportunity, as_of, window),
        "trigger": mechanism["trigger"],
        "pathway": mechanism["pathway"],
        "outcomes": [
            {
                "id": f"{rule_id}:pathway",
                "kind": "pathway",
                "label": mechanism["outcome"],
                "evidence_status": "mechanism_hypothesis",
                "conditions": list(required),
                "event_types": list(mechanism["gates"]["execution"]),
            }
        ],
        "negative_controls": [
            {
                "id": f"{rule_id}:alternative",
                "kind": "alternative_pathway",
                "label": mechanism["negative_control"],
                "evidence_status": "synthetic_assumption",
            },
            {
                "id": f"{rule_id}:no_direct_effect",
                "kind": "no_direct_effect",
                "label": "未记录直接变化，其他暴露渠道依各自证据判定。",
                "evidence_status": "synthetic_assumption",
            },
        ],
        "record_check": mechanism["record_check"],
        "probability": {"value": None, "basis": "uncalibrated", "interval": None},
        "source_refs": [source["url"] for source in rule["sources"]],
        "source_documents": list(SOURCE_DOCUMENTS),
    }
