"""Tests for emitter category and ICAO type lookups."""

from __future__ import annotations

import pytest

from squawk_decoder.core import decode_category, fuzzy_type, lookup_type, type_count

# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected_label", "expected_group"),
    [
        ("A0", "None", "air"),
        ("A3", "Large", "air"),
        ("A4", "High vortex large", "air"),
        ("A7", "Rotorcraft", "air"),
        ("B1", "Emergency", "surface"),
        ("B3", "Fixed ground", "surface"),
        ("C2", "Towing", "vehicle"),
        ("C5", "De-icing", "vehicle"),
        ("D5", "Personnel", "fixed"),
        ("D7", "Obstacle", "fixed"),
    ],
)
def test_known_categories(code: str, expected_label: str, expected_group: str) -> None:
    category = decode_category(code)
    assert category is not None
    assert category.code == code
    assert category.label == expected_label
    assert category.group == expected_group
    assert category.description


def test_category_is_case_insensitive() -> None:
    lower = decode_category("a3")
    assert lower is not None
    assert lower.code == "A3"
    assert decode_category(" b1 ") is not None


@pytest.mark.parametrize("bad", ["", "A", "A33", "Z9", "A3X", "33", "A", "7"])
def test_invalid_categories_return_none(bad: str) -> None:
    assert decode_category(bad) is None


def test_category_tolerates_surrounding_whitespace() -> None:
    # Trimming is deliberate: callers commonly pass values sliced from CSV.
    assert decode_category(" A3 ") is not None
    assert decode_category(" b1 ").code == "B1"  # type: ignore[union-attr]


def test_category_has_non_air_groups() -> None:
    for code, group in [("B1", "surface"), ("C1", "vehicle"), ("D1", "fixed")]:
        category = decode_category(code)
        assert category is not None
        assert category.group == group


# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


def test_type_count_is_reasonable() -> None:
    assert type_count() >= 350


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("A320", "Airbus A320"),
        ("B738", "Boeing 737-800"),
        ("B77W", "Boeing 777-300ER"),
        ("C172", "Cessna 172 Skyhawk"),
        ("AT72", "ATR 72"),
        ("E190", "Embraer 190"),
    ],
)
def test_known_types(code: str, expected: str) -> None:
    assert lookup_type(code) == expected


def test_lookup_is_case_insensitive() -> None:
    assert lookup_type("a320") == "Airbus A320"
    assert lookup_type("  B738 ") == "Boeing 737-800"


@pytest.mark.parametrize("bad", ["", "   ", "ZZZZ9", "NOTAPLANE"])
def test_unknown_types_return_none(bad: str) -> None:
    assert lookup_type(bad) is None


def test_no_duplicate_type_codes() -> None:
    from squawk_decoder.core import _type_table

    table = _type_table()
    assert len(table) == type_count()
    assert all(name for name in table.values())


# ---------------------------------------------------------------------------
# fuzzy search
# ---------------------------------------------------------------------------


def test_fuzzy_finds_by_name() -> None:
    results = fuzzy_type("skyhawk")
    assert any(code == "C172" for code, _ in results)


def test_fuzzy_finds_by_code_prefix() -> None:
    results = fuzzy_type("A320")
    assert results
    assert results[0][0] == "A320"


def test_fuzzy_handles_punctuation_and_spacing() -> None:
    # "737-800" and "737 800" must both match.
    assert fuzzy_type("737-800")
    assert fuzzy_type("737 800")
    assert {c for c, _ in fuzzy_type("737-800")} == {c for c, _ in fuzzy_type("737 800")}


def test_fuzzy_respects_limit() -> None:
    assert len(fuzzy_type("cessna", limit=3)) <= 3
    assert len(fuzzy_type("cessna", limit=1)) == 1


def test_fuzzy_limit_zero_or_negative_returns_empty() -> None:
    assert fuzzy_type("cessna", limit=0) == []
    assert fuzzy_type("cessna", limit=-1) == []


def test_fuzzy_empty_query_returns_empty() -> None:
    assert fuzzy_type("") == []
    assert fuzzy_type("  ") == []
    assert fuzzy_type("!!!") == []


def test_fuzzy_no_match_returns_empty() -> None:
    assert fuzzy_type("zzzznotarealplanenamezzzz") == []


def test_fuzzy_results_are_deduplicated() -> None:
    results = fuzzy_type("king air")
    codes = [code for code, _ in results]
    assert len(codes) == len(set(codes))


def test_fuzzy_is_deterministic() -> None:
    assert fuzzy_type("boeing") == fuzzy_type("boeing")
