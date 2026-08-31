"""The web port (spec `docs/web_handoff.md`, web build order W1-W7).

Additive HTTP front end on top of the existing CLI/orchestrator/store —
`docs/handoff.md`'s pipeline (phases, models, prompts, skills, confidence
math, the report renderer) is unchanged. This package owns everything
specific to serving the pipeline over HTTP: the FastAPI app (`app.py`),
the in-memory run registry backing the future-based suspend/resume hold
(`registry.py`), and the run-lifecycle endpoints (`runs.py`). The
`QuestionPort` implementation itself lives at `io/web_port.py`, alongside
`io/cli_port.py`, not in this package — it's a port, not a web-app concern.
"""

from __future__ import annotations
