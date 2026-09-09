"""End-to-end check: launch the server as a subprocess and speak MCP over stdio.

Proves the console entry point starts, completes the handshake and serves the
tool list. It performs no network I/O, so it runs offline and without an API key.
"""

import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server_params():
    env = dict(os.environ)
    env.pop("ALEPH_API_KEY", None)  # must start fine without credentials
    env["ALEPH_HOST"] = "https://aleph.example.org"
    return StdioServerParameters(
        command=sys.executable, args=["-m", "aleph_mcp"], env=env
    )


async def test_handshake_and_tool_listing(server_params):
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info.name == "aleph"
            assert init.server_info.version == "0.1.0"
            assert "followthemoney" in (init.instructions or "")

            tools = (await session.list_tools()).tools
            assert len(tools) == 10
            assert all(t.annotations.read_only_hint for t in tools)

            search = next(t for t in tools if t.name == "aleph_search")
            assert search.input_schema["required"] == ["query"]
