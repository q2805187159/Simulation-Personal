import re
from datetime import date
from pathlib import Path

from app.historical_mechanisms import HISTORY_MECHANISMS, assess_history
from app.world_rules import SCENARIOS


def dated_record(rule_id, kind, key, status, at, **details):
    return {
        "rule_id": rule_id,
        "kind": kind,
        "gate" if kind == "gate" else "channel": key,
        "evidence_ref": f"record-{key}-{status}",
        "status": status,
        "date": at,
        "recorded_at": at,
        **details,
    }


def test_all_node_mechanisms_are_executable_with_conservative_defaults():
    report = (
        Path(__file__).resolve().parents[2]
        / "docs/v3/历史暴露推演与个人路径机制规范_v1.md"
    )
    ids = set(
        re.findall(r"^\| `([A-Z0-9-]+)` ", report.read_text(encoding="utf-8"), re.M)
    )
    assert len(ids) == 69
    assert set(HISTORY_MECHANISMS) == set(SCENARIOS) == ids
    for rule_id in ids:
        assessment = assess_history(rule_id, as_of=date(2026, 11, 23))
        assert assessment["status"] == "unknown"
        assert assessment["eligibility"]["status"] == "unknown"
        assert all(channel["status"] == "unknown" for channel in assessment["channels"])
        assert assessment["outcomes"] and assessment["negative_controls"]
        assert assessment["probability"] == {
            "value": None,
            "basis": "uncalibrated",
            "interval": None,
        }


def test_disaster_channels_keep_separate_status_and_provenance():
    node = "DIS-WC-2008"
    day = date(2008, 5, 12)
    broad_match = assess_history(node, as_of=day, region="51", window=(day, day))
    assert broad_match["opportunity"]["status"] == "potential"
    assert broad_match["status"] == "potential"
    assert all(channel["status"] == "unknown" for channel in broad_match["channels"])

    observed = assess_history(
        node,
        as_of=day,
        region="11",
        evidence=[
            dated_record(node, "exposure", "direct", "excluded", "2008-05-12"),
            dated_record(
                node,
                "exposure",
                "household",
                "confirmed",
                "2008-05-12",
                subject_ref="parent-1",
                location_ref="affected-county",
                known_at="2008-05-12",
            ),
        ],
    )
    channels = {entry["channel"]: entry for entry in observed["channels"]}
    assert observed["status"] == "confirmed"
    assert channels["direct"]["status"] == "excluded"
    assert channels["household"]["evidence_refs"] == ["record-household-confirmed"]
    assert channels["household"]["observations"][0]["subject_ref"] == "parent-1"
    assert channels["household"]["known_at"] == "2008-05-12"
    assert channels["media"]["status"] == "unknown"


def test_retroactive_benefit_qualification_approval_and_payment_are_independent():
    node = "WEL-CHILD-2025"
    records = [
        dated_record(node, "gate", "local_program", "met", "2025-09-15"),
        dated_record(node, "gate", "age_guardian_eligible", "met", "2025-09-15"),
        dated_record(node, "gate", "approval", "met", "2025-10-04"),
        dated_record(
            node, "gate", "payment", "met", "2025-11-01", recorded_at="2025-12-01"
        ),
    ]
    early = assess_history(node, as_of=date(2025, 2, 1), region="51", evidence=records)
    assert early["opportunity"]["status"] == "unknown"
    assert early["opportunity"]["availability"]["published_at"] == "2025-07-28"

    approved = assess_history(
        node, as_of=date(2025, 11, 15), region="51", evidence=records
    )
    gates = {gate["id"]: gate for gate in approved["policy_gates"]}
    assert approved["eligibility"]["status"] == "eligible"
    assert gates["approval"]["status"] == "met"
    assert gates["payment"]["status"] == "unknown"
    assert approved["status"] != "confirmed"

    later_archive = assess_history(
        node,
        as_of=date(2025, 11, 15),
        archive_as_of=date(2025, 12, 15),
        region="51",
        evidence=records,
    )
    gates = {gate["id"]: gate for gate in later_archive["policy_gates"]}
    assert gates["payment"]["evidence_refs"] == ["record-payment-met"]


