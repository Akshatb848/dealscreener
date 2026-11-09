"""Sanity checks for OpenAPI and core routes (offline)."""

from tests.asgi_client import SyncASGIClient
from api_app import app


class TestOpenAPI:
    def test_security_schemes(self):
        client = SyncASGIClient(app)
        schema = client.get("/openapi.json").json()
        schemes = schema.get("components", {}).get("securitySchemes", {})
        assert "X-API-Key" in schemes

    def test_all_documented_routes_exist(self):
        client = SyncASGIClient(app)
        schema = client.get("/openapi.json").json()
        paths = set(schema.get("paths", {}))
        expected = {
            "/",
            "/health",
            "/readiness",
            "/api/v1/config/status",
            "/api/v1/ops/summary",
            "/api/v1/upload",
            "/api/v1/status/{company_id}",
            "/api/v1/evaluation/{evaluation_id}",
            "/api/v1/review/override",
            "/api/v1/review/queue",
            "/api/v1/deals/{deal_id}",
            "/api/v1/deals/{deal_id}/ic-memo",
            "/api/v1/review-queue",
            "/api/v1/jobs/{job_id}",
        }
        assert expected.issubset(paths)
