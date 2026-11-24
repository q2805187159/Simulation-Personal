"""Project the v3 dimension registry onto one revision of a life graph."""

from collections import Counter, defaultdict
import csv
from datetime import date
from functools import lru_cache
import hashlib
from io import StringIO
from pathlib import Path
from typing import Any

from .world_rules import SCENARIOS


REGISTRY_VERSION = "v3.0"
CATALOG_PATH = Path(__file__).with_name("data") / "dimension_catalog.csv"
SOURCE_PATH = Path(__file__).resolve().parents[2] / "docs" / "v3" / "维度字典.csv"
RECORD_FIELDS = {
    "name": "维度",
    "definition": "定义",
    "value_type": "值类型",
    "time_nature": "时间性质",
    "state_layer": "状态层",
    "source_suggestions": "来源建议",
    "missing_policy": "缺失处理",
    "forbidden_inference": "禁止推断",
}
QUESTION_FAMILIES = {"SURVEYCOM", "SURVEYRESP"}
WORK_FAMILIES = {"WORK", "CAREER"}
EVENT_REASONS = {
    "synthetic_event": "合成事件依据。",
    "simulation_event": "来自仿真过程已生成的个体事件。",
    "world_context": "世界事件与人物条件匹配为背景候选，个人经历待确认。",
    "mixed_events": "来自多类事件，逐项依据见对应事件。",
}


@lru_cache(maxsize=1)
def _registry() -> tuple[list[dict], str]:
    path = CATALOG_PATH if CATALOG_PATH.is_file() else SOURCE_PATH
    raw = path.read_bytes()
    families: dict[str, dict] = {}
    seen = set()
    for row in csv.DictReader(StringIO(raw.decode("utf-8-sig"))):
        family_id = row["family_id"]
        dim_id = row["dim_id"]
        if dim_id in seen or not dim_id.startswith(f"{family_id}."):
            raise ValueError(f"Invalid dimension catalog identifier: {dim_id}")
        seen.add(dim_id)
        family = families.setdefault(
            family_id, {"family_id": family_id, "name": row["族"], "dimensions": []}
        )
        family["dimensions"].append(
            {
                "dim_id": dim_id,
                "family_id": family_id,
                **{key: row[column] for key, column in RECORD_FIELDS.items()},
                "related_family_ids": row["关联维度"].split(";"),
            }
        )
    if len(families) != 55 or any(len(f["dimensions"]) != 8 for f in families.values()):
        raise ValueError(
            "Dimension catalog v3 must contain 55 families of 8 dimensions"
        )
    return list(families.values()), hashlib.sha256(raw).hexdigest()


def _window(start, end, precision="day") -> dict:
    return {"start": start, "end": end, "precision": precision}


def _provenance(kind: str, rule_id: str | None, source_ids=()) -> dict:
    return {"kind": kind, "rule_id": rule_id, "source_ids": list(source_ids)}


def _age(birthday: date, reference: date) -> int:
    return (
        reference.year
        - birthday.year
        - ((reference.month, reference.day) < (birthday.month, birthday.day))
    )


def _stages(earliest_age: int, latest_age: int) -> list[str]:
    bands = (
        (0, 5, "早期儿童"),
        (6, 17, "学龄至青少年"),
        (18, 64, "成年"),
        (65, 2000, "老年"),
    )
    return [
        label
        for lower, upper, label in bands
        if earliest_age <= upper and latest_age >= lower
    ]


def _missing(definition: dict, person: dict, maximum_age: int) -> tuple[str, str]:
    family = definition["family_id"]
    if family in QUESTION_FAMILIES:
        return "not_applicable", "需要指定问卷题目、会话和参照期。"
    if family == "DEATH" and person["life_status"] == "living":
        return "not_applicable", "参照时点仍在世，未建立未来死亡事件。"
    if family in WORK_FAMILIES and maximum_age < 16:
        return "not_applicable", "在当前模拟的年龄范围之外。"
    if definition["state_layer"] == "观测":
        return "unresolved", "当前骨架未包含该项测量、自报或档案观察。"
    if definition["state_layer"] == "主观状态":
        return "unresolved", "需要人物视角及相关事件依据。"
    return "unresolved", "当前修订缺少适用的个体依据或生成规则。"


