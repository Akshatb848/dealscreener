"""Market and moat extraction agent."""

from __future__ import annotations

from agent_schemas import MarketMoatAgentOutput
from agents import run_agent_inference
from parser_layer import ParsedChunk


class MarketMoatAgent:
    PROMPT = (
        "Extract market size, TAM, SAM, SOM, market category, competitive landscape, "
        "operational moats, defensibility signals, and GTM signals from the pitch deck. "
        "Include evidence with chunk_id references."
    )

    def run(self, chunks: list[ParsedChunk], *, model: str | None = None) -> tuple[MarketMoatAgentOutput | None, bool]:
        if not chunks:
            return MarketMoatAgentOutput(
                missing_variables=["all_market_fields"],
            ), False
        return run_agent_inference(self.PROMPT, MarketMoatAgentOutput, chunks, model=model)
