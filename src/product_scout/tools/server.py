"""The `scout` SDK MCP server (spec docs/handoff.md §3, build order steps
5 and 8).

`record_product` only. `ask_user`/`ask_topic`/`ask_choice` are explicitly
out of scope here — `phases/refine.py` (build order step 7) calls
`QuestionPort` directly rather than wiring those as SDK tools; see that
module's docstring for why.
"""

from __future__ import annotations

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server

from product_scout.hooks.ledger import FetchLedger
from product_scout.hooks.progress import ProgressFn
from product_scout.models import Location, SurveyReport
from product_scout.tools.record_product import ProductSink, make_record_product


def build_scout_server(
    sink: ProductSink,
    survey: SurveyReport,
    ledger: FetchLedger,
    location: Location,
    low_evidence_mode: bool,
    progress: ProgressFn | None = None,
) -> McpSdkServerConfig:
    """Wire `record_product` (writing into `sink`, evidence-scored against
    `survey`, §4.3-validated against `ledger`, §10.3-gated against
    `location`, §8.3/§14-gated against `low_evidence_mode`, §16.2-ticked
    against `progress`) into the `scout` server.

    Referenced on the wire as `mcp__scout__record_product`, matching §3's
    `allowed_tools` entries.
    """
    return create_sdk_mcp_server(
        name="scout",
        tools=[make_record_product(sink, survey, ledger, location, low_evidence_mode, progress)],
    )