def _context_dimensions(node: dict) -> dict:
    event_id = node["event_id"]
    value = {
        "title": node["title"],
        "match_region": node["location"],
        "match_scope": "background_candidate",
    }
    result = {"HISTORY.01": value}
    if event_id.startswith(("POL-", "WEL-", "MED-", "WORK-", "PUB-")):
        result["HISTORY.02"] = value
    if event_id.startswith("DIS-"):
        result["ENVIRON.02"] = value
    if event_id.startswith("COVID-"):
        result["ENVIRON.06"] = value
    return result


def _engine_event_values(node: dict, person_id: str) -> dict:
    event_id = node["event_id"]
    state = node["state_delta"]
    if event_id == "job-exit" and isinstance(status := state.get("work_status"), str):
        return {"WORK.01": status, "CAREER.04": {"transition": status}}
    if event_id == "relocation" and state.get("residence_changed") is True:
        return {"GEO.05": {"residence_changed": True}}
    if event_id == "health-transition" and isinstance(
        need := state.get("care_need"), str
    ):
        return {"HOUSEHOLD.04": {"need": need}}
    if event_id == "partnership" and isinstance(
        status := state.get("partnership_status"), str
    ):
        return {"PARTNER.01": {"relationship": status}}
    if event_id != "shared-care" or not isinstance(
        arrangement := state.get("care_arrangement"), str
    ):
        return {}
    values = {
        "HOUSEHOLD.08": {"arrangement": arrangement, "scope": "modeled_care_support"}
    }
    participants = node.get("participant_deltas", [])
    if any(
        item["role"] == "recipient" and item["person_id"] == person_id
        for item in participants
    ):
        links = [
            {
                "provider_id": item["person_id"],
                "recipient_id": person_id,
                "scope": "modeled_care_commitment",
            }
            for item in participants
            if item["role"] == "supporter"
            and item["person_id"] != person_id
            and item["delta"].get("support_commitment") is True
        ]
        if links:
            values["HOUSEHOLD.05"] = links[0] if len(links) == 1 else links
    return values


