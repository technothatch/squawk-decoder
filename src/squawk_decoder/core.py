"""Pure decoding logic for Mode A squawks, emitter categories and ICAO types.

Nothing in this module touches the network or the filesystem, which keeps it
trivially testable and safe to import from any context.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

SquawkKind = Literal["emergency", "standard", "military", "government", "reserved"]
CategoryGroup = Literal["air", "surface", "vehicle", "fixed"]


@dataclass(frozen=True, slots=True)
class Squawk:
    """A decoded Mode A code.

    ``flags`` is the machine-readable summary: ``emergency``, ``intercept``,
    ``military``, ``vfr``, ``ifr``, ``reserved``, ``assigned`` or ``unknown``.
    ``kind`` is the single most useful bucket for sorting/filtering.
    """

    code: str
    name: str
    note: str
    kind: SquawkKind
    emergency: bool
    flags: tuple[str, ...] = ()
    in_range: str | None = None

    @property
    def digits(self) -> tuple[int, int, int, int]:
        """The four octal digits, most significant first."""
        return tuple(int(c) for c in self.code)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class SquawkCategory:
    """A decoded emitter category such as ``A3`` or ``B1``."""

    code: str
    label: str
    group: CategoryGroup
    description: str


class UnknownCodeError(ValueError):
    """Raised when a squawk code cannot be decoded."""


_EMERGENCY_CODES = frozenset({"7500", "7600", "7700"})
_INTERCEPT_CODES = frozenset({"7776", "7777", "4400", "3777"})
_CODE_RE = re.compile(r"^[0-7]{4}$")

# ---------------------------------------------------------------------------
# squawks
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _squawk_table() -> dict[str, dict[str, str]]:
    raw = resources.files("squawk_decoder.data").joinpath("squawks.json").read_text("utf-8")
    payload: dict[str, Any] = json.loads(raw)
    table: dict[str, dict[str, str]] = {}
    for entry in payload["squawks"]:
        code = str(entry["code"])
        # First definition wins so an intentional override at the top of the
        # file beats a later duplicate.
        table.setdefault(
            code,
            {
                "name": str(entry["name"]),
                "note": str(entry["note"]),
                "kind": str(entry["kind"]),
            },
        )
    return table


@lru_cache(maxsize=1)
def _squawk_ranges() -> tuple[tuple[int, int, str, str], ...]:
    raw = resources.files("squawk_decoder.data").joinpath("squawks.json").read_text("utf-8")
    payload: dict[str, Any] = json.loads(raw)
    ranges: list[tuple[int, int, str, str]] = []
    for entry in payload["ranges"]:
        ranges.append(
            (
                int(str(entry["from"]), 8),
                int(str(entry["to"]), 8),
                str(entry["name"]),
                str(entry["kind"]),
            )
        )
    return tuple(ranges)


def _normalise_squawk(value: str | int) -> str:
    """Coerce user input into a canonical 4-digit octal string."""
    text = str(value).strip()
    if text == "":
        raise UnknownCodeError("empty squawk")
    # Mode A codes are four *octal* digits. Accept "1200", 1200 and " 1200 ".
    if not text.isdigit():
        raise UnknownCodeError(f"not a numeric squawk: {value!r}")
    if len(text) > 4:
        raise UnknownCodeError(f"squawk too long: {value!r}")
    text = text.zfill(4)
    if not _CODE_RE.match(text):
        raise UnknownCodeError(f"Mode A codes are octal (digits 0-7), got {value!r}")
    return text


def _kind_for(code: str, declared: str) -> SquawkKind:
    if declared in {"emergency", "standard", "military", "government", "reserved"}:
        return declared  # type: ignore[return-value]
    if code in _EMERGENCY_CODES:
        return "emergency"
    if code in _INTERCEPT_CODES:
        return "military"
    return "standard"


def _flags_for(code: str, kind: SquawkKind, name: str) -> tuple[str, ...]:
    flags: list[str] = []
    if code in _EMERGENCY_CODES:
        flags.append("emergency")
        flags.append(
            {
                "7500": "hijack",
                "7600": "radio-failure",
                "7700": "general-emergency",
            }[code]
        )
    if code in _INTERCEPT_CODES:
        flags.append("intercept")
    if kind == "military":
        flags.append("military")
    if kind == "government":
        flags.append("government")
    if kind == "reserved":
        flags.append("reserved")
    if kind == "standard" and "VFR" in name:
        # "VFR" in the reference name is the reliable signal: it covers both the
        # North American 12xx block and the European 7000 code, while codes such
        # as 1216 "ATC interphone" are correctly left out.
        flags.append("vfr")
    if kind == "standard" and "IFR" in name:
        flags.append("ifr")
        flags.append("default" if code == "1000" else "assigned")
    # Regional reserved ranges (7040-7074 etc.) are only flagged when no exact
    # entry exists; a range flag is additive, never exclusive.
    value = int(code, 8)
    for low, high, _name, range_kind in _squawk_ranges():
        if low <= value <= high:
            flags.append(range_kind)
    if not flags:
        flags.append("unknown")
    # Preserve order, drop repeats.
    return tuple(dict.fromkeys(flags))


def _range_for(code: str) -> str | None:
    value = int(code, 8)
    for low, high, name, _kind in _squawk_ranges():
        if low <= value <= high:
            return name
    return None


def decode_squawk(value: str | int, *, strict: bool = False) -> Squawk:
    """Decode a Mode A squawk code.

    Args:
        value: A 1-4 digit code, as ``str`` or ``int``. Octal digits are
            enforced: ``8`` and ``9`` are rejected.
        strict: When ``True``, unknown-but-valid codes raise
            :class:`UnknownCodeError` instead of returning a generic result.

    Returns:
        A :class:`Squawk`. Unknown codes get ``kind="standard"`` and
        ``flags=("unknown",)`` unless ``strict`` is set.

    Raises:
        UnknownCodeError: Input is not a valid octal Mode A code, or is an
            unknown code while ``strict=True``.
    """
    code = _normalise_squawk(value)
    entry = _squawk_table().get(code)
    if entry is None:
        if strict:
            raise UnknownCodeError(f"unknown squawk {code}")
        generic_name = "Unassigned / locally assigned"
        generic_note = "Not in the bundled reference table; likely assigned by a local ATC unit."
        # Still compute flags so an unknown code inside a reserved range is
        # reported as such rather than as a bare "unknown".
        flags = _flags_for(code, "standard", generic_name)
        return Squawk(
            code=code,
            name=generic_name,
            note=generic_note,
            kind="standard",
            emergency=False,
            flags=flags,
            in_range=_range_for(code),
        )
    kind = _kind_for(code, entry["kind"])
    return Squawk(
        code=code,
        name=entry["name"],
        note=entry["note"],
        kind=kind,
        emergency=code in _EMERGENCY_CODES,
        flags=_flags_for(code, kind, entry["name"]),
        in_range=_range_for(code),
    )


def squawk_flags(value: str | int) -> tuple[str, ...]:
    """Convenience wrapper returning only the flags for ``value``."""
    return decode_squawk(value).flags


def emergency_codes() -> frozenset[str]:
    """The three ICAO emergency squawks."""
    return _EMERGENCY_CODES


# ---------------------------------------------------------------------------
# emitter categories
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _category_table() -> dict[str, dict[str, str]]:
    raw = resources.files("squawk_decoder.data").joinpath("categories.json").read_text("utf-8")
    payload: dict[str, Any] = json.loads(raw)
    table: dict[str, dict[str, str]] = {}
    for code, entry in payload["categories"].items():
        table[code.upper()] = {
            "label": str(entry["label"]),
            "group": str(entry["group"]),
            "description": str(entry["description"]),
        }
    return table


def decode_category(value: str) -> SquawkCategory | None:
    """Decode an ADS-B emitter category such as ``"A3"`` or ``"b1"``.

    Returns ``None`` for unknown or malformed codes rather than raising, since
    ``category`` is optional in ``aircraft.json`` and frequently absent.
    """
    text = str(value).strip().upper()
    if len(text) != 2:
        return None
    if not ("A" <= text[0] <= "D"):
        return None
    if not text[1].isdigit():
        return None
    entry = _category_table().get(text)
    if entry is None:
        return None
    groups: dict[str, CategoryGroup] = {
        "air": "air",
        "surface": "surface",
        "vehicle": "vehicle",
        "fixed": "fixed",
    }
    return SquawkCategory(
        code=text,
        label=entry["label"],
        group=groups[entry["group"]],
        description=entry["description"],
    )


# ---------------------------------------------------------------------------
# ICAO type codes
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _type_table() -> dict[str, str]:
    raw = resources.files("squawk_decoder.data").joinpath("types.csv").read_text("utf-8")
    reader = csv.DictReader(raw.splitlines())
    table: dict[str, str] = {}
    for row in reader:
        if row.get("icao"):
            table[row["icao"].strip().upper()] = row["name"].strip()
    return table


def lookup_type(code: str) -> str | None:
    """Look up an ICAO aircraft type designator, e.g. ``"A321"``.

    Returns ``None`` when the code is unknown or empty.
    """
    text = str(code).strip().upper()
    if not text:
        return None
    return _type_table().get(text)


def _fold(text: str) -> str:
    """Lowercase and strip accents/spacing so 'airbus a-320' matches 'A320'."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


