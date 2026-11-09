"""Sync httpx wrapper for FastAPI ASGI apps in tests."""

from __future__ import annotations

import asyncio

from httpx import ASGITransport


class SyncASGIClient:
    """Sync wrapper around httpx.AsyncClient for ASGI apps (httpx 0.28+)."""

    def __init__(self, asgi_app, base_url: str = "http://test"):
        self._transport = ASGITransport(app=asgi_app)
        self._base_url = base_url

    def _run(self, coro):
        return asyncio.run(coro)

    def get(self, url: str, **kwargs):
        async def _call():
            import httpx

            async with httpx.AsyncClient(transport=self._transport, base_url=self._base_url) as ac:
                return await ac.get(url, **kwargs)

        return self._run(_call())

    def post(self, url: str, **kwargs):
        async def _call():
            import httpx

            async with httpx.AsyncClient(transport=self._transport, base_url=self._base_url) as ac:
                return await ac.post(url, **kwargs)

        return self._run(_call())
