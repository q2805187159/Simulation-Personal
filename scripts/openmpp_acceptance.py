"""Run with `Get-Content scripts/openmpp_acceptance.py -Raw | docker compose exec -T api python -`."""

from __future__ import annotations

import argparse
import json
import math
import random
import resource
import shutil
import sqlite3
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from statistics import mean, variance
from tempfile import TemporaryDirectory
from time import perf_counter

from app.domain import compile_spec
from app.skeleton import (
    MODEL_CODES,
    OpenMppRunner,
    ReferenceRunner,
    _case_tsv,
    configured_runner,
    draw_for,
    revise_skeleton,
    run_skeleton,
)


INCIDENCE_CASES = 384
INCIDENCE_DAYS = (10, 20, 35)
INCIDENCE_FAMILYWISE_ALPHA = 0.01
RISK_START_DAY = 5
JOB_EXIT_RATE = 0.025
RELOCATION_RATE = 0.015
RAISED_JOB_EXIT_RATE = 0.035
MAX_PAIRED_VARIANCE_RATIO = 0.65
REPLAY_CASES = 24
REPLAY_WORKERS = 4


def career_case(seed: int, job_exit_rate: float, address_prefix: str = "") -> dict:
    return {
        "cutoff_elapsed_day": INCIDENCE_DAYS[-1],
        "alive_at_cutoff": True,
        "candidates": [
            {
                "code": code,
                "rate_per_day": rate,
                "start_day": RISK_START_DAY,
                "forced_day": None,
                "draw": draw_for(seed, address_prefix + code),
            }
            for code, rate in (
                ("job-exit", job_exit_rate),
                ("relocation", RELOCATION_RATE),
            )
        ],
    }


def cumulative_incidence(rate: float, competing_rate: float, day: int) -> float:
    total_rate = rate + competing_rate
    exposure_days = max(0, day - RISK_START_DAY)
    return (rate / total_rate) * -math.expm1(-total_rate * exposure_days)


def competing_risks(runner: OpenMppRunner) -> tuple[dict, list]:
    # Hoeffding plus a union bound over the fixed set of six Bernoulli means.
    comparison_count = 2 * len(INCIDENCE_DAYS)
    tolerance = math.sqrt(
        math.log(2 * comparison_count / INCIDENCE_FAMILYWISE_ALPHA)
        / (2 * INCIDENCE_CASES)
    )
    baseline = []
    for seed in range(INCIDENCE_CASES):
        events = runner.run(career_case(seed, JOB_EXIT_RATE))
        assert len(events) <= 1, (
            f"Competing job exit and relocation both fired for seed {seed}"
        )
        assert all(
            event["code"] in {"job-exit", "relocation"}
            and event["elapsed_day"] >= RISK_START_DAY
            for event in events
        ), f"Unexpected or premature career outcome for seed {seed}"
        baseline.append(events)

    checks = []
    for code, rate, rival_rate in (
        ("job-exit", JOB_EXIT_RATE, RELOCATION_RATE),
        ("relocation", RELOCATION_RATE, JOB_EXIT_RATE),
    ):
        for day in INCIDENCE_DAYS:
            count = sum(
                bool(
                    events
                    and events[0]["code"] == code
                    and events[0]["elapsed_day"] <= day
                )
                for events in baseline
            )
            actual = count / INCIDENCE_CASES
            expected = cumulative_incidence(rate, rival_rate, day)
            error = abs(actual - expected)
            assert error <= tolerance, f"{code} day {day}: {actual} vs. {expected}"
            checks.append(
                {
                    "code": code,
                    "day": day,
                    "count": count,
                    "observed": actual,
                    "expected": expected,
                    "absolute_error": error,
                }
            )
    return {
        "cases": INCIDENCE_CASES,
        "risk_start_day": RISK_START_DAY,
        "hazards_per_day": {"job-exit": JOB_EXIT_RATE, "relocation": RELOCATION_RATE},
        "familywise_alpha": INCIDENCE_FAMILYWISE_ALPHA,
        "absolute_tolerance": tolerance,
        "checks": checks,
    }, baseline


