"""Cycle 1F: telemetry to atlas.events with NO arg values (secrets discipline).

Verifies:
- atlas.events row count delta after list_tools + call_tool
- 'tools_list' and 'tool_call' kinds present for source='atlas.mcp_client'
- Tool argument VALUES (e.g. 'whoami', 'ciscokid') NEVER appear in any payload
  -- payloads contain only tool_name + arg_keys + status + duration_ms + endpoint
"""

import json

import pytest

from atlas.db import Database
from atlas.mcp_client import McpClient


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_token_logging_no_arg_values() -> None:
    db = Database()
    await db.open()
    try:
        # capture row count before
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM atlas.events WHERE source='atlas.mcp_client'"
                )
                row = await cur.fetchone()
                assert row is not None
                pre_count = row[0]

        # exercise both list_tools and call_tool with db wired in
        async with McpClient(db=db) as client:
            await client.list_tools()
            await client.call_tool(
                "homelab_ssh_run",
                {"host": "ciscokid", "command": "whoami"},
            )

        # verify count went up by exactly 2 (1 tools_list + 1 tool_call)
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM atlas.events WHERE source='atlas.mcp_client'"
                )
                row = await cur.fetchone()
                assert row is not None
                post_count = row[0]
                assert post_count == pre_count + 2, (
                    f"expected +2 events (tools_list + tool_call), "
                    f"got delta {post_count - pre_count}"
                )

                # fetch the 2 latest rows + audit secrets discipline
                await cur.execute(
                    "SELECT kind, payload FROM atlas.events "
                    "WHERE source='atlas.mcp_client' "
                    "ORDER BY ts DESC LIMIT 2"
                )
                latest = await cur.fetchall()
                assert len(latest) == 2
                kinds = {r[0] for r in latest}
                assert kinds == {"tools_list", "tool_call"}, (
                    f"expected kinds {{tools_list, tool_call}}, got {kinds}"
                )

                # CRITICAL: arg VALUES (whoami, ciscokid) must NEVER appear in any payload
                for kind, payload in latest:
                    payload_str = (
                        json.dumps(payload)
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    assert "whoami" not in payload_str, (
                        f"SECRETS LEAK: arg value 'whoami' found in {kind} "
                        f"payload: {payload_str[:300]}"
                    )
                    assert "ciscokid" not in payload_str, (
                        f"SECRETS LEAK: arg value 'ciscokid' found in {kind} "
                        f"payload: {payload_str[:300]}"
                    )

                # tool_call payload structure validation
                for kind, payload in latest:
                    if kind == "tool_call":
                        assert payload["tool_name"] == "homelab_ssh_run"
                        assert payload["arg_keys"] == ["command", "host"]  # sorted
                        assert payload["status"] == "success"
                        assert isinstance(payload["duration_ms"], (int, float))
                        assert payload["endpoint"].startswith("https://")
    finally:
        await db.close()
