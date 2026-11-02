"""NEMAD HTTP contract and sanitized failure behavior; no live API calls."""

import httpx
import pytest
import respx

from nemad import server


def error_text(result):
    assert result.is_error, result
    assert not server._search_cache
    return "\n".join(item.text for item in result.content if item.type == "text")


@pytest.mark.parametrize(
    "database", ["magnetic", "magnetic_anisotropy", "thermoelectric", "superconductor"]
)
async def test_element_search_contract(client, api_key, database):
    data = {"count": 42, "results": [{"Material_Name": "FeCo", "Curie": 900}]}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"https://api.nemad.org/api/{database}/search").respond(json=data)
        result = await client.call_tool(
            "nemad_search", {"database_type": database, "elements": " Fe, Co "}
        )
    assert not result.is_error
    request = route.calls.last.request
    assert request.headers["X-API-Key"] == api_key
    assert request.headers["Accept"] == "application/json"
    assert dict(request.url.params) == {"elements": "Fe,Co", "exact_match": "false", "limit": "100"}
    structured = result.structured_content
    assert structured["database_type"] == database
    assert structured["count"] == 42
    assert structured["results_returned"] == 1
    assert structured["expires_in_seconds"] == 900
    page = await client.call_tool("nemad_read_results", {"cache_key": structured["cache_key"]})
    assert page.structured_content["results"] == data["results"]


async def test_formula_and_explicit_search_parameters(client, api_key):
    with respx.mock(assert_all_called=True) as mock:
        formula = mock.get("https://api.nemad.org/api/magnetic_anisotropy/formula").respond(
            json={"count": 0, "results": []}
        )
        search = mock.get("https://api.nemad.org/api/magnetic/search").respond(
            json={"count": 0, "results": []}
        )
        result = await client.call_tool(
            "nemad_formula_search",
            {"database_type": "magnetic_anisotropy", "formula": " Nd2Fe14B ", "limit": 10},
        )
        assert not result.is_error
        result = await client.call_tool(
            "nemad_search",
            {"database_type": "magnetic", "elements": "Fe", "exact_match": True, "limit": -1},
        )
        assert not result.is_error
    assert dict(formula.calls.last.request.url.params) == {"formula": "Nd2Fe14B", "limit": "10"}
    assert formula.calls.last.request.headers["X-API-Key"] == api_key
    assert dict(search.calls.last.request.url.params) == {
        "elements": "Fe",
        "exact_match": "true",
        "limit": "-1",
    }


@pytest.mark.parametrize("base", ["https://example.test/custom/api/", "http://127.0.0.1:8765/api"])
async def test_configured_base_url(client, api_key, monkeypatch, base):
    monkeypatch.setenv("NEMAD_BASE_URL", base)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{base.rstrip('/')}/magnetic/search").respond(
            json={"count": 0, "results": []}
        )
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    assert not result.is_error
    assert route.calls.last.request.headers["X-API-Key"] == api_key


@pytest.mark.parametrize(
    "base",
    [
        "http://example.test/api",
        "ftp://example.test/api",
        "https:///api",
        "https://user:password@example.test/api",
        "https://example.test/api?q=1",
        "https://example.test/api#fragment",
    ],
)
async def test_unsafe_base_url_rejected(client, api_key, monkeypatch, base):
    monkeypatch.setenv("NEMAD_BASE_URL", base)
    with respx.mock(assert_all_called=False) as mock:
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
        assert not mock.calls
    assert "NEMAD_BASE_URL" in error_text(result)
    assert "password" not in error_text(result)


@pytest.mark.parametrize("key", [None, "", "   "])
async def test_missing_api_key(client, monkeypatch, key):
    if key is not None:
        monkeypatch.setenv("NEMAD_API_KEY", key)
    with respx.mock(assert_all_called=False) as mock:
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
        assert not mock.calls
    assert "NEMAD_API_KEY" in error_text(result)


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "rejected"),
        (403, "rejected"),
        (429, "rate limit"),
        (500, "HTTP 500"),
        (404, "HTTP 404"),
    ],
)
async def test_http_errors_are_mcp_errors(client, api_key, status, message):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.nemad.org/api/magnetic/search").respond(
            status_code=status, text=f"upstream detail: {api_key}"
        )
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    text = error_text(result)
    assert message in text
    assert api_key not in text
    assert "upstream detail" not in text


async def test_redirect_is_not_followed(client, api_key):
    with respx.mock(assert_all_called=False) as mock:
        original = mock.get("https://api.nemad.org/api/magnetic/search").respond(
            status_code=302, headers={"Location": "https://other.example/leak"}
        )
        destination = mock.get("https://other.example/leak").respond(
            json={"count": 0, "results": []}
        )
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    assert original.called
    assert not destination.called
    assert "redirect" in error_text(result)


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (httpx.ReadTimeout("private upstream message"), "timed out"),
        (httpx.ConnectError("private upstream message"), "Unable to reach"),
    ],
)
async def test_network_failures_are_sanitized(client, api_key, exception, message):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.nemad.org/api/magnetic/search").mock(side_effect=exception)
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    text = error_text(result)
    assert message in text
    assert "private upstream message" not in text


@pytest.mark.parametrize("body", [b"not json", b"{broken", b"\xff"])
async def test_invalid_json(client, api_key, body):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.nemad.org/api/magnetic/search").respond(content=body)
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    assert "invalid JSON" in error_text(result)


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "wrong",
        {},
        {"count": 0},
        {"results": []},
        {"count": -1, "results": []},
        {"count": True, "results": []},
        {"count": 1.5, "results": []},
        {"count": "1", "results": []},
        {"count": 1, "results": {}},
        {"count": 1, "results": ["not an object"]},
    ],
)
async def test_malformed_api_responses(client, api_key, data):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.nemad.org/api/magnetic/search").respond(json=data)
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    assert "NEMAD" in error_text(result)


async def test_response_size_bound(client, api_key, monkeypatch):
    monkeypatch.setattr(server, "MAX_RESPONSE_BYTES", 32)
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.nemad.org/api/magnetic/search").respond(
            json={"count": 1, "results": [{"Material_Name": "x" * 64}]}
        )
        result = await client.call_tool(
            "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
        )
    assert "too large" in error_text(result)
