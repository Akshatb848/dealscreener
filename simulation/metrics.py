"""Resilience metric aggregation for the stochastic shock simulator."""

from __future__ import annotations


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def weighted_mean(pairs: list[tuple[float, float]]) -> float:
    """pairs = list of (value, weight)."""
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return 0.0
    return sum(v * w for v, w in pairs) / total_w


def build_risk_narrative(
    *,
    resilience_score: float,
    failure_probability: float,
    weakest: list[tuple[str, float]],
    moat_sensitivity: float,
) -> str:
    band = (
        "robust" if resilience_score >= 70
        else "moderate" if resilience_score >= 50
        else "fragile" if resilience_score >= 35
        else "high-risk"
    )
    weak_txt = "; ".join(f"{label} (survival {rate*100:.0f}%)" for label, rate in weakest) or "none"
    return (
        f"Simulated resilience is {band} ({resilience_score:.0f}/100) with an estimated "
        f"failure probability of {failure_probability*100:.0f}% across stressed scenarios. "
        f"Moat sensitivity is {moat_sensitivity*100:.0f}% (drop attributable to competitive/moat shocks). "
        f"Weakest scenarios: {weak_txt}. Results are seed-reproducible decision support, "
        f"not a prediction; partner judgment governs the decision."
    )
