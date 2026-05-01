"""Cycle 1F: call homelab_ssh_run whoami on ciscokid; result contains 'jes'."""

import pytest

from atlas.mcp_client import McpClient


@pytest.mark.asyncio
async def test_homelab_ssh_run_whoami() -> None:
    async with McpClient() as client:
        result = await client.call_tool(
            "homelab_ssh_run",
            {"host": "ciscokid", "command": "whoami"},
        )
        # mcp.types.CallToolResult contains list of content blocks (TextContent etc.)
        text_parts = []
        for content in result.content:
            if hasattr(content, "text"):
                text_parts.append(content.text)
        joined = "\n".join(text_parts)
        assert "jes" in joined, (
            f"expected 'jes' in tool result, got: {joined[:200]}"
        )
