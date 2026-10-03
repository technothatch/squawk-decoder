"""Live squawk / emergency monitor.

Watches a dump1090 or readsb ``aircraft.json`` endpoint and reports squawk
*transitions* rather than repeating state every poll — that is the whole point
of a monitor, and it is what makes the output usable in a terminal or a log.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from squawk_decoder.core import UnknownCodeError, decode_squawk

DEFAULT_URL = "http://localhost:8080/data/aircraft.json"
DEFAULT_TIMEOUT = 5.0

Aircraft = Mapping[str, Any]


class FetchError(RuntimeError):
    """Raised when the decoder endpoint cannot be read."""


def fetch_aircraft(
    url: str = DEFAULT_URL,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[Aircraft]:
    """Fetch and decode an ``aircraft.json`` document.

    Args:
        url: Endpoint serving ``aircraft.json``.
        timeout: Socket timeout in seconds.
        opener: Injectable for tests; mirrors ``urllib.request.urlopen``.

    Returns:
        The ``aircraft`` array. A missing or null array yields ``[]`` so a
        quiet decoder does not look like a crash.

    Raises:
        FetchError: Network failure or a response that is not JSON.
    """
    try:
        with opener(url, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(f"cannot read {url}: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url} did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FetchError(f"{url} returned a JSON {type(payload).__name__}, expected an object")
    aircraft = payload.get("aircraft")
    if aircraft is None:
        return []
    if not isinstance(aircraft, list):
        raise FetchError(f"{url} returned 'aircraft' as {type(aircraft).__name__}, expected a list")
    return [a for a in aircraft if isinstance(a, dict)]


@dataclass(frozen=True, slots=True)
class SquawkEvent:
    """A single observed squawk state for one aircraft."""

    hex: str
    squawk: str
    callsign: str
    emergency: str
    kind: str
    name: str
    flags: tuple[str, ...]

    @property
    def is_emergency(self) -> bool:
        return self.kind == "emergency"

    def render(self) -> str:
        """One human-readable line."""
        marker = "!" if self.is_emergency else " "
        callsign = self.callsign or "--------"
        head = f"{marker} {self.hex.upper():>7} {callsign:<8} {self.squawk}"
        return f"{head}  {self.kind:<9} {self.name}"


def _event_for(aircraft: Aircraft) -> SquawkEvent | None:
    """Build an event for ``aircraft``, or ``None`` if it has no usable squawk."""
    hex_id = aircraft.get("hex")
    squawk = aircraft.get("squawk")
    if not isinstance(hex_id, str) or not hex_id or not isinstance(squawk, str) or not squawk:
        return None
    try:
        decoded = decode_squawk(squawk, strict=True)
    except UnknownCodeError:
        # Malformed codes are common on the wire; skip rather than crash.
        return None
    flight = aircraft.get("flight")
    emergency = aircraft.get("emergency")
    return SquawkEvent(
        hex=hex_id,
        squawk=decoded.code,
        callsign=flight.strip() if isinstance(flight, str) else "",
        emergency=emergency if isinstance(emergency, str) else "none",
        kind=decoded.kind,
        name=decoded.name,
        flags=decoded.flags,
    )


class SquawkMonitor:
    """Stateful monitor that emits events only when something *changes*.

    Args:
        source: Zero-arg callable returning the aircraft array — usually a
            :func:`functools.partial` around :func:`fetch_aircraft`.
        on_event: Called for each new or changed squawk. Defaults to writing
            to stdout.
        include_normal: Also emit events for non-emergency squawks. When
            ``False`` only emergency, intercept, military and reserved codes
            are reported, which is the usual "alert me" configuration.
    """

    def __init__(
        self,
        source: Callable[[], list[Aircraft]],
        *,
        on_event: Callable[[SquawkEvent], None] | None = None,
        include_normal: bool = False,
        max_state: int = 50_000,
    ) -> None:
        self._source = source
        self._on_event = on_event if on_event is not None else _print_event
        self._include_normal = include_normal
        # Squawk state for departed aircraft is *kept*: dump1090 ages an aircraft
        # out after ~60 s of silence, and re-firing just because it reappeared on
        # the same code would be pure noise. The cap bounds memory for a monitor
        # left running for months by evicting the least recently updated entries
        # (dicts preserve insertion order, and re-inserting on update refreshes it).
        self._max_state = max(1, max_state)
        self._state: dict[str, str] = {}
        self._baseline_done = False
        self.polls = 0
        self.errors = 0
        self.events = 0
        self.seen: set[str] = set()

    def _remember(self, hex_id: str, squawk: str) -> None:
        if hex_id in self._state:
            del self._state[hex_id]
        self._state[hex_id] = squawk
        while len(self._state) > self._max_state:
            oldest = next(iter(self._state))
            del self._state[oldest]

    def _wanted(self, event: SquawkEvent) -> bool:
        if self._include_normal:
            return True
        interesting = {"emergency", "intercept", "military", "government"}
        return bool(interesting.intersection(event.flags)) or event.is_emergency

    def poll(self) -> list[SquawkEvent]:
        """Run one poll. Returns the events that were emitted.

        The first poll only establishes a baseline: existing squawks are
        recorded without emitting, so restarting the monitor does not replay
        history. Subsequent polls emit on new aircraft, changed squawks, and
        emergency-state changes.

        Aircraft that drop out of ``aircraft.json`` keep their last squawk, so
        a brief dropout followed by a reappearance on the same code is silent.
        """
        aircraft = self._source()
        self.polls += 1
        emitted: list[SquawkEvent] = []
        baseline = not self._baseline_done

        for entry in aircraft:
            event = _event_for(entry)
            if event is None:
                continue
            self.seen.add(event.hex)
            previous = self._state.get(event.hex)
            if previous == event.squawk:
                # Refresh recency so active aircraft are evicted last.
                self._remember(event.hex, event.squawk)
                continue
            first_sighting = previous is None
            self._remember(event.hex, event.squawk)
            if baseline and first_sighting:
                continue
            if not self._wanted(event):
                continue
            emitted.append(event)
            self.events += 1
            self._on_event(event)

        self._baseline_done = True
        return emitted

    def snapshot(self) -> dict[str, str]:
        """Current ``hex -> squawk`` state; used by tests and the CLI."""
        return dict(self._state)


def _print_event(event: SquawkEvent) -> None:
    print(event.render(), flush=True)
