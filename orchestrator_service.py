"""Parallel specialized agents with primary/fallback inference routing."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

from pydantic import BaseModel

from agent_schemas import (
    FinancialMetricsAgentOutput,
    FounderMarketFitAgentOutput,
    MarketMoatAgentOutput,
)
from agents.financial_metrics_agent import FinancialMetricsAgent
from agents.founder_market_fit_agent import FounderMarketFitAgent
from agents.market_moat_agent import MarketMoatAgent
from parser_layer import ParsedChunk
from structured_logging import log_event

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def run_agents_parallel(
    chunks: list[ParsedChunk],
    *,
    use_live_llm: bool,
) -> tuple[MarketMoatAgentOutput, FinancialMetricsAgentOutput, FounderMarketFitAgentOutput, bool]:
    """
    Run Market, Financial, and Founder agents in parallel (architecture diagram).
    Returns (market, financial, founder, needs_review).
    """
    if not use_live_llm:
        return _offline_agent_outputs(chunks)

    agents: list[tuple[str, Callable]] = [
        ("market_moat", lambda: MarketMoatAgent().run(chunks)),
        ("financial_metrics", lambda: FinancialMetricsAgent().run(chunks)),
        ("founder_market_fit", lambda: FounderMarketFitAgent().run(chunks)),
    ]

    results: dict[str, tuple[BaseModel | None, bool]] = {}
    needs_review = False

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): name for name, fn in agents}
        agent_timeout = int(os.environ.get("AGENT_POOL_TIMEOUT_SECONDS", "240"))
        for future in as_completed(futures):
            name = futures[future]
            try:
                output, nr = future.result(timeout=agent_timeout)
                results[name] = (output, nr)
                needs_review = needs_review or nr
            except TimeoutError:
                logger.error("Agent %s timed out after %ss", name, agent_timeout)
                results[name] = (None, True)
                needs_review = True
            except Exception as exc:
                logger.exception("Agent %s failed: %s", name, exc)
                results[name] = (None, True)
                needs_review = True

    market = results.get("market_moat", (None, True))[0] or MarketMoatAgentOutput(
        missing_variables=["market_moat"]
    )
    financial = results.get("financial_metrics", (None, True))[0] or FinancialMetricsAgentOutput(
        missing_variables=["financial_metrics"]
    )
    founder = results.get("founder_market_fit", (None, True))[0] or FounderMarketFitAgentOutput(
        missing_variables=["founder_market_fit"]
    )

    log_event(logger, "agents_parallel_complete", needs_review=needs_review)
    return market, financial, founder, needs_review


def _offline_agent_outputs(
    chunks: list[ParsedChunk],
) -> tuple[MarketMoatAgentOutput, FinancialMetricsAgentOutput, FounderMarketFitAgentOutput, bool]:
    text = " ".join(c.raw_text for c in chunks)
    log_event(logger, "fallback_triggered", mode="offline_extraction")
    market = MarketMoatAgentOutput(
        market_category="extracted_offline",
        missing_variables=[] if "market" in text.lower() else ["tam", "sam"],
    )
    financial = FinancialMetricsAgentOutput(
        revenue=next((w for w in text.split() if w.startswith("$")), None),
        missing_variables=["arr", "burn", "runway"],
    )
    founder = FounderMarketFitAgentOutput(missing_variables=["founder_names", "prior_exits"])
    return market, financial, founder, False


def inference_route_config() -> dict[str, str]:
    """Primary Ollama host + fallback local Mistral (per architecture diagram)."""
    return {
        "primary_base_url": os.environ.get("OLLAMA_PRIMARY_BASE_URL", os.environ.get("OLLAMA_BASE_URL", "")),
        "primary_model": os.environ.get("OLLAMA_PRIMARY_MODEL", os.environ.get("OLLAMA_MODEL", "llama3:70b")),
        "fallback_base_url": os.environ.get("OLLAMA_FALLBACK_BASE_URL", "http://127.0.0.1:11434"),
        "fallback_model": os.environ.get("OLLAMA_FALLBACK_MODEL", "mistral"),
    }
