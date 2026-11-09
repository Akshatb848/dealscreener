"""Agent 10 — IC Memo Agent.

Composes validated agent outputs, the deterministic scorecard, simulation
results and review flags into a decision-ready Investment Committee memo.
The memo is decision-*support*: the recommendation mirrors deterministic
scoring and the final decision authority remains the human IC.
"""

from __future__ import annotations

from agent_schemas_advanced import (
    ESGGovernanceOutput,
    ICMemoOutput,
    ICMemoScorecard,
    ICMemoSimulationSummary,
    MacroTrendOutput,
    SemanticMatrixOutput,
)


class ICMemoAgent:
    name = "ic_memo_agent"

    def build(
        self,
        *,
        company_id: str,
        company_name: str,
        evaluation_id: str,
        semantic: SemanticMatrixOutput | None,
        market_summary: str,
        financial_summary: str,
        founder_summary: str,
        macro: MacroTrendOutput | None,
        esg: ESGGovernanceOutput | None,
        scoring_version: str,
        final_score: float,
        recommendation: str,
        recommendation_reason: str,
        confidence_score: float,
        sub_scores: dict[str, float] | None,
        key_risks: list[str],
        review_flags: list[str],
        simulation_summary: ICMemoSimulationSummary | None = None,
    ) -> ICMemoOutput:
        thesis = "Business thesis could not be deconstructed from source material."
        if semantic:
            parts = []
            if semantic.problem:
                parts.append(f"Problem: {semantic.problem}.")
            if semantic.product:
                parts.append(f"Product: {semantic.product}.")
            if semantic.customer:
                parts.append(f"Customer: {semantic.customer}.")
            if semantic.revenue_model:
                parts.append(f"Revenue model: {semantic.revenue_model}.")
            if semantic.thesis_dimensions:
                top = sorted(semantic.thesis_dimensions.items(), key=lambda kv: kv[1], reverse=True)[:3]
                parts.append("Strongest thesis dimensions: " + ", ".join(f"{k} ({v:.2f})" for k, v in top) + ".")
            thesis = " ".join(parts) or thesis

        macro_context = "No verified macro signals available; treat macro view as unverified."
        if macro:
            sig = macro.macro_signals + macro.technology_shifts + macro.regulatory_signals
            tag = " (UNVERIFIED — not source-backed)" if macro.unverified else ""
            macro_context = (
                ("Detected signals: " + ", ".join(sig) if sig else "No macro signals detected") + tag + "."
            )

        esg_text = "ESG/governance not assessed."
        if esg:
            esg_text = (
                f"{esg.esg_summary or ''} Governance risk score: {esg.governance_risk_score:.0f}/100. "
                f"Gaps: {', '.join(esg.governance_gaps) or 'none noted'}."
            ).strip()

        exec_summary = (
            f"{company_name}: deterministic score {final_score:.1f}/100 → recommendation '{recommendation}'. "
            f"Confidence {confidence_score:.2f}. "
            + (f"{len(review_flags)} item(s) flagged for partner review. " if review_flags else "")
            + "This memo supports, but does not replace, the IC decision."
        )

        return ICMemoOutput(
            company_id=company_id,
            company_name=company_name,
            evaluation_id=evaluation_id,
            title=f"Investment Committee Memo — {company_name}",
            executive_summary=exec_summary,
            business_thesis=thesis,
            market_and_moat=market_summary,
            financials=financial_summary,
            founder_assessment=founder_summary,
            macro_context=macro_context,
            esg_and_governance=esg_text,
            key_risks=key_risks,
            scorecard=ICMemoScorecard(
                scoring_version=scoring_version,
                final_score=final_score,
                recommendation=recommendation,
                confidence_score=confidence_score,
                sub_scores=sub_scores or {},
            ),
            simulation_summary=simulation_summary,
            review_flags=review_flags,
            recommendation=recommendation,
            recommendation_reason=recommendation_reason,
        )
