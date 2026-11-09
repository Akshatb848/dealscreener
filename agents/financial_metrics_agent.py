"""Financial metrics extraction agent."""

from __future__ import annotations

from agent_schemas import FinancialMetricsAgentOutput
from agents import run_agent_inference
from financial_normalization import normalize_financial_dict
from parser_layer import ParsedChunk


class FinancialMetricsAgent:
    PROMPT = (
        "Extract financial metrics: ARR, MRR, revenue, GMV, burn, runway, EBITDA, CAC, LTV, "
        "margins, growth rate, funding ask, valuation. "
        "Only report values explicitly stated. Include evidence with chunk_id."
    )

    def run(self, chunks: list[ParsedChunk], *, model: str | None = None) -> tuple[FinancialMetricsAgentOutput | None, bool]:
        if not chunks:
            return FinancialMetricsAgentOutput(
                missing_variables=["all_financial_fields"],
            ), False

        output, needs_review = run_agent_inference(
            self.PROMPT, FinancialMetricsAgentOutput, chunks, model=model
        )
        if output is None:
            return None, needs_review

        raw_fields = {
            "arr": output.arr,
            "mrr": output.mrr,
            "revenue": output.revenue,
            "gmv": output.gmv,
            "burn": output.burn,
            "funding_ask": output.funding_ask,
            "valuation": output.valuation,
        }
        output.normalized_values = normalize_financial_dict(raw_fields)
        return output, needs_review
