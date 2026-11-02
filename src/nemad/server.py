"""Read-only NEMAD tools with bounded local caching and explicit MCP errors.

The cache and API key belong to one local user. Do not expose this process as
an unauthenticated, shared internet service.
"""

import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__

DatabaseType = Literal["magnetic", "magnetic_anisotropy", "thermoelectric", "superconductor"]
DEFAULT_BASE_URL = "https://api.nemad.org/api"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_CACHE_ENTRIES = 32
CACHE_TTL_SECONDS = 900
MAX_PAGE_SIZE = 100

mcp = MCPServer(
    "nemad",
    title="NEMAD MCP",
    description="Read-only access to the Northeast Materials Database (NEMAD).",
    instructions=(
        "Search first, then read cached results in pages. Cache keys expire after 15 minutes."
    ),
    website_url="https://nemad.org",
    version=__version__,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
LOCAL_READ = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


class SearchResult(BaseModel):
    database_type: DatabaseType
    count: int
    results_returned: int
    cache_key: str
    expires_in_seconds: int = CACHE_TTL_SECONDS
    next_step: str = "Call nemad_read_results with cache_key to read a page of results."


class ResultsPage(BaseModel):
    cache_key: str
    total_count: int
    start_index: int
    end_index: int
    results: list[dict[str, Any]]


class CacheSummary(BaseModel):
    cache_key: str
    results_count: int


class CacheList(BaseModel):
    entries: list[CacheSummary]


@dataclass
class CachedResults:
    created_at: float
    results: list[dict[str, Any]]


_search_cache: OrderedDict[str, CachedResults] = OrderedDict()


def _prune_cache() -> None:
    now = time.monotonic()
    for key, entry in list(_search_cache.items()):
        if now - entry.created_at >= CACHE_TTL_SECONDS:
            del _search_cache[key]


def _base_url() -> str:
    value = os.getenv("NEMAD_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    parsed = urlsplit(value)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or (parsed.scheme != "https" and not local_http)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ToolError("NEMAD_BASE_URL must be an HTTPS URL (HTTP is allowed only on loopback).")
    return value


def _validate_limit(limit: int) -> None:
    if limit != -1 and not 1 <= limit <= 1000:
        raise ToolError("limit must be -1 (all results) or an integer between 1 and 1000.")


async def _request(database_type: DatabaseType, endpoint: str, params: dict[str, Any]) -> dict:
    key = os.getenv("NEMAD_API_KEY", "").strip()
    if not key:
        raise ToolError("Set NEMAD_API_KEY to a key from your account at https://nemad.org.")
    url = f"{_base_url()}/{database_type}/{endpoint}"
    try:
        # Redirects are disabled so an upstream redirect cannot forward the API key elsewhere.
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            async with client.stream(
                "GET", url, params=params, headers={"X-API-Key": key, "Accept": "application/json"}
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ToolError("NEMAD response is too large. Retry with a smaller limit.")
        data = json.loads(body)
    except httpx.TimeoutException:
        raise ToolError("NEMAD request timed out. Retry with a smaller limit.") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            message = "NEMAD rejected the API key. Check your account and NEMAD_API_KEY."
        elif status == 429:
            message = "NEMAD rate limit reached. Wait before retrying."
        elif 300 <= status < 400:
            message = (
                "NEMAD returned a redirect; check NEMAD_BASE_URL against the API documentation."
            )
        else:
            message = f"NEMAD API returned HTTP {status}."
        raise ToolError(message) from None
    except httpx.RequestError:
        raise ToolError("Unable to reach NEMAD. Check the network and API base URL.") from None
    except (ValueError, UnicodeDecodeError):
        raise ToolError("NEMAD returned invalid JSON.") from None
    if not isinstance(data, dict):
        raise ToolError("NEMAD returned an unexpected response format.")
    results = data.get("results")
    count = data.get("count")
    if (
        not isinstance(results, list)
        or not all(isinstance(item, dict) for item in results)
        or type(count) is not int
        or count < 0
    ):
        raise ToolError("NEMAD response must contain a non-negative count and a results array.")
    return data


def _cache(database_type: DatabaseType, data: dict) -> SearchResult:
    _prune_cache()
    while len(_search_cache) >= MAX_CACHE_ENTRIES:
        _search_cache.popitem(last=False)
    key = uuid4().hex
    _search_cache[key] = CachedResults(time.monotonic(), data["results"])
    return SearchResult(
        database_type=database_type,
        count=data["count"],
        results_returned=len(data["results"]),
        cache_key=key,
    )


@mcp.tool(annotations=READ_ONLY)
async def nemad_search(
    database_type: DatabaseType,
    elements: Annotated[str, Field(min_length=1, max_length=300)],
    exact_match: bool = False,
    limit: int = 100,
) -> SearchResult:
    """Search NEMAD by comma-separated elements (for example Fe,Co), then cache results.

    exact_match requires exactly those elements. limit is 1..1000, or -1 for all
    results subject to the response-size bound. Use nemad_read_results to read pages.
    """
    normalized = ",".join(part.strip() for part in elements.split(","))
    if any(not part or not part.isalpha() for part in normalized.split(",")):
        raise ToolError("elements must be comma-separated element symbols, for example Fe,Co.")
    _validate_limit(limit)
    data = await _request(
        database_type,
        "search",
        {"elements": normalized, "exact_match": str(exact_match).lower(), "limit": limit},
    )
    return _cache(database_type, data)


@mcp.tool(annotations=READ_ONLY)
async def nemad_formula_search(
    database_type: DatabaseType,
    formula: Annotated[str, Field(min_length=1, max_length=500)],
    limit: int = 100,
) -> SearchResult:
    """Search NEMAD by exact chemical formula (for example Fe2O3), then cache results."""
    formula = formula.strip()
    if not formula:
        raise ToolError("formula must not be blank.")
    _validate_limit(limit)
    data = await _request(database_type, "formula", {"formula": formula, "limit": limit})
    return _cache(database_type, data)


@mcp.tool(annotations=LOCAL_READ)
async def nemad_read_results(
    cache_key: str,
    start_index: Annotated[int, Field(ge=0)] = 0,
    end_index: Annotated[int, Field(ge=0)] | None = None,
) -> ResultsPage:
    """Read a cached page: start inclusive, end exclusive, at most 100 results.

    Omit end_index for the next 100 results. An empty search produces an empty page.
    Results are local to this process and expire after 15 minutes.
    """
    _prune_cache()
    entry = _search_cache.get(cache_key)
    if entry is None:
        raise ToolError("Unknown or expired cache_key. Run a new search.")
    total = len(entry.results)
    if start_index > total:
        raise ToolError("start_index is beyond the cached result count.")
    end = min(start_index + MAX_PAGE_SIZE if end_index is None else end_index, total)
    if end < start_index or end - start_index > MAX_PAGE_SIZE:
        raise ToolError("Request a valid range of at most 100 results (end_index is exclusive).")
    return ResultsPage(
        cache_key=cache_key,
        total_count=total,
        start_index=start_index,
        end_index=end,
        results=entry.results[start_index:end],
    )


@mcp.tool(annotations=LOCAL_READ)
async def nemad_list_cache() -> CacheList:
    """List unexpired cache keys and result counts for this local single-user process."""
    _prune_cache()
    return CacheList(
        entries=[
            CacheSummary(cache_key=key, results_count=len(entry.results))
            for key, entry in _search_cache.items()
        ]
    )