def paired_variance(runner: OpenMppRunner, baseline: list) -> dict:
    paired, independent = [], []
    for seed, events in enumerate(baseline):
        previous = int(bool(events and events[0]["code"] == "job-exit"))
        for address_prefix, changes in (
            ("", paired),
            ("acceptance:independent:", independent),
        ):
            revised = runner.run(
                career_case(seed, RAISED_JOB_EXIT_RATE, address_prefix)
            )
            assert len(revised) <= 1, f"Multiple career outcomes for seed {seed}"
            updated = int(bool(revised and revised[0]["code"] == "job-exit"))
            changes.append(updated - previous)

    # Raising this hazard only tips a shared-draw case toward job exit.
    assert all(change >= 0 for change in paired), (
        "A higher paired job hazard removed a job exit"
    )
    paired_variance_value = variance(paired)
    independent_variance = variance(independent)
    assert independent_variance > 0, "Independent outcomes must vary in this fixture"
    observed_ratio = paired_variance_value / independent_variance
    assert observed_ratio < MAX_PAIRED_VARIANCE_RATIO, observed_ratio

    day = INCIDENCE_DAYS[-1]
    p_before = cumulative_incidence(JOB_EXIT_RATE, RELOCATION_RATE, day)
    p_after = cumulative_incidence(RAISED_JOB_EXIT_RATE, RELOCATION_RATE, day)
    analytic_effect = p_after - p_before
    expected_paired = analytic_effect * (1 - analytic_effect)
    expected_independent = p_before * (1 - p_before) + p_after * (1 - p_after)
    return {
        "pairs": len(paired),
        "day": day,
        "raised_job_exit_hazard_per_day": RAISED_JOB_EXIT_RATE,
        "expected_effect": analytic_effect,
        "paired_effect": mean(paired),
        "independent_effect": mean(independent),
        "paired_sample_variance": paired_variance_value,
        "independent_sample_variance": independent_variance,
        "observed_variance_ratio": observed_ratio,
        "expected_variance_ratio": expected_paired / expected_independent,
        "maximum_variance_ratio": MAX_PAIRED_VARIANCE_RATIO,
    }


def replay_submission(runner: OpenMppRunner, baseline: list) -> dict:
    seeds = list(range(REPLAY_CASES))
    random.Random(93057).shuffle(seeds)
    assert seeds != sorted(seeds)
    cases = [career_case(seed, JOB_EXIT_RATE) for seed in seeds]
    for case in cases:
        case["candidates"].reverse()
    with ThreadPoolExecutor(max_workers=REPLAY_WORKERS) as executor:
        for seed, events in zip(seeds, executor.map(runner.run, cases), strict=True):
            assert events == baseline[seed], f"Concurrent replay changed seed {seed}"
    return {
        "cases": len(cases),
        "workers": REPLAY_WORKERS,
        "matches": len(cases),
        "submission_order": seeds,
        "candidate_order_reversed": True,
    }


