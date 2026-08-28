"""The §4.3 provenance ledger (spec docs/handoff.md §4.3/§13, build order
step 8).

`FetchLedger` is the run-scoped record of every URL this run has actually
fetched or merely seen in search results. `tools/record_product.py`'s §4.3
enforcement point checks every `source_url` against it before constructing
a `Product` — presence of a well-formed URL was never a hallucination guard
(a model can invent a plausible one); only a ledger the model didn't write
to itself can be.

### Two write paths, matching §4.3's table exactly

| Mode | Written by | Admissible for |
|---|---|---|
| `fetched` | `PostToolUse` on `WebFetch` | Anything, including `Product.specs` |
| `seen_not_fetched` | `PostToolUse` on `WebSearch` | Judgment-bearing values only |

`record_fetch`/`record_seen` are the two write paths. `is_admissible` is
the one read path `record_product.py` needs, parameterized by
`require_fetched` so the same ledger answers both questions §4.3 asks:
"was this fetched" (specs, price) and "was this at least seen" (judgment-
bearing values like `ownership_notes`).

### `LedgerEntry` — §13's fuller record (build order step 13, added post-hoc)

§13's `PostToolUse` bullet, quoted in full: "Append every fetched URL
(pre- and post-redirect), **status, and timestamp**." Build order step 8
only ever stored the URL and its access mode — `mode_for`/`is_admissible`
never needed more than that, since §4.3's validation is purely "was this
URL seen, in what mode." `status`/`observed_at` sat unimplemented until a
re-verification pass against §13's literal text caught the gap. `entry_for`
below is the accessor for this fuller record; `mode_for`/`is_admissible`
are UNCHANGED — every existing caller (`record_product.py`,
`phases/survey.py`'s ledger validation) keeps working exactly as before.
`status` is best-effort (see `_extract_status`'s own docstring on why it
can't be more than that); `observed_at` is when THIS HOOK recorded the
entry, not a claim about the live tool call's own internal timing, which
this environment can't observe either.

### Normalization is reused, not reimplemented

`models.py`'s `normalize_url` already implements §4.3's exact rule (scheme
+ host + path, query discarded) — its own docstring says as much: "Shared
by `confidence.py` ... and, later, the run-scoped fetch ledger (build
order step 8)." Reimplementing it here would risk the two drifting apart,
which is exactly the failure §4.3 warns about ("a redirect/tracking-
parameter mismatch between them would silently reject good data").

### Redirects: both URLs go in as `fetched`

§4.3: "record both pre- and post-redirect URLs." `record_fetch(url,
redirected_to=...)` marks both the requested URL and, if a redirect
landed somewhere else, the final URL as `fetched` — citing either one is
correct provenance, and rejecting one over the other is exactly the false
rejection §4.3 calls "the worst possible failure shape... because it looks
like clean extraction."

### `FetchLedger` is a plain, mutable class — not a pydantic model, not
### persisted

Like `tools/record_product.py`'s `ListProductSink`, it exists only for the
lifetime of a live run to answer "was this URL actually seen," and nothing
in `RunRecord` (models.py, locked) has a field for it — it's derived,
run-scoped, throwaway state, not part of the persisted record.

### `is_run_scoped`, not phase-scoped — a note on what this build step does
### and doesn't wire

§4.3: "The ledger is run-scoped, not phase-scoped. Extraction legitimately
cites pages SURVEY fetched." No orchestrator exists yet (every phase module
built so far — `survey.py`, `refine.py` — is independently callable, not
wired into a shared run loop), so nothing in this codebase can yet
literally carry one `FetchLedger` instance across a SURVEY query() and an
EXTRACTION query(). `phases/extraction.py`'s `Extractor.extract()` and
`SdkExtractor.extract()` both take `ledger: FetchLedger` as a REQUIRED
parameter (no default) for exactly this reason: defaulting silently to a
fresh, empty ledger per call would be a *correct-looking* way to violate
"run-scoped, not phase-scoped" the moment an orchestrator exists and starts
passing one ledger through multiple phases — better to force that decision
on the caller now than paper over it with a convenient default.

### The `PostToolUse` hook itself is best-effort and unverified against a
### live response

`make_ledger_post_tool_use_hook` / `ledger_hook_matchers` below build a
real, wireable `HookMatcher` list for `ClaudeAgentOptions(hooks={...})` —
`SdkExtractor` (phases/extraction.py) does wire it, so a live EXTRACTION
run's own `WebFetch` calls populate the same ledger `record_product`
validates against. But `PostToolUseHookInput["tool_response"]` is typed
`Any` — its concrete shape for the built-in `WebFetch`/`WebSearch` tools is
undocumented by the SDK and not something this environment can observe (no
`ANTHROPIC_API_KEY` in this suite — the same limitation `phases/survey.py`
already flags for `_flatten_strings`/`evidence_pool`). `_extract_urls` and
`_extract_status` (the latter added build order step 13, for `LedgerEntry.
status`) are both deliberately tolerant for this reason, exactly like
`_flatten_strings`. What *is* fully verified here, independent of that
uncertainty, is `FetchLedger` itself (now including `LedgerEntry`/
`entry_for`) and `record_product.py`'s consumption of it — the three cases
build order step 8 names — plus `_extract_status`'s own pure logic against
hand-built payload shapes (not a live response), since all of these are
pure Python, exercised directly with hand-built inputs, no live call or
hook required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from claude_agent_sdk import HookMatcher

from product_scout.models import normalize_url

AccessMode = Literal["fetched", "seen_not_fetched"]

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


@dataclass(frozen=True)
class LedgerEntry:
    """§13's fuller per-URL record — mode, status, and timestamp — behind
    `FetchLedger.entry_for`. Frozen: a ledger entry is a fact about what
    this run observed, never mutated in place; a later write (e.g. an
    upgrade from `seen_not_fetched` to `fetched`) replaces the whole entry,
    it doesn't edit one field of an existing one."""

    mode: AccessMode
    status: str | None  # best-effort; see _extract_status
    observed_at: datetime  # when THIS HOOK recorded the entry


