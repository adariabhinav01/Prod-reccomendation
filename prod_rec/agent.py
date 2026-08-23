"""Core agent logic for the product recommendation agent.

Uses the Claude Agent SDK to drive a tool-use loop: given a product type,
the agent searches the web (via the built-in WebSearch tool) and returns a
set of recommended products.

Skills that should run at specific points in the flow (e.g. filtering,
ranking, formatting) live under `.claude/skills/` and are picked up
automatically by the SDK when `cwd` is set to the project root.
"""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions  # TODO: also import `query` when implementing get_recommendations

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = """\
# TODO: write the system prompt that instructs Claude how to search for
# products and what a good recommendation response looks like.
"""


def build_agent_options() -> ClaudeAgentOptions:
    """Build the ClaudeAgentOptions used for every recommendation request."""
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["WebSearch"],
        cwd=str(PROJECT_ROOT),
    )


async def get_recommendations(product_type: str) -> str:
    """Return product recommendations for the given product type.

    Args:
        product_type: The kind of product to recommend, e.g. "running shoes".

    Returns:
        A string containing the agent's recommendations.
    """
    # TODO: implement — call claude_agent_sdk.query() with build_agent_options()
    # and the product_type, then collect/return the final response text.
    raise NotImplementedError("get_recommendations is not implemented yet")