def differential(runner: OpenMppRunner, cases: int) -> dict:
    oracle = ReferenceRunner()
    rng = random.Random(317301)
    selected = Counter()
    durations = []
    for seed in range(cases):
        alive = rng.choice((True, False))
        cutoff = rng.randint(3_000, 50_000)
        rows = []
        for code in sorted(MODEL_CODES):
            if code == "death":
                start = 0
                forced = None if alive else rng.randint(0, cutoff)
                rate = 0
            else:
                start = rng.randint(0, cutoff)
                forced = rng.randint(start, cutoff) if seed % 4 == 0 else None
                rate = rng.choice((0, 0.2, 0.05, 0.001, 0.0001))
            rows.append(
                {
                    "code": code,
                    "rate_per_day": rate,
                    "start_day": start,
                    "forced_day": forced,
                    "draw": draw_for(seed, code),
                }
            )
        case = {
            "cutoff_elapsed_day": cutoff,
            "alive_at_cutoff": alive,
            "candidates": rows,
        }
        started = perf_counter()
        actual = runner.run(case)
        durations.append(perf_counter() - started)
        expected = oracle.run(case)
        assert actual == expected, (
            f"Differential case {seed}: {actual!r} != {expected!r}"
        )
        selected.update(event["code"] for event in actual)

    assert set(selected) == MODEL_CODES, f"Event code coverage: {sorted(selected)}"
    durations.sort()
    return {
        "cases": cases,
        "selected_by_code": dict(selected),
        "latency_ms": {
            f"p{p}": round(durations[math.ceil(p / 100 * cases) - 1] * 1_000, 3)
            for p in (50, 95, 99)
        },
        "serial_cases_per_second": round(cases / sum(durations), 3),
        "peak_child_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    }


def linked_state(runner: OpenMppRunner) -> list[dict]:
    specs = (
        ("care-before-cutoff", None, 6, 2, 1),
        ("death-ties-care", 6, 6, 2, 0),
        ("death-follows-care", 7, 6, 2, 1),
        ("care-disabled", None, None, 1, 0),
    )
    results = []
    for name, death_day, care_day, actors, expected in specs:
        rows = {"death": death_day, "shared-care": care_day, "health-transition": 5}
        case = {
            "cutoff_elapsed_day": 10,
            "alive_at_cutoff": death_day is None,
            "candidates": [
                {
                    "code": code,
                    "rate_per_day": 0,
                    "start_day": 0,
                    "forced_day": day,
                    "draw": 0.5,
                }
                for code, day in rows.items()
            ],
        }
        with TemporaryDirectory(prefix="openmpp-linked-check-") as directory:
            cwd = Path(directory)
            database = cwd / runner.template.name
            shutil.copyfile(runner.template, database)
            (cwd / "case.tsv").write_text(_case_tsv(case), encoding="ascii")
            result = subprocess.run(
                [str(runner.executable)],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=25,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert f"entities/case={actors}.0" in result.stdout, result.stdout
            rows = (cwd / "events.tsv").read_text(encoding="ascii").splitlines()
            shared_events = sum(row.startswith("shared-care\t") for row in rows)
            with sqlite3.connect(database) as connection:
                recipient = accumulator(connection, "Cases", 1)
                caregiver = accumulator(connection, "CaregiverOutcomes", 0)
            assert shared_events == recipient == caregiver == expected, name
        results.append(
            {
                "case": name,
                "entities": actors,
                "shared_events": shared_events,
                "recipient_count": recipient,
                "caregiver_count": caregiver,
            }
        )
    return results


def accumulator(
    connection: sqlite3.Connection, output_table: str, acc_id: int
) -> float:
    match = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB ?",
        (f"{output_table}_a[0-9a-f]*",),
    ).fetchall()
    [(table,)] = match
    quoted_table = '"' + table.replace('"', '""') + '"'
    [(value,)] = connection.execute(
        f"SELECT acc_value FROM {quoted_table} WHERE acc_id = ?", (acc_id,)
    ).fetchall()
    return value


def anchored_rerun(runner: OpenMppRunner) -> dict:
    spec = compile_spec(
        {
            "region": "51",
            "ethnicity": "01",
            "birth_year": 1984,
            "as_of": "2026-11-23",
            "seed": 2026,
            "household": "family",
        }
    )["spec"]
    started = perf_counter()
    parent = run_skeleton(spec, runner, "openmpp-acceptance-subject")
    creation_ms = (perf_counter() - started) * 1_000
    frozen_parent = deepcopy(parent)
    new_work_start = "2005-07-01"
    started = perf_counter()
    child = revise_skeleton(
        parent,
        spec,
        [{"op": "update", "event_id": "work", "start": new_work_start}],
        runner,
    )
    revision_ms = (perf_counter() - started) * 1_000
    before = parent["simulation"]
    after = child["simulation"]
    old_candidates = {row["code"]: row for row in before["candidates"]}
    new_candidates = {row["code"]: row for row in after["candidates"]}
    assert parent == frozen_parent, "The revision changed its parent graph"
    assert before["input_sha256"] != after["input_sha256"]
    assert after["replay"]["parent_run_id"] == before["run_id"]
    assert (
        old_candidates["job-exit"]["start_day"]
        != new_candidates["job-exit"]["start_day"]
    )
    assert {key: row["draw"] for key, row in old_candidates.items()} == {
        key: row["draw"] for key, row in new_candidates.items()
    }
    old_work_start = next(
        node["start"] for node in parent["nodes"] if node["event_id"] == "work"
    )
    anchor = min(old_work_start, new_work_start)
    prefix = {
        node["event_id"]: node for node in parent["nodes"] if node["start"] < anchor
    }
    assert prefix, "Expected a nonempty prefix before the edited anchor"
    child_prefix = {
        node["event_id"]: node for node in child["nodes"] if node["start"] < anchor
    }
    assert prefix == child_prefix, "The pre-anchor records changed"
    return {
        "anchor": anchor,
        "new_work_start": new_work_start,
        "prefix_events_retained": sorted(prefix),
        "before_job_exit_start_day": old_candidates["job-exit"]["start_day"],
        "after_job_exit_start_day": new_candidates["job-exit"]["start_day"],
        "parent_run_id": before["run_id"],
        "child_run_id": after["run_id"],
        "creation_ms": round(creation_ms, 3),
        "revision_ms": round(revision_ms, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=1_000)
    args = parser.parse_args()
    if args.cases < 100:
        parser.error("--cases must be at least 100 to exercise all event types")
    runner = configured_runner()
    if not isinstance(runner, OpenMppRunner):
        parser.error("OPENMPP_MODEL must point to the compiled model")
    identity = runner.identity()
    comparison = differential(runner, args.cases)
    incidence, baseline = competing_risks(runner)
    print(
        json.dumps(
            {
                "engine": identity,
                "linked_state": linked_state(runner),
                "anchored_rerun": anchored_rerun(runner),
                "differential": comparison,
                "competing_risk_incidence": incidence,
                "paired_variance": paired_variance(runner, baseline),
                "concurrent_replay": replay_submission(runner, baseline),
            },
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
