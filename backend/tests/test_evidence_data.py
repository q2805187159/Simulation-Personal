import pytest

from app.evidence_data import (
    population_evidence_catalog,
    population_evidence_for,
    population_frame_cells,
    sample_frame,
)


def test_official_mainland_cells_match_regional_and_ethnicity_margins():
    cells = population_frame_cells("mainland_2020")
    assert len({region for region, _, _ in cells}) == 31
    assert len({ethnicity for _, ethnicity, _ in cells}) == 58
    assert sum(count for _, _, count in cells) == 1_409_778_724
    assert (
        sum(count for _, ethnicity, count in cells if ethnicity == "01")
        == 1_284_446_389
    )
    assert sum(count for region, _, count in cells if region == "51") == 83_674_866

    evidence = population_evidence_for("51", "01")
    assert evidence["status"] == "observed"
    assert evidence["joint_observation"]["count"] == 77_986_638
    assert evidence["joint_observation"]["denominator"] == 83_674_866
    assert evidence["joint_observation"]["reference_date"] == "2020-11-01"
    assert evidence["joint_observation"]["source_url"].endswith("/A0104.xls")

    observed_zero = population_evidence_for("54", "23")
    assert observed_zero["status"] == "observed"
    assert observed_zero["joint_observation"]["count"] == 0


@pytest.mark.parametrize(
    ("frame_id", "expected_count", "categories"),
    [
        ("hong_kong_2021", 7_413_070, 13),
        ("macao_2021", 682_070, 6),
        ("taiwan_indigenous_registered_2020", 576_374, 17),
    ],
)
def test_local_observations_keep_their_own_published_denominators(
    frame_id, expected_count, categories
):
    cells = population_frame_cells(frame_id)
    assert len({ethnicity for _, ethnicity, _ in cells}) == categories
    assert sum(count for _, _, count in cells) == expected_count
    frame = next(
        frame
        for frame in population_evidence_catalog()["frames"]
        if frame["id"] == frame_id
    )
    assert frame["population"] == expected_count
    assert frame["source_url"].startswith("https://")


def test_different_local_classifications_are_not_imputed_or_conflated():
    assert population_frame_cells("taiwan_2020") == ()
    assert population_frame_cells("all_regions") == ()
    hong_kong_han = population_evidence_for("81", "01")
    assert hong_kong_han["status"] == "unavailable"
    assert hong_kong_han["region_observation"]["count"] == 7_413_070
    assert hong_kong_han["joint_observation"] is None
    taiwan = population_evidence_for("71", "tw-amis")
    assert taiwan["region_observation"]["count"] == 23_829_897
    assert taiwan["joint_observation"]["count"] == 215_271
    assert taiwan["joint_observation"]["denominator"] == 576_374


def test_catalog_returns_detached_public_data():
    catalog = population_evidence_catalog()
    catalog["frames"].clear()
    assert population_evidence_catalog()["frames"]


def test_observed_sampling_applies_both_filters():
    selected = sample_frame("mainland_2020", region="51", ethnicity="01", seed=2026)
    assert selected["frame_id"] == "mainland_2020"
    assert selected["region"] == "51"
    assert selected["ethnicity"] == "01"
    assert selected["filters"] == {"region": "51", "ethnicity": "01"}
    assert selected["frame_population"] == 1_409_778_724
    assert selected["cell_count"] == selected["eligible_population"] == 77_986_638
    assert selected["selection_probability"] == 1.0
    assert selected["observation"]["count"] == 77_986_638
    assert selected["observation"]["denominator"] == 83_674_866
    assert selected["observation"]["geography_basis"] == "普查时点常住地"


@pytest.mark.parametrize(
    ("region", "ethnicity", "eligible_population"),
    [(None, None, 1_409_778_724), ("51", None, 83_674_866), (None, "23", 3_479)],
)
def test_observed_sampling_is_seeded_with_the_correct_eligible_population(
    region, ethnicity, eligible_population
):
    options = {"region": region, "ethnicity": ethnicity, "seed": 2026}
    selected = sample_frame("mainland_2020", **options)
    assert sample_frame("mainland_2020", **options) == selected
    assert selected["eligible_population"] == eligible_population
    assert region is None or selected["region"] == region
    assert ethnicity is None or selected["ethnicity"] == ethnicity
    assert selected["cell_count"] > 0
    assert (
        selected["selection_probability"]
        == selected["cell_count"] / eligible_population
    )


def test_observed_sampling_uses_published_counts_for_distributions():
    draws = [sample_frame("hong_kong_2021", seed=seed) for seed in range(1000)]
    chinese_share = sum(draw["ethnicity"] == "hk-chinese" for draw in draws) / len(
        draws
    )
    assert chinese_share == pytest.approx(6_793_502 / 7_413_070, abs=0.04)
    assert {draw["region"] for draw in draws} == {"81"}
    assert draws[0]["observation"]["sampling_basis"] == "census_sample_estimate"


@pytest.mark.parametrize(
    ("frame_id", "region", "ethnicity", "error"),
    [
        ("all_regions", None, None, "未知"),
        ("taiwan_2020", None, None, "联合分布"),
        ("mainland_2020", "54", "23", "无正计数"),
        ("hong_kong_2021", "81", "01", "无正计数"),
        ("macao_2021", "81", None, "无正计数"),
    ],
)
def test_observed_sampling_rejects_unavailable_combinations(
    frame_id, region, ethnicity, error
):
    with pytest.raises(ValueError, match=error):
        sample_frame(frame_id, region=region, ethnicity=ethnicity, seed=2026)
