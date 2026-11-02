"""Bounded pagination, TTL eviction, and MCP input validation."""

from types import SimpleNamespace

import pytest
import respx

from nemad import server


def cache_rows(count):
    return server._cache(
        "magnetic", {"count": count, "results": [{"index": n} for n in range(count)]}
    )


async def test_default_page_and_next_page(client):
    saved = cache_rows(150)
    first = await client.call_tool("nemad_read_results", {"cache_key": saved.cache_key})
    assert not first.is_error
    assert first.structured_content == {
        "cache_key": saved.cache_key,
        "total_count": 150,
        "start_index": 0,
        "end_index": 100,
        "results": [{"index": n} for n in range(100)],
    }
    next_page = await client.call_tool(
        "nemad_read_results", {"cache_key": saved.cache_key, "start_index": 100}
    )
    assert next_page.structured_content["end_index"] == 150
    assert next_page.structured_content["results"] == [{"index": n} for n in range(100, 150)]


async def test_explicit_range_is_exclusive_and_clamped(client):
    saved = cache_rows(5)
    page = await client.call_tool(
        "nemad_read_results", {"cache_key": saved.cache_key, "start_index": 1, "end_index": 3}
    )
    assert page.structured_content["results"] == [{"index": 1}, {"index": 2}]
    page = await client.call_tool(
        "nemad_read_results", {"cache_key": saved.cache_key, "start_index": 3, "end_index": 100}
    )
    assert page.structured_content["end_index"] == 5
    assert page.structured_content["results"] == [{"index": 3}, {"index": 4}]


@pytest.mark.parametrize("count", [0, 5])
async def test_empty_page_at_end_is_valid(client, count):
    saved = cache_rows(count)
    result = await client.call_tool(
        "nemad_read_results", {"cache_key": saved.cache_key, "start_index": count}
    )
    assert not result.is_error
    assert result.structured_content["results"] == []
    assert result.structured_content["start_index"] == count
    assert result.structured_content["end_index"] == count


async def test_ttl_expiry_boundary(client, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: now[0]))
    saved = cache_rows(1)
    now[0] += server.CACHE_TTL_SECONDS - 0.001
    before = await client.call_tool("nemad_list_cache", {})
    assert before.structured_content["entries"] == [
        {"cache_key": saved.cache_key, "results_count": 1}
    ]
    now[0] = 10.0 + server.CACHE_TTL_SECONDS
    expired = await client.call_tool("nemad_read_results", {"cache_key": saved.cache_key})
    assert expired.is_error
    assert "expired" in expired.content[0].text
    after = await client.call_tool("nemad_list_cache", {})
    assert after.structured_content == {"entries": []}


async def test_cache_is_bounded_and_evicts_oldest(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_CACHE_ENTRIES", 2)
    first, second, third = [cache_rows(1) for _ in range(3)]
    assert len({first.cache_key, second.cache_key, third.cache_key}) == 3
    result = await client.call_tool("nemad_list_cache", {})
    assert [entry["cache_key"] for entry in result.structured_content["entries"]] == [
        second.cache_key,
        third.cache_key,
    ]
    removed = await client.call_tool("nemad_read_results", {"cache_key": first.cache_key})
    assert removed.is_error


async def test_insertion_prunes_expired_cache(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: now[0]))
    old = cache_rows(1)
    now[0] = server.CACHE_TTL_SECONDS
    fresh = cache_rows(1)
    assert old.cache_key not in server._search_cache
    assert list(server._search_cache) == [fresh.cache_key]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("nemad_search", {"database_type": "unknown", "elements": "Fe"}),
        ("nemad_search", {"database_type": "magnetic", "elements": ""}),
        ("nemad_search", {"database_type": "magnetic", "elements": " "}),
        ("nemad_search", {"database_type": "magnetic", "elements": "Fe,,Co"}),
        ("nemad_search", {"database_type": "magnetic", "elements": "Fe2"}),
        ("nemad_search", {"database_type": "magnetic", "elements": "Fe;Co"}),
        ("nemad_search", {"database_type": "magnetic", "elements": "Fe" * 151}),
        ("nemad_search", {"database_type": "magnetic"}),
        ("nemad_formula_search", {"database_type": "magnetic", "formula": ""}),
        ("nemad_formula_search", {"database_type": "magnetic", "formula": "   "}),
        ("nemad_formula_search", {"database_type": "magnetic", "formula": "F" * 501}),
    ]
    + [
        (tool, {"database_type": "magnetic", field: "Fe", "limit": limit})
        for tool, field in [("nemad_search", "elements"), ("nemad_formula_search", "formula")]
        for limit in [-2, 0, 1001]
    ],
)
async def test_invalid_search_arguments(client, api_key, tool, args):
    with respx.mock(assert_all_called=False) as mock:
        result = await client.call_tool(tool, args)
        assert not mock.calls
    assert result.is_error
    assert result.content
    assert not server._search_cache


@pytest.mark.parametrize(
    "indices",
    [
        {"start_index": -1},
        {"end_index": -1},
        {"start_index": 151},
        {"start_index": 10, "end_index": 9},
        {"start_index": 0, "end_index": 101},
    ],
)
async def test_invalid_pagination(client, indices):
    saved = cache_rows(150)
    result = await client.call_tool("nemad_read_results", {"cache_key": saved.cache_key, **indices})
    assert result.is_error


async def test_unknown_cache_key_is_error(client):
    result = await client.call_tool("nemad_read_results", {"cache_key": "missing"})
    assert result.is_error
    assert "Unknown or expired" in result.content[0].text
