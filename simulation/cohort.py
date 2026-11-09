"""Synthetic Cohort Injector (Agent 6).

Generates a seeded set of synthetic competitor/startup variants from the
applicant's category, market, tech stack, funding stage, geography and GTM
assumptions. Fully reproducible: identical (assumptions, seed, size) → identical
cohort. Used as the competitive ecosystem the shock simulator stresses against.
"""

from __future__ import annotations

import random
from typing import Any


class SyntheticCohortInjector:
    name = "synthetic_cohort_injector"

    def generate(
        self,
        *,
        assumptions: dict[str, Any],
        seed: int,
        size: int,
    ) -> dict[str, Any]:
        rng = random.Random(seed)
        category = assumptions.get("category") or "unspecified"
        geography = assumptions.get("geography") or "global"
        stage = assumptions.get("funding_stage") or "seed"
        tech_stack = assumptions.get("tech_stack") or "generic"
        gtm = assumptions.get("gtm_model") or "unspecified"
        base_moat = float(assumptions.get("moat_strength", 0.5))
        base_capital = float(assumptions.get("capital_strength", 0.5))

        archetypes = ["fast-follower", "incumbent-funded", "lean-bootstrapped",
                      "well-capitalised", "regional-specialist", "platform-native"]

        members: list[dict[str, Any]] = []
        for i in range(max(0, size)):
            arche = archetypes[rng.randrange(len(archetypes))]
            members.append({
                "id": f"synthetic-{seed}-{i:03d}",
                "archetype": arche,
                "category": category,
                "geography": geography if rng.random() > 0.3 else "adjacent-region",
                "funding_stage": stage,
                "tech_stack": tech_stack,
                "gtm_model": gtm,
                # Perturbed competitive attributes, clamped to [0,1].
                "relative_moat": round(min(1.0, max(0.0, base_moat + rng.uniform(-0.3, 0.3))), 4),
                "capital_strength": round(min(1.0, max(0.0, base_capital + rng.uniform(-0.35, 0.35))), 4),
                "aggressiveness": round(rng.uniform(0.2, 1.0), 4),
                "growth_rate": round(rng.uniform(0.1, 2.5), 4),
            })

        return {
            "seed": seed,
            "size": len(members),
            "category": category,
            "assumptions": {
                "category": category, "geography": geography, "funding_stage": stage,
                "tech_stack": tech_stack, "gtm_model": gtm,
                "moat_strength": base_moat, "capital_strength": base_capital,
            },
            "members": members,
        }