def _event_values(node: dict, person: dict, engine_name: str | None) -> dict:
    event_id = node["event_id"]
    state = node.get("state_delta", {})
    decision = node.get("decision")
    path = decision.get("selected") if decision else None
    event_values: dict[str, Any] = {
        "PROV.07": (
            {"event_id": event_id, "source": "simulation_event", "engine": engine_name}
            if engine_name
            else {"event_id": event_id, "source": "synthetic_graph_event"}
        ),
        "PROV.04": {
            "valid_time": _window(
                node["start"], node.get("end"), node.get("precision", "day")
            ),
            "recorded_at": None,
            "known_at": None,
        },
    }
    if node.get("summary_node"):
        return _context_dimensions(node)
    if engine_name:
        event_values.update(_engine_event_values(node, person["person_id"]))
    if decision:
        event_values.update(
            {
                "GOAL.05": {
                    "options": decision["options"],
                    "scope": "synthetic_visible_options",
                },
                "GOAL.06": {"selected": path, "condition": decision["condition"]},
                "LIFECHANCE.03": {"options": decision["options"]},
                "LIFECHANCE.04": decision["condition"],
                "LIFECHANCE.08": {
                    "unselected": [
                        option for option in decision["options"] if option != path
                    ]
                },
            }
        )
        pressure = state.get("pressure_delta")
        if type(pressure) in {int, float} and pressure > 0:
            event_values["AFFECT.03"] = {
                "condition": decision["condition"],
                "scope": "scenario_stressor",
            }
    if event_id not in {"birth", "death"}:
        event_values["MEMORY.01"] = {
            "title": node["title"],
            "scope": "memory_candidate",
        }
    if event_id == "birth":
        event_values["TIME.01"] = _window(
            node["start"], node.get("end"), node["precision"]
        )
        event_values["GEO.01"] = {
            "label": node["location"],
            "precision": "province_or_region",
        }
        if roles := [
            member["id"]
            for member in person.get("family", [])
            if member["id"] in node["participants"]
        ]:
            event_values["ORIGIN.01"] = {
                "participant_ids": roles,
                "scope": "birth_household_link",
            }
    if state.get("education_stage"):
        event_values["EDU.01"] = {
            "stage": state["education_stage"],
            "location": node["location"],
        }
        event_values["GEO.02"] = {
            "label": node["location"],
            "precision": "province_or_region",
        }
    if event_id == "school-path" and path:
        event_values["EDU.08"] = {"selected": path, "scope": "synthetic_school_choice"}
        event_values["KNOW.05"] = decision["condition"]
    if event_id == "early-routine" and path:
        event_values["DEV.08"] = {"activity": path, "scope": "synthetic_peer_activity"}
    if event_id in {"work", "employment-entry"}:
        if state.get("entry_kind") == "education" and state.get("entry_route"):
            event_values["EDU.01"] = {
                "stage": state["entry_route"],
                "location": node["location"],
            }
            event_values["DAILY.08"] = "日程以继续学习为主。"
            event_values["EDU.07"] = {
                "activity": state["entry_route"],
                "credential": None,
            }
            event_values["DAILY.05"] = {"activity": "实务学习和练习", "hours": None}
        elif state.get("entry_kind") == "work" and all(
            state.get(key)
            for key in ("work_status", "work_context", "work_task", "entry_route")
        ):
            event_values["WORK.01"] = state["work_status"]
            event_values["WORK.02"] = {
                "sector": state["work_context"],
                "task": state["work_task"],
            }
            event_values["CAREER.01"] = state["entry_route"]
            event_values["DAILY.08"] = "日程开始围绕劳动安排变化。"
            event_values["KNOW.02"] = {
                "practice": state["work_task"],
                "proficiency": None,
            }
            event_values["WORK.03"] = {
                "arrangement": state.get("work_arrangement"),
                "contract": None,
            }
    if event_id == "commute-path" and path:
        event_values["MOBILITY.01"] = {
            "region": node["location"],
            "activity": "日常劳动",
        }
        event_values["MOBILITY.03"] = {"arrangement": path, "scope": "work_commute"}
        event_values["DAILY.08"] = path
        if path == "靠近工作点暂住":
            event_values["HOUSING.01"] = {
                "arrangement": "靠近工作点暂住",
                "tenure": None,
            }
    if event_id in {"work-adjustment", "later-life"} and path:
        event_values["DAILY.08"] = path
    if event_id == "work-adjustment" and path:
        event_values["CAREER.02"] = {"arrangement": path, "scope": "work_adjustment"}
    if event_id == "household":
        event_values["GEO.03"] = {
            "label": node["location"],
            "precision": "province_or_region",
        }
    if event_id == "support-plan" and path:
        event_values["HOUSEHOLD.08"] = {
            "arrangement": path,
            "scope": "planned_daily_support",
        }
        event_values["DAILY.08"] = path
        if any(
            member["id"] in node["participants"] for member in person.get("family", [])
        ):
            event_values["SOCIAL.01"] = {
                "scope": "family_relationship",
                "relation": "同辈亲属",
            }
            event_values["HOUSEHOLD.06"] = {
                "arrangement": path,
                "scope": "日常事务分工",
            }
            if path == "亲属分时分担日常事务":
                event_values["WELFARE.08"] = {
                    "arrangement": path,
                    "provider": "家庭亲属",
                }
    if event_id == "community-contact" and path:
        event_values["SOCIAL.02"] = {"setting": path, "counterparty": "邻近熟人"}
        event_values["COMMUNITY.05"] = {"contact": path, "scope": "日常社区往来"}
    if event_id == "daily-routine" and path:
        event_values["DAILY.07"] = {
            "arrangement": path,
            "scope": "simulated_time_allocation",
        }
        event_values["DAILY.08"] = path
        event_values["PREF.05"] = {"routine": path, "scope": "该阶段日常习惯"}
    if event_id == "budget-routine" and path:
        event_values["INCOME.08"] = {"rule": path, "scope": "budget_practice"}
        event_values["CONSUME.07"] = decision["condition"]
        event_values["FUNC.05"] = {
            "activity": "安排日常开支",
            "practice": path,
            "assessment": None,
        }
    if event_id == "leisure-routine" and path:
        event_values["LEISURE.02"] = {"activity": path, "scope": "simulated_activity"}
        event_values["LEISURE.07"] = decision["condition"]
        event_values["DAILY.06"] = {"activity": path, "hours": None}
        if path == "在附近散步":
            event_values["FOOD.08"] = {"activity": "休闲散步", "frequency": None}
    if event_id == "digital-adoption" and path:
        event_values["DIGITAL.01"] = {"access": path, "scope": "synthetic_access"}
    if event_id == "digital-learning" and path:
        event_values["DIGITAL.02"] = {"activity": path, "proficiency": None}
        event_values["KNOW.06"] = path
    if event_id == "health-check" and state.get("health_review"):
        event_values["HEALTH.07"] = {
            "activity": state["health_review"],
            "scope": "preventive_contact",
        }
    if event_id == "death":
        event_values["DEATH.01"] = {
            "date": node["start"],
            "location_precision": "unknown",
        }
        event_values["DEATH.08"] = {
            "cutoff": node["start"],
            "scope": "personal_records",
        }
    return event_values


