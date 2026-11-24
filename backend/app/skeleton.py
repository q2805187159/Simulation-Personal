"""Versioned case inputs, engine boundary and LifeGraph projection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from . import domain
from .dimensions import attach_dimension_ledger
from .evidence_data import population_evidence_for
from .history_assertions import reproject_history_assertions
from .population import extend_population
from .lifecycle_rules import (
    LIFECYCLE_SHA256,
    LIFECYCLE_VERSION,
    generate_lifecycle_events,
)
from .skeleton_constraints import ConstraintEngineError, check_hard_constraints
from .world_rules import CATALOG_PATH, WORLD_VERSION

DATA = Path(__file__).with_name("data")
PACK_BYTES = (DATA / "process_parameters.json").read_bytes()
PACK = json.loads(PACK_BYTES)
PARAMETER_DIGEST = hashlib.sha256(PACK_BYTES).hexdigest()
WORLD_DIGEST = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
SCHEMA = DATA / "skeleton_spec.schema.json"
RANDOM_VERSION = "sha256-addressed-v1"
TITLES = {
    "job-exit": ("work", "离开原有岗位", {"work_status": "离开原有岗位"}),
    "relocation": ("mobility", "调整常住地点", {"residence_changed": True}),
    "health-transition": ("health", "健康需求发生变化", {"care_need": "出现照护需求"}),
    "shared-care": (
        "household",
        "与亲属共同安排照护",
        {"care_arrangement": "亲属参与"},
    ),
    "partnership": (
        "household",
        "建立稳定伴侣关系",
        {"partnership_status": "伴侣关系"},
    ),
}
MODELED = frozenset(TITLES)
MODEL_CODES = MODELED | {"death"}


class EngineError(RuntimeError):
    pass


class CaseRunner(Protocol):
    def identity(self) -> dict: ...

    def run(self, case: dict) -> list[dict]: ...


def checksum(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def draw_for(seed: int, address: str) -> float:
    digest = hashlib.sha256(f"{RANDOM_VERSION}:{seed}:{address}".encode()).digest()
    return ((int.from_bytes(digest[:8], "big") >> 12) + 0.5) / 2**52


def wait_days(row: dict, start: float) -> float:
    if row["forced_day"] is not None:
        return row["forced_day"] if row["forced_day"] >= start else math.inf
    rate = row["rate_per_day"]
    return start - math.log1p(-row["draw"]) / rate if rate else math.inf


class ReferenceRunner:
    """Independent analytic reference for the current bounded case model."""

    def identity(self) -> dict:
        return {"name": "reference-des", "version": "1", "mode": "case-based"}

    def run(self, case: dict) -> list[dict]:
        selected = {}
        for row in case["candidates"]:
            if row["code"] != "shared-care":
                selected[row["code"]] = wait_days(row, row["start_day"])
        first, second = sorted(
            ("job-exit", "relocation"), key=lambda code: (selected[code], code)
        )
        selected[second] = math.inf
        care = next(row for row in case["candidates"] if row["code"] == "shared-care")
        selected[care["code"]] = wait_days(
            care, max(care["start_day"], selected["health-transition"])
        )
        death = selected["death"]
        return [
            {"code": code, "elapsed_day": day}
            for code, day in sorted(
                selected.items(), key=lambda pair: (pair[1], pair[0])
            )
            if day <= case["cutoff_elapsed_day"] and (code == "death" or day < death)
        ]


class OpenMppRunner:
    def __init__(self, executable: Path):
        self.executable = executable.resolve()
        self.template = self.executable.with_suffix(".sqlite")

    def identity(self) -> dict:
        if not self.executable.is_file() or not self.template.is_file():
            raise EngineError("找不到已编译的 OpenM++ 模型或模型数据库。")
        return {
            "name": "openmpp",
            "version": "1.17.15",
            "mode": "case-based",
            "model": self.executable.name,
            "model_sha256": hashlib.sha256(self.executable.read_bytes()).hexdigest(),
            "database_sha256": hashlib.sha256(self.template.read_bytes()).hexdigest(),
        }

    def run(self, case: dict) -> list[dict]:
        self.identity()
        with tempfile.TemporaryDirectory(prefix="lifegraph-case-") as directory:
            root = Path(directory)
            shutil.copyfile(self.template, root / self.template.name)
            (root / "case.tsv").write_text(_case_tsv(case), encoding="ascii")
            try:
                result = subprocess.run(
                    [str(self.executable)],
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=25,
                    check=False,
                )
                output = root / "events.tsv"
                if result.returncode or not output.is_file():
                    raise EngineError("OpenM++ 模型执行失败，未产生完整事件输出。")
                return _read_events(output.read_text(encoding="ascii"), case)
            except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
                raise EngineError("无法读取 OpenM++ 结果或运行超时。") from exc


def _case_tsv(case: dict) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(
        ["SKELETON_CASE_V1", case["cutoff_elapsed_day"], int(case["alive_at_cutoff"])]
    )
    writer.writerow(["code", "rate_per_day", "start_day", "forced_day", "draw"])
    for row in case["candidates"]:
        writer.writerow(
            [
                row["code"],
                row["rate_per_day"],
                row["start_day"],
                -1 if row["forced_day"] is None else row["forced_day"],
                row["draw"],
            ]
        )
    return stream.getvalue()


def _read_events(data: str, case: dict) -> list[dict]:
    reader = csv.DictReader(io.StringIO(data), delimiter="\t")
    if reader.fieldnames != ["code", "elapsed_day"]:
        raise EngineError("OpenM++ 事件输出列与运行契约不符。")
    events = []
    seen = set()
    try:
        for row in reader:
            code, day = row["code"], float(row["elapsed_day"])
            if (
                code not in MODEL_CODES
                or code in seen
                or not math.isfinite(day)
                or not 0 <= day <= case["cutoff_elapsed_day"]
            ):
                raise ValueError
            seen.add(code)
            events.append({"code": code, "elapsed_day": day})
    except (TypeError, ValueError) as exc:
        raise EngineError("OpenM++ 事件行包含无效编码、重复事件或无效时间。") from exc
    if ("death" in seen) == case["alive_at_cutoff"]:
        raise EngineError("OpenM++ 输出与生命终止条件不符。")
    return sorted(events, key=lambda row: (row["elapsed_day"], row["code"]))


def configured_runner() -> CaseRunner:
    executable = os.environ.get("OPENMPP_MODEL")
    return OpenMppRunner(Path(executable)) if executable else ReferenceRunner()


def validate_exchange(spec: dict) -> dict:
    schema_sha = hashlib.sha256(SCHEMA.read_bytes()).hexdigest()
    binary = os.environ.get("CUE_BIN")
    if not binary:
        return {"schema_sha256": schema_sha, "validator": "pydantic-domain"}
    try:
        completed = subprocess.run(
            [binary, "vet", "-c", "jsonschema:", SCHEMA.name, "json:", "-"],
            input=json.dumps(spec, ensure_ascii=False).encode(),
            cwd=DATA,
            capture_output=True,
            check=False,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError("无法运行 CUE 骨架规格校验。") from exc
    if completed.returncode:
        raise ValueError("骨架规格未通过 CUE 导出的 JSON Schema 校验。")
    return {"schema_sha256": schema_sha, "validator": "cue-json-schema"}


def _case_input(spec: dict, graph: dict, overrides: dict) -> dict:
    person = graph["person"]
    origin = date(person["birth_year"], 1, 1)
    nodes = {node["event_id"]: node for node in graph["nodes"]}
    cutoff = (date.fromisoformat(spec["as_of"]) - origin).days
    job = nodes.get("employment-entry") or nodes.get("work")
    work_started = job and job["state_delta"].get("entry_kind") == "work"
    employment_day = (
        (date.fromisoformat(job["start"]) - origin).days if job else cutoff + 1
    )
    starts = {
        "job-exit": employment_day + 365,
        "relocation": employment_day + 365,
        "health-transition": 35 * 365,
        "shared-care": 35 * 365,
        "partnership": 20 * 365,
        "death": 0,
    }
    candidates = []
    for code, annual in PACK["annual_rates"].items():
        override = overrides.get(code, {})
        eligible = (
            (code not in {"job-exit", "relocation"} or work_started)
            and (code != "shared-care" or bool(person["family"]))
            and code != "death"
        )
        forced = override.get("forced_date")
        if code == "death":
            forced = person["death_date"]
        forced_day = (date.fromisoformat(forced) - origin).days if forced else None
        if forced_day is not None and forced_day < starts[code]:
            raise ValueError(f"编辑将事件放在适用窗口之前：{code}")
        candidates.append(
            {
                "code": code,
                "start_day": starts[code],
                "rate_per_day": (
                    annual * _risk_multiplier(spec, code) / 365.25
                    if eligible and not override.get("disabled")
                    else 0
                ),
                "forced_day": forced_day,
                "draw": draw_for(spec["seed"], code),
                "reads": {
                    "job-exit": ["employment", "relocation"],
                    "relocation": ["employment", "job-exit"],
                    "shared-care": ["family", "health-transition"],
                }.get(code, ["birth", "cutoff"]),
            }
        )
    return {
        "origin": origin.isoformat(),
        "cutoff_elapsed_day": cutoff,
        "alive_at_cutoff": not person["death_date"],
        "candidates": candidates,
    }


def _risk_multiplier(spec: dict, code: str) -> float:
    if code != "health-transition":
        return 1.0
    risk_class = spec.get("population", {}).get("risk_class")
    return PACK["health_rate_multiplier"].get(risk_class, 1.0)


def _engine_nodes(
    graph: dict, case: dict, events: list[dict], overrides: dict
) -> list[dict]:
    origin = date.fromisoformat(case["origin"])
    person = graph["person"]
    existing = {node["event_id"]: node for node in graph["nodes"]}
    nodes = [node for node in graph["nodes"] if node["event_id"] not in MODELED]
    for event in events:
        code = event["code"]
        if code == "death":
            continue
        category, title, delta = TITLES[code]
        day = (origin + timedelta(days=math.floor(event["elapsed_day"]))).isoformat()
        dependency = "birth"
        participants = [person["person_id"]]
        if code in {"job-exit", "relocation"}:
            dependency = (
                "employment-entry" if "employment-entry" in existing else "work"
            )
        if code == "shared-care":
            dependency = "health-transition"
            participants.append(person["family"][0]["id"])
        node = domain._node(
            code,
            title,
            category,
            day,
            day,
            "day",
            person["region"],
            depends_on=[dependency],
            participants=participants,
            state_delta=dict(delta),
            evidence=[
                {
                    "id": f"{PACK['version']}:{code}",
                    "label": "工程试验参数包",
                    "kind": "synthetic_rule",
                }
            ],
        )
        if code == "shared-care":
            node["participant_deltas"] = [
                {
                    "person_id": participants[0],
                    "role": "recipient",
                    "delta": dict(delta),
                },
                {
                    "person_id": participants[1],
                    "role": "supporter",
                    "delta": {"support_commitment": True},
                },
            ]
        node.update(overrides.get(code, {}).get("fields", {}))
        nodes.append(node)
    return sorted(nodes, key=domain._timeline_key)


def _execute(
    spec: dict,
    graph: dict,
    runner: CaseRunner,
    overrides: dict,
    exchange: dict,
    *,
    suppressed_lifecycle: set[str] | None = None,
) -> dict:
    case = _case_input(spec, graph, overrides)
    identity = runner.identity()
    events = runner.run(case)
    suppressed_lifecycle = suppressed_lifecycle or set()
    modeled_nodes = _engine_nodes(graph, case, events, overrides)
    graph["nodes"] = [
        node
        for node in modeled_nodes
        if not node.get("lifecycle_rule") or node.get("lifecycle_authored")
    ]
    lifecycle_nodes = generate_lifecycle_events(
        spec,
        graph["nodes"],
        date.fromisoformat(graph["person"]["death_date"] or spec["as_of"]),
        suppressed=suppressed_lifecycle,
    )
    graph["nodes"].extend(lifecycle_nodes)
    graph["nodes"].sort(key=domain._timeline_key)
    graph["edges"] = domain._edges(graph["nodes"])
    selected = {event["code"] for event in events}
    forced = {key for key, rule in overrides.items() if rule.get("forced_date")}
    if missing := forced - selected:
        raise ValueError(
            f"编辑违反竞争风险、照护资格或生命时限：{', '.join(sorted(missing))}"
        )
    case_digest = checksum(case)
    graph["simulation"] = {
        "schema": "skeleton-ir/2",
        "engine": identity,
        "exchange": exchange,
        "input_sha256": case_digest,
        "output_sha256": checksum(events),
        "run_id": checksum([identity, case_digest]),
        "parameter_pack": {
            "version": PACK["version"],
            "sha256": PARAMETER_DIGEST,
            "basis": PACK["basis"],
        },
        "lifecycle": {"version": LIFECYCLE_VERSION, "sha256": LIFECYCLE_SHA256},
        "suppressed_lifecycle": sorted(suppressed_lifecycle),
        "random": {"scheme": RANDOM_VERSION, "seed": spec["seed"]},
        "entry": spec["entry"],
        "population": spec.get("population"),
        "population_selection": spec.get("population_selection"),
        "population_evidence": population_evidence_for(
            spec["region"], spec["ethnicity"]
        ),
        "world_version": WORLD_VERSION,
        "world_sha256": WORLD_DIGEST,
        "spec_sha256": checksum(spec),
        "case": {key: value for key, value in case.items() if key != "candidates"},
        "candidates": [
            {**row, "status": "selected" if row["code"] in selected else "not_selected"}
            for row in case["candidates"]
        ],
        "events": events,
        "edit_constraints": deepcopy(overrides),
    }
    graph["validation"] = domain.validate_graph(graph)
    if not graph["validation"]["valid"]:
        raise ValueError("；".join(graph["validation"]["issues"]))
    try:
        constraints = check_hard_constraints(graph)
    except ConstraintEngineError as exc:
        raise EngineError(str(exc)) from exc
    if constraints["status"] == "infeasible":
        raise ValueError("；".join(constraints["issues"]))
    graph["simulation"]["constraints"] = constraints
    attach_dimension_ledger(graph)
    return graph


def run_skeleton(spec: dict, runner: CaseRunner, person_id: str) -> dict:
    exchange = validate_exchange(spec)
    graph = domain.generate_graph(spec, person_id=person_id)
    result = _execute(spec, graph, runner, {}, exchange)
    result["diff"]["added"] = [node["event_id"] for node in result["nodes"]]
    return result


def revise_skeleton(
    graph: dict, spec: dict, operations: list[dict], runner: CaseRunner
) -> dict:
    if "simulation" not in graph:
        return domain.apply_edits(graph, operations)
    previous_run = graph["simulation"]
    if (
        previous_run["engine"] != runner.identity()
        or previous_run["parameter_pack"]["sha256"] != PARAMETER_DIGEST
        or previous_run.get("world_sha256", WORLD_DIGEST) != WORLD_DIGEST
        or previous_run.get("lifecycle", {}).get("sha256", LIFECYCLE_SHA256)
        != LIFECYCLE_SHA256
    ):
        raise ValueError("此骨架使用其他模型或参数版本，请先升级骨架。")
    overrides = deepcopy(previous_run["edit_constraints"])
    original_nodes = {item["event_id"]: item for item in graph["nodes"]}
    operations = deepcopy(operations)
    for operation in operations:
        code = operation.get("event_id")
        if code not in MODELED:
            continue
        if operation["op"] in {"split", "add"}:
            raise ValueError(f"此事件由原生模型排程，请使用修改或撤销操作：{code}")
        override = overrides.setdefault(code, {})
        if operation["op"] == "delete":
            override.clear()
            override["disabled"] = True
        else:
            if start := operation.get("start"):
                override["forced_date"] = start
                previous = original_nodes.get(code)
                if previous and previous["start"] == previous.get("end"):
                    if operation.get("end", previous["end"]) == previous["end"]:
                        operation["end"] = start
            fields = domain._edit_fields(operation)
            override.setdefault("fields", {}).update(fields)
    child = domain.apply_edits(graph, operations, before_simulation=True)
    suppressed_lifecycle = set(previous_run.get("suppressed_lifecycle", []))
    touched = {operation["event_id"]: operation["op"] for operation in operations}
    suppressed_lifecycle.update(
        key
        for key, op in touched.items()
        if op == "delete" and original_nodes.get(key, {}).get("lifecycle_rule")
    )
    for node in child["nodes"]:
        if node.get("lifecycle_rule") and node["event_id"] in touched:
            node["lifecycle_authored"] = True
    result = _execute(
        spec,
        child,
        runner,
        overrides,
        validate_exchange(spec),
        suppressed_lifecycle=suppressed_lifecycle,
    )
    old = {node["event_id"]: node for node in graph["nodes"]}
    new = {node["event_id"]: node for node in result["nodes"]}
    changed = {
        key
        for key in old.keys() & new.keys()
        if _event_content(old[key]) != _event_content(new[key])
    }
    result["diff"]["added"] = sorted(new.keys() - old.keys())
    result["diff"]["removed"] = sorted(old.keys() - new.keys())
    result["diff"]["recomputed"] = sorted(
        set(result["diff"]["recomputed"]) | (changed - set(result["diff"]["modified"]))
    )
    result["simulation"]["replay"] = {
        "strategy": "pinned-case-rerun",
        "parent_run_id": graph["simulation"]["run_id"],
        "retained_event_ids": sorted((old.keys() & new.keys()) - changed),
    }
    result["simulation"]["spec"] = spec
    result["simulation"]["population_anchor"] = previous_run.get(
        "population_anchor", spec["as_of"]
    )
    suppressed = set(previous_run.get("suppressed_history", []))
    suppressed.update(
        operation["event_id"]
        for operation in operations
        if operation["op"] == "delete" and operation["event_id"] in domain.SCENARIOS
    )
    result["simulation"]["suppressed_history"] = sorted(suppressed)
    return result


def upgrade_skeleton(
    graph: dict, spec: dict, runner: CaseRunner, *, through: date | None = None
) -> dict:
    """Create a child with the deployed model and refresh eligible world references."""
    child = deepcopy(graph)
    person = child["person"]
    old_as_of = date.fromisoformat(graph["as_of"])
    parent_run = graph.get("simulation", {})
    anchor = parent_run.get("population_anchor") or spec["as_of"]
    spec = {**spec, "death_date": person["death_date"]}
    if through:
        if person["death_date"] or through <= old_as_of:
            raise ValueError("在世骨架的续写日必须晚于当前截止日。")
        spec = extend_population(spec, date.fromisoformat(anchor), through)
        person.update(
            as_of=spec["as_of"],
            life_status=spec["life_status"],
            death_date=spec["death_date"],
        )
        child["as_of"] = spec["as_of"]
    cutoff = date.fromisoformat(person["death_date"] or spec["as_of"])
    history = domain._match_history(spec, cutoff)
    spec["history_selection"] = history
    spec["themes"] = [item["id"] for item in history]
    old = {node["event_id"]: node for node in graph["nodes"]}
    refreshed = {
        event_id: domain._event_for_scenario(
            event_id, spec, person["person_id"], cutoff
        )
        for event_id in spec["themes"]
    }
    # Preserve a researcher's manual notes and exposure constraints on retained history.
    for event_id, node in refreshed.items():
        if event_id in old:
            previous = old[event_id]
            node["notes"] = previous["notes"]
            for key in ("known_at", "recorded_at"):
                if key in previous:
                    node[key] = previous[key]
            node["state_delta"] = {**previous["state_delta"], **node["state_delta"]}
            if (
                previous["state_delta"].get("exposure_channel")
                == "requires_personal_evidence"
            ):
                node["state_delta"]["exposure_channel"] = "requires_personal_evidence"
            if previous.get("historical_assertions"):
                node["historical_assertions"] = previous["historical_assertions"]
                node["historical_assessment"] = reproject_history_assertions(
                    node, person=person
                )
    suppressed = set(parent_run.get("suppressed_history", []))
    child["nodes"] = [
        node
        for node in child["nodes"]
        if not (node["category"] == "history" and node["summary_node"])
    ] + [node for event_id, node in refreshed.items() if event_id not in suppressed]
    if through:
        template = domain.generate_graph(spec, person_id=person["person_id"])
        child["nodes"].extend(
            node
            for node in template["nodes"]
            if node["event_id"] not in old
            and not node["summary_node"]
            and node["start"] > old_as_of.isoformat()
        )
    overrides = parent_run.get("edit_constraints", {})
    result = _execute(
        spec,
        child,
        runner,
        overrides,
        validate_exchange(spec),
        suppressed_lifecycle=set(parent_run.get("suppressed_lifecycle", [])),
    )
    new = {node["event_id"]: node for node in result["nodes"]}
    kept = {
        key
        for key in old.keys() & new.keys()
        if _event_content(old[key]) == _event_content(new[key])
    }
    result["diff"] = {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "modified": [],
        "recomputed": sorted((old.keys() & new.keys()) - kept),
    }
    result["simulation"]["replay"] = {
        "strategy": "extend-observation" if through else "catalog-model-upgrade",
        "parent_run_id": parent_run.get("run_id"),
        "retained_event_ids": sorted(kept),
    }
    result["simulation"]["population_anchor"] = anchor
    result["simulation"]["suppressed_history"] = sorted(suppressed)
    result["simulation"]["spec"] = spec
    return result


def _event_content(node: dict) -> dict:
    return {key: value for key, value in node.items() if key != "recorded_at"}
