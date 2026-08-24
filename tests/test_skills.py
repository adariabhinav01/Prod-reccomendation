"""Unit tests for skills.py — the §3 "fail loudly" startup assertion
(build order step 5)."""

import pytest

from product_scout import config
from product_scout.skills import assert_skill_loaded


def test_research_protocol_skill_actually_exists_in_this_repo():
    """No override — resolves the real repo root. This is the assertion
    that would fire during a live run; pinning it here means a future
    accidental deletion/rename of the skill file fails the test suite
    instead of only failing loudly at run time."""
    assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)  # must not raise


def test_missing_skill_raises_with_actionable_message(tmp_path):
    with pytest.raises(RuntimeError) as excinfo:
        assert_skill_loaded("nonexistent-skill", repo_root=tmp_path)
    message = str(excinfo.value)
    assert "nonexistent-skill" in message
    assert "fail loudly" in message.lower()


def test_present_skill_at_custom_root_does_not_raise(tmp_path):
    skill_dir = tmp_path / ".claude" / "skills" / "a-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: a-skill\n---\n")
    assert_skill_loaded("a-skill", repo_root=tmp_path)  # must not raise


def test_directory_without_skill_md_file_raises(tmp_path):
    """A skill directory that exists but has no SKILL.md inside it is the
    same failure mode as a missing directory — nothing for the SDK to read."""
    (tmp_path / ".claude" / "skills" / "a-skill").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        assert_skill_loaded("a-skill", repo_root=tmp_path)