def test_ineligible_historical_rule_does_not_erase_recorded_execution():
    node = "WORK-HK-MINWAGE-2011"
    records = [
        dated_record(node, "gate", "covered_employment", "not_met", "2025-01-01"),
        dated_record(node, "gate", "wage_statement", "met", "2025-01-31"),
    ]
    assessment = assess_history(
        node, as_of=date(2025, 2, 1), region="81", evidence=records
    )
    gates = {gate["id"]: gate for gate in assessment["policy_gates"]}
    assert assessment["eligibility"]["status"] == "ineligible"
    assert gates["wage_statement"]["status"] == "met"


def test_qualified_long_term_care_with_no_service_capacity_has_no_receipt():
    node = "WEL-LTCI-2026"
    records = [
        dated_record(node, "gate", key, "met", "2026-10-01", region="43")
        for key in (
            "local_program",
            "insurance_category",
            "disability_assessment",
            "approval",
        )
    ]
    records.append(
        dated_record(
            node, "gate", "service_capacity", "not_met", "2026-10-03", region="43"
        )
    )
    assessment = assess_history(
        node, as_of=date(2026, 11, 1), region="43", evidence=records
    )

    assert assessment["eligibility"]["status"] == "eligible"
    assert assessment["opportunity"]["practical_status"] == "blocked"
    assert {gate["id"]: gate["status"] for gate in assessment["policy_gates"]}[
        "service_use"
    ] == "unknown"

    different_province = assess_history(
        node, as_of=date(2026, 11, 1), region="51", evidence=records
    )
    assert different_province["eligibility"]["status"] == "unknown"


def test_direct_historical_exposure_uses_place_of_occurrence():
    node = "DIS-WC-2008"
    outside_record = dated_record(
        node, "exposure", "direct", "confirmed", "2008-05-12", region="11"
    )
    day = date(2008, 5, 12)
    assessment = assess_history(node, as_of=day, region="51", evidence=[outside_record])
    assert assessment["status"] == "potential"
    assert {entry["channel"]: entry["status"] for entry in assessment["channels"]}[
        "direct"
    ] == "unknown"

    affected_record = {**outside_record, "region": "51", "location_ref": "visit-2008"}
    assessment = assess_history(
        node, as_of=day, region="11", evidence=[affected_record]
    )
    assert assessment["status"] == "confirmed"


def test_exclusion_requires_evidence_for_all_relevant_channels():
    node = "DIS-WC-2008"
    day = date(2008, 5, 12)
    records = [
        dated_record(node, "exposure", channel, "excluded", "2008-05-12")
        for channel in HISTORY_MECHANISMS[node]["channels"]
    ]

    assert (
        assess_history(node, as_of=day, region="51", evidence=records[:-1])["status"]
        == "potential"
    )
    assert (
        assess_history(node, as_of=day, region="51", evidence=records)["status"]
        == "excluded"
    )


def test_wage_schedule_splits_period_at_effective_date():
    node = "WORK-HK-MINWAGE-2011"
    first, last = date(2026, 4, 30), date(2026, 5, 1)
    assessment = assess_history(node, as_of=last, region="81", window=(first, last))

    assert [rate["rates"]["hour"] for rate in assessment["policy_rates"]] == [
        42.1,
        43.1,
    ]
    assert [rate["period_start"] for rate in assessment["policy_rates"]] == [
        "2026-04-30",
        "2026-05-01",
    ]
    assert all(
        rate["currency"] == "HKD" and rate["source_ref"]
        for rate in assessment["policy_rates"]
    )
    assert assessment["eligibility"]["status"] == "unknown"


def test_macau_wage_schedule_keeps_prior_versions_and_wage_units():
    node = "WORK-MO-MINWAGE-2020-2026"
    first, last = date(2020, 11, 1), date(2026, 5, 1)
    assessment = assess_history(node, as_of=last, region="82", window=(first, last))

    assert [rate["rates"]["hour"] for rate in assessment["policy_rates"]] == [
        32,
        34,
        35,
    ]
    assert [rate["rates"]["month"] for rate in assessment["policy_rates"]] == [
        6656,
        7072,
        7280,
    ]
    assert all(rate["currency"] == "MOP" for rate in assessment["policy_rates"])
    assert assess_history(node, as_of=last, region="81")["policy_rates"] == []
