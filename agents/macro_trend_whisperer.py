"""Agent 5 — Macro Trend Whisperer.

Detects macro / regulatory / technology / supply-chain / policy signals and
"silent competitors". In the MVP there is no live external feed, so outputs are
source-aware and **clearly marked unverified**. Real connectors (news, patents,
regulatory feeds) plug in at ``_external_signals``.
"""

from __future__ import annotations

from agent_schemas_advanced import MacroTrendOutput
from agents.heuristics import find_terms, lower_text
from parser_layer import ParsedChunk

_MACRO = ["inflation", "interest rate", "recession", "funding winter", "macro", "downturn", "gdp"]
_REG = ["regulation", "compliance", "gdpr", "hipaa", "sec", "rbi", "license", "policy", "data protection"]
_TECH = ["ai", "llm", "genai", "blockchain", "ev", "iot", "cloud", "automation", "ml", "quantum"]
_TAILWIND = ["growing", "tailwind", "adoption", "demand", "shift to", "rising", "boom"]
_HEADWIND = ["headwind", "slowdown", "saturated", "declining", "pressure", "contraction"]
_SUPPLY = ["supply chain", "logistics", "semiconductor", "shortage", "shipping", "raw material"]
_COMPETITORS = ["incumbent", "big tech", "google", "amazon", "microsoft", "competitor", "platform risk"]


class MacroTrendWhisperer:
    name = "macro_trend_whisperer"

    def run(self, chunks: list[ParsedChunk], *, context: dict | None = None) -> MacroTrendOutput:
        context = context or {}
        low = lower_text(chunks) if chunks else ""

        external, sources, unverified = self._external_signals(context)

        out = MacroTrendOutput(
            macro_signals=find_terms(low, _MACRO) + external.get("macro", []),
            regulatory_signals=find_terms(low, _REG),
            technology_shifts=find_terms(low, _TECH),
            sector_tailwinds=find_terms(low, _TAILWIND),
            sector_headwinds=find_terms(low, _HEADWIND),
            supply_chain_signals=find_terms(low, _SUPPLY),
            policy_changes=find_terms(low, _REG),
            silent_competitors=find_terms(low, _COMPETITORS),
            unverified=unverified,
            sources=sources,
        )
        if not any(
            [out.macro_signals, out.regulatory_signals, out.technology_shifts,
             out.sector_tailwinds, out.sector_headwinds]
        ):
            out.missing_data.append("No macro/regulatory/technology signals detected in source material.")
        return out

    def _external_signals(self, context: dict) -> tuple[dict, list[str], bool]:
        """Extension point for live macro/regulatory/news/patent connectors.

        Returns (signals_by_bucket, source_urls, unverified). With no connector
        configured we return nothing and mark the output unverified so partners
        know macro claims are not source-backed yet.
        """
        connector = context.get("macro_connector")
        if connector is None:
            return {}, [], True
        try:  # pragma: no cover - exercised only when a real connector is injected
            data = connector.fetch(context)
            return data.get("signals", {}), data.get("sources", []), False
        except Exception:
            return {}, [], True
