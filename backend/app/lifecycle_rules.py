"""Versioned, declarative scenario rules for personal life stages."""

from __future__ import annotations

import hashlib
import random
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dimensions import _registry

CATALOG_PATH = Path(__file__).with_name("data") / "lifecycle_rules.json"
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")]
NonEmptyText = Annotated[str, Field(min_length=1)]


class DimensionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=r"^[A-Z]+\.[0-9]{2}$")]
    value_key: Identifier
    unknown_fields: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_value_fields(self):
        if self.value_key in self.unknown_fields or len(
            set(self.unknown_fields)
        ) != len(self.unknown_fields):
            raise ValueError("dimension value fields must be distinct")
        return self


class LifecycleRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Identifier
    title: NonEmptyText
    category: Identifier
    minimum_age: int = Field(ge=0, le=120)
    maximum_age: int = Field(ge=0, le=120)
    latest_year_offset: int | None = Field(default=None, ge=0, le=120)
    anchors: list[Identifier] = Field(min_length=1)
    condition: NonEmptyText
    state_field: Identifier
    choices: list[NonEmptyText] = Field(min_length=1)
    dimensions: list[DimensionBinding] = Field(min_length=1)
    household: Literal["family", "individual"] | None = None
    participants: Literal["subject", "anchor"] = "subject"
    location: Literal["region", "unknown"] = "region"

    @model_validator(mode="after")
    def validate_rule(self):
        if self.maximum_age < self.minimum_age:
            raise ValueError("maximum_age precedes minimum_age")
        if self.id in self.anchors or len(set(self.anchors)) != len(self.anchors):
            raise ValueError("rule anchors must be distinct and exclude the rule")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("synthetic alternatives must be distinct")
        if len({binding.id for binding in self.dimensions}) != len(self.dimensions):
            raise ValueError("each dimension is assigned at most once")
        return self


class LifecycleCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    version: NonEmptyText
    rules: list[LifecycleRule] = Field(min_length=1)


def _load_catalog(data: bytes) -> LifecycleCatalog:
    catalog = LifecycleCatalog.model_validate_json(data)
    rule_ids = {rule.id for rule in catalog.rules}
    if len(rule_ids) != len(catalog.rules):
        raise ValueError("duplicate lifecycle rule IDs")
    dimensions = {
        row["dim_id"] for family in _registry()[0] for row in family["dimensions"]
    }
    previous: set[str] = set()
    for rule in catalog.rules:
        if any(
            anchor in rule_ids and anchor not in previous for anchor in rule.anchors
        ):
            raise ValueError(f"lifecycle dependencies must appear before {rule.id}")
        if invalid := {binding.id for binding in rule.dimensions} - dimensions:
            raise ValueError(f"unknown dimension IDs in {rule.id}: {sorted(invalid)}")
        previous.add(rule.id)
    return catalog


_CATALOG_BYTES = CATALOG_PATH.read_bytes()
_CATALOG = _load_catalog(_CATALOG_BYTES)
LIFECYCLE_VERSION = _CATALOG.version
LIFECYCLE_SHA256 = hashlib.sha256(_CATALOG_BYTES).hexdigest()


def generate_lifecycle_events(
    spec: dict, nodes: list[dict], cutoff: date, *, suppressed: set[str] | None = None
) -> list[dict]:
    """Generate applicable scenario events using one stable draw per event and field."""
    existing = {node["event_id"]: node for node in nodes}
    birth = existing.get("birth")
    if not birth:
        return []
    generated = []
    for rule in _CATALOG.rules:
        if (
            rule.id in existing
            or (suppressed is not None and rule.id in suppressed)
            or (rule.household is not None and rule.household != spec["household"])
            or any(anchor not in existing for anchor in rule.anchors)
        ):
            continue

        anchors = [existing[anchor] for anchor in rule.anchors]
        latest_start = max(date.fromisoformat(anchor["start"]) for anchor in anchors)
        first_full_year = latest_start.year + (
            latest_start != date(latest_start.year, 1, 1)
        )
        earliest_year = max(
            spec["birth_year"] + rule.minimum_age,
            first_full_year,
        )
        latest_year = min(spec["birth_year"] + rule.maximum_age, date.max.year)
        if rule.latest_year_offset is not None:
            latest_year = min(latest_year, first_full_year + rule.latest_year_offset)
        if earliest_year > latest_year:
            continue
        seed = f"{spec['seed']}:lifecycle:{rule.id}"
        year = random.Random(f"{seed}:year").randint(earliest_year, latest_year)
        start = date(year, 1, 1)
        if start > cutoff:
            continue
        selected = random.Random(f"{seed}:alternative").choice(rule.choices)
        participants = (
            list(
                dict.fromkeys(
                    person for anchor in anchors for person in anchor["participants"]
                )
            )
            if rule.participants == "anchor"
            else [birth["participants"][0]]
        )
        node = {
            "event_id": rule.id,
            "title": rule.title,
            "category": rule.category,
            "start": start.isoformat(),
            "end": min(date(year, 12, 31), cutoff).isoformat(),
            "precision": "year",
            "location": spec["region_label"] if rule.location == "region" else None,
            "participants": participants,
            "notes": f"{rule.condition}。合成安排为「{selected}」。",
            "state_delta": {rule.state_field: selected},
            "evidence": [
                {
                    "id": f"{LIFECYCLE_VERSION}:{rule.id}",
                    "label": "人生阶段合成情景规则",
                    "kind": "synthetic_rule",
                }
            ],
            "summary_node": False,
            "depends_on": rule.anchors.copy(),
            "decision": {
                "selected": selected,
                "options": rule.choices.copy(),
                "condition": rule.condition,
                "basis": "seeded_scenario",
            },
            "lifecycle_rule": {
                "id": rule.id,
                "version": LIFECYCLE_VERSION,
                "basis": "synthetic_rule",
            },
            "dimension_values": {
                binding.id: {
                    binding.value_key: selected,
                    **dict.fromkeys(binding.unknown_fields),
                }
                for binding in rule.dimensions
            },
        }
        generated.append(node)
        existing[rule.id] = node
    return generated
