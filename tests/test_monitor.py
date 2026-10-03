"""Tests for the live squawk monitor."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from squawk_decoder.monitor import (
    FetchError,
    SquawkMonitor,
    _event_for,
    fetch_aircraft,
)


def aircraft(hex_id: str, squawk: str | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"hex": hex_id, "messages": 12}
    if squawk is not None:
        entry["squawk"] = squawk
    entry.update(extra)
    return entry


class FakeResponse:
    """Minimal context-manager stand-in for urlopen."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def make_opener(payload: Any) -> Callable[..., Any]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def opener(url: str, timeout: float = 0.0) -> FakeResponse:
        return FakeResponse(body)

    return opener


class FailingOpener:
    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise OSError("connection refused")


# ---------------------------------------------------------------------------
# fetch_aircraft
# ---------------------------------------------------------------------------


def test_fetch_returns_aircraft_array() -> None:
    payload = {"now": 1, "messages": 5, "aircraft": [{"hex": "abc123"}]}
    assert fetch_aircraft("http://x", opener=make_opener(payload)) == [{"hex": "abc123"}]


def test_fetch_missing_aircraft_key_returns_empty() -> None:
    assert fetch_aircraft("http://x", opener=make_opener({"now": 1})) == []


def test_fetch_null_aircraft_returns_empty() -> None:
    assert fetch_aircraft("http://x", opener=make_opener({"aircraft": None})) == []


def test_fetch_skips_non_object_entries() -> None:
    payload = {"aircraft": [{"hex": "a"}, "junk", 42, None]}
    assert fetch_aircraft("http://x", opener=make_opener(payload)) == [{"hex": "a"}]


def test_fetch_bad_json_raises() -> None:
    with pytest.raises(FetchError, match="did not return JSON"):
        fetch_aircraft("http://x", opener=make_opener(b"not json"))


def test_fetch_non_object_root_raises() -> None:
    with pytest.raises(FetchError, match="expected an object"):
        fetch_aircraft("http://x", opener=make_opener([1, 2, 3]))


def test_fetch_aircraft_not_a_list_raises() -> None:
    with pytest.raises(FetchError, match="expected a list"):
        fetch_aircraft("http://x", opener=make_opener({"aircraft": {"hex": "a"}}))


def test_fetch_network_error_raises_fetch_error() -> None:
    with pytest.raises(FetchError, match="cannot read"):
        fetch_aircraft("http://x", opener=FailingOpener())


# ---------------------------------------------------------------------------
# _event_for
# ---------------------------------------------------------------------------


def test_event_for_valid_aircraft() -> None:
    event = _event_for(aircraft("abc123", "7700", flight="UAL123  "))
    assert event is not None
    assert event.hex == "abc123"
    assert event.squawk == "7700"
    assert event.callsign == "UAL123"
    assert event.is_emergency is True


def test_event_for_missing_squawk_returns_none() -> None:
    assert _event_for(aircraft("abc123")) is None


def test_event_for_missing_hex_returns_none() -> None:
    assert _event_for({"squawk": "7700"}) is None


def test_event_for_malformed_squawk_returns_none() -> None:
    assert _event_for(aircraft("abc123", "8901")) is None


def test_event_for_non_string_flight() -> None:
    event = _event_for(aircraft("abc123", "1200", flight=123))
    assert event is not None
    assert event.callsign == ""


def test_event_render_marks_emergency() -> None:
    event = _event_for(aircraft("abc123", "7700", flight="UAL123"))
    assert event is not None
    rendered = event.render()
    assert rendered.startswith("!")
    assert "7700" in rendered
    assert "UAL123" in rendered


def test_event_render_handles_absent_callsign() -> None:
    event = _event_for(aircraft("abc123", "1200"))
    assert event is not None
    assert "--------" in event.render()


# ---------------------------------------------------------------------------
# SquawkMonitor
# ---------------------------------------------------------------------------