class FetchLedger:
    """Run-scoped record of every URL this run has fetched or seen (§4.3)."""

    def __init__(self) -> None:
        self._fetched: set[str] = set()
        self._seen: set[str] = set()
        self._entries: dict[str, LedgerEntry] = {}

    def record_fetch(
        self,
        url: str,
        *,
        redirected_to: str | None = None,
        status: str | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        """§4.3: written by `PostToolUse` on `WebFetch`. Marks `url` as
        `fetched`, and `redirected_to` too if a redirect occurred — see
        module docstring. `status`/`observed_at` (§13, build order step 13)
        are recorded for both URLs identically — they came from the same
        tool call/response."""
        if not url:
            return
        when = observed_at if observed_at is not None else datetime.now(timezone.utc)
        normalized = normalize_url(url)
        self._fetched.add(normalized)
        self._entries[normalized] = LedgerEntry(mode="fetched", status=status, observed_at=when)
        if redirected_to:
            redirected_normalized = normalize_url(redirected_to)
            self._fetched.add(redirected_normalized)
            self._entries[redirected_normalized] = LedgerEntry(
                mode="fetched", status=status, observed_at=when
            )

    def record_seen(
        self, url: str, *, status: str | None = None, observed_at: datetime | None = None
    ) -> None:
        """§4.3: written by `PostToolUse` on `WebSearch`. A URL that
        appeared in search results but was never fetched. A URL already
        marked `fetched` is left alone — `fetched` is strictly more
        admissible than `seen_not_fetched`, so a later search result for
        an already-fetched URL must never downgrade it (its `LedgerEntry`
        is left alone too, for the identical reason)."""
        if not url:
            return
        normalized = normalize_url(url)
        if normalized in self._fetched:
            return
        self._seen.add(normalized)
        when = observed_at if observed_at is not None else datetime.now(timezone.utc)
        self._entries[normalized] = LedgerEntry(
            mode="seen_not_fetched", status=status, observed_at=when
        )

    def mode_for(self, url: str) -> AccessMode | None:
        """`None` means the ledger has never seen this URL in any form —
        the case a model inventing a plausible-but-uncited URL produces."""
        if not url:
            return None
        normalized = normalize_url(url)
        if normalized in self._fetched:
            return "fetched"
        if normalized in self._seen:
            return "seen_not_fetched"
        return None

    def entry_for(self, url: str) -> LedgerEntry | None:
        """§13's fuller record — status and timestamp — behind `mode_for`'s
        bare mode. `None` under the exact same condition `mode_for` returns
        `None`: the ledger has never seen this URL in any form. Not used by
        §4.3's own admissibility check (`is_admissible` below still only
        needs `mode_for`) — this is the seam §5.4/§5.5 (unbuilt) would read
        from, per §13's "makes §5.4/§5.5 free" framing."""
        if not url:
            return None
        return self._entries.get(normalize_url(url))

    def all_entries(self) -> dict[str, LedgerEntry]:
        """Every URL this run has fetched or seen, keyed by its normalized
        form, with its full `LedgerEntry`. Read-only view (returns a copy)
        — added for `eval.py`'s `capture_case` (build order step 15),
        which needs to serialize a completed run's ledger into a golden
        case's frozen fixtures; no other caller needs to enumerate the
        whole ledger, so this stayed unbuilt until now."""
        return dict(self._entries)

    def is_admissible(self, url: str, *, require_fetched: bool) -> bool:
        """§4.3's admissibility rule. `require_fetched=True` for spec
        values and price (facts, never judgment); `False` for judgment-
        bearing values (`ownership_notes`, review sources), which accept
        either access mode — a `seen_not_fetched` search snippet is a
        legitimate basis for "this reviewer said X," never for a spec
        figure."""
        mode = self.mode_for(url)
        if mode is None:
            return False
        if require_fetched:
            return mode == "fetched"
        return True


def _extract_urls(value: object) -> list[str]:
    """Best-effort URL extraction from an opaque tool_response payload —
    see module docstring's "best-effort and unverified" note. Mirrors
    `phases/survey.py`'s `_flatten_strings` tolerance for an SDK-version-
    dependent shape, but scans for URL-looking substrings specifically
    rather than collecting every string leaf."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return _URL_RE.findall(text)


_STATUS_KEYS = ("status", "http_status", "status_code")


def _extract_status(tool_response: object) -> str | None:
    """Best-effort status extraction for `LedgerEntry.status` (§13) — same
    "best-effort and unverified" caveat as `_extract_urls`:
    `PostToolUseHookInput["tool_response"]`'s concrete shape is undocumented
    by the SDK. Checks a few conventional key names first; failing that,
    checks for a truthy `error`/`is_error` flag. A response that reaches
    THIS hook at all already represents some level of completion — this
    codebase never wires the SDK's separate `PostToolUseFailure` event — so
    an unrecognized-but-dict-shaped response defaults to `"ok"` rather than
    `None`. `None` is reserved for a `tool_response` this function can't
    even inspect as a mapping (e.g. a bare string, or absent entirely)."""
    if not isinstance(tool_response, dict):
        return None
    for key in _STATUS_KEYS:
        value = tool_response.get(key)
        if value is not None:
            return str(value)
    if tool_response.get("error") or tool_response.get("is_error"):
        return "error"
    return "ok"


def make_ledger_post_tool_use_hook(ledger: FetchLedger):
    """Factory mirroring `tools/record_product.py`'s
    `make_record_product(sink, ...)` pattern. Returns a `HookCallback`
    (see `claude_agent_sdk.types.HookCallback`) that writes into `ledger`
    on `WebFetch`/`WebSearch` `PostToolUse` events; every other event this
    hook might be matched against (it shouldn't be, given
    `ledger_hook_matchers`' matcher string, but hooks are best kept
    defensive) is a no-op."""

    async def _ledger_hook(hook_input, tool_use_id, context):  # noqa: ARG001
        tool_name = hook_input.get("tool_name")
        tool_input = hook_input.get("tool_input") or {}
        tool_response = hook_input.get("tool_response")
        status = _extract_status(tool_response)

        if tool_name == "WebFetch":
            requested = tool_input.get("url")
            if requested:
                candidates = _extract_urls(tool_response)
                redirected_to = next(
                    (
                        u
                        for u in candidates
                        if normalize_url(u) != normalize_url(requested)
                    ),
                    None,
                )
                ledger.record_fetch(requested, redirected_to=redirected_to, status=status)
        elif tool_name == "WebSearch":
            for url in _extract_urls(tool_response):
                ledger.record_seen(url, status=status)

        return {}

    return _ledger_hook


def ledger_hook_matchers(ledger: FetchLedger) -> list[HookMatcher]:
    """Ready-to-use `list[HookMatcher]` for
    `ClaudeAgentOptions(hooks={"PostToolUse": ledger_hook_matchers(ledger)})`.
    """
    return [
        HookMatcher(matcher="WebFetch|WebSearch", hooks=[make_ledger_post_tool_use_hook(ledger)])
    ]
