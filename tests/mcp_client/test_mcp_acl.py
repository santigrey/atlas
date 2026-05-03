"""Cycle 1F: ACL denies homelab_file_write under /home/jes/control-plane/ BEFORE network."""

import pytest

from atlas.mcp_client import AtlasAclDenied, McpClient


pytestmark = pytest.mark.homelab


@pytest.mark.asyncio
async def test_acl_denies_control_plane_write() -> None:
    async with McpClient() as client:
        with pytest.raises(AtlasAclDenied):
            await client.call_tool(
                "homelab_file_write",
                {
                    "host": "ciscokid",
                    "path": "/home/jes/control-plane/test_acl_denied_should_never_write.txt",
                    "content": "this should never be written",
                },
            )
