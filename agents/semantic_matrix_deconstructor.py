"""Agent 1 — Semantic Matrix Deconstructor.

Converts raw pitch material into a structured business thesis plus a
12-dimensional thesis vector. Deterministic MVP implementation grounded in the
parsed chunks; swap ``_deterministic`` for an LLM call to upgrade fidelity.
"""

from __future__ import annotations

from agent_schemas_advanced import THESIS_DIMENSIONS, SemanticMatrixOutput
from agents.heuristics import find_terms, first_chunk_evidence, lower_text, presence_score
from parser_layer import ParsedChunk

_PROBLEM = ["problem", "pain point", "challenge", "inefficiency", "broken"]
_PRODUCT = ["product", "platform", "solution", "saas", "app", "api", "software"]
_MARKET = ["market", "tam", "sam", "som", "industry", "segment", "addressable"]
_MOAT = ["moat", "defensib", "proprietary", "patent", "network effect", "switching cost", "data advantage"]
_CUSTOMER = ["customer", "client", "user", "enterprise", "b2b", "b2c", "buyer"]
_REVENUE = ["revenue", "subscription", "pricing", "monetiz", "arr", "mrr", "take rate", "commission"]
_TRACTION = ["traction", "growth", "users", "customers", "revenue", "pilot", "loi", "retention", "mau"]
_FOUNDER = ["founder", "ceo", "co-founder", "team", "ex-", "previously", "phd", "serial"]
_TIMING = ["timing", "now", "shift", "tailwind", "inflection", "regulation", "adoption"]
_COMPETITION = ["competitor", "incumbent", "alternative", "rivals", "competitive"]


class SemanticMatrixDeconstructor:
    name = "semantic_matrix_deconstructor"

    def run(self, chunks: list[ParsedChunk], *, context: dict | None = None) -> SemanticMatrixOutput:
        context = context or {}
        if not chunks:
            return SemanticMatrixOutput(
                missing_data=[
                    "problem", "product", "market", "moat", "customer",
                    "revenue_model", "traction", "founder",
                ],
                thesis_dimensions={d: 0.0 for d in THESIS_DIMENSIONS},
            )

        low = lower_text(chunks)

        def field(terms: list[str]) -> str | None:
            hits = find_terms(low, terms)
            return ", ".join(hits) if hits else None

        problem = field(_PROBLEM)
        product = field(_PRODUCT)
        market = field(_MARKET)
        moat = field(_MOAT)
        customer = field(_CUSTOMER)
        revenue_model = field(_REVENUE)
        traction = field(_TRACTION)
        founder = field(_FOUNDER)

        missing = [
            label
            for label, val in [
                ("problem", problem), ("product", product), ("market", market),
                ("moat", moat), ("customer", customer), ("revenue_model", revenue_model),
                ("traction", traction), ("founder", founder),
            ]
            if not val
        ]

        risks: list[str] = []
        if not moat:
            risks.append("No explicit moat/defensibility described in source material.")
        if not revenue_model:
            risks.append("Revenue model not articulated in source material.")
        if not traction:
            risks.append("No quantified traction evidence found.")

        # Deterministic 12-dimensional thesis vector.
        dims = {
            "problem_clarity": presence_score(low, _PROBLEM),
            "product_strength": presence_score(low, _PRODUCT),
            "market_attractiveness": presence_score(low, _MARKET),
            "moat_defensibility": presence_score(low, _MOAT),
            "customer_pull": presence_score(low, _CUSTOMER),
            "revenue_model_quality": presence_score(low, _REVENUE),
            "traction_evidence": presence_score(low, _TRACTION),
            "founder_capability": presence_score(low, _FOUNDER),
            "capital_efficiency": presence_score(low, ["efficient", "low burn", "profitable", "runway"]),
            "timing": presence_score(low, _TIMING),
            "competitive_intensity": round(1.0 - presence_score(low, _COMPETITION), 4),
            "execution_risk": round(1.0 - presence_score(low, _TRACTION + _FOUNDER), 4),
        }

        evidence = []
        anchor = first_chunk_evidence(chunks, "Business thesis deconstructed from pitch material.", terms=_PRODUCT)
        if anchor:
            evidence.append(anchor)

        return SemanticMatrixOutput(
            problem=problem, product=product, market=market, moat=moat,
            customer=customer, revenue_model=revenue_model, traction=traction,
            founder=founder, risks=risks, missing_data=missing,
            thesis_dimensions=dims, evidence=evidence,
        )
