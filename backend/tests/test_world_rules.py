import re
from datetime import date
from pathlib import Path

import pytest

from app.world_rules import SCENARIOS, WORLD_VERSION, history_window


def test_world_catalog_contains_each_documented_node_with_source_and_evidence_policy():
    report = Path(__file__).resolve().parents[2] / "docs/v3/历史节点与环境暴露目录.md"
    report_ids = set(
        re.findall(r"^\| `([A-Z0-9-]+)` ", report.read_text(encoding="utf-8"), re.M)
    )

    assert len(report_ids) == 69
    assert set(SCENARIOS) == report_ids
    assert WORLD_VERSION
    for rule in SCENARIOS.values():
        assert rule["version"]
        assert rule["eligibility"] and rule["mechanism"] and rule["affected_dimensions"]
        assert rule["evidence_policy"]
        assert rule["sources"]
        assert all(source["url"].startswith("https://") for source in rule["sources"])


def test_retroactive_benefit_retains_both_effective_and_publication_dates():
    benefit = SCENARIOS["WEL-CHILD-2025"]

    assert benefit["valid_time"]["start"] == "2025-01-01"
    assert benefit["release_and_record_times"]["published_at"] == "2025-07-28"


@pytest.mark.parametrize(
    ("rule_id", "region", "birth_year", "cutoff", "expected"),
    [
        ("DIS-WC-2008", "51", 1984, "2008-05-11", None),
        ("DIS-WC-2008", "51", 1984, "2008-05-12", ("2008-05-12", "2008-05-12")),
        ("DIS-WC-2008", "51", 2004, "2008-05-12", None),
        ("DIS-LS-2013", "11", 1984, "2026-11-23", None),
        ("DIS-FLOOD-2021-ZZ", "41", 1984, "2021-07-20", ("2021-07-17", "2021-07-20")),
        (
            "DIS-HK-MANGKHUT-2018",
            "81",
            1984,
            "2026-11-23",
            ("2018-09-16", "2018-09-17"),
        ),
        ("DIS-HK-MANGKHUT-2018", "44", 1984, "2026-11-23", None),
        ("PUB-COVID-B-2023", "51", 1984, "2023-01-07", None),
        ("WEL-CHILD-2025", "51", 1984, "2024-12-31", None),
        ("WEL-CHILD-2025", "51", 1984, "2025-01-01", ("2025-01-01", "2025-01-01")),
        ("POL-HUKOU-2024", "82", 1984, "2026-11-23", None),
        ("POL-WEST-2000", "15", 1984, "2000-12-31", ("2000-01-01", "2000-12-31")),
        ("MIG-CA-1923", "44", 1900, "1945-12-31", None),
        ("MIG-US-EXCLUSION-1882", "44", 1880, "1940-12-31", None),
    ],
)
def test_history_window_uses_effective_dates_and_geographic_scope(
    rule_id, region, birth_year, cutoff, expected
):
    result = history_window(rule_id, region, birth_year, date.fromisoformat(cutoff))

    assert result == (tuple(map(date.fromisoformat, expected)) if expected else None)
