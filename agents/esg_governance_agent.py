"""Agent 8 — ESG & Governance Agent.

Reviews ESG posture, governance gaps, compliance, board readiness, founder
policies and documentation maturity. Deterministic, evidence-grounded MVP that
produces a 0–100 governance risk score (higher = more risk).
"""

from __future__ import annotations

from agent_schemas_advanced import ESGGovernanceOutput
from agents.heuristics import find_terms, first_chunk_evidence, lower_text
from parser_layer import ParsedChunk

_ENV = ["sustainab", "carbon", "emission", "renewable", "esg", "climate", "green"]
_SOCIAL = ["diversity", "inclusion", "community", "fair", "accessib", "ethic", "wellbeing"]
_GOV_GOOD = ["board", "governance", "advisor", "independent director", "audit", "cap table", "vesting"]
_COMPLIANCE = ["gdpr", "hipaa", "soc 2", "iso 27001", "compliance", "license", "regulat", "kyc", "aml"]
_DOC_GOOD = ["data room", "audited", "incorporat", "shareholder agreement", "term sheet", "due diligence"]
_RED = ["litigation", "lawsuit", "fraud", "investigation", "conflict of interest", "related party", "sanction"]


class ESGGovernanceAgent:
    name = "esg_governance_agent"

    def run(self, chunks: list[ParsedChunk], *, context: dict | None = None) -> ESGGovernanceOutput:
        context = context or {}
        if not chunks:
            return ESGGovernanceOutput(
                esg_summary="No source material available for ESG/governance review.",
                governance_risk_score=60.0,
                missing_data=["esg_documentation", "governance_structure", "compliance_evidence"],
            )

        low = lower_text(chunks)
        env = find_terms(low, _ENV)
        social = find_terms(low, _SOCIAL)
        gov_present = find_terms(low, _GOV_GOOD)
        compliance = find_terms(low, _COMPLIANCE)
        docs = find_terms(low, _DOC_GOOD)
        red = find_terms(low, _RED)

        governance_gaps: list[str] = []
        if not gov_present:
            governance_gaps.append("No board/governance structure described.")
        if "vesting" not in low:
            governance_gaps.append("Founder vesting not mentioned.")
        if "cap table" not in low:
            governance_gaps.append("Cap table not provided.")

        missing: list[str] = []
        if not env and not social:
            missing.append("esg_policy")
        if not compliance:
            missing.append("compliance_certifications")
        if not docs:
            missing.append("data_room_documentation")

        # Risk score: start moderate, add for gaps/red flags, subtract for good signals.
        score = 40.0
        score += 8.0 * len(governance_gaps)
        score += 15.0 * len(red)
        score += 5.0 * len(missing)
        score -= 4.0 * len(gov_present)
        score -= 4.0 * len(docs)
        score -= 3.0 * len(compliance)
        score = round(max(0.0, min(100.0, score)), 2)

        board_readiness = "Board/advisory signals present" if gov_present else "No board readiness evidence"
        doc_maturity = "Documentation signals present" if docs else "Low documentation maturity"

        evidence = []
        anchor = first_chunk_evidence(chunks, "ESG/governance reviewed from source material.", terms=_GOV_GOOD + _COMPLIANCE)
        if anchor:
            evidence.append(anchor)

        return ESGGovernanceOutput(
            esg_summary=(
                f"Environmental signals: {', '.join(env) or 'none'}; "
                f"Social signals: {', '.join(social) or 'none'}."
            ),
            environmental_signals=env,
            social_signals=social,
            governance_gaps=governance_gaps,
            compliance_risks=[f"Unconfirmed: {c}" for c in compliance] if compliance else [],
            board_readiness=board_readiness,
            founder_policies=find_terms(low, ["code of conduct", "vesting", "esop", "policy"]),
            documentation_maturity=doc_maturity,
            red_flags=[f"Potential red flag: {r}" for r in red],
            governance_risk_score=score,
            missing_data=missing,
            evidence=evidence,
        )
