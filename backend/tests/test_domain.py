from datetime import date

import pytest

from app.domain import (
    apply_edits,
    catalog,
    compile_spec,
    generate_graph,
    suggest_operations,
    validate_graph,
)
from app.schemas import GraphOperation


def options(**overrides):
    value = {
        "region": "51",
        "ethnicity": "01",
        "birth_year": 1984,
        "as_of": "2026-11-23",
        "entry": "birth_cohort",
        "household": "family",
        "detail": "standard",
        "seed": 2026,
        "life_status": "living",
    }
    value.update(overrides)
    return value


def test_catalog_covers_all_regions_and_ethnicities():
    result = catalog()
    assert len(result["regions"]) >= 34
    assert len(result["ethnicities"]) >= 56
    assert any(item["id"] == "51" for item in result["regions"])
    assert any(item["id"] == "56" for item in result["ethnicities"])


def test_compile_spec_rejects_future_birth():
    with pytest.raises(ValueError, match="birth_year"):
        compile_spec(options(birth_year=2027))


def test_unset_birth_year_and_household_are_seeded_and_reproducible():
    request = options(birth_year=None, household=None)
    first = compile_spec(request)["spec"]
    second = compile_spec(request)["spec"]

    assert first == second
    assert first["sampling"]["birth_year"] == {
        "source": "sampled",
        "range": [1926, 2026],
    }
    assert 1926 <= first["birth_year"] <= 2026
    assert first["household"] in {"individual", "family"}
    assert first["sampling"]["household"] == {"source": "sampled"}
    assert first["themes"]
    assert first["history_selection"]

    samples = [
        compile_spec(options(seed=seed, birth_year=None, household=None))["spec"]
        for seed in range(2026, 2042)
    ]
    assert len({spec["birth_year"] for spec in samples}) > 1
    assert {spec["household"] for spec in samples} == {"individual", "family"}


def test_history_topics_are_automatic_and_respect_region_and_personal_time():
    sichuan = compile_spec(options(region="51", birth_year=1984, as_of="2026-11-23"))[
        "spec"
    ]
    beijing = compile_spec(options(region="11", birth_year=1984, as_of="2026-11-23"))[
        "spec"
    ]
    young = compile_spec(options(region="51", birth_year=2004, as_of="2026-11-23"))[
        "spec"
    ]

    assert "DIS-WC-2008" in sichuan["themes"]
    assert "DIS-WC-2008" not in beijing["themes"]
    assert "DIS-WC-2008" not in young["themes"]
    before = compile_spec(options(as_of="2008-05-11"))["spec"]
    on_day = compile_spec(options(as_of="2008-05-12"))["spec"]
    assert "DIS-WC-2008" not in before["themes"]
    assert "DIS-WC-2008" in on_day["themes"]


def test_history_matching_uses_death_cutoff_and_local_policy_scope():
    deceased = compile_spec(options(birth_year=None, life_status="deceased", seed=9))[
        "spec"
    ]
    assert deceased["sampling"]["birth_year"]["range"] == [1926, 1968]
    graph = generate_graph(deceased)
    history = {node["event_id"] for node in graph["nodes"] if node["summary_node"]}
    assert history == set(deceased["themes"])
    assert all(
        node["start"] <= graph["person"]["death_date"] for node in graph["nodes"]
    )

    hong_kong = compile_spec(options(region="81"))["spec"]
    assert "DIS-HK-MANGKHUT-2018" in hong_kong["themes"]
    assert "POL-HUKOU-2024" not in hong_kong["themes"]


def test_callers_cannot_choose_historical_topics():
    with pytest.raises(ValueError, match="automatically"):
        compile_spec(options(themes=["POL-EMP-1998"]))


