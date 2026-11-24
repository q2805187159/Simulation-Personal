"""Versioned synthetic population entry for the single-person POC frame."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
import math
import random
from typing import Any


POPULATION_MODEL_VERSION = "synthetic-population/1"
_DAYS_PER_YEAR = 365.2425
# Illustrative annual hazards bracketed by attained age. All strata use this
# curve. Its rates and the latent mixture are synthetic parameter choices.
_AGE_BANDS = (
    (1, 0.016),
    (15, 0.00035),
    (40, 0.00065),
    (55, 0.0025),
    (65, 0.006),
    (75, 0.015),
    (85, 0.04),
    (95, 0.09),
    (105, 0.18),
    (115, 0.30),
    (math.inf, 0.50),
)
# Name, probability at birth, and mortality hazard multiplier.
_RISK_CLASSES = (
    ("lower", 0.25, 0.65),
    ("middle", 0.55, 1.0),
    ("higher", 0.20, 1.75),
)
_SURVIVOR = "survived_through_as_of"
_DECEASED = "died_by_as_of"
_UNCONDITIONAL = "unconditional"


def _uniform(seed: int, address: str) -> float:
    return random.Random(f"{POPULATION_MODEL_VERSION}:{seed}:{address}").random()


def _cumulative_hazard(age: float) -> float:
    hazard = 0.0
    start = 0.0
    for end, annual_rate in _AGE_BANDS:
        hazard += (min(age, end) - start) * annual_rate
        if age <= end:
            return hazard
        start = end
    raise AssertionError("mortality age bands must cover every age")


def _age_at_hazard(hazard: float) -> float:
    start = 0.0
    for end, annual_rate in _AGE_BANDS:
        band_hazard = (end - start) * annual_rate
        if hazard <= band_hazard:
            return start + hazard / annual_rate
        hazard -= band_hazard
        start = end
    raise AssertionError("mortality age bands must cover every hazard")


def _end_hazard(birth_year: int, as_of: date) -> float:
    # A cohort has year precision. January 1 is the simulation origin, and the
    # end of the full as_of day is the exclusive continuous-time boundary.
    days = (as_of - date(birth_year, 1, 1)).days + 1
    return _cumulative_hazard(days / _DAYS_PER_YEAR)


def _class_log_weights(hazard: float, conditioning: str) -> tuple[float, ...]:
    if conditioning == _SURVIVOR:
        return tuple(
            math.log(prior) - hazard * scale for _, prior, scale in _RISK_CLASSES
        )
    if conditioning == _DECEASED:
        return tuple(
            math.log(prior) + math.log(-math.expm1(-hazard * scale))
            for _, prior, scale in _RISK_CLASSES
        )
    return tuple(math.log(prior) for _, prior, _ in _RISK_CLASSES)


def _log_total(log_weights: tuple[float, ...]) -> float:
    peak = max(log_weights)
    return peak + math.log(math.fsum(math.exp(value - peak) for value in log_weights))


def _draw_index(log_weights: tuple[float, ...], uniform: float) -> tuple[int, float]:
    log_total = _log_total(log_weights)
    remaining = uniform
    for index, weight in enumerate(log_weights):
        probability = math.exp(weight - log_total)
        remaining -= probability
        if remaining < 0 or index == len(log_weights) - 1:
            return index, probability
    raise AssertionError("a population draw requires at least one candidate")


def _inclusion_log_probability(hazard: float, conditioning: str) -> float:
    if conditioning == _UNCONDITIONAL:
        return 0.0
    return min(0.0, _log_total(_class_log_weights(hazard, conditioning)))


@lru_cache(maxsize=128)
def _birth_frame(as_of: date, conditioning: str) -> tuple[int, tuple[float, ...]]:
    # The automatic deceased scenario retains the POC's adult-cohort range.
    # The death date can fall anywhere in the selected cohort's lifespan.
    earliest = max(1880, as_of.year - 100)
    latest = min(2100, as_of.year - (58 if conditioning == _DECEASED else 0))
    if earliest > latest:
        raise ValueError("当前截止日期下没有适用的自动出生年份，请填写出生年份。")
    return earliest, tuple(
        _inclusion_log_probability(_end_hazard(year, as_of), conditioning)
        for year in range(earliest, latest + 1)
    )


def sample_population(
    *,
    birth_year: int | None,
    as_of: date,
    entry: str,
    life_status: str | None,
    seed: int,
) -> dict[str, Any]:
    """Draw one life from the requested synthetic birth or survivor frame."""
    if entry not in {"birth_cohort", "living_at_as_of"}:
        raise ValueError("entry is invalid")
    if life_status not in {None, "auto", "living", "deceased"}:
        raise ValueError("life_status is invalid")
    if entry == "living_at_as_of" and life_status == "deceased":
        raise ValueError("living_at_as_of requires a living person")

    conditioning = _UNCONDITIONAL
    if entry == "living_at_as_of" or life_status == "living":
        conditioning = _SURVIVOR
    elif life_status == "deceased":
        conditioning = _DECEASED

    birth_selection: dict[str, Any] = {"source": "provided"}
    birth_probability = 1.0
    if birth_year is None:
        earliest, log_weights = _birth_frame(as_of, conditioning)
        # Use the original uniform draw as the quantile's year bucket, with a
        # fractional draw for the conditional distribution's weighted years.
        birth_rng = random.Random(f"{seed}:birth_year")
        count = len(log_weights)
        quantile = (birth_rng.randrange(count) + birth_rng.random()) / count
        index, birth_probability = _draw_index(log_weights, quantile)
        birth_year = earliest + index
        birth_selection = {
            "source": "sampled",
            "range": [earliest, earliest + len(log_weights) - 1],
        }
    elif type(birth_year) is int and 1880 <= birth_year <= min(as_of.year, 2100):
        log_weights = (
            _inclusion_log_probability(_end_hazard(birth_year, as_of), conditioning),
        )
    else:
        raise ValueError(
            "birth_year must be an integer between 1880 and the cutoff year (maximum 2100)"
        )
    log_frame_probability = min(
        0.0, _log_total(log_weights) - math.log(len(log_weights))
    )

    hazard = _end_hazard(birth_year, as_of)
    risk_index, risk_probability = _draw_index(
        _class_log_weights(hazard, conditioning), _uniform(seed, "birth_risk_class")
    )
    risk_class, _, risk_multiplier = _RISK_CLASSES[risk_index]
    lifetime_uniform = _uniform(seed, "lifetime")
    censor_hazard = hazard * risk_multiplier
    if conditioning == _DECEASED:
        # Invert the truncated event CDF directly, with expm1 for short windows.
        death_hazard = -math.log1p(lifetime_uniform * math.expm1(-censor_hazard))
    elif conditioning == _SURVIVOR:
        death_hazard = censor_hazard - math.log1p(-lifetime_uniform)
    else:
        death_hazard = -math.log1p(-lifetime_uniform)

    death = None
    if conditioning == _DECEASED or death_hazard < censor_hazard:
        days = int(_age_at_hazard(death_hazard / risk_multiplier) * _DAYS_PER_YEAR)
        birth_origin = date(birth_year, 1, 1)
        # Guard the inclusive day boundary against floating-point conversion.
        death = birth_origin + timedelta(days=min(days, (as_of - birth_origin).days))

    return {
        "birth_year": birth_year,
        "life_status": "deceased" if death else "living",
        "death_date": death.isoformat() if death else None,
        "sampling": {
            "model_version": POPULATION_MODEL_VERSION,
            "synthetic": True,
            "frame": entry,
            "conditioning": conditioning,
            "birth_year": birth_selection,
            "birth_year_selection_probability": birth_probability,
            "frame_birth_year_count": len(log_weights),
            "frame_population_size": None,
            "frame_inclusion_probability": math.exp(log_frame_probability),
            "frame_log_inclusion_probability": log_frame_probability,
            "cohort_survival_probability": math.exp(
                _inclusion_log_probability(hazard, _SURVIVOR)
            ),
            "risk_class": risk_class,
            "risk_class_selection_probability": risk_probability,
            "right_censored": death is None,
            "analysis_weight": None,
        },
    }


def extend_population(spec: dict, anchor: date, through: date) -> dict:
    """Continue the original draw with its risk class and observation anchor."""
    birth_year = spec["birth_year"]
    sampling = spec.get("population")
    if not sampling:
        sampling = sample_population(
            birth_year=birth_year,
            as_of=anchor,
            entry=spec["entry"],
            life_status="living",
            seed=spec["seed"],
        )["sampling"]
    risk_multiplier = next(
        rate for name, _, rate in _RISK_CLASSES if name == sampling["risk_class"]
    )
    death_hazard = -math.log1p(-_uniform(spec["seed"], "lifetime"))
    if sampling["conditioning"] == _SURVIVOR:
        death_hazard += _end_hazard(birth_year, anchor) * risk_multiplier
    death = None
    if death_hazard < _end_hazard(birth_year, through) * risk_multiplier:
        days = int(_age_at_hazard(death_hazard / risk_multiplier) * _DAYS_PER_YEAR)
        origin = date(birth_year, 1, 1)
        death = origin + timedelta(days=min(days, (through - origin).days))
    return {
        **spec,
        "as_of": through.isoformat(),
        "life_status": "deceased" if death else "living",
        "death_date": death.isoformat() if death else None,
        "population": {**sampling, "right_censored": death is None},
    }