def test_first_poll_is_baseline_and_emits_nothing() -> None:
    data = [aircraft("a1", "7700"), aircraft("a2", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    assert monitor.poll() == []
    assert monitor.poll() == []
    assert monitor.events == 0


def test_changed_squawk_emits_event() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    data[0] = aircraft("a1", "7700")
    events = monitor.poll()
    assert len(events) == 1
    assert events[0].squawk == "7700"
    assert monitor.events == 1


def test_new_aircraft_after_baseline_emits() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    data.append(aircraft("a2", "2000"))
    events = monitor.poll()
    assert [e.hex for e in events] == ["a2"]


def test_unchanged_aircraft_does_not_refire() -> None:
    data = [aircraft("a1", "7700")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    assert monitor.poll() == []
    assert monitor.poll() == []
    assert monitor.events == 0


def test_interesting_only_filters_normal_codes() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=False)
    monitor.poll()
    data[0] = aircraft("a1", "7700")
    events = monitor.poll()
    assert [e.squawk for e in events] == ["7700"]


def test_interesting_only_reports_military() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=False)
    monitor.poll()
    data[0] = aircraft("a1", "7777")
    assert [e.squawk for e in monitor.poll()] == ["7777"]


def test_departed_aircraft_state_is_retained() -> None:
    # dump1090 ages aircraft out after ~60s of silence. Retaining the squawk
    # means a brief dropout does not cause the same code to re-fire on return.
    data: list[dict[str, Any]] = [aircraft("a1", "1200"), aircraft("a2", "2000")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    data[:] = [aircraft("a2", "2000")]
    assert monitor.poll() == []  # a1 absent produces no events
    data.append(aircraft("a1", "1200"))
    assert monitor.poll() == []  # returns on the same code: silent
    assert monitor.snapshot()["a1"] == "1200"


def test_reappearing_on_new_code_fires() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    data.clear()
    monitor.poll()
    data.append(aircraft("a1", "7700"))
    assert [e.squawk for e in monitor.poll()] == ["7700"]


def test_state_is_bounded() -> None:
    # With max_state=3, inserting a 4th hex evicts the oldest.
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True, max_state=3)
    monitor.poll()
    for hex_id in ("a2", "a3", "a4"):
        data.append(aircraft(hex_id, "1200"))
        monitor.poll()
    assert len(monitor.snapshot()) == 3
    assert "a1" not in monitor.snapshot()


def test_active_aircraft_survive_eviction() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True, max_state=2)
    monitor.poll()
    # a1 is touched every poll, so it should be evicted last.
    for hex_id in ("a2", "a3"):
        data.append(aircraft(hex_id, "1200"))
        monitor.poll()
        data[:] = [a for a in data if a["hex"] != hex_id]
        monitor.poll()
    assert "a1" in monitor.snapshot()
    assert len(monitor.snapshot()) <= 2


def test_polls_counter_tracks() -> None:
    data = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    monitor.poll()
    assert monitor.polls == 2


def test_custom_callback_receives_events() -> None:
    received: list[Any] = []
    data: list[dict[str, Any]] = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, on_event=received.append, include_normal=True)
    monitor.poll()
    data[0] = aircraft("a1", "7500")
    monitor.poll()
    assert len(received) == 1
    assert received[0].squawk == "7500"
    assert monitor.events == 1


def test_malformed_aircraft_do_not_crash_monitor() -> None:
    data: list[dict[str, Any]] = [aircraft("a1", "1200"), {"hex": "b"}, {"squawk": "8901"}, "junk"]
    monitor = SquawkMonitor(lambda: [d for d in data if isinstance(d, dict)], include_normal=True)
    monitor.poll()
    data[0] = aircraft("a1", "7700")
    assert len(monitor.poll()) == 1


def test_snapshot_returns_a_copy() -> None:
    data = [aircraft("a1", "1200")]
    monitor = SquawkMonitor(lambda: data, include_normal=True)
    monitor.poll()
    snap = monitor.snapshot()
    snap["a1"] = "7700"
    assert monitor.snapshot()["a1"] == "1200"
