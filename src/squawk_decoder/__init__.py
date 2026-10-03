"""Offline Mode A squawk, emitter category and ICAO type decoder."""

from squawk_decoder.core import (
    Squawk,
    SquawkCategory,
    decode_category,
    decode_squawk,
    fuzzy_type,
    lookup_type,
    squawk_flags,
)

__all__ = [
    "Squawk",
    "SquawkCategory",
    "decode_category",
    "decode_squawk",
    "fuzzy_type",
    "lookup_type",
    "squawk_flags",
]

__version__ = "0.1.0"
