"""Cycle 1F: connect + list_tools returns >=13 tools (per Phase 3 ratification 77759f8)."""

import pytest

from atlas.mcp_client import McpClient


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_connect_and_list_tools() -> None:
    async with McpClient() as client:
        tools = await client.list_tools()
        assert len(tools) >= 13, f"expected >=13 tools, got {len(tools)}"
        names = {t.name for t in tools}
        assert "homelab_ssh_run" in names
        assert "homelab_file_write" in names
