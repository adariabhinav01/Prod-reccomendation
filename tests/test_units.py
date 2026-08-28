"""Unit tests for render/units.py — unit conversion at render (§10.5,
build order step 6)."""

from product_scout.render.units import _leading_number, convert_display_value


# -- _leading_number -----------------------------------------------------


def test_leading_number_simple_integer():
    assert _leading_number("42 lb") == 42.0


def test_leading_number_decimal():
    assert _leading_number("8.5 oz") == 8.5


def test_leading_number_negative():
    assert _leading_number("-3 cm") == -3.0


def test_leading_number_no_number_returns_none():
    assert _leading_number("Bluetooth 5.0") is None
    # "Bluetooth 5.0" doesn't start with a digit, so nothing is parsed.


def test_leading_number_blank_returns_none():
    assert _leading_number("") is None
    assert _leading_number("   ") is None


# -- convert_display_value ------------------------------------------------


def test_imperial_value_target_imperial_unchanged():
    assert convert_display_value("42 lb", "lb", "imperial") == "42 lb"


def test_metric_value_target_metric_unchanged():
    assert convert_display_value("19 kg", "kg", "metric") == "19 kg"


def test_imperial_value_target_metric_converts():
    result = convert_display_value("42 lb", "lb", "metric")
    assert result == "42 lb (19.1 kg)"


def test_metric_value_target_imperial_converts():
    result = convert_display_value("19 kg", "kg", "imperial")
    assert result == "19 kg (41.9 lb)"


def test_ounces_to_grams():
    assert convert_display_value("8 oz", "oz", "metric") == "8 oz (226.8 g)"


def test_inches_to_centimeters():
    assert convert_display_value("10 in", "in", "metric") == "10 in (25.4 cm)"


def test_feet_to_meters():
    assert convert_display_value("6 ft", "ft", "metric") == "6 ft (1.8 m)"


def test_native_unit_none_unchanged():
    assert convert_display_value("Bluetooth 5.0", None, "metric") == "Bluetooth 5.0"


def test_unrecognized_unit_unchanged():
    assert convert_display_value("5 widgets", "widgets", "metric") == "5 widgets"


def test_unparseable_value_with_known_unit_unchanged():
    # native_unit is known, but the published string doesn't lead with a
    # number — nothing to convert, so it passes through unchanged rather
    # than raising.
    assert convert_display_value("approx. lb", "lb", "metric") == "approx. lb"
