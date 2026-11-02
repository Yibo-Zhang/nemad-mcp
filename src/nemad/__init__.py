"""NEMAD MCP server command-line entry point."""

import argparse
import logging

__version__ = "0.2.0"


def main() -> None:
    """Serve NEMAD over stdio, or a local single-user Streamable HTTP endpoint."""
    from .server import mcp

    parser = argparse.ArgumentParser(description="NEMAD Model Context Protocol server")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="Local HTTP port (default: 8000)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    logging.basicConfig(level=logging.WARNING)  # stderr only; stdout is the MCP wire.
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)