def _project_events(
    graph: dict, family_ids: set[str], family_by_dimension: dict[str, str]
) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    histories: dict[str, list[dict]] = defaultdict(list)
    rule_links: dict[str, list[str]] = defaultdict(list)
    simulation = graph.get("simulation", {})
    engine_name = simulation.get("engine", {}).get("name")
    engine_events = {
        item["code"] for item in simulation.get("events", []) if item["code"] != "death"
    }
    for node in graph["nodes"]:
        event_id = node["event_id"]
        node.pop("dimension_relevance", None)
        node["dimension_updates"] = []
        if "decomposition_state_delta" in node:
            continue

        world = node.get("summary_node")
        event_engine = engine_name if not world and event_id in engine_events else None
        valid_time = _window(
            node["start"], node.get("end"), node.get("precision", "day")
        )
        sources = [item["id"] for item in node.get("evidence", [])]
        if world:
            kind, rule_id = "world_context", event_id
        elif event_engine:
            kind, rule_id = "simulation_event", sources[0] if sources else None
        else:
            kind, rule_id = "synthetic_event", f"lifegraph-dimensions-v1:{event_id}"
        if world and (rule := SCENARIOS.get(event_id)):
            targets = list(dict.fromkeys(rule["affected_dimensions"]))
            relevant_dimensions = [
                target for target in targets if target in family_by_dimension
            ]
            relevant_families = list(
                dict.fromkeys(
                    family_by_dimension.get(target, target)
                    for target in targets
                    if target in family_ids or target in family_by_dimension
                )
            )
            node["dimension_relevance"] = {
                "rule_id": rule_id,
                "rule_version": rule["version"],
                "scope": "background_candidate",
                "family_ids": relevant_families,
                "dimension_ids": relevant_dimensions,
                "valid_time": valid_time,
                "source_ids": sources,
            }
            for target in (*relevant_families, *relevant_dimensions):
                rule_links[target].append(event_id)
        provenance = _provenance(kind, rule_id, sources)
        values = _event_values(node, graph["person"], event_engine)
        values.update(node.get("dimension_values", {}))
        for dim_id, value in values.items():
            update = {
                "dim_id": dim_id,
                "value": value,
                "valid_time": valid_time,
                "provenance": provenance,
            }
            node["dimension_updates"].append(update)
            histories[dim_id].append({"event_id": event_id, **update})
    return histories, rule_links


