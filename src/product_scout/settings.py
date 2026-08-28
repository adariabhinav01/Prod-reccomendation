"""User settings persisted at ~/.product-scout/config.toml (spec
docs/handoff.md §10.1; build order step 3).

    [location]
    country = "US"
    currency = "USD"

    [display]
    output_language = "en"   # English default; changed only on explicit request
    units = "imperial"       # derived from country on first run, overridable

    [sources]
    trusted = []             # §15 seam — parsed, unused

§10.1: "Asked once in Phase 0 if missing, written back, reused silently."
This module owns all config.toml I/O; §2's project layout keeps it distinct
from config.py, which holds pinned model IDs and cost-cap constants and does
no I/O at all — the two files are not the same concern despite both being
named "config" in casual conversation. No [models]/[cost] table exists here,
and none should be added. No API-key handling here either — that is
deferred to cli.py via python-dotenv, per config.py's own docstring.

Unlike index.json (store/index.py), config.toml is not a derived/rebuildable
artifact — it is user-authored state. A malformed or unrecognized file is
therefore a loud SettingsError, not a silent empty-dict fallback: house
style is "validate hard, fail loudly" (models.py), and silently discarding
or ignoring part of a user's saved config is worse than telling them it's
broken.

`display.units` defaults to None, not the "imperial" shown in §10.1's
literal example: that example is the *post-derivation* state, and no
country -> units derivation logic exists yet (a future step). None means
"not yet derived."

Reading uses stdlib tomllib (3.11+, read-only). Writing uses a small
hand-rolled serializer scoped to exactly this three-table schema rather than
adding a tomli-w dependency for one file with a fixed, known shape.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_CONFIG_PATH = Path.home() / ".product-scout" / "config.toml"


class LocationSettings(BaseModel):
    """§10.1. Both fields optional and independent — NOT models.Location,
    which requires both (that class describes a *resolved* run's location;
    this describes a possibly-incomplete saved default)."""

    model_config = ConfigDict(extra="forbid")  # a typo'd key (e.g. zipcode)
    # must raise, not silently vanish — extra="forbid" on the outer Settings
    # model does not cascade to nested submodels in pydantic v2, so each
    # table-level model repeats it.

    country: str | None = None  # ISO 3166-1 alpha-2, unvalidated (spec gives
    # no country-code enforcement rule)
    currency: str | None = None  # ISO 4217, unvalidated — same reasoning

    @property
    def has_location(self) -> bool:
        """True only when both are set. §7.1: intake asks 'only if not
        already in settings' — a half-filled location still counts as
        missing, since a country without a currency can't price anything."""
        return self.country is not None and self.currency is not None


class DisplaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_language: str = "en"  # §10.1: "English default; changed only on
    # explicit request" — always present, so a fixed default is correct.
    units: Literal["imperial", "metric"] | None = None  # None until a
    # future step derives it from country on first run.


class SourcesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trusted: list[str] = Field(default_factory=list)  # §15 seam — parsed,
    # unused. list[str] since §15 hasn't specified a richer structure yet;
    # this only needs to round-trip through TOML today.


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown tables/keys raise
    # loudly on load rather than silently vanishing on the next save().

    location: LocationSettings = Field(default_factory=LocationSettings)
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)


class SettingsError(ValueError):
    """Malformed config.toml, or an invalid `scout config set` operation.

    Subclasses ValueError (not bare Exception) so a caller that only cares
    that *something* about settings input was invalid can still catch
    ValueError generically; cli.py catches SettingsError specifically to
    print a clean message instead of a traceback."""


_SET_KEYS: dict[str, tuple[str, str]] = {
    "location.country": ("location", "country"),
    "location.currency": ("location", "currency"),
    "display.output_language": ("display", "output_language"),
    "display.units": ("display", "units"),
}


def load(path: Path | str | None = None) -> Settings:
    """Load settings from `path` (default ~/.product-scout/config.toml).

    A missing file is the "not configured yet" case §7.1 checks against —
    it returns all-default Settings(), not an error. A malformed or
    unrecognized file raises SettingsError.
    """
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return Settings()

    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Malformed config.toml at {path}: {exc}") from exc

    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise SettingsError(f"Invalid config.toml at {path}: {exc}") from exc


def save(settings: Settings, path: Path | str | None = None) -> Path:
    """Write `settings` to `path` atomically, creating parent dirs as needed."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _to_toml(settings)
    tmp_path = path.with_suffix(".toml.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)  # atomic on POSIX
    return path


def _to_toml(settings: Settings) -> str:
    """Deterministic serializer for exactly this three-table schema.

    Not a general TOML writer: string values are quoted via json.dumps
    (TOML basic-string escaping is a close enough superset for the plain
    values this schema ever holds — country/currency/language codes, URLs).
    Keys whose value is None are omitted entirely (TOML has no null
    literal) except sources.trusted, which is non-Optional and always
    emitted, including as `trusted = []`.
    """
    lines: list[str] = []

    lines.append("[location]")
    if settings.location.country is not None:
        lines.append(f"country = {json.dumps(settings.location.country)}")
    if settings.location.currency is not None:
        lines.append(f"currency = {json.dumps(settings.location.currency)}")
    lines.append("")

    lines.append("[display]")
    lines.append(f"output_language = {json.dumps(settings.display.output_language)}")
    if settings.display.units is not None:
        lines.append(f"units = {json.dumps(settings.display.units)}")
    lines.append("")

    lines.append("[sources]")
    trusted = ", ".join(json.dumps(u) for u in settings.sources.trusted)
    lines.append(f"trusted = [{trusted}]")
    lines.append("")

    return "\n".join(lines)


def set_value(settings: Settings, dotted_key: str, value: str) -> Settings:
    """Return a new Settings with `dotted_key` set to `value`.

    Only the 4 known scalar keys are settable (location.country,
    location.currency, display.output_language, display.units).
    sources.trusted is list-typed and explicitly rejected with a distinct
    message, not lumped in with "unknown key". Input is never mutated.

    Re-validates via reconstruction rather than model_copy(update=...),
    which in pydantic v2 skips validation entirely — a bare model_copy
    would silently accept an invalid display.units value (e.g. "furlongs")
    instead of raising.
    """
    parts = dotted_key.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise SettingsError(
            f"Invalid key {dotted_key!r}; expected '<table>.<field>', "
            "e.g. 'location.country'."
        )
    table, field = parts

    if table == "sources":
        raise SettingsError(
            f"{dotted_key!r} is not settable via 'scout config set'; "
            "sources.trusted has no scalar fields."
        )

    resolved = _SET_KEYS.get(dotted_key)
    if resolved is None:
        raise SettingsError(f"Unknown settings key: {dotted_key!r}")
    table, field = resolved

    sub_model = getattr(settings, table)
    updated_data = {**sub_model.model_dump(), field: value}
    try:
        updated_sub_model = type(sub_model).model_validate(updated_data)
    except ValidationError as exc:
        raise SettingsError(f"Invalid value for {dotted_key!r}: {exc}") from exc

    return settings.model_copy(update={table: updated_sub_model})
