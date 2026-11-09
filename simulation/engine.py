"""Stochastic Shock Simulator (Agent 7).

Runs seeded Monte-Carlo stress tests of a deal's resilience profile against the
scenario catalogue and (optionally) a synthetic competitor cohort, producing
reproducible resilience metrics, a per-scenario survival matrix and an
IC-level risk narrative.
"""

from __future__ import annotations

import os
import random
from typing import Any

from pydantic import BaseModel, Field

from simulation.metrics import build_risk_narrative, clamp01, weighted_mean
from simulation.scenarios import SCENARIOS, DealResilienceProfile

ENGINE_VERSION = os.environ.get("SIMULATION_ENGINE_VERSION", "1.0.0")


class ScenarioOutcome(BaseModel):
    scenario_key: str
    scenario_label: str
    severity: str
    survival_rate: float
    mean_shock: float
    parameters: dict[str, Any] = Field(default_factory=dict)


class SimulationResult(BaseModel):
    engine_version: str
    seed: int
    iterations: int
    horizon_years: int
    resilience_score: float          # 0–100
    failure_probability: float       # 0–1
    moat_sensitivity: float          # 0–1
    founder_adaptability_index: float  # 0–1
    capital_efficiency_under_stress: float  # 0–1
    scenario_outcomes: list[ScenarioOutcome] = Field(default_factory=list)
    weakest_scenarios: list[str] = Field(default_factory=list)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    risk_narrative: str = ""

    def survival_matrix(self) -> dict[str, float]:
        return {o.scenario_key: o.survival_rate for o in self.scenario_outcomes}


class StochasticShockSimulator:
    name = "stochastic_shock_simulator"

    def run(
        self,
        *,
        profile: DealResilienceProfile,
        seed: int,
        iterations: int,
        horizon_years: int = 5,
        cohort: dict[str, Any] | None = None,
    ) -> SimulationResult:
        iterations = max(1, iterations)
        rng = random.Random(seed)

        # Competitive pressure from the synthetic cohort (raises effective shocks).
        comp_pressure = 0.0
        if cohort and cohort.get("members"):
            members = cohort["members"]
            comp_pressure = sum(
                0.5 * m.get("aggressiveness", 0.5) + 0.5 * m.get("capital_strength", 0.5)
                for m in members
            ) / len(members)

        # Founder adaptability gives a small global resilience bonus.
        adapt_bonus = 0.12 * (profile.founder_adaptability - 0.5)

        outcomes: list[ScenarioOutcome] = []
        for sc in SCENARIOS:
            survivors = 0
            shocks_sum = 0.0
            capacity = sum(profile.axis(a) for a in sc.stressed_axes) / len(sc.stressed_axes)
            extra = comp_pressure * 0.25 if "moat" in sc.tags else 0.0
            for _ in range(iterations):
                lo, hi = max(0.0, sc.intensity - 0.25), min(1.0, sc.intensity + 0.25)
                shock = rng.triangular(lo, hi, sc.intensity) + extra
                shocks_sum += shock
                effective_capacity = clamp01(capacity + adapt_bonus + rng.gauss(0.0, 0.08))
                if effective_capacity >= shock:
                    survivors += 1
            survival = survivors / iterations
            outcomes.append(ScenarioOutcome(
                scenario_key=sc.key, scenario_label=sc.label, severity=sc.severity,
                survival_rate=round(survival, 4), mean_shock=round(shocks_sum / iterations, 4),
                parameters={"stressed_axes": list(sc.stressed_axes), "intensity": sc.intensity,
                            "weight": sc.weight, "competitive_pressure": round(comp_pressure, 4)},
            ))

        resilience_score = round(
            100.0 * weighted_mean([(o.survival_rate, _w(o.scenario_key)) for o in outcomes]), 2
        )
        failure_probability = round(1.0 - (sum(o.survival_rate for o in outcomes) / len(outcomes)), 4)

        moat_keys = {s.key for s in SCENARIOS if "moat" in s.tags}
        capital_keys = {s.key for s in SCENARIOS if "capital" in s.tags}
        founder_keys = {"founder_conflict", "delayed_follow_on"}

        baseline = sum(o.survival_rate for o in outcomes) / len(outcomes)
        moat_survival = _avg([o.survival_rate for o in outcomes if o.scenario_key in moat_keys])
        moat_sensitivity = round(clamp01(baseline - moat_survival + 0.5 * (1 - moat_survival)), 4)
        founder_adaptability_index = round(
            _avg([o.survival_rate for o in outcomes if o.scenario_key in founder_keys]), 4
        )
        capital_efficiency_under_stress = round(
            _avg([o.survival_rate for o in outcomes if o.scenario_key in capital_keys]), 4
        )

        weakest = sorted(outcomes, key=lambda o: o.survival_rate)[:3]
        narrative = build_risk_narrative(
            resilience_score=resilience_score,
            failure_probability=failure_probability,
            weakest=[(o.scenario_label, o.survival_rate) for o in weakest],
            moat_sensitivity=moat_sensitivity,
        )

        return SimulationResult(
            engine_version=ENGINE_VERSION,
            seed=seed,
            iterations=iterations,
            horizon_years=horizon_years,
            resilience_score=resilience_score,
            failure_probability=failure_probability,
            moat_sensitivity=moat_sensitivity,
            founder_adaptability_index=founder_adaptability_index,
            capital_efficiency_under_stress=capital_efficiency_under_stress,
            scenario_outcomes=outcomes,
            weakest_scenarios=[o.scenario_key for o in weakest],
            assumptions={
                "profile": profile.model_dump(),
                "competitive_pressure": round(comp_pressure, 4),
                "cohort_size": len(cohort["members"]) if cohort and cohort.get("members") else 0,
            },
            risk_narrative=narrative,
        )


def _w(key: str) -> float:
    from simulation.scenarios import SCENARIO_BY_KEY
    return SCENARIO_BY_KEY[key].weight


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
