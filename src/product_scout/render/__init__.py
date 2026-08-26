"""Deterministic report rendering (spec docs/handoff.md §5, §1: 'Phase 8
RENDER — deterministic Python, no model'; §0: 'question-asking and
rendering must sit behind swappable interfaces'). No Claude Agent SDK call
happens anywhere in this package — everything here is a pure function of an
already-validated `RunRecord`, which is what makes it testable against a
hand-written fixture before Opus (phases 6-7) exists."""
