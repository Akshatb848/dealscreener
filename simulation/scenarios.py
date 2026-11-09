"""Stress-test scenario catalogue and the deal resilience profile.

A ``DealResilienceProfile`` captures the startup's capacity (0.0–1.0) along the
axes that scenarios stress. Each ``Scenario`` names the axes it attacks, an
intensity (mean shock magnitude), a severity and a weight used in aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class DealResilienceProfile(BaseModel):
    """Normalised capacity vector (0=fragile, 1=robust) per resilience axis."""

    moat: float = Field(default=0.5, ge=0.0, le=1.0)
    capital_buffer: float = Field(default=0.5, ge=0.0, le=1.0)        # runway / cash
    capital_efficiency: float = Field(default=0.5, ge=0.0, le=1.0)
    founder_adaptability: float = Field(default=0.5, ge=0.0, le=1.0)
    market_demand: float = Field(default=0.5, ge=0.0, le=1.0)
    platform_independence: float = Field(default=0.5, ge=0.0, le=1.0)
    regulatory_readiness: float = Field(default=0.5, ge=0.0, le=1.0)
    sales_motion_strength: float = Field(default=0.5, ge=0.0, le=1.0)

    def axis(self, name: str) -> float:
        return float(getattr(self, name, 0.5))


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    severity: str                  # low | medium | high | critical
    stressed_axes: tuple[str, ...]
    intensity: float               # mean shock magnitude 0.0–1.0
    weight: float = 1.0
    tags: tuple[str, ...] = field(default_factory=tuple)


# The ten required stress scenarios.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("funding_winter", "Funding winter / capital markets freeze", "high",
             ("capital_buffer", "capital_efficiency"), 0.65, 1.4, ("capital",)),
    Scenario("competitor_mega_round", "Larger competitor raises a mega round", "high",
             ("moat", "market_demand"), 0.55, 1.2, ("moat",)),
    Scenario("cac_increase", "CAC increase / paid channel inflation", "medium",
             ("capital_efficiency", "sales_motion_strength"), 0.5, 1.0, ("capital",)),
    Scenario("churn_spike", "Churn spike / retention deterioration", "high",
             ("market_demand", "moat"), 0.55, 1.2, ("moat",)),
    Scenario("margin_compression", "Gross-margin compression", "medium",
             ("capital_efficiency",), 0.5, 1.0, ("capital",)),
    Scenario("platform_dependency_failure", "API / platform dependency failure", "high",
             ("platform_independence",), 0.6, 1.1, ()),
    Scenario("regulatory_delay", "Regulatory delay / new compliance burden", "medium",
             ("regulatory_readiness",), 0.5, 1.0, ()),
    Scenario("sales_cycle_extension", "Enterprise sales-cycle extension", "medium",
             ("sales_motion_strength", "capital_buffer"), 0.45, 1.0, ()),
    Scenario("founder_conflict", "Founder conflict / key-person departure", "critical",
             ("founder_adaptability",), 0.6, 1.3, ()),
    Scenario("delayed_follow_on", "Delayed follow-on funding", "high",
             ("capital_buffer", "founder_adaptability"), 0.6, 1.2, ("capital",)),
)

SCENARIO_BY_KEY = {s.key: s for s in SCENARIOS}
