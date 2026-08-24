"""Project-skill discovery guard (spec docs/handoff.md §3, build order
step 5).

§3: "`setting_sources` is a known gotcha. Whether filesystem `.claude/`
config loads by default has changed across SDK versions. Set it explicitly
and add a startup assertion that skills actually loaded — fail loudly
rather than silently running without the recommendation logic."

The SDK doesn't expose a stable, documented "skills actually loaded" signal
on any message type that's cheap to assert against without spending a real
API call. This checks the precondition the SDK's skill loading depends on
directly instead: that `.claude/skills/<name>/SKILL.md` exists on disk. That
catches the exact failure mode §3 names — the skill silently isn't there to
be read — before a single dollar is spent on a live call, and it's testable
with no network or API key involved.
"""

from __future__ import annotations

from pathlib import Path

# src/product_scout/skills.py -> product_scout -> src -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def assert_skill_loaded(skill_name: str, repo_root: Path | None = None) -> None:
    """Raise `RuntimeError` if `.claude/skills/<skill_name>/SKILL.md` is
    missing. Call this before constructing `ClaudeAgentOptions` for any
    agent that depends on that skill's content — fail loudly (§3), don't
    run silently degraded without the research protocol."""
    root = repo_root if repo_root is not None else _REPO_ROOT
    skill_path = root / ".claude" / "skills" / skill_name / "SKILL.md"
    if not skill_path.is_file():
        raise RuntimeError(
            f"Skill '{skill_name}' not found at {skill_path} — "
            "setting_sources=['project'] has nothing to load, and the "
            "agent would run without the research protocol (source "
            "tiering, extraction rules, low-evidence rules). Per §3: fail "
            "loudly rather than silently running degraded."
        )
