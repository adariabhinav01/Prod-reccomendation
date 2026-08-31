"""FastAPI app factory (spec `docs/web_handoff.md` §4/§5/§6, web build
order W1).

Local, single-user, no auth (§0) — `create_app()` binds nothing itself;
binding to `127.0.0.1` only, never `0.0.0.0`, is `cli.py`'s `scout serve`
subcommand's job (§6), not this module's.

`ANTHROPIC_API_KEY` is checked once, at startup, before the app finishes
initializing — this is genuinely new: `cli.py`'s other subcommands run
`load_dotenv()` with no validation at all and rely on the Claude Agent SDK
itself failing on the first real `query()` call. §5 is explicit that the
web server must fail fast with a clear message instead, matching
`config.py`'s own docstring ("`ANTHROPIC_API_KEY` handling belongs at a
future `cli.py` entrypoint") — this is that entrypoint's web counterpart.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from product_scout.models import RunRecord
from product_scout.orchestrator import run_pipeline as _default_pipeline_runner
from product_scout.store.runs import RunStore
from product_scout.web import runs as runs_module
from product_scout.web.registry import RunRegistry

PipelineRunner = Callable[..., Awaitable[RunRecord | None]]


class MissingApiKeyError(RuntimeError):
    """Raised at startup when `ANTHROPIC_API_KEY` is unset — §5: fail
    before binding, with a clear message, not mid-run."""


def assert_api_key_present() -> None:
    """Raises `MissingApiKeyError` if `ANTHROPIC_API_KEY` is unset. Called
    from `create_app`'s startup lifespan, and directly by `cli.py`'s
    `scout serve` before it ever calls `create_app`/`uvicorn.run` — two
    layers of the same check, same reasoning as `run_pipeline`'s own
    `_REQUIRED_SKILLS` assertion plus each `Sdk<Phase>` adapter's own copy:
    the earlier check gives a cleaner message for the common path (running
    `scout serve` directly), the later one is a safety net for anything
    that constructs the ASGI app another way."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingApiKeyError(
            "ANTHROPIC_API_KEY is not set. `scout serve` needs it before it can "
            "run any research phase — set it in the environment or in a .env "
            "file, then try again."
        )


def create_app(
    *,
    run_store: RunStore | None = None,
    pipeline_runner: PipelineRunner | None = None,
    check_api_key: bool = True,
) -> FastAPI:
    """`run_store`/`pipeline_runner` default to the real adapters
    (`RunStore()`, `orchestrator.run_pipeline`) — overridable so tests can
    inject an isolated store root and a fake pipeline, mirroring
    `cli.py main()`'s and `orchestrator.run_pipeline`'s own DI pattern.
    `check_api_key=False` lets tests that never start a real run skip the
    startup check entirely, rather than every test needing a real key set."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if check_api_key:
            assert_api_key_present()
        yield

    app = FastAPI(title="Product Scout", lifespan=lifespan)
    app.state.run_store = run_store or RunStore()
    app.state.pipeline_runner = pipeline_runner or _default_pipeline_runner
    app.state.registry = RunRegistry()

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(runs_module.router)
    return app
