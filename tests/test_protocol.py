"""Protocol discovery, legacy handshakes, schemas, and real stdio startup."""

import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from nemad import __version__, server

TOOL_NAMES = {
    "nemad_search",
    "nemad_formula_search",
    "nemad_read_results",
    "nemad_list_cache",
}


@pytest.mark.parametrize(("mode", "version"), [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
async def test_inprocess_protocol_paths(mode, version):
    async with Client(server.mcp, mode=mode) as client:
        assert client.protocol_version == version
        assert client.server_info.name == "nemad"
        assert client.server_info.version == __version__
        assert (client.session.initialize_result is not None) == (mode == "legacy")
        assert (client.session.discover_result is not None) == (mode == "auto")
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == TOOL_NAMES
        result = await client.call_tool("nemad_list_cache", {})
        assert not result.is_error
        assert result.structured_content == {"entries": []}


async def test_tool_schemas_and_readonly_annotations(client):
    listing = await client.list_tools()
    tools = {tool.name: tool for tool in listing.tools}
    assert set(tools) == TOOL_NAMES
    expected_outputs = {
        "nemad_search": {"database_type", "count", "results_returned", "cache_key"},
        "nemad_formula_search": {"database_type", "count", "results_returned", "cache_key"},
        "nemad_read_results": {"cache_key", "total_count", "start_index", "end_index", "results"},
        "nemad_list_cache": {"entries"},
    }
    for name, tool in tools.items():
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema["type"] == "object"
        assert expected_outputs[name] <= tool.output_schema["properties"].keys()
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.description
    for name in ("nemad_search", "nemad_formula_search"):
        assert tools[name].annotations.open_world_hint is True
        assert (
            "magnetic_anisotropy" in tools[name].input_schema["properties"]["database_type"]["enum"]
        )
    for name in ("nemad_read_results", "nemad_list_cache"):
        assert tools[name].annotations.open_world_hint is False
        assert tools[name].annotations.idempotent_hint is True


@pytest.mark.parametrize(("mode", "version"), [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
async def test_real_stdio_subprocess(mode, version):
    root = Path(__file__).resolve().parents[1]
    env = {"PYTHONPATH": str(root / "src"), "PYTHONUNBUFFERED": "1"}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    target = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nemad"],
        cwd=str(root),
        env=env,
    )
    with anyio.fail_after(20):
        async with Client(target, mode=mode) as client:
            assert client.protocol_version == version
            assert client.server_info.name == "nemad"
            assert (client.session.initialize_result is not None) == (mode == "legacy")
            assert (client.session.discover_result is not None) == (mode == "auto")
            listing = await client.list_tools()
            assert {tool.name for tool in listing.tools} == TOOL_NAMES
            result = await client.call_tool("nemad_list_cache", {})
            assert not result.is_error
            assert result.structured_content == {"entries": []}
            missing_key = await client.call_tool(
                "nemad_search", {"database_type": "magnetic", "elements": "Fe"}
            )
            assert missing_key.is_error
            assert "NEMAD_API_KEY" in missing_key.content[0].text
