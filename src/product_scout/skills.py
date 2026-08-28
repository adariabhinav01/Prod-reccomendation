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

import hashlib
from collections.abc import Iterable
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


def hash_skill(skill_name: str, repo_root: Path | None = None) -> str:
    """Return the hex SHA-256 digest of `.claude/skills/<skill_name>/SKILL.md`'s
    raw bytes — populates `RunRecord.skill_hashes` (§16), which `rescore`
    later compares against to warn when a skill's content has drifted since
    the run. Raises the same `RuntimeError` as `assert_skill_loaded` if the
    skill isn't present — a hash can't be computed for a file that doesn't
    exist, and silently omitting it would make `skill_hashes` quietly
    incomplete rather than failing loudly, which is exactly what §3 already
    decided against for this same file."""
    assert_skill_loaded(skill_name, repo_root)
    root = repo_root if repo_root is not None else _REPO_ROOT
    skill_path = root / ".claude" / "skills" / skill_name / "SKILL.md"
    return hashlib.sha256(skill_path.read_bytes()).hexdigest()


def hash_required_skills(
    skill_names: Iterable[str], repo_root: Path | None = None
) -> dict[str, str]:
    """`{skill_name: hash_skill(skill_name)}` for every name in
    `skill_names`. Used to populate `RunRecord.skill_hashes` (§16)."""
    return {name: hash_skill(name, repo_root) for name in skill_names}
