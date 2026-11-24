"""Regenerate or verify the JSON Schema exported from the skeleton CUE contract."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


CONTRACT = Path(__file__).resolve().parents[1] / "backend" / "app" / "data"
CUE_FILE = CONTRACT / "skeleton_spec.cue"
SCHEMA_FILE = CONTRACT / "skeleton_spec.schema.json"
CUE_VERSION = "v0.17.1"


def run_cue(
    cue: str, *args: str, directory: Path, payload: dict | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [cue, *args],
        cwd=directory,
        input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )


def fixtures() -> Iterator[tuple[str, dict, bool]]:
    provided = {
        "region": "51",
        "region_label": "四川",
        "ethnicity": "01",
        "ethnicity_label": "汉族",
        "birth_year": 1984,
        "as_of": "2026-11-23",
        "entry": "birth_cohort",
        "household": "family",
        "detail": "standard",
        "seed": 2026,
        "life_status": "living",
        "sampling": {
            "birth_year": {"source": "provided"},
            "household": {"source": "provided"},
        },
        "history_selection": [
            {
                "id": "POL-EMP-1998",
                "label": "国企下岗与再就业",
                "reason": "年龄和范围重合。",
            }
        ],
        "themes": ["POL-EMP-1998"],
    }
    yield "provided options", provided, True

    population = {
        "model_version": "synthetic-population/1",
        "synthetic": True,
        "frame": "birth_cohort",
        "conditioning": "died_by_as_of",
        "birth_year": {"source": "provided"},
        "birth_year_selection_probability": 1,
        "frame_birth_year_count": 1,
        "frame_population_size": None,
        "frame_inclusion_probability": 0.2,
        "frame_log_inclusion_probability": -1.61,
        "cohort_survival_probability": 0.8,
        "risk_class": "middle",
        "risk_class_selection_probability": 0.52,
        "right_censored": False,
        "analysis_weight": None,
    }
    deceased = {
        **provided,
        "life_status": "deceased",
        "death_date": "2024-02-15",
        "population": population,
    }
    yield "conditioned death with population evidence", deceased, True
    yield "censored life with no observed death", {**provided, "death_date": None}, True
    yield "invalid death date format", {**deceased, "death_date": "2024"}, False
    yield (
        "out-of-range population probability",
        {
            **deceased,
            "population": {**population, "risk_class_selection_probability": 1.1},
        },
        False,
    )
    yield (
        "unrecognized population field",
        {**deceased, "population": {**population, "extra": True}},
        False,
    )

    sampled = {
        **provided,
        "history_selection": [],
        "themes": [],
        "sampling": {
            "birth_year": {"source": "sampled", "range": [1926, 2026]},
            "household": {"source": "sampled"},
        },
    }
    yield "sampled options and empty history", sampled, True
    yield "whole JSON number", {**provided, "seed": 2026.0}, True
    yield "unexpected field", {**provided, "code": "unexpected"}, False
    yield (
        "missing required field",
        {k: v for k, v in provided.items() if k != "seed"},
        False,
    )
    yield "invalid detail enum", {**provided, "detail": "extended"}, False
    yield "negative seed", {**provided, "seed": -1}, False
    yield "fractional year", {**provided, "birth_year": 1984.5}, False
    yield "out-of-range year", {**provided, "birth_year": 1879}, False
    yield "non-canonical date", {**provided, "as_of": "2026-11-23\n"}, False
    yield "malformed theme", {**provided, "themes": ["POL-EMP-1998\n"]}, False

    missing_range = deepcopy(sampled)
    del missing_range["sampling"]["birth_year"]["range"]
    yield "missing sampled birth range", missing_range, False

    malformed_sampling = deepcopy(sampled)
    malformed_sampling["sampling"]["birth_year"]["range"].append(2100)
    yield "malformed year range", malformed_sampling, False
    yield (
        "unknown history field",
        {
            **provided,
            "history_selection": [{**provided["history_selection"][0], "extra": True}],
        },
        False,
    )


def check_fixtures(cue: str, schema_text: str) -> int:
    with TemporaryDirectory() as path:
        directory = Path(path)
        schema = directory / SCHEMA_FILE.name
        schema.write_text(schema_text, encoding="utf-8")
        count = 0
        for name, payload, valid in fixtures():
            native = run_cue(
                cue,
                "vet",
                "-c",
                "-d",
                "#SkeletonSpec",
                CUE_FILE.name,
                "json:",
                "-",
                directory=CUE_FILE.parent,
                payload=payload,
            )
            exported = run_cue(
                cue,
                "vet",
                "-c",
                "jsonschema:",
                schema.name,
                "json:",
                "-",
                directory=directory,
                payload=payload,
            )
            for contract, result in (("CUE", native), ("JSON Schema", exported)):
                if (result.returncode == 0) != valid:
                    expected = "accept" if valid else "reject"
                    raise ValueError(
                        f"{contract} should {expected} fixture {name!r}.\n"
                        f"{result.stderr.strip()}"
                    )
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cue", help=f"path to the CUE {CUE_VERSION} executable")
    parser.add_argument("--write", action="store_true", help="regenerate the schema")
    args = parser.parse_args()
    configured = args.cue or os.environ.get("CUE_BIN") or "cue"
    cue = shutil.which(configured)
    if not cue:
        parser.exit(1, f"Install CUE {CUE_VERSION} or pass --cue PATH.\n")
    cue = str(Path(cue).resolve())
    try:
        version = run_cue(cue, "version", directory=CONTRACT)
        if version.returncode or version.stdout.splitlines()[:1] != [
            f"cue version {CUE_VERSION}"
        ]:
            raise ValueError(f"Schema generation requires CUE {CUE_VERSION}.")
        result = run_cue(
            cue,
            "def",
            "--out",
            "jsonschema",
            "-e",
            "#SkeletonSpec",
            CUE_FILE.name,
            directory=CONTRACT,
        )
        if result.returncode:
            raise ValueError(f"CUE export failed.\n{result.stderr.strip()}")
        generated = json.loads(result.stdout)
        schema_text = result.stdout
        if not args.write:
            if not SCHEMA_FILE.is_file():
                raise ValueError("The generated schema is missing. Rerun with --write.")
            schema_text = SCHEMA_FILE.read_text(encoding="utf-8")
            if generated != json.loads(schema_text):
                raise ValueError("The generated schema differs. Rerun with --write.")
        count = check_fixtures(cue, schema_text)
        if args.write:
            SCHEMA_FILE.write_text(schema_text, encoding="utf-8", newline="\n")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        parser.exit(1, f"Skeleton schema check failed. {exc}\n")
    print(f"CUE and its exported JSON Schema agreed on {count} fixtures.")


if __name__ == "__main__":
    main()
