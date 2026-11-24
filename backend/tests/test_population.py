from collections import Counter
from datetime import date

import pytest

from app.population import sample_population


def sample(seed=7, **overrides):
    options = {
        "birth_year": 1936,
        "as_of": date(2026, 1, 1),
        "entry": "birth_cohort",
        "life_status": "auto",
        "seed": seed,
    }
    return sample_population(**{**options, **overrides})


def test_birth_cohort_draws_mortality_and_is_stable_across_later_cutoff():
    cohort = [sample(seed) for seed in range(5_000)]
    survivors = [person for person in cohort if person["life_status"] == "living"]

    # The weighted survival curve at age 90 is approximately 0.322.
    assert 0.30 < len(survivors) / len(cohort) < 0.35
    assert all(person["death_date"] is None for person in survivors)
    for seed, person in enumerate(cohort[:100]):
        if person["death_date"] is not None:
            assert (
                person["death_date"]
                == sample(seed, as_of=date(2046, 1, 1))["death_date"]
            )
    assert sample() == sample()


def test_survivor_entry_draws_the_latent_risk_posterior():
    cohort = [sample(seed, entry="living_at_as_of") for seed in range(5_000)]
    classes = Counter(person["sampling"]["risk_class"] for person in cohort)

    assert all(person["life_status"] == "living" for person in cohort)
    assert all(person["death_date"] is None for person in cohort)
    # The birth prior is 0.25/0.55/0.20. Multiplying each term by its
    # survival probability and normalizing gives roughly 0.369/0.546/0.085.
    assert classes["lower"] / len(cohort) == pytest.approx(0.369, abs=0.025)
    assert classes["middle"] / len(cohort) == pytest.approx(0.546, abs=0.025)
    assert classes["higher"] / len(cohort) == pytest.approx(0.085, abs=0.015)
    evidence = cohort[0]["sampling"]
    assert evidence["conditioning"] == "survived_through_as_of"
    assert evidence["synthetic"] is True
    assert evidence["analysis_weight"] is None


def test_death_conditioning_samples_within_interval_without_cutoff_spike():
    cutoff = date(2026, 1, 1)
    cohort = [
        sample(seed, birth_year=2025, life_status="deceased") for seed in range(3_000)
    ]
    dates = [date.fromisoformat(person["death_date"]) for person in cohort]

    assert all(date(2025, 1, 1) <= value <= cutoff for value in dates)
    assert 0.47 < sum(value < date(2025, 7, 1) for value in dates) / len(dates) < 0.53
    assert len(set(dates)) > 350
    assert dates.count(cutoff) < 15
    assert cohort[0]["sampling"]["conditioning"] == "died_by_as_of"


def test_omitted_birth_year_is_weighted_by_frame_eligibility():
    birth = [sample(seed, birth_year=None) for seed in range(1_500)]
    living = [
        sample(seed, birth_year=None, entry="living_at_as_of") for seed in range(1_500)
    ]
    deceased = sample(birth_year=None, life_status="deceased")

    assert sum(person["birth_year"] for person in living) / len(living) > 1982
    assert sum(person["birth_year"] for person in birth) / len(birth) == pytest.approx(
        1976, abs=2
    )
    assert living[0]["sampling"]["birth_year"] == {
        "source": "sampled",
        "range": [1926, 2026],
    }
    assert deceased["sampling"]["birth_year"]["range"] == [1926, 1968]
    assert living[0]["sampling"]["frame_inclusion_probability"] < 1


def test_death_on_first_inclusive_observation_day():
    person = sample(birth_year=2026, life_status="deceased")
    assert person["death_date"] == "2026-01-01"


def test_very_late_survivor_retains_finite_log_probability():
    person = sample(birth_year=1880, as_of=date.max, entry="living_at_as_of")
    assert person["sampling"]["risk_class"] == "lower"
    assert person["sampling"]["frame_log_inclusion_probability"] < -2_000
    assert person["life_status"] == "living"


def test_survivor_entry_rejects_deceased_filter():
    with pytest.raises(ValueError, match="living_at_as_of"):
        sample(entry="living_at_as_of", life_status="deceased")
