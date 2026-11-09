"""Agent 9 — Portfolio Monitoring Agent.

Tracks portfolio-update quality, runway risk, growth performance, governance
issues, follow-on readiness and exit-readiness signals. Activated for portfolio
companies (context flag or a recognised portfolio update source).
"""

from __future__ import annotations

from agent_schemas_advanced import PortfolioMonitoringOutput
from agents.heuristics import find_terms, lower_text
from parser_layer import ParsedChunk

_RUNWAY_BAD = ["short runway", "running out", "bridge", "emergency", "less than 6 months", "default risk"]
_GROWTH_GOOD = ["growth", "expansion", "record quarter", "up ", "increase", "scaling", "new customers"]
_GROWTH_BAD = ["decline", "churn", "missed", "down ", "flat", "slowdown"]
_GOV_BAD = ["resignation", "departure", "dispute", "audit issue", "restated", "co-founder left"]
_FOLLOW_ON = ["series", "raising", "follow-on", "next round", "term sheet", "lead investor"]
_EXIT = ["acquisition", "exit", "ipo", "strategic interest", "m&a", "secondary"]


class PortfolioMonitoringAgent:
    name = "portfolio_monitoring_agent"

    def run(self, chunks: list[ParsedChunk], *, context: dict | None = None) -> PortfolioMonitoringOutput:
        context = context or {}
        is_portfolio = bool(context.get("is_portfolio_company")) or context.get("source") == "portfolio_update"

        if not chunks:
            return PortfolioMonitoringOutput(
                is_portfolio_company=is_portfolio,
                missing_data=["portfolio_update_document"],
            )

        low = lower_text(chunks)
        runway_bad = find_terms(low, _RUNWAY_BAD)
        growth_good = find_terms(low, _GROWTH_GOOD)
        growth_bad = find_terms(low, _GROWTH_BAD)
        gov_bad = find_terms(low, _GOV_BAD)

        runway_risk = "high" if runway_bad else ("medium" if "runway" not in low else "low")
        if growth_bad and not growth_good:
            growth_trend = "declining"
        elif growth_good and not growth_bad:
            growth_trend = "growing"
        elif growth_good and growth_bad:
            growth_trend = "mixed"
        else:
            growth_trend = "unknown"

        governance_risk = "high" if gov_bad else "low"
        red_flags = (
            [f"Runway concern: {t}" for t in runway_bad]
            + [f"Governance concern: {t}" for t in gov_bad]
        )

        missing: list[str] = []
        if "runway" not in low and "burn" not in low:
            missing.append("runway_or_burn_metrics")
        if not find_terms(low, _GROWTH_GOOD + _GROWTH_BAD):
            missing.append("growth_metrics")

        return PortfolioMonitoringOutput(
            is_portfolio_company=is_portfolio,
            update_quality="structured" if len(chunks) > 1 else "thin",
            runway_risk=runway_risk,  # type: ignore[arg-type]
            growth_trend=growth_trend,
            governance_risk=governance_risk,  # type: ignore[arg-type]
            follow_on_readiness="signalled" if find_terms(low, _FOLLOW_ON) else "not signalled",
            exit_readiness="signalled" if find_terms(low, _EXIT) else "not signalled",
            red_flags=red_flags,
            missing_data=missing,
        )
