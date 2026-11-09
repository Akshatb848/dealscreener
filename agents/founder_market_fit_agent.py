"""Founder-market fit extraction agent."""

from __future__ import annotations

from agent_schemas import FounderMarketFitAgentOutput
from agents import run_agent_inference
from parser_layer import ParsedChunk


class FounderMarketFitAgent:
    PROMPT = (
        "Extract founder names, backgrounds, prior startup experience, prior exits, "
        "domain experience, education, execution signals, and hiring/team strength. "
        "Only cite information present in the source. Include evidence with chunk_id."
    )

    def run(
        self, chunks: list[ParsedChunk], *, model: str | None = None
    ) -> tuple[FounderMarketFitAgentOutput | None, bool]:
        if not chunks:
            return FounderMarketFitAgentOutput(
                missing_variables=["all_founder_fields"],
            ), False
        return run_agent_inference(self.PROMPT, FounderMarketFitAgentOutput, chunks, model=model)