_TYPE_KEYS: dict[str, str] | None = None


def fuzzy_type(query: str, *, limit: int = 10) -> list[tuple[str, str]]:
    """Search type descriptions by name or code.

    Matching is accent- and punctuation-insensitive, so ``"a320"``,
    ``"airbus 320"`` and ``"Boeing 737-800"`` all return sensible hits.
    Ordered best-match first, then alphabetically for ties.
    """
    global _TYPE_KEYS
    if limit < 1:
        return []
    needle = _fold(query)
    if not needle:
        return []
    if _TYPE_KEYS is None:
        _TYPE_KEYS = {code: _fold(f"{code}{name}") for code, name in _type_table().items()}
    hits: list[tuple[int, str, str]] = []
    for code, haystack in _TYPE_KEYS.items():
        if needle in haystack:
            # Exact code match ranks highest, then prefix, then substring.
            if needle == code.lower().replace("-", "").replace(" ", ""):
                rank = 0
            elif haystack.startswith(needle):
                rank = 1
            else:
                rank = 2
            hits.append((rank, code, _type_table()[code]))
    hits.sort(key=lambda item: (item[0], item[1]))
    return [(code, name) for _rank, code, name in hits[:limit]]


def type_count() -> int:
    """Number of bundled ICAO type designators."""
    return len(_type_table())
