# Product Scout

Python application on the Claude Agent SDK that produces balanced,
research-backed product recommendations for any product category.

## Setup

```
pip install -e .
cp .env.example .env   # fill in ANTHROPIC_API_KEY
```

## Usage

```
scout config set location.country DE      # one-time setup; asked interactively if skipped
scout config set location.currency EUR

scout research "standing desks"           # run the full pipeline, produce a recommendation
scout research --resume <run_id>          # pick up a partially-completed run
scout research "standing desks" --location US

scout history                             # list past runs
scout history --category "standing desks"

scout rescore <run_id> --set "Product Name=199"   # re-score a past run with a price overridden

scout eval --stable-only                  # golden set (§17.1) — replays frozen fixtures, no network
scout eval                                # golden set, live — hits the real web; run manually
```

## Where things live

- `docs/handoff.md` — the authoritative build spec (v7). Read this before
  changing anything in `src/`.
- `CLAUDE.md` — build-order status, the eleven invariants, and
  session-level notes for whoever (human or Claude) is working on this
  codebase next.
- `.claude/skills/` — the four skills phases 1-6b depend on
  (`research-protocol`, `question-design`, `recommendation-logic`,
  `market-timing`). The app fails loudly at startup if any is missing.
- `eval/cases/` — the golden set's frozen fixtures (§17.1). `scout eval
  --stable-only` gates every skill edit against these.

## Tests

```
pytest
```
