"""Company evaluation synthesis schema."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_schemas import EvidenceItem


class CompanyEvaluationSchema(BaseModel):
    company_id: str
    company_name: str
    document_ids: list[str]
    evaluation_id: str
    market_moat_summary: str
    financial_metrics_summary: str
    founder_market_fit_summary: str
    extracted_metrics: dict[str, Any] = Field(default_factory=dict)
    red_flags: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
    deterministic_score: float
    recommendation: str
    recommendation_reason: str
    model_versions_used: dict[str, str] = Field(default_factory=dict)
    scoring_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
