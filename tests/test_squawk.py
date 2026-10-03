"""Tests for Mode A squawk decoding."""

from __future__ import annotations

import pytest

from squawk_decoder.core import (
    UnknownCodeError,
    decode_squawk,
    emergency_codes,
    squawk_flags,
)


@pytest.mark.parametrize(
    ("code", "expected_name", "expected_kind"),
    [
        ("7500", "Hijack / unlawful interference", "emergency"),
        ("7600", "Radio failure", "emergency"),
        ("7700", "General emergency", "emergency"),
        ("1200", "VFR (North America)", "standard"),
        ("2000", "IFR (Europe default)", "standard"),
        ("7000", "VFR flight (Europe)", "standard"),
        ("1000", "Standard IFR (no assignment)", "standard"),
        ("7777", "Military intercept (US)", "military"),
    ],
)
def test_known_codes(code: str, expected_name: str, expected_kind: str) -> None:
    squawk = decode_squawk(code)
    assert squawk.code == code
    assert squawk.name == expected_name
    assert squawk.kind == expected_kind


def test_emergency_flagging() -> None:
    for code in ("7500", "7600", "7700"):
        squawk = decode_squawk(code)
        assert squawk.emergency is True
        assert "emergency" in squawk.flags


def test_emergency_codes_constant() -> None:
    assert emergency_codes() == frozenset({"7500", "7600", "7700"})


def test_specific_emergency_flags() -> None:
    assert "hijack" in squawk_flags("7500")
    assert "radio-failure" in squawk_flags("7600")
    assert "general-emergency" in squawk_flags("7700")


@pytest.mark.parametrize("code", ["1200", "1201", "1204", "1231", "7000"])
def test_vfr_flags(code: str) -> None:
    assert "vfr" in squawk_flags(code)


def test_1216_is_not_vfr() -> None:
    # 1216 "ATC interphone" sits inside the 12xx block but is not a VFR code,
    # which is exactly why the flag follows the reference name, not the prefix.
    assert "vfr" not in squawk_flags("1216")
    assert squawk_flags("1216") == ("unknown",)


@pytest.mark.parametrize("code", ["1000", "2000", "3000"])
def test_ifr_flags(code: str) -> None:
    flags = squawk_flags(code)
    assert "ifr" in flags


def test_military_flags() -> None:
    assert "military" in squawk_flags("7777")
    assert "intercept" in squawk_flags("7777")
    assert "military" in squawk_flags("1253")


def test_reserved_range_flags() -> None:
    squawk = decode_squawk("7050")
    assert "reserved" in squawk.flags
    assert squawk.in_range is not None


def test_unknown_code_is_lenient_by_default() -> None:
    squawk = decode_squawk("4321")
    assert squawk.code == "4321"
    assert squawk.emergency is False
    assert "unknown" in squawk.flags


def test_unknown_code_strict_raises() -> None:
    with pytest.raises(UnknownCodeError):
        decode_squawk("4321", strict=True)


@pytest.mark.parametrize("bad", ["", "  ", "1289", "1290", "abcd", "12345", "-120"])
def test_invalid_codes_raise(bad: str) -> None:
    with pytest.raises(UnknownCodeError):
        decode_squawk(bad)


def test_integer_input_is_accepted() -> None:
    assert decode_squawk(1200).code == "1200"
    assert decode_squawk(7700).code == "7700"


def test_zero_padding() -> None:
    assert decode_squawk("200").code == "0200"
    assert decode_squawk(7).code == "0007"


def test_leading_whitespace_tolerated() -> None:
    assert decode_squawk("  7700 ").code == "7700"


def test_digits_property() -> None:
    assert decode_squawk("7700").digits == (7, 7, 0, 0)
    assert decode_squawk("1200").digits == (1, 2, 0, 0)


def test_octal_is_enforced() -> None:
    with pytest.raises(UnknownCodeError, match="octal"):
        decode_squawk("1900")


def test_military_kind_for_intercept_block() -> None:
    for code in ("2001", "2015", "2030"):
        assert decode_squawk(code).kind == "military"


def test_government_kind() -> None:
    squawk = decode_squawk("1222")
    assert squawk.kind == "government"
    assert "government" in squawk.flags


def test_unknown_within_reserved_range_gets_range_name() -> None:
    squawk = decode_squawk("7055")
    assert squawk.in_range is not None
    assert "Reserved" in squawk.in_range


def test_code_is_idempotent() -> None:
    first = decode_squawk("7700")
    second = decode_squawk("7700")
    assert first == second
    assert first is second or first == second


def test_flags_have_no_duplicates() -> None:
    flags = squawk_flags("7700")
    assert len(flags) == len(set(flags))


def test_all_table_codes_decode_cleanly() -> None:
    from squawk_decoder.core import _squawk_table

    for code in _squawk_table():
        squawk = decode_squawk(code, strict=True)
        assert squawk.code == code
        assert squawk.name
        assert squawk.kind in {"emergency", "standard", "military", "government", "reserved"}
