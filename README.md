# NEMAD MCP

A standalone [Model Context Protocol](https://modelcontextprotocol.io) server for
the **Northeast Materials Database (NEMAD)** at **[nemad.org](https://nemad.org)**.
Connect an MCP-compatible assistant to read-only material searches without
giving the assistant your API key in a prompt.

- **Website and account:** https://nemad.org
- **NEMAD API documentation:** https://nemad.org/api-docs
- **Source and issues:** https://github.com/Yibo-Zhang/nemad-mcp

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A NEMAD account/API key for material queries (startup, discovery, and local
  cache inspection work without a key)

This project uses the official **MCP Python SDK 2.x** (tested and locked at **2.3.0**), not the unrelated
third-party `fastmcp` package. It supports the **2026-07-28** protocol via
`server/discover` and older clients via the SDK's `initialize` compatibility path.
The automated tests exercise both eras. See the
[official SDK migration guide](https://py.sdk.modelcontextprotocol.io/migration/).

## Quick start

Get a key from your NEMAD account. Keep it in an environment variable or your
MCP client's secret store, never in Git or a chat prompt.

```bash
export NEMAD_API_KEY="your-api-key"
uvx --from git+https://github.com/Yibo-Zhang/nemad-mcp nemad-mcp
```

This starts a **stdio** server: it waits for an MCP client rather than showing an
interactive prompt. `Ctrl-C` stops it. `mcp-nemad` is also provided as a
backward-compatible executable name. The package has **not** been published to
PyPI; use the Git URL or a local checkout, not `uvx nemad-mcp` alone.

To pin the source revision, replace the Git URL with
`git+https://github.com/Yibo-Zhang/nemad-mcp@<reviewed-commit-sha>`.
This fixes the source, not its transitive dependencies. For a locked dependency
environment, use the local checkout instructions with `uv sync --locked` below.

### MCP client configuration

In an MCP client that accepts a command/args configuration:

```json
{
  "mcpServers": {
    "nemad": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Yibo-Zhang/nemad-mcp",
        "nemad-mcp"
      ],
      "env": {
        "NEMAD_API_KEY": "your-api-key"
      }
    }
  }
}
```

The key above is a placeholder. Prefer your client's secret facility when
available. If your GUI client cannot find `uvx`, use its absolute installed path.
Restart or reconnect the client after changing its configuration.

### Local checkout

```bash
git clone https://github.com/Yibo-Zhang/nemad-mcp.git
cd nemad-mcp
uv sync --locked
uv run --locked nemad-mcp --help
uv run --locked nemad-mcp
```

A local client can use `uv --directory /absolute/path/to/nemad-mcp run --locked nemad-mcp`.

### Optional local Streamable HTTP

```bash
uv run --locked nemad-mcp --transport streamable-http --port 8000
```

Connect a compatible client to `http://127.0.0.1:8000/mcp`. This mode is bound to
loopback and is **single-user only**. The process shares one API key and one cache;
it is not a multi-tenant hosted service and does not implement user authentication.
Do not publicly proxy it. Legacy SSE is not exposed by this CLI; use stdio for
older clients or Streamable HTTP for current clients.

## Tools

All tools return schema-described structured results with a text fallback.
Expected failures are MCP tool errors (`isError: true`), not successful text
containing an error message.

| Tool | Purpose |
| --- | --- |
| `nemad_search` | Search by comma-separated elements, such as `Fe,Co` |
| `nemad_formula_search` | Search by exact chemical formula, such as `Fe2O3` |
| `nemad_read_results` | Read a page from a previous search using its `cache_key` |
| `nemad_list_cache` | List this process's unexpired cache keys and result counts |

`database_type` is one of `magnetic`, `magnetic_anisotropy`, `thermoelectric`, or
`superconductor`. Availability and permissions are controlled by the NEMAD API.

Example tool arguments:

```json
{"database_type":"magnetic","elements":"Fe,Co","exact_match":false,"limit":100}
```

Use the returned `cache_key` in `nemad_read_results`. `start_index` is inclusive
and `end_index` is exclusive. A page contains at most 100 records; omitting
`end_index` requests the next 100. Empty search results produce an empty page.

Search `limit` defaults to 100. It accepts 1–1000, or `-1` for all results subject
to a 10 MiB response-size bound. Prefer a finite limit. Up to 32 searches are
cached for 15 minutes. Cache keys are opaque, process-local, and lost on restart.
These bounds, opaque keys, and structured output replace the earlier implementation's
unbounded cache and text-only result format.

## Configuration and safety

| Variable | Default / purpose |
| --- | --- |
| `NEMAD_API_KEY` | Required only when making material queries |
| `NEMAD_BASE_URL` | `https://api.nemad.org/api` |

The base URL must include the API prefix `/api`. The separate public documentation
page currently contains examples with and without that prefix; the `/api` path is
the route verified during this project's compatibility check. An override is
available for trusted alternative deployments and local tests. Only HTTPS or
loopback HTTP is accepted. Do not send a production key to an untrusted override.

Requests have a 30-second timeout. Redirects are not followed, and HTTP response
bodies, credentials, and raw exception details are not copied into tool errors.
The server uses GET requests only. Query results are untrusted source data, not
instructions for the assistant.

## Development and verification

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv build
```

Tests cover API requests against mocks, input/error handling, bounded caching,
structured tool output, in-process discovery, and actual stdio subprocesses using
both the current protocol and the legacy handshake. No production API key is
required by tests or CI. Passing these checks does **not** establish authenticated
production query availability; that must be verified separately with the account
owner's key. This repository does not deploy NEMAD or publish a PyPI package.

## License

MIT. See [LICENSE](LICENSE) for the license and applicable copyright notices.
