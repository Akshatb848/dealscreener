"""Deterministic scoring configuration and logic."""

from __future__ import annotations

import os
from dataclasses import dataclass

SCORING_VERSION = os.environ.get("SCORING_VERSION", "1.0.0")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# ILLUSTRATIVE DEFAULTS. These numbers are neutral examples chosen for the public
# version of this project; they are NOT any fund's real investment criteria.
# Every value can be overridden with an environment variable of the same name
# prefixed with ``SCORING_`` (e.g. SCORING_THRESHOLD_YES=80).
# ---------------------------------------------------------------------------

# Component weights
BASE_SCORE_DEFAULT = _env_float("SCORING_BASE_SCORE_DEFAULT", 70.0)
CONFIDENCE_WEIGHT_MIN = _env_float("SCORING_CONFIDENCE_WEIGHT_MIN", 0.5)
CONFIDENCE_WEIGHT_MAX = _env_float("SCORING_CONFIDENCE_WEIGHT_MAX", 1.0)
MISSING_VAR_PENALTY = _env_float("SCORING_MISSING_VAR_PENALTY", 3.0)
RED_FLAG_PENALTY_EACH = _env_float("SCORING_RED_FLAG_PENALTY_EACH", 8.0)
EVIDENCE_QUALITY_WEIGHT = _env_float("SCORING_EVIDENCE_QUALITY_WEIGHT", 0.15)

# Recommendation thresholds (higher score = better)
THRESHOLD_YES = _env_float("SCORING_THRESHOLD_YES", 75.0)
THRESHOLD_MAYBE = _env_float("SCORING_THRESHOLD_MAYBE", 50.0)
THRESHOLD_NEEDS_REVIEW = _env_float("SCORING_THRESHOLD_NEEDS_REVIEW", 30.0)


@dataclass
class ScoringBreakdown:
    base_score: float
    confidence_weight: float
    missing_data_penalty: float
    red_flag_penalty: float
    evidence_quality_score: float
    first_call_probability: float
    final_score: float
    recommendation: str
    recommendation_reason: str


def map_recommendation(final_score: float) -> tuple[str, str]:
    if final_score >= THRESHOLD_YES:
        return "Yes", f"Final score {final_score:.1f} >= {THRESHOLD_YES}"
    if final_score >= THRESHOLD_MAYBE:
        return "Maybe", f"Final score {final_score:.1f} in Maybe range"
    if final_score >= THRESHOLD_NEEDS_REVIEW:
        return "Needs Review", f"Final score {final_score:.1f} requires human review"
    return "No", f"Final score {final_score:.1f} < {THRESHOLD_NEEDS_REVIEW}"


def compute_score(
    *,
    base_score: float | None = None,
    confidence_score: float = 0.8,
    missing_variables: list[str] | None = None,
    red_flags: list[str] | None = None,
    evidence_quality_score: float | None = None,
) -> ScoringBreakdown:
    """
    Final Score = (Base Score × Confidence Weight) - Missing Data Penalty - Red Flag Penalty

    Recommendation is derived ONLY from this formula, never from LLM text.
    """
    missing_variables = missing_variables or []
    red_flags = red_flags or []

    base = base_score if base_score is not None else BASE_SCORE_DEFAULT
    conf = max(CONFIDENCE_WEIGHT_MIN, min(CONFIDENCE_WEIGHT_MAX, confidence_score))
    missing_penalty = len(missing_variables) * MISSING_VAR_PENALTY
    red_penalty = len(red_flags) * RED_FLAG_PENALTY_EACH

    if evidence_quality_score is None:
        evidence_quality_score = conf

    weighted_base = base * conf
    final = max(0.0, min(100.0, weighted_base - missing_penalty - red_penalty))

    # first_call_probability: monotonic mapping from final score
    first_call_probability = final / 100.0

    recommendation, reason = map_recommendation(final)

    return ScoringBreakdown(
        base_score=base,
        confidence_weight=conf,
        missing_data_penalty=missing_penalty,
        red_flag_penalty=red_penalty,
        evidence_quality_score=evidence_quality_score,
        first_call_probability=first_call_probability,
        final_score=final,
        recommendation=recommendation,
        recommendation_reason=reason,
    )
