"""Unit conversion at render time (spec docs/handoff.md §10.5, build order
step 6).

§10.5: "Store native units as published, convert at render only. The
no-source-no-field invariant argues for storing what was actually printed:
the source said '8 oz,' not '227 g.'" `SourcedValue.value` is therefore the
as-published string (e.g. `"8 oz"`, `"42 lb"`, or non-measurement text like
`"Bluetooth 5.0"`), and `SourcedValue.native_unit` is a separate, normalized
tag (e.g. `"oz"`, `"lb"`) — present only when `value` carries a convertible
measurement, `None` otherwise.

### SPEC GAP-FILL — what "convert" produces

The spec doesn't say whether conversion *replaces* the published figure or
*augments* it. Replacing it would contradict §10.5's own reasoning (storing
what was printed is explicitly the point) — a render step throwing that
away the moment `RunRecord.units` doesn't match feels like exactly the kind
of silent loss the no-source-no-field invariant exists to prevent, even
though this is display-only and the stored record is untouched either way.
`convert_display_value` therefore *augments*: unchanged when no conversion
applies, `"{as-published} ({converted})"` when one does — e.g. `"42 lb
(19.1 kg)"` for a metric-preferring reader looking at an imperial source.
"""

from __future__ import annotations

from typing import Literal

# unit -> (family, factor-to-counterpart, counterpart unit). Deliberately
# small — only the unit families likely to actually appear in product specs
# (weight, length). Anything not listed here is left alone: `native_unit`
# being `None` (non-measurement specs) or an unlisted unit both fall through
# to "no conversion possible," which is the safe default — an unconverted
# figure is still correct, just not translated.
_FAMILY: dict[str, Literal["imperial", "metric"]] = {
    "lb": "imperial",
    "oz": "imperial",
    "in": "imperial",
    "ft": "imperial",
    "kg": "metric",
    "g": "metric",
    "cm": "metric",
    "m": "metric",
}

# unit -> (counterpart unit, factor to multiply by to reach it).
_CONVERSION: dict[str, tuple[str, float]] = {
    "lb": ("kg", 0.45359237),
    "oz": ("g", 28.349523125),
    "in": ("cm", 2.54),
    "ft": ("m", 0.3048),
    "kg": ("lb", 2.2046226218),
    "g": ("oz", 0.0352739619),
    "cm": ("in", 0.3937007874),
    "m": ("ft", 3.2808398950),
}


def _leading_number(value: str) -> float | None:
    """Pull the leading numeric figure out of a published value string
    (`"42 lb"` -> `42.0`, `"8.5 oz"` -> `8.5`). `None` when the string
    doesn't start with one — e.g. non-measurement specs like `"Bluetooth
    5.0"`, which have no `native_unit` anyway and never reach this."""
    digits = ""
    seen_dot = False
    for ch in value.strip():
        if ch.isdigit():
            digits += ch
        elif ch == "." and not seen_dot and digits:
            digits += ch
            seen_dot = True
        elif ch in ("-", "+") and not digits:
            digits += ch
        else:
            break
    if not digits or digits in ("-", "+", "."):
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def convert_display_value(
    value: str,
    native_unit: str | None,
    target_units: Literal["imperial", "metric"],
) -> str:
    """§10.5. Returns `value` unchanged when there's nothing to convert —
    `native_unit` is `None`, unrecognized, already matches `target_units`'
    family, or `value` doesn't start with a parseable number. Otherwise
    returns `"{value} ({converted amount} {converted unit})"`.
    """
    if native_unit is None:
        return value
    family = _FAMILY.get(native_unit)
    if family is None or family == target_units:
        return value

    amount = _leading_number(value)
    if amount is None:
        return value

    counterpart_unit, factor = _CONVERSION[native_unit]
    converted = amount * factor
    return f"{value} ({converted:.1f} {counterpart_unit})"
