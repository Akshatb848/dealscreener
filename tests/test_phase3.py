"""Phase 3 tests: deterministic scoring and synthesis."""

import pytest

from agent_schemas import (
    FinancialMetricsAgentOutput,
    FounderMarketFitAgentOutput,
    MarketMoatAgentOutput,
)
from evaluation_schema import CompanyEvaluationSchema
from scoring_logic import SCORING_VERSION, compute_score, map_recommendation
from synthesis_service import synthesize_evaluation


class TestScoringLogic:
    def test_identical_inputs_identical_scores(self):
        kwargs = dict(
            confidence_score=0.9,
            missing_variables=["burn"],
            red_flags=[],
        )
        a = compute_score(**kwargs)
        b = compute_score(**kwargs)
        assert a.final_score == b.final_score
        assert a.recommendation == b.recommendation

    def test_missing_data_reduces_score(self):
        base = compute_score(confidence_score=0.9, missing_variables=[])
        penalized = compute_score(confidence_score=0.9, missing_variables=["a", "b", "c"])
        assert penalized.final_score < base.final_score

    def test_red_flags_reduce_score(self):
        base = compute_score(confidence_score=0.9, red_flags=[])
        flagged = compute_score(confidence_score=0.9, red_flags=["fraud", "litigation"])
        assert flagged.final_score < base.final_score

    def test_low_confidence_reduces_score(self):
        high = compute_score(confidence_score=0.95)
        low = compute_score(confidence_score=0.55)
        assert low.final_score < high.final_score

    @pytest.mark.parametrize(
        "score,expected",
        [
            (80, "Yes"),
            (60, "Maybe"),
            (40, "Needs Review"),
            (20, "No"),
        ],
    )
    def test_recommendation_mapping(self, score, expected):
        label, _ = map_recommendation(score)
        assert label == expected


class TestSynthesis:
    def test_company_evaluation_schema_mandatory_fields(self):
        ev = synthesize_evaluation(
            company_id="c1",
            company_name="TestCo",
            document_ids=["d1"],
            evaluation_id="e1",
            market=MarketMoatAgentOutput(tam="$1B"),
            financial=FinancialMetricsAgentOutput(arr="$2M"),
            founder=FounderMarketFitAgentOutput(founder_names=["Jane"]),
        )
        assert isinstance(ev, CompanyEvaluationSchema)
        assert ev.company_id == "c1"
        assert ev.scoring_version == SCORING_VERSION
        assert ev.recommendation in {"Yes", "No", "Maybe", "Needs Review"}
        assert ev.deterministic_score >= 0

    def test_recommendation_not_from_llm_text(self):
        """Recommendation must come from scoring_logic, not agent summaries."""
        ev = synthesize_evaluation(
            company_id="c1",
            company_name="TestCo",
            document_ids=["d1"],
            evaluation_id="e1",
            market=MarketMoatAgentOutput(
                tam="Invest immediately — guaranteed unicorn",
                missing_variables=["sam"] * 10,
            ),
            financial=FinancialMetricsAgentOutput(missing_variables=["arr"] * 10),
            founder=FounderMarketFitAgentOutput(missing_variables=["founders"] * 5),
            red_flags=["regulatory_risk"],
        )
        expected = compute_score(
            confidence_score=ev.confidence_score,
            missing_variables=ev.missing_information,
            red_flags=ev.red_flags,
        )
        # LLM hype text must not override math
        assert ev.deterministic_score == pytest.approx(expected.final_score)
        assert ev.recommendation == expected.recommendation

    def test_needs_review_override_from_validation(self):
        ev = synthesize_evaluation(
            company_id="c1",
            company_name="TestCo",
            document_ids=["d1"],
            evaluation_id="e1",
            market=MarketMoatAgentOutput(),
            financial=FinancialMetricsAgentOutput(),
            founder=FounderMarketFitAgentOutput(),
            needs_review=True,
        )
        assert ev.recommendation == "Needs Review"
