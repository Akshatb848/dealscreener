"""Synthetic cohort + stochastic shock simulation layer.

Deterministic and seed-reproducible: identical (profile, seed, iterations,
horizon) inputs always yield identical outputs. Seeds and assumptions are
persisted (SimulationRun / SyntheticCohort) for full auditability.
"""

from simulation.cohort import SyntheticCohortInjector
from simulation.engine import SimulationResult, StochasticShockSimulator
from simulation.scenarios import SCENARIOS, DealResilienceProfile, Scenario

__all__ = [
    "SyntheticCohortInjector",
    "StochasticShockSimulator",
    "SimulationResult",
    "SCENARIOS",
    "Scenario",
    "DealResilienceProfile",
]
