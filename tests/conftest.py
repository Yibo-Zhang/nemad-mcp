"""Isolation and client fixtures for the NEMAD server tests."""

import pytest
from mcp import Client

from nemad import server


@pytest.fixture(autouse=True)
def isolated_server(monkeypatch):
    """Never consume a developer's real API key or preserve another test's cache."""
    monkeypatch.delenv("NEMAD_API_KEY", raising=False)
    monkeypatch.delenv("NEMAD_BASE_URL", raising=False)
    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    server._search_cache.clear()
    yield
    server._search_cache.clear()


class IsolatedClient:
    """Keep AnyIO context enter/exit inside the same task under pytest-asyncio."""

    async def call_tool(self, name, arguments):
        async with Client(server.mcp, raise_exceptions=True) as connection:
            return await connection.call_tool(name, arguments)

    async def list_tools(self):
        async with Client(server.mcp, raise_exceptions=True) as connection:
            return await connection.list_tools()


@pytest.fixture
def client():
    return IsolatedClient()


@pytest.fixture
def api_key(monkeypatch):
    key = "test-only-key-not-a-real-secret"
    monkeypatch.setenv("NEMAD_API_KEY", key)
    return key
