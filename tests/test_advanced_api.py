"""Tests for the multi-agent / simulation / governance API endpoints."""

from __future__ import annotations

import io

import pytest


@pytest.fixture
def advanced_client(client, monkeypatch):
    monkeypatch.setenv("ENABLE_ADVANCED_PIPELINE", "true")
    monkeypatch.setenv("SIMULATION_ITERATIONS", "30")
    return client


def _upload(client, name="Acme Pay (fictional)", deal_id="42"):
    data = (b"SaaS compliance platform ARR 2M moat patent founder ex-google gdpr "
            b"board independent subscription enterprise traction growth")
    r = client.post(
        "/api/v1/upload",
        data={"company_name": name, "deal_id": deal_id, "domain": "fintech"},
        files={"file": ("deck.txt", io.BytesIO(data), "text/plain")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_deal_detail_and_simulation(advanced_client):
    body = _upload(advanced_client)
    cid = body["company_id"]

    deal = advanced_client.get(f"/api/v1/deals/{cid}")
    assert deal.status_code == 200
    dj = deal.json()
    assert dj["deal_id"] == cid
    assert dj["simulation"] is not None
    assert 0.0 <= dj["simulation"]["resilience_score"] <= 100.0
    assert dj["decision_authority"].startswith("Investment Committee")

    # resolvable by external (CRM) deal_id too
    assert advanced_client.get("/api/v1/deals/42").status_code == 200

    sims = advanced_client.get(f"/api/v1/deals/{cid}/simulation-runs").json()
    assert sims["count"] >= 1
    assert len(sims["simulation_runs"][0]["survival_matrix"]) == 10


def test_ic_memo_endpoint(advanced_client):
    cid = _upload(advanced_client)["company_id"]
    memo = advanced_client.get(f"/api/v1/deals/{cid}/ic-memo")
    assert memo.status_code == 200
    mj = memo.json()
    assert mj["company_id"] == cid
    assert "scorecard" in mj and "recommendation" in mj
    assert mj["decision_authority"].startswith("Investment Committee")


def test_review_queue_and_override(advanced_client):
    cid = _upload(advanced_client)["company_id"]
    queue = advanced_client.get("/api/v1/review-queue").json()
    assert queue["count"] >= 1
    item_id = queue["items"][0]["review_item_id"]

    ovr = advanced_client.post(
        f"/api/v1/review-queue/{item_id}/override",
        json={"choice": "No", "reviewer_name": "Partner A", "notes": "weak resilience"},
    )
    assert ovr.status_code == 200
    body = ovr.json()
    assert body["status"] == "resolved"
    assert body["override_id"]
    # item no longer open
    assert advanced_client.get("/api/v1/review-queue").json()["count"] == queue["count"] - 1


def test_deal_404(advanced_client):
    assert advanced_client.get("/api/v1/deals/does-not-exist").status_code == 404