def test_generation_is_seed_stable_and_inputs_change_observable_graph():
    first = generate_graph(compile_spec(options())["spec"])
    again = generate_graph(compile_spec(options())["spec"])
    assert first["person"] == again["person"]
    assert first["nodes"] == again["nodes"]

    other = generate_graph(
        compile_spec(options(region="21", seed=9, detail="rich"))["spec"]
    )
    assert other["person"]["region_id"] == "21"
    assert other["person"]["name"] != first["person"]["name"]
    same_cohort_standard = generate_graph(
        compile_spec(options(region="21", seed=9, detail="standard"))["spec"]
    )
    assert {node["event_id"] for node in other["nodes"]} - {
        node["event_id"] for node in same_cohort_standard["nodes"]
    } == {"health-check", "digital-learning"}


def test_history_event_requires_matching_age_and_location():
    young = generate_graph(compile_spec(options(birth_year=2004))["spec"])
    assert not any(node["event_id"] == "DIS-WC-2008" for node in young["nodes"])
    assert any(node["event_id"] == "COVID-19-2020" for node in young["nodes"])
    beijing = generate_graph(compile_spec(options(region="11"))["spec"])
    assert any(node["event_id"] == "COVID-19-2020" for node in beijing["nodes"])
    assert not any(node["event_id"] == "DIS-WC-2008" for node in beijing["nodes"])


def test_deceased_graph_closes_snapshot_at_death():
    graph = generate_graph(
        compile_spec(options(birth_year=1930, life_status="deceased"))["spec"]
    )
    death_date = graph["person"]["death_date"]
    assert death_date < graph["person"]["as_of"]
    assert graph["nodes"][-1]["event_id"] == "death"
    assert all(node["start"] <= death_date for node in graph["nodes"])
    assert all(
        node["end"] is None or node["end"] <= death_date for node in graph["nodes"]
    )


def test_individual_graph_has_only_primary_participant():
    graph = generate_graph(compile_spec(options(household="individual"))["spec"])
    assert graph["person"]["family"] == []
    assert all(
        node["participants"] == [graph["person"]["person_id"]]
        for node in graph["nodes"]
    )


def test_scope_living_at_as_of_rejects_deceased_record():
    with pytest.raises(ValueError, match="living_at_as_of"):
        compile_spec(options(entry="living_at_as_of", life_status="deceased"))


def test_edit_moves_times_only_along_dependency_ancestry():
    graph = generate_graph(compile_spec(options(detail="rich"))["spec"])
    original = {node["event_id"]: node for node in graph["nodes"]}
    work_date = date.fromisoformat(original["work"]["start"])
    newer = work_date.replace(year=work_date.year + 1).isoformat()
    child = apply_edits(graph, [{"op": "update", "event_id": "work", "start": newer}])
    current = {node["event_id"]: node for node in child["nodes"]}
    assert current["work"]["start"] == newer
    assert current["health-check"]["start"] > original["health-check"]["start"]
    assert current["household"] == original["household"]


def test_delete_reconnects_to_predecessor_and_is_listed_in_diff():
    graph = generate_graph(compile_spec(options())["spec"])
    child = apply_edits(graph, [{"op": "delete", "event_id": "education"}])
    assert child["diff"]["removed"] == ["education"]
    assert "work" in child["diff"]["recomputed"]
    assert {"source": "birth", "target": "work", "kind": "depends_on"} in child["edges"]


def test_default_history_event_uses_documented_occurrence_and_url():
    graph = generate_graph(compile_spec(options())["spec"])
    event = next(node for node in graph["nodes"] if node["event_id"] == "DIS-WC-2008")
    assert event["start"] == "2008-05-12"
    assert event["evidence"][0]["url"].startswith("https://www.ndrc.gov.cn/")


def test_apply_edit_creates_child_graph_and_recomputes_dependents():
    graph = generate_graph(compile_spec(options())["spec"])
    edited = apply_edits(
        graph, [{"op": "update", "event_id": "education", "title": "县城中学毕业"}]
    )
    assert edited["parent_revision_id"] == graph.get("graph_revision_id")
    assert edited["nodes"] != graph["nodes"]
    assert "education" in edited["diff"]["modified"]
    assert "work" in edited["diff"]["recomputed"]
    assert graph["nodes"] != edited["nodes"]


