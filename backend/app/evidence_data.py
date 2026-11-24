"""Published population snapshots with their original statistical definitions."""

from __future__ import annotations

from copy import deepcopy
from functools import cache
import json
from pathlib import Path
import random
from typing import Any


_DATA_PATH = Path(__file__).with_name("data") / "population_evidence.json"
_DETAIL_FIELDS = {"regions", "ethnicities", "ethnicity_totals", "subregions"}


def _confirms_partition(counts: dict[str, int], keys: set[str], total: int) -> bool:
    return (
        set(counts) == keys
        and all(type(count) is int and count >= 0 for count in counts.values())
        and sum(counts.values()) == total
    )


@cache
def _evidence_data() -> dict[str, Any]:
    data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    for frame in data["frames"]:
        regions = frame["regions"]
        if (
            sum(region["population"] for region in regions.values())
            != frame["population"]
        ):
            raise ValueError(f"Region totals do not match population for {frame['id']}")
        ethnicities = set(frame["ethnicities"])
        if not ethnicities:
            continue
        totals = frame["ethnicity_totals"]
        if not _confirms_partition(totals, ethnicities, frame["population"]):
            raise ValueError(
                f"Ethnicity totals do not match population for {frame['id']}"
            )
        for region in regions.values():
            if not _confirms_partition(
                region["counts"], ethnicities, region["population"]
            ):
                raise ValueError(f"Incomplete ethnicity partition for {frame['id']}")
        for ethnicity, total in totals.items():
            if sum(region["counts"][ethnicity] for region in regions.values()) != total:
                raise ValueError(
                    f"Regional totals do not match {ethnicity} for {frame['id']}"
                )
    return data


def population_evidence_catalog() -> dict[str, Any]:
    """Return the snapshots and source-specific categories for selection."""
    data = _evidence_data()
    frames = []
    additional_ethnicities = []
    for frame in data["frames"]:
        region_ids = list(frame["regions"])
        frames.append(
            {
                **{
                    key: value
                    for key, value in frame.items()
                    if key not in _DETAIL_FIELDS
                },
                "region_ids": region_ids,
                "has_ethnicity_distribution": bool(frame["ethnicities"]),
            }
        )
        additional_ethnicities.extend(
            {
                "id": ethnicity,
                "label": label,
                "region_id": region_ids[0] if len(region_ids) == 1 else None,
                "region_ids": region_ids,
                "frame_id": frame["id"],
            }
            for ethnicity, label in frame["ethnicities"].items()
            if not ethnicity.isdecimal()
        )
    return deepcopy(
        {
            "version": data["version"],
            "frames": frames,
            "additional_ethnicities": additional_ethnicities,
        }
    )


@cache
def _frame_cells() -> dict[str, tuple[tuple[str, str, int], ...]]:
    return {
        frame["id"]: tuple(
            (region, ethnicity, count)
            for region, row in frame["regions"].items()
            for ethnicity, count in row.get("counts", {}).items()
        )
        for frame in _evidence_data()["frames"]
    }


def population_frame_cells(frame_id: str) -> tuple[tuple[str, str, int], ...]:
    """Return only reported region-by-ethnicity cells from the requested frame."""
    return _frame_cells().get(frame_id, ())


def _observation(frame: dict[str, Any], count: int, denominator: int) -> dict[str, Any]:
    return {
        "frame_id": frame["id"],
        "count": count,
        "denominator": denominator,
        "share": count / denominator,
        "reference_date": frame["reference_date"],
        "reference_period": frame["reference_period"],
        "scope": frame["scope"],
        "geography_basis": frame["geography_basis"],
        "source_url": frame["source_url"],
        "source_title": frame["source_title"],
        "table": frame["table"],
        "sampling_basis": frame["sampling_basis"],
    }


def sample_frame(
    frame_id: str,
    *,
    region: str | None = None,
    ethnicity: str | None = None,
    seed: int,
) -> dict[str, Any]:
    """Draw a region and category in proportion to one snapshot's published counts."""
    data = _evidence_data()
    frame = next((item for item in data["frames"] if item["id"] == frame_id), None)
    if frame is None:
        raise ValueError("未知的官方人口统计框")
    cells = population_frame_cells(frame_id)
    if not cells:
        raise ValueError("该人口统计框缺少地区与族群联合分布")

    eligible = sorted(
        (cell_region, cell_ethnicity, count)
        for cell_region, cell_ethnicity, count in cells
        if count > 0
        and (region is None or region == cell_region)
        and (ethnicity is None or ethnicity == cell_ethnicity)
    )
    eligible_population = sum(count for _, _, count in eligible)
    if not eligible_population:
        raise ValueError("该人口统计框中的筛选组合无正计数")

    rng = random.Random(f"{data['version']}:{frame_id}:{seed}:region_ethnicity")
    index = rng.randrange(eligible_population)
    for cell_region, cell_ethnicity, count in eligible:
        if index < count:
            selected_region = frame["regions"][cell_region]
            return {
                "version": data["version"],
                "frame_id": frame_id,
                "region": cell_region,
                "region_label": selected_region["label"],
                "ethnicity": cell_ethnicity,
                "ethnicity_label": frame["ethnicities"][cell_ethnicity],
                "cell_count": count,
                "eligible_population": eligible_population,
                "frame_population": frame["population"],
                "selection_probability": count / eligible_population,
                "filters": {"region": region, "ethnicity": ethnicity},
                "observation": _observation(
                    frame, count, selected_region["population"]
                ),
            }
        index -= count
    raise AssertionError("The sampled index must fall within an eligible cell")


def population_evidence_for(region: str, ethnicity: str) -> dict[str, Any]:
    """Look up background observations for the supplied region and category."""
    data = _evidence_data()
    region_observation = None
    ethnicity_observation = None
    joint_observation = None
    for frame in data["frames"]:
        row = frame["regions"].get(region)
        if row and region_observation is None:
            region_observation = _observation(
                frame, row["population"], frame["population"]
            )
        if ethnicity in frame["ethnicity_totals"]:
            ethnicity_observation = _observation(
                frame, frame["ethnicity_totals"][ethnicity], frame["population"]
            )
        if row and ethnicity in row.get("counts", {}):
            joint_observation = _observation(
                frame, row["counts"][ethnicity], row["population"]
            )
    return {
        "version": data["version"],
        "region": region,
        "ethnicity": ethnicity,
        "region_observation": region_observation,
        "ethnicity_observation": ethnicity_observation,
        "joint_observation": joint_observation,
        "status": "observed" if joint_observation is not None else "unavailable",
    }
