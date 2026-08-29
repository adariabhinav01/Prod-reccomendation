"""Unit tests for skills.py — the §3 "fail loudly" startup assertion
(build order step 5) and the §16 skill-hashing helpers (build order step 14)."""

import hashlib

import pytest

from product_scout import config
from product_scout.skills import assert_skill_loaded, hash_required_skills, hash_skill


def test_research_protocol_skill_actually_exists_in_this_repo():
    """No override — resolves the real repo root. This is the assertion
    that would fire during a live run; pinning it here means a future
    accidental deletion/rename of the skill file fails the test suite
    instead of only failing loudly at run time."""
    assert_skill_loaded(config.RESEARCH_PROTOCOL_SKILL)  # must not raise


def test_recommendation_logic_skill_actually_exists_in_this_repo():
    """Same guard as the research-protocol test above, for the skill
    Phases 6a/6b (build order step 9) depend on — CLAUDE.md's invariant
    that running without it "produces plausible-looking garbage rather
    than an error" is exactly what this pins against regressing silently."""
    assert_skill_loaded(config.RECOMMENDATION_LOGIC_SKILL)  # must not raise


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


def test_hash_skill_returns_sha256_hex_digest_of_skill_md_bytes(tmp_path):
    skill_dir = tmp_path / ".claude" / "skills" / "a-skill"
    skill_dir.mkdir(parents=True)
    content = b"---\nname: a-skill\n---\nSome instructions.\n"
    (skill_dir / "SKILL.md").write_bytes(content)

    assert hash_skill("a-skill", repo_root=tmp_path) == hashlib.sha256(content).hexdigest()


def test_hash_skill_changes_when_skill_md_content_changes(tmp_path):
    skill_dir = tmp_path / ".claude" / "skills" / "a-skill"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"

    skill_md.write_text("version one")
    first = hash_skill("a-skill", repo_root=tmp_path)

    skill_md.write_text("version two")
    second = hash_skill("a-skill", repo_root=tmp_path)

    assert first != second


def test_hash_skill_raises_on_missing_skill(tmp_path):
    with pytest.raises(RuntimeError):
        hash_skill("nonexistent-skill", repo_root=tmp_path)


def test_hash_required_skills_returns_one_entry_per_skill_name(tmp_path):
    for name in ("a", "b"):
        skill_dir = tmp_path / ".claude" / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"content for {name}")

    result = hash_required_skills(["a", "b"], repo_root=tmp_path)

    assert result == {
        "a": hash_skill("a", repo_root=tmp_path),
        "b": hash_skill("b", repo_root=tmp_path),
    }


def test_hash_skill_of_research_protocol_skill_in_this_repo():
    """No override — resolves the real repo root, same spirit as the
    no-override `assert_skill_loaded` tests above: pins that hashing the
    real skill file doesn't silently break."""
    digest = hash_skill(config.RESEARCH_PROTOCOL_SKILL)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
