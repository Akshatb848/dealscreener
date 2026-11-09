"""Tests for the synthetic cohort + stochastic shock simulation layer.

Covers seed reproducibility, all ten scenarios, metric ranges, and that a
different seed yields different (but still bounded) results.
"""

from __future__ import annotations

from simulation import (
    SCENARIOS,
    DealResilienceProfile,
    StochasticShockSimulator,
    SyntheticCohortInjector,
)

ASSUMPTIONS = {
    "category": "fintech", "geography": "India", "funding_stage": "seed",
    "tech_stack": "cloud/AI", "gtm_model": "B2B SaaS",
    "moat_strength": 0.6, "capital_strength": 0.5,
}


def _profile():
    return DealResilienceProfile(
        moat=0.6, capital_buffer=0.4, capital_efficiency=0.5, founder_adaptability=0.7,
        market_demand=0.6, platform_independence=0.5, regulatory_readiness=0.5,
        sales_motion_strength=0.5,
    )


class TestCohort:
    def test_reproducible(self):
        a = SyntheticCohortInjector().generate(assumptions=ASSUMPTIONS, seed=1337, size=25)
        b = SyntheticCohortInjector().generate(assumptions=ASSUMPTIONS, seed=1337, size=25)
        assert a == b
        assert a["size"] == 25
        assert all(0.0 <= m["relative_moat"] <= 1.0 for m in a["members"])

    def test_seed_changes_cohort(self):
        a = SyntheticCohortInjector().generate(assumptions=ASSUMPTIONS, seed=1, size=10)
        b = SyntheticCohortInjector().generate(assumptions=ASSUMPTIONS, seed=2, size=10)
        assert a["members"] != b["members"]


class TestSimulation:
    def test_all_ten_scenarios(self):
        cohort = SyntheticCohortInjector().generate(assumptions=ASSUMPTIONS, seed=1337, size=25)
        res = StochasticShockSimulator().run(profile=_profile(), seed=1337, iterations=200, cohort=cohort)
        assert len(res.scenario_outcomes) == len(SCENARIOS) == 10
        keys = {o.scenario_key for o in res.scenario_outcomes}
        assert "funding_winter" in keys and "founder_conflict" in keys

    def test_reproducible_with_seed(self):
        cohort = SyntheticCohortInjector().generate(assumptions=ASSUMPTIONS, seed=1337, size=25)
        r1 = StochasticShockSimulator().run(profile=_profile(), seed=1337, iterations=200, cohort=cohort)
        r2 = StochasticShockSimulator().run(profile=_profile(), seed=1337, iterations=200, cohort=cohort)
        assert r1.model_dump() == r2.model_dump()

    def test_metric_ranges(self):
        res = StochasticShockSimulator().run(profile=_profile(), seed=7, iterations=150)
        assert 0.0 <= res.resilience_score <= 100.0
        assert 0.0 <= res.failure_probability <= 1.0
        assert 0.0 <= res.moat_sensitivity <= 1.0
        assert 0.0 <= res.founder_adaptability_index <= 1.0
        assert 0.0 <= res.capital_efficiency_under_stress <= 1.0
        assert res.risk_narrative
        assert len(res.weakest_scenarios) == 3

    def test_robust_profile_scores_higher(self):
        weak = DealResilienceProfile(moat=0.1, capital_buffer=0.1, capital_efficiency=0.1,
                                     founder_adaptability=0.1, market_demand=0.1,
                                     platform_independence=0.1, regulatory_readiness=0.1,
                                     sales_motion_strength=0.1)
        strong = DealResilienceProfile(moat=0.95, capital_buffer=0.95, capital_efficiency=0.95,
                                       founder_adaptability=0.95, market_demand=0.95,
                                       platform_independence=0.95, regulatory_readiness=0.95,
                                       sales_motion_strength=0.95)
        sim = StochasticShockSimulator()
        rs = sim.run(profile=strong, seed=1337, iterations=200)
        rw = sim.run(profile=weak, seed=1337, iterations=200)
        assert rs.resilience_score > rw.resilience_score
