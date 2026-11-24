from datetime import date

from app.domain import apply_edits, compile_spec, generate_graph


def graph(**overrides):
    options = {
        "birth_year": 1984,
        "region": "51",
        "household": "family",
        "seed": 2026,
        **overrides,
    }
    return generate_graph(compile_spec(options)["spec"])


def event(record, event_id):
    return next(node for node in record["nodes"] if node["event_id"] == event_id)


def test_adult_path_records_seeded_choices_and_synthetic_provenance():
    adult = graph()
    assert adult == graph()
    nodes = {node["event_id"]: node for node in adult["nodes"]}
    decisions = {
        node["event_id"]: node for node in adult["nodes"] if "decision" in node
    }
    assert {"school-path", "commute-path", "work-adjustment", "support-plan"} <= set(
        decisions
    )
    for node in decisions.values():
        decision = node["decision"]
        assert decision["selected"] in decision["options"]
        assert len(decision["options"]) > 1
        assert node["precision"] == "year"
        assert node["evidence"][0]["kind"] == "synthetic_rule"
        assert all(nodes[dep]["start"] <= node["start"] for dep in node["depends_on"])
        assert abs(node["state_delta"]["pressure_delta"]) <= 2
        assert abs(node["state_delta"]["support_delta"]) <= 2
    assert any(
        graph(seed=seed)["nodes"] != adult["nodes"] for seed in range(2027, 2032)
    )


def test_cohort_region_and_household_change_options_but_ethnicity_does_not():
    adult = graph()
    individual = graph(household="individual")
    urban = graph(region="81")
    different_ethnicity = graph(ethnicity="07")
    old = graph(birth_year=1905)
    assert event(adult, "support-plan")["participants"] == ["primary", "family-sibling"]
    assert event(individual, "support-plan")["participants"] == ["primary"]
    assert (
        event(adult, "commute-path")["decision"]["options"]
        != event(urban, "commute-path")["decision"]["options"]
    )
    assert (
        event(adult, "school-path")["decision"]["options"]
        != event(old, "school-path")["decision"]["options"]
    )
    assert adult["nodes"] == different_ethnicity["nodes"]


def test_generated_paths_stop_at_cutoff_and_recompute_descendants():
    child = graph(birth_year=2025, as_of="2026-01-01")
    assert not any("decision" in node for node in child["nodes"])

    adult = graph()
    moved = apply_edits(
        adult, [{"op": "update", "event_id": "work", "start": "2005-07-01"}]
    )
    assert {"commute-path", "work-adjustment"} <= set(moved["diff"]["recomputed"])
    assert any("decision" in node for node in moved["nodes"])

    for birth_year in range(1880, 2027, 5):
        for life_status in ("living", "deceased"):
            result = graph(
                birth_year=birth_year, life_status=life_status, detail="rich"
            )
            assert result["validation"]["valid"], result["validation"]["issues"]
            cutoff = date.fromisoformat(
                result["person"]["death_date"] or result["as_of"]
            )
            assert all(
                date.fromisoformat(node["end"] or node["start"]) <= cutoff
                for node in result["nodes"]
            )
