"""End-to-end tests on synthetic deals using the deterministic mock LLM.

Every deal here is fictional (see tests/fixtures/deals and examples/pitches).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.mock_llm import mock_call_ollama

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "deals").glob("*.json"))
VERDICTS = {"Yes", "Maybe", "Needs Review", "No"}


@pytest.fixture
def live_mock_client(client, monkeypatch):
    import inference_service

    monkeypatch.setattr(inference_service, "_call_ollama", mock_call_ollama)
    monkeypatch.setenv("RUN_LIVE_LLM", "true")
    monkeypatch.setenv("ENABLE_ADVANCED_PIPELINE", "true")
    monkeypatch.setenv("SIMULATION_ITERATIONS", "30")
    return client


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _upload(client, deal: dict):
    pitch = ROOT / deal["pitch_file"]
    r = client.post(
        "/api/v1/upload",
        data={"company_name": deal["company_name"], "deal_id": deal["deal_id"], "domain": deal["domain"]},
        files={"file": (pitch.name, pitch.read_bytes(), "text/markdown")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_fixtures_are_synthetic():
    assert len(FIXTURES) >= 3
    for path in FIXTURES:
        deal = _load(path)
        assert "(fictional)" in deal["company_name"]
        assert deal["domain"].endswith(".example")
        assert (ROOT / deal["pitch_file"]).is_file()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_synthetic_deal_end_to_end(live_mock_client, fixture):
    deal = _load(fixture)
    up = _upload(live_mock_client, deal)
    assert up["evaluation_id"]

    evaluation = live_mock_client.get(f"/api/v1/evaluation/{up['evaluation_id']}").json()
    assert evaluation["recommendation"] in VERDICTS
    assert evaluation["recommendation"] == deal["expected_recommendation"]
    assert 0.0 <= evaluation["deterministic_score"] <= 100.0

    by_external_id = live_mock_client.get(f"/api/v1/deals/{deal['deal_id']}")
    assert by_external_id.status_code == 200
    assert by_external_id.json()["deal_id"] == up["company_id"]

    memo = live_mock_client.get(f"/api/v1/deals/{up['company_id']}/ic-memo")
    assert memo.status_code == 200
    body = memo.json()
    assert body["recommendation"] == evaluation["recommendation"]
    assert body["decision_authority"].startswith("Investment Committee")


def test_strong_deal_extracts_financials(live_mock_client):
    deal = _load(ROOT / "tests" / "fixtures" / "deals" / "acme_robotics.json")
    up = _upload(live_mock_client, deal)
    evaluation = live_mock_client.get(f"/api/v1/evaluation/{up['evaluation_id']}").json()
    assert "ARR: $3.2M" in evaluation["financial_metrics_summary"]
    assert "Ada Okafor (fictional)" in evaluation["founder_market_fit_summary"]
    assert evaluation["extracted_metrics"]["financial_normalized"]


def test_weak_deal_raises_red_flags(live_mock_client):
    deal = _load(ROOT / "tests" / "fixtures" / "deals" / "nimbus_ledger.json")
    up = _upload(live_mock_client, deal)
    detail = live_mock_client.get(f"/api/v1/deals/{up['company_id']}").json()
    descriptions = " ".join(rf["description"].lower() for rf in detail["risk_flags"])
    assert "lawsuit" in descriptions
    assert detail["open_review_items"] >= 1