def test_apply_edit_rejects_cycles_and_unknown_dependencies():
    graph = generate_graph(compile_spec(options())["spec"])
    with pytest.raises(ValueError, match="unknown event"):
        apply_edits(graph, [{"op": "update", "event_id": "missing", "title": "x"}])
    with pytest.raises(ValueError, match="cycle"):
        apply_edits(
            graph, [{"op": "update", "event_id": "birth", "depends_on": ["work"]}]
        )


def test_llm_suggestion_is_bounded_to_graph_operations():
    graph = generate_graph(compile_spec(options())["spec"])
    operations = suggest_operations(
        graph, "把教育节点改为职业学校，并将工作节点延后一年"
    )
    assert operations
    assert all(operation["op"] == "update" for operation in operations)
    assert {operation["event_id"] for operation in operations} <= {"education", "work"}


def test_delete_suggestion_does_not_emit_an_update_for_the_same_event():
    graph = generate_graph(compile_spec(options(detail="rich"))["spec"])
    assert suggest_operations(graph, "删除工作节点") == [
        {"op": "delete", "event_id": "work"}
    ]


def test_history_suggestion_keeps_exposure_and_family_impact_as_candidates():
    graph = generate_graph(compile_spec(options())["spec"])
    world_delta = next(
        node["state_delta"]
        for node in graph["nodes"]
        if node["event_id"] == "DIS-WC-2008"
    )
    assert suggest_operations(
        graph, "把汶川地震节点改为候选暴露，并补充家庭照护影响"
    ) == [
        {
            "op": "update",
            "event_id": "DIS-WC-2008",
            "state_delta": {
                **world_delta,
                "exposure_status": "candidate",
                "exposure_channel": "requires_personal_evidence",
                "family_care_impact": "possible_shared_event_channel",
            },
            "notes": "家庭照护影响仍是候选机制，需同住关系与家庭记录支持。",
        }
    ]


def test_validate_graph_rejects_reversed_interval():
    graph = generate_graph(compile_spec(options())["spec"])
    graph["nodes"][0]["start"] = "2030-01-01"
    graph["nodes"][0]["end"] = "2029-01-01"
    result = validate_graph(graph)
    assert result["valid"] is False
    assert any("interval" in issue for issue in result["issues"])


def _school_split():
    return {
        "op": "split",
        "event_id": "education",
        "children": [
            {"event_id": "school-start", "title": "入学", "start": "1990-09-01"},
            {
                "event_id": "school-completion",
                "title": "完成基础教育",
                "start": "2002-06-30",
                "depends_on": ["school-start"],
                "state_delta": {"education_stage": "基础教育"},
            },
        ],
    }


def test_split_preserves_anchor_and_projects_effect_from_its_owner():
    graph = generate_graph(compile_spec(options())["spec"])
    operation = GraphOperation.model_validate(_school_split()).model_dump(
        mode="json", exclude_unset=True
    )
    child = apply_edits(graph, [operation])
    nodes = {node["event_id"]: node for node in child["nodes"]}
    anchor = nodes["education"]
    assert anchor["summary_node"]
    assert anchor["state_delta"] == {}
    assert anchor["dimension_updates"] == []
    assert nodes["school-start"]["depends_on"] == ["birth"]
    assert nodes["school-completion"]["state_delta"] == {"education_stage": "基础教育"}
    assert any(
        update["dim_id"] == "EDU.01"
        for update in nodes["school-completion"]["dimension_updates"]
    )
    assert {
        (e["source"], e["target"]) for e in child["edges"] if e["kind"] == "decomposes"
    } == {("school-start", "education"), ("school-completion", "education")}
    assert "work" in child["diff"]["recomputed"]
    assert next(node for node in graph["nodes"] if node["event_id"] == "education")[
        "state_delta"
    ] == {"education_stage": "基础教育"}


