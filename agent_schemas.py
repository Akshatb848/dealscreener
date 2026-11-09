"""Pydantic schemas for specialized agents and shared evidence objects."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    chunk_id: str
    source_file: str
    page_index: int
    quoted_or_paraphrased_evidence: str
    confidence_score: float = Field(ge=0.0, le=1.0)


class MarketMoatAgentOutput(BaseModel):
    market_size: str | None = None
    tam: str | None = None
    sam: str | None = None
    som: str | None = None
    market_category: str | None = None
    competitive_landscape: str | None = None
    operational_moats: list[str] = Field(default_factory=list)
    defensibility_signals: list[str] = Field(default_factory=list)
    gtm_signals: list[str] = Field(default_factory=list)
    missing_variables: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class FinancialMetricsAgentOutput(BaseModel):
    arr: str | None = None
    mrr: str | None = None
    revenue: str | None = None
    gmv: str | None = None
    burn: str | None = None
    runway: str | None = None
    ebitda: str | None = None
    cac: str | None = None
    ltv: str | None = None
    margins: str | None = None
    growth_rate: str | None = None
    funding_ask: str | None = None
    valuation: str | None = None
    missing_variables: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    normalized_values: dict[str, Any] = Field(default_factory=dict)


class FounderMarketFitAgentOutput(BaseModel):
    founder_names: list[str] = Field(default_factory=list)
    founder_backgrounds: list[str] = Field(default_factory=list)
    prior_startup_experience: list[str] = Field(default_factory=list)
    prior_exits: list[str] = Field(default_factory=list)
    domain_experience: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    execution_signals: list[str] = Field(default_factory=list)
    hiring_team_strength: list[str] = Field(default_factory=list)
    missing_variables: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
