"""The `scout` SDK MCP server (spec docs/handoff.md §3, build order step 5).

`record_product` only. `ask_user` is explicitly out of scope here — it's
fully spec'd in §3 but no phase in this build order wires it as an SDK tool
yet (that's Phase 6/7's refine loop); adding it now would be dead code with
no caller and no test target.
"""

from __future__ import annotations

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server

from product_scout.tools.record_product import ProductSink, make_record_product


def build_scout_server(sink: ProductSink) -> McpSdkServerConfig:
    """Wire `record_product` (writing into `sink`) into the `scout` server.

    Referenced on the wire as `mcp__scout__record_product`, matching §3's
    `allowed_tools`/`AgentDefinition.tools` entries.
    """
    return create_sdk_mcp_server(name="scout", tools=[make_record_product(sink)])
