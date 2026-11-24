"""Versioned historical context rules for automatic candidate selection."""

import json
from datetime import date
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("data") / "world_rules.json"
MAINLAND_REGION_IDS = frozenset(
    "11 12 13 14 15 21 22 23 31 32 33 34 35 36 37 41 42 43 44 45 46 "
    "50 51 52 53 54 61 62 63 64 65".split()
)
REGION_IDS = MAINLAND_REGION_IDS | {"71", "81", "82"}


def _load_catalog() -> tuple[str, dict[str, dict[str, Any]]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog["schema_version"] != 1 or not catalog["world_version"]:
        raise ValueError("Unsupported world rules catalog version")

    scenarios: dict[str, dict[str, Any]] = {}
    for record in catalog["rules"]:
        rule = dict(record)
        rule_id = rule.pop("id")
        if rule_id in scenarios:
            raise ValueError(f"Duplicate world rule: {rule_id}")
        regions = rule["regions"]
        scope = rule["scope"]
        if (
            scope not in {"mainland", "regions", "external"}
            or bool(regions) != (scope == "regions")
            or not set(regions) <= REGION_IDS
        ):
            raise ValueError(f"Invalid geographic scope for world rule {rule_id}")

        validity = rule["valid_time"]
        start = date.fromisoformat(validity["start"])
        end = date.fromisoformat(validity["end"]) if validity["end"] else None
        candidate_end = date.fromisoformat(
            rule.get("candidate_through", "")
            or validity["end"]
            or f"{start.year}-12-31"
        )
        precision = validity["precision"]
        minimum_age, maximum_age = rule["age_band"]
        if (
            precision not in {"day", "month", "year", "range"}
            or candidate_end < start
            or (end is not None and candidate_end > end)
            or minimum_age < 0
            or (maximum_age is not None and maximum_age < minimum_age)
            or not rule["sources"]
        ):
            raise ValueError(
                f"Invalid date, age band, or source list for world rule {rule_id}"
            )
        rule["start"] = start.year
        rule["end"] = end.year if end and end.year != start.year else None
        rule["precision"] = precision
        if precision == "day" and end == start:
            rule["day"] = validity["start"]
        rule["candidate_through"] = candidate_end.isoformat()
        scenarios[rule_id] = rule

    return catalog["world_version"], scenarios


WORLD_VERSION, SCENARIOS = _load_catalog()


def history_window(
    scenario_id: str, region: str, birth_year: int, cutoff: date
) -> tuple[date, date] | None:
    """Return possible contextual overlap within the person's modeled lifetime."""
    rule = SCENARIOS[scenario_id]
    scope = rule["scope"]
    if scope == "external":
        return None
    region_ids = MAINLAND_REGION_IDS if scope == "mainland" else rule["regions"]
    if region not in region_ids:
        return None

    minimum_age, maximum_age = rule["age_band"]
    first = date.fromisoformat(rule["valid_time"]["start"])
    last = date.fromisoformat(rule["candidate_through"])
    start = max(first, date(birth_year + minimum_age, 1, 1))
    end = min(last, cutoff)
    if maximum_age is not None:
        end = min(end, date(birth_year + maximum_age, 12, 31))
    return (start, end) if start <= end else None
