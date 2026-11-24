"""Check bounded care schedules and explicit resource claims on a LifeGraph."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from pathlib import Path

MODEL = Path(__file__).with_name("data") / "skeleton_constraints.mzn"
DAY_MINUTES = 1440
CARE_DURATION_MINUTES = 60
MAX_CENTS = 1_000_000_000
RESOURCE_FIELDS = {
    "resource_start_minute",
    "resource_duration_minutes",
    "resource_cost_cents",
    "resource_budget_cents",
}


class ConstraintEngineError(RuntimeError):
    """A configured MiniZinc invocation failed to establish feasibility."""


@dataclass(frozen=True)
class Booking:
    event_id: str
    day: int
    earliest_day: int
    participants: frozenset[str]
    fixed_start: int
    duration: int
    cost: int
    budget: int
    issues: tuple[str, ...]


def _care_participants_valid(node: dict, subject: str, relatives: set[str]) -> bool:
    participants = node["participants"]
    if (
        len(participants) != 2
        or participants[0] == participants[1]
        or subject not in participants
    ):
        return False
    supporter = next(person_id for person_id in participants if person_id != subject)
    deltas = node.get("participant_deltas", [])
    return (
        supporter in relatives
        and len(deltas) == 2
        and {(row.get("person_id"), row.get("role")) for row in deltas}
        == {(subject, "recipient"), (supporter, "supporter")}
    )


def _bookings(graph: dict) -> list[Booking]:
    nodes = {node["event_id"]: node for node in graph["nodes"]}
    person = graph["person"]
    birth_day = date(person["birth_year"], 1, 1).toordinal()
    subject = person["person_id"]
    relatives = {relative["id"] for relative in person.get("family", [])}
    health = nodes.get("health-transition")
    bookings = []
    for event_id, node in sorted(nodes.items()):
        delta = node.get("state_delta", {})
        has_claim = bool(RESOURCE_FIELDS.intersection(delta))
        is_care = event_id == "shared-care"
        if not (has_claim or is_care):
            continue

        issues = []
        participants = frozenset(node.get("participants", []))
        day = date.fromisoformat(node["start"]).toordinal()
        earliest_day = birth_day
        if is_care:
            if not _care_participants_valid(node, subject, relatives):
                issues.append(f"照护参与者与家庭成员或角色记录不一致：{event_id}")
            if health:
                earliest_day = max(
                    birth_day, date.fromisoformat(health["start"]).toordinal()
                )
            else:
                issues.append(f"照护时间缺少健康变化事件：{event_id}")
        elif not participants or not participants <= relatives | {subject}:
            issues.append(f"资源预约的参与者未登记：{event_id}")

        defaults = (
            (-1, CARE_DURATION_MINUTES, 0, -1) if is_care else (None, None, 0, -1)
        )
        start = delta.get("resource_start_minute", defaults[0])
        duration = delta.get("resource_duration_minutes", defaults[1])
        cost = delta.get("resource_cost_cents", defaults[2])
        budget = delta.get("resource_budget_cents", defaults[3])
        limits = [
            (start, 0 if "resource_start_minute" in delta else -1, DAY_MINUTES - 1),
            (duration, 1, DAY_MINUTES),
            (cost, 0, MAX_CENTS),
            (budget, 0 if "resource_budget_cents" in delta else -1, MAX_CENTS),
        ]
        if any(
            type(value) is not int or not lower <= value <= upper
            for value, lower, upper in limits
        ):
            issues.append(f"资源申报缺少有效的整分钟或整数分金额：{event_id}")
            start, duration, cost, budget = -1, 1, 0, -1

        bookings.append(
            Booking(
                event_id,
                day,
                earliest_day,
                participants,
                start,
                duration,
                cost,
                budget,
                tuple(issues),
            )
        )
    return bookings


def _day_data(
    bookings: list[Booking],
) -> tuple[dict[int, list[Booking]], list[tuple[int, int]]]:
    by_day = defaultdict(list)
    indices = defaultdict(list)
    for index, booking in enumerate(bookings, 1):
        by_day[booking.day].append(booking)
        indices[booking.day].append(index)
    conflicts = [
        (first, second)
        for group in indices.values()
        for first, second in combinations(group, 2)
        if bookings[first - 1].participants & bookings[second - 1].participants
    ]
    return by_day, conflicts


def _reference_issues(
    bookings: list[Booking],
    by_day: dict[int, list[Booking]],
    conflicts: list[tuple[int, int]],
    cutoff: int,
) -> list[str]:
    issues = [issue for booking in bookings for issue in booking.issues]
    for booking in bookings:
        if not booking.earliest_day <= booking.day <= cutoff:
            issues.append(f"事件时间超出生命或照护时限：{booking.event_id}")
        if max(0, booking.fixed_start) + booking.duration > DAY_MINUTES:
            issues.append(f"预约时间跨出当天：{booking.event_id}")
    for day, same_day in by_day.items():
        caps = [booking.budget for booking in same_day if booking.budget >= 0]
        if caps and sum(booking.cost for booking in same_day) > min(caps):
            members = "、".join(
                booking.event_id
                for booking in same_day
                if booking.cost or booking.budget >= 0
            )
            issues.append(
                f"{date.fromordinal(day).isoformat()} 的家庭开支超出预算：{members}"
            )

    fixed_occupancy = defaultdict(list)
    for first_index, second_index in conflicts:
        first, second = bookings[first_index - 1], bookings[second_index - 1]
        if first.issues or second.issues:
            continue
        if first.fixed_start >= 0 and second.fixed_start >= 0:
            if (
                first.fixed_start < second.fixed_start + second.duration
                and second.fixed_start < first.fixed_start + first.duration
            ):
                issues.append(
                    f"参与者在重叠时间被重复占用：{first.event_id}、{second.event_id}"
                )
        else:
            care, fixed = (first, second) if first.fixed_start < 0 else (second, first)
            fixed_occupancy[care].append(fixed)

    # A LifeGraph has at most one flexible claim, the shared-care event.
    for care, fixed in fixed_occupancy.items():
        candidate = 0
        for booking in sorted(fixed, key=lambda item: item.fixed_start):
            if candidate + care.duration <= booking.fixed_start:
                break
            candidate = max(candidate, booking.fixed_start + booking.duration)
        if candidate + care.duration > DAY_MINUTES:
            members = "、".join(booking.event_id for booking in fixed)
            issues.append(
                f"参与者的时间占用使照护预约无法排入当天：{care.event_id}、{members}"
            )
    return issues


def _run_minizinc(binary: str, solver_input: dict) -> bool:
    with tempfile.TemporaryDirectory(prefix="lifegraph-constraints-") as directory:
        data = Path(directory, "case.json")
        data.write_text(json.dumps(solver_input), encoding="ascii")
        try:
            result = subprocess.run(
                [
                    binary,
                    "--solver",
                    "gecode",
                    "--json-stream",
                    "--time-limit",
                    "5000",
                    str(MODEL),
                    str(data),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=8,
                check=False,
            )
            messages = [
                json.loads(line) for line in result.stdout.splitlines() if line.strip()
            ]
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise ConstraintEngineError(
                "无法运行 MiniZinc 骨架约束检查或读取结果。"
            ) from exc

    if result.returncode or any(msg.get("type") == "error" for msg in messages):
        raise ConstraintEngineError("MiniZinc 骨架约束检查执行失败。")
    if any(msg.get("type") == "solution" for msg in messages):
        return True
    if any(
        msg.get("type") == "status" and msg.get("status") == "UNSATISFIABLE"
        for msg in messages
    ):
        return False
    raise ConstraintEngineError("MiniZinc 未能确定骨架约束是否可行。")


def check_hard_constraints(graph: dict) -> dict:
    """Test a fixed graph without projecting the solver's arbitrary schedule into it."""
    bookings = _bookings(graph)
    report = {
        "status": "not_applicable",
        "validator": None,
        "event_ids": [booking.event_id for booking in bookings],
        "issues": [],
    }
    if not bookings:
        return report

    cutoff = date.fromisoformat(
        graph["person"].get("death_date") or graph["as_of"]
    ).toordinal()
    by_day, conflicts = _day_data(bookings)
    solver_input = {
        "task_count": len(bookings),
        "conflict_count": len(conflicts),
        "day_minutes": DAY_MINUTES,
        "cutoff_day": cutoff,
        "task_day": [booking.day for booking in bookings],
        "earliest_day": [booking.earliest_day for booking in bookings],
        "valid_input": [not booking.issues for booking in bookings],
        "fixed_start": [booking.fixed_start for booking in bookings],
        "duration_minutes": [booking.duration for booking in bookings],
        "cost_cents": [booking.cost for booking in bookings],
        "budget_cents": [booking.budget for booking in bookings],
        "conflict_first": [first for first, _ in conflicts],
        "conflict_second": [second for _, second in conflicts],
    }
    binary = os.environ.get("MINIZINC_BIN")
    feasible = _run_minizinc(binary, solver_input) if binary else None
    if feasible is not True:
        report["issues"] = _reference_issues(bookings, by_day, conflicts, cutoff)
    if feasible is None:
        feasible = not report["issues"]
    elif not feasible and not report["issues"]:
        report["issues"] = ["MiniZinc 判定骨架硬约束无可行解。"]

    return {
        **report,
        "status": "feasible" if feasible else "infeasible",
        "validator": "minizinc" if binary else "python-reference",
        "model_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        "input_sha256": hashlib.sha256(
            json.dumps(solver_input, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
