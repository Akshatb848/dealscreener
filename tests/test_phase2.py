"""Phase 2 tests: agents, JSON repair, normalization, evidence."""

from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from agent_schemas import (
    EvidenceItem,
    FinancialMetricsAgentOutput,
)
from agents.financial_metrics_agent import FinancialMetricsAgent
from agents.market_moat_agent import MarketMoatAgent
from financial_normalization import normalize_financial_value
from inference_service import InferenceResult
from json_validation import validate_or_repair_json
from parser_layer import ParsedChunk


class SampleSchema(BaseModel):
    score: int
    missing_variables: list[str] = Field(default_factory=list)


class TestJSONValidation:
    def test_valid_structured_output(self):
        raw = '{"score": 8, "missing_variables": []}'
        result, needs_review = validate_or_repair_json(raw, SampleSchema)
        assert needs_review is False
        assert result.score == 8

    def test_malformed_json_repair_attempt(self):
        raw = 'Here is JSON: {"score": 5, "missing_variables": []}'
        result, needs_review = validate_or_repair_json(raw, SampleSchema)
        assert needs_review is False
        assert result.score == 5

    def test_repair_failure_needs_review(self):
        raw = "not json at all"
        result, needs_review = validate_or_repair_json(raw, SampleSchema)
        assert result is None
        assert needs_review is True


class TestFinancialNormalization:
    @pytest.mark.parametrize(
        "raw,expected_amount,currency",
        [
            ("₹5 Cr", 50_000_000, "INR"),
            ("INR 50 lakh", 5_000_000, "INR"),
            ("$2M", 2_000_000, "USD"),
            ("USD 1.5 million", 1_500_000, "USD"),
        ],
    )
    def test_currency_mixed_normalization(self, raw, expected_amount, currency):
        nv = normalize_financial_value(raw)
        assert nv.normalized_amount == expected_amount
        assert nv.normalized_currency == currency
        assert nv.raw_value == raw

    def test_no_fx_guess_between_currencies(self):
        nv = normalize_financial_value("100")
        assert nv.currency_conversion_available is False


class TestAgents:
    @pytest.fixture
    def sample_chunks(self):
        return [
            ParsedChunk(
                source_file="deck.pdf",
                page_index=0,
                raw_text="TAM is $1B. ARR is $2M. Founder Jane Doe, ex-Google.",
                extraction_method="plain_text",
                confidence_score=0.9,
            )
        ]

    def test_market_agent_empty_chunks_missing_vars(self):
        agent = MarketMoatAgent()
        output, needs_review = agent.run([])
        assert "all_market_fields" in output.missing_variables
        assert needs_review is False

    @patch("agents.financial_metrics_agent.run_agent_inference")
    def test_financial_agent_with_mock(self, mock_infer, sample_chunks):
        mock_infer.return_value = (
            FinancialMetricsAgentOutput(
                arr="$2M",
                revenue="$2M",
                missing_variables=["burn"],
                evidence=[
                    EvidenceItem(
                        chunk_id="deck.pdf:0",
                        source_file="deck.pdf",
                        page_index=0,
                        quoted_or_paraphrased_evidence="ARR is $2M",
                        confidence_score=0.9,
                    )
                ],
            ),
            False,
        )
        output, needs_review = FinancialMetricsAgent().run(sample_chunks)
        assert needs_review is False
        assert output.arr == "$2M"
        assert "burn" in output.missing_variables
        assert output.evidence[0].chunk_id == "deck.pdf:0"
        assert "arr" in output.normalized_values

    @patch("inference_service.run_inference")
    def test_inference_retry_and_fallback(self, mock_run):
        mock_run.side_effect = [
            RuntimeError("fail"),
            InferenceResult(content='{"score": 1}', provider="ollama", model="m", attempts=2),
        ]
        from inference_service import run_inference

        with pytest.raises(RuntimeError):
            run_inference("test", retry_limit=1)

    def test_evidence_maps_chunk_ids(self):
        ev = EvidenceItem(
            chunk_id="deck.pdf:1",
            source_file="deck.pdf",
            page_index=1,
            quoted_or_paraphrased_evidence="quoted text",
            confidence_score=0.8,
        )
        assert ev.chunk_id == "deck.pdf:1"
