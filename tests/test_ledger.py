"""Unit tests for hooks/ledger.py — the §4.3 provenance ledger (build
order step 8). Pure Python, no SDK/model call needed — `FetchLedger` is
constructed directly and fed hand-built fetch/search records, exactly the
way a real `PostToolUse` hook would populate it, but without depending on
that hook's own (unverified, see module docstring) parsing of a live tool
response.
"""

from product_scout.hooks.ledger import FetchLedger

FETCHED_URL = "https://example.com/spec-sheet"
REDIRECT_TARGET = "https://cdn.example.com/spec-sheet-final"
SEARCH_ONLY_URL = "https://forum.example.com/thread/42"
NEVER_SEEN_URL = "https://example.com/invented-page"


# -- mode_for / is_admissible basics -----------------------------------------


def test_unseen_url_has_no_mode_and_is_never_admissible():
    ledger = FetchLedger()
    assert ledger.mode_for(NEVER_SEEN_URL) is None
    assert ledger.is_admissible(NEVER_SEEN_URL, require_fetched=True) is False
    assert ledger.is_admissible(NEVER_SEEN_URL, require_fetched=False) is False


def test_fetched_url_is_admissible_for_both_specs_and_judgment():
    ledger = FetchLedger()
    ledger.record_fetch(FETCHED_URL)
    assert ledger.mode_for(FETCHED_URL) == "fetched"
    assert ledger.is_admissible(FETCHED_URL, require_fetched=True) is True
    assert ledger.is_admissible(FETCHED_URL, require_fetched=False) is True


# -- §4.3 case 1: a spec citing an unfetched URL is rejected -----------------


def test_spec_value_requires_fetched_not_just_seen():
    """A URL never recorded at all — the case a model inventing a
    plausible-but-uncited source_url produces — must fail require_fetched."""
    ledger = FetchLedger()
    assert ledger.is_admissible(NEVER_SEEN_URL, require_fetched=True) is False


def test_search_only_url_is_not_admissible_as_fetched():
    """Distinguishes 'never seen' from 'seen but only via search' — both
    fail require_fetched=True, but for a different, more specific reason
    (covered by test_seen_not_fetched_* below)."""
    ledger = FetchLedger()
    ledger.record_seen(SEARCH_ONLY_URL)
    assert ledger.mode_for(SEARCH_ONLY_URL) == "seen_not_fetched"
    assert ledger.is_admissible(SEARCH_ONLY_URL, require_fetched=True) is False


# -- §4.3 case 2: a redirect must not cause false rejection ------------------


def test_both_pre_and_post_redirect_urls_are_admissible():
    ledger = FetchLedger()
    ledger.record_fetch(FETCHED_URL, redirected_to=REDIRECT_TARGET)

    assert ledger.is_admissible(FETCHED_URL, require_fetched=True) is True
    assert ledger.is_admissible(REDIRECT_TARGET, require_fetched=True) is True


def test_redirect_with_no_actual_redirect_only_records_the_one_url():
    ledger = FetchLedger()
    ledger.record_fetch(FETCHED_URL, redirected_to=None)
    assert ledger.is_admissible(FETCHED_URL, require_fetched=True) is True
    assert ledger.is_admissible(REDIRECT_TARGET, require_fetched=True) is False


def test_normalization_ignores_query_string_and_trailing_slash():
    """§4.3: 'Match on scheme + host + path with query string discarded.'
    A citation with a different query string or a trailing slash than what
    was literally fetched must not be falsely rejected."""
    ledger = FetchLedger()
    ledger.record_fetch("https://example.com/spec-sheet?utm_source=x")
    assert ledger.is_admissible("https://example.com/spec-sheet", require_fetched=True) is True
    assert ledger.is_admissible("https://example.com/spec-sheet/", require_fetched=True) is True


def test_normalization_is_case_insensitive_on_scheme_and_host():
    ledger = FetchLedger()
    ledger.record_fetch("HTTPS://Example.com/spec-sheet")
    assert ledger.is_admissible("https://example.com/spec-sheet", require_fetched=True) is True


# -- §4.3 case 3: seen_not_fetched admissible for judgment, not specs --------


def test_seen_not_fetched_admissible_for_judgment_bearing_values():
    ledger = FetchLedger()
    ledger.record_seen(SEARCH_ONLY_URL)
    assert ledger.is_admissible(SEARCH_ONLY_URL, require_fetched=False) is True


def test_seen_not_fetched_not_admissible_for_specs():
    ledger = FetchLedger()
    ledger.record_seen(SEARCH_ONLY_URL)
    assert ledger.is_admissible(SEARCH_ONLY_URL, require_fetched=True) is False


def test_fetched_after_seen_upgrades_admissibility():
    """A URL first seen in search, later actually fetched (e.g. by a
    different phase), becomes admissible for specs too — 'fetched'
    strictly supersedes 'seen_not_fetched'."""
    ledger = FetchLedger()
    ledger.record_seen(SEARCH_ONLY_URL)
    ledger.record_fetch(SEARCH_ONLY_URL)
    assert ledger.mode_for(SEARCH_ONLY_URL) == "fetched"
    assert ledger.is_admissible(SEARCH_ONLY_URL, require_fetched=True) is True


def test_seen_after_fetched_does_not_downgrade():
    """The reverse ordering must never regress an already-fetched URL back
    down to search-only — see FetchLedger.record_seen's own guard."""
    ledger = FetchLedger()
    ledger.record_fetch(FETCHED_URL)
    ledger.record_seen(FETCHED_URL)
    assert ledger.mode_for(FETCHED_URL) == "fetched"
    assert ledger.is_admissible(FETCHED_URL, require_fetched=True) is True


# -- blank/empty input is never admissible, never crashes --------------------


def test_empty_url_is_never_admissible():
    ledger = FetchLedger()
    assert ledger.mode_for("") is None
    assert ledger.is_admissible("", require_fetched=False) is False


def test_recording_empty_url_is_a_no_op():
    ledger = FetchLedger()
    ledger.record_fetch("")
    ledger.record_seen("")
    assert ledger.mode_for("") is None