def attach_dimension_ledger(graph: dict) -> None:
    """Rebuild resolved values and coverage after graph generation or an edit."""
    families, checksum = _registry()
    person = graph["person"]
    reference = person.get("death_date") or graph.get("as_of") or person["as_of"]
    current = date.fromisoformat(reference)
    birth = next(node for node in graph["nodes"] if node["event_id"] == "birth")
    birth_start = date.fromisoformat(birth["start"])
    birth_end = min(current, date.fromisoformat(birth["end"] or birth["start"]))
    minimum_age = _age(birth_end, current)
    maximum_age = _age(birth_start, current)
    role_events = {
        node["event_id"]: node["start"]
        for node in graph["nodes"]
        if node["event_id"]
        in {"education", "school-path", "work", "employment-entry", "household"}
    }
    family_ids = {family["family_id"] for family in families}
    family_by_dimension = {
        dimension["dim_id"]: family["family_id"]
        for family in families
        for dimension in family["dimensions"]
    }
    history, rule_links = _project_events(graph, family_ids, family_by_dimension)
    contextual_values = {
        "TIME.02": ("derived", reference, []),
        "TIME.03": (
            "derived",
            {"min": minimum_age, "max": maximum_age, "unit": "year"},
            ["birth"],
        ),
        "TIME.04": (
            "constraint",
            {
                "birth_year": person["birth_year"],
                "bounds": [birth["start"], birth["end"]],
            },
            ["birth"],
        ),
        "TIME.05": (
            "derived",
            person["life_status"],
            ["birth", "death"]
            if "death" in (node["event_id"] for node in graph["nodes"])
            else ["birth"],
        ),
        "TIME.06": (
            "derived",
            {
                "candidates": _stages(minimum_age, maximum_age),
                "classification": "simulation_age_bands_v1",
            },
            ["birth"],
        ),
        "ID.01": (
            "constraint",
            {"name": person["name"], "scope": "synthetic_name"},
            [],
        ),
        "PROV.01": ("constraint", "synthetic_person", []),
        "PROV.03": ("constraint", {"dictionary": REGISTRY_VERSION, "ledger": 1}, []),
    }
    if role_events:
        contextual_values["TIME.07"] = ("derived", role_events, list(role_events))
    if members := person.get("family"):
        contextual_values["SOCIAL.01"] = (
            "constraint",
            {
                "roles": [
                    {"id": item["id"], "relation": item["relation"]} for item in members
                ],
                "scope": "modeled_family_links",
            },
            [],
        )
    # The selected option records a generation input, with no registration claim.
    contextual_values["ETHNIC.01"] = (
        "constraint",
        {"label": person["ethnicity"], "scope": "synthetic_input_category"},
        [],
    )
    statuses: Counter = Counter()
    projected = []
    for family in families:
        entries = []
        for definition in family["dimensions"]:
            dim_id = definition["dim_id"]
            status, reason = _missing(definition, person, maximum_age)
            row = {
                **definition,
                "status": status,
                "reason": reason,
                "value": None,
                "valid_time": None,
                "event_ids": [],
                "provenance": _provenance(status, None),
            }
            if rule_ids := rule_links.get(dim_id):
                row["rule_relevance"] = rule_ids
            samples = history.get(dim_id)
            if samples:
                kinds = {sample["provenance"]["kind"] for sample in samples}
                kind = (
                    samples[0]["provenance"]["kind"]
                    if len(kinds) == 1
                    else "mixed_events"
                )
                values = [
                    {key: sample[key] for key in ("event_id", "value", "valid_time")}
                    for sample in samples
                ]
                row.update(
                    status="event_anchored",
                    reason=EVENT_REASONS[kind],
                    value=values[0]["value"] if len(values) == 1 else values,
                    valid_time=(
                        samples[0]["valid_time"]
                        if len(values) == 1
                        else _window(
                            samples[0]["valid_time"]["start"],
                            None
                            if any(
                                sample["valid_time"]["end"] is None
                                for sample in samples
                            )
                            else max(sample["valid_time"]["end"] for sample in samples),
                            "range",
                        )
                    ),
                    event_ids=[sample["event_id"] for sample in samples],
                    provenance=_provenance(
                        kind,
                        f"dimension-projection-v1:{dim_id}",
                        dict.fromkeys(
                            source
                            for sample in samples
                            for source in sample["provenance"]["source_ids"]
                        ),
                    ),
                )
            elif dim_id in contextual_values:
                status, value, anchors = contextual_values[dim_id]
                row.update(
                    status=status,
                    value=value,
                    reason="由档案参照时点与已建模事件派生。"
                    if status == "derived"
                    else "生成约束或合成标识。",
                    valid_time=_window(reference, reference),
                    event_ids=anchors,
                    provenance=_provenance(
                        "derived" if status == "derived" else "generation_constraint",
                        f"dimension-projection-v1:{dim_id}",
                    ),
                )
            statuses[row["status"]] += 1
            entries.append(row)
        item = {**family, "dimensions": entries}
        if rule_ids := rule_links.get(family["family_id"]):
            item["rule_relevance"] = rule_ids
        projected.append(item)
    graph["dimensions"] = {
        "schema_version": 1,
        "registry_version": REGISTRY_VERSION,
        "registry_sha256": checksum,
        "reference_date": reference,
        "summary": {
            "family_count": len(families),
            "dimension_count": sum(statuses.values()),
            "configured": statuses["constraint"],
            "resolved": statuses["derived"] + statuses["event_anchored"],
            "unknown": statuses["unresolved"],
            "not_applicable": statuses["not_applicable"],
        },
        "families": projected,
    }