@pytest.mark.parametrize(
    "effect", [{}, {"education_stage": "高等教育"}, {"education_stage": "基础教育"}]
)
def test_split_rejects_missing_changed_or_duplicate_categorical_effects(effect):
    graph = generate_graph(compile_spec(options())["spec"])
    operation = _school_split()
    if effect == {"education_stage": "基础教育"}:
        operation["children"][0]["state_delta"] = effect
    else:
        operation["children"][1]["state_delta"] = effect
    with pytest.raises(ValueError, match="decomposition effect.*education_stage"):
        apply_edits(graph, [operation])


def test_split_conserves_numeric_amounts_and_revalidates_later_edits():
    graph = generate_graph(compile_spec(options())["spec"])
    graph = apply_edits(
        graph,
        [
            {
                "op": "update",
                "event_id": "education",
                "state_delta": {"cost": 0.3},
            }
        ],
    )
    operation = _school_split()
    operation["children"][0]["state_delta"] = {"cost": 0.1}
    operation["children"][1]["state_delta"] = {"cost": 0.2}
    child = apply_edits(graph, [operation])
    assert child["validation"]["valid"]
    with pytest.raises(ValueError, match="decomposition effect.*cost"):
        apply_edits(child, [{"op": "delete", "event_id": "school-completion"}])


def test_split_requires_subwindows_inside_anchor():
    graph = generate_graph(compile_spec(options())["spec"])
    operation = _school_split()
    operation["children"][1]["start"] = "2002-07-01"
    with pytest.raises(ValueError, match="outside decomposition anchor"):
        apply_edits(graph, [operation])


def test_shared_split_preserves_each_participant_effect_once():
    graph = generate_graph(compile_spec(options())["spec"])
    education = next(node for node in graph["nodes"] if node["event_id"] == "education")
    increment = {
        "person_id": "family-elder",
        "role": "supporter",
        "delta": {"hours": 5},
    }
    education["participants"].append("family-elder")
    education["participant_deltas"] = [increment]
    operation = _school_split()
    operation["children"][0]["participant_deltas"] = [increment]
    operation = GraphOperation.model_validate(operation).model_dump(
        mode="json", exclude_unset=True
    )
    child = apply_edits(graph, [operation])
    nodes = {node["event_id"]: node for node in child["nodes"]}
    assert nodes["education"].get("participant_deltas", []) == []
    assert nodes["school-start"]["participant_deltas"] == [increment]
    assert nodes["school-start"]["participants"] == ["primary", "family-elder"]
    operation["children"][1]["participant_deltas"] = [increment]
    with pytest.raises(ValueError, match="decomposition effect.*family-elder.*hours"):
        apply_edits(graph, [operation])


def test_temporal_relationship_keeps_definite_edges_across_overlapping_events():
    graph = generate_graph(compile_spec(options())["spec"])
    graph = apply_edits(
        graph,
        [
            {
                "op": "add",
                "event_id": "wide",
                "title": "持续区间",
                "start": "2026-01-01",
                "end": "2026-05-01",
            },
            {
                "op": "add",
                "event_id": "early",
                "title": "较早事件",
                "start": "2026-02-01",
            },
            {
                "op": "add",
                "event_id": "same-day",
                "title": "同日事件",
                "start": "2026-02-01",
            },
            {
                "op": "add",
                "event_id": "late",
                "title": "较晚事件",
                "start": "2026-06-01",
            },
        ],
    )
    ids = {"wide", "early", "same-day", "late"}
    assert {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
        if edge["kind"] == "temporal" and {edge["source"], edge["target"]} <= ids
    } == {("wide", "late"), ("early", "late"), ("same-day", "late")}
