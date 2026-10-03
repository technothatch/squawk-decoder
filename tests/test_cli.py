"""Tests for the squawk-decoder CLI."""

from __future__ import annotations

import json

import pytest

from squawk_decoder.cli import main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys)
    assert code == 1
    assert "usage:" in out
    assert "watch" in out


def test_decode_emergency(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "decode", "7700")
    assert code == 2
    assert "General emergency" in out
    assert "EMERGENCY SQUAWK" in err


def test_decode_normal_code(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "decode", "1200")
    assert code == 0
    assert "VFR" in out
    assert "digits" in out


def test_decode_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "decode", "7600", "--json")
    assert code == 2
    payload = json.loads(out)
    assert payload["code"] == "7600"
    assert payload["emergency"] is True
    assert "radio-failure" in payload["flags"]
    assert payload["kind"] == "emergency"


def test_decode_invalid_code(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = run(capsys, "decode", "1289")
    assert code == 2
    assert "octal" in err


def test_decode_unknown_strict(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = run(capsys, "decode", "4321", "--strict")
    assert code == 2
    assert "unknown squawk" in err


def test_decode_unknown_lenient(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "decode", "4321")
    assert code == 0
    assert "4321" in out


def test_category_command(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "category", "a3")
    assert code == 0
    assert "Large" in out
    assert "(air)" in out


def test_category_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "category", "B1", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["group"] == "surface"
    assert payload["label"] == "Emergency"


def test_category_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = run(capsys, "category", "ZZ")
    assert code == 2
    assert "unknown emitter category" in err


def test_type_command(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "type", "B738")
    assert code == 0
    assert "Boeing 737-800" in out


def test_type_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "type", "a320", "--json")
    assert code == 0
    assert json.loads(out) == {"code": "A320", "name": "Airbus A320"}


def test_type_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = run(capsys, "type", "ZZZZ9")
    assert code == 2
    assert "unknown ICAO type" in err


def test_search_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "search", "skyhawk", "--json", "--limit", "5")
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert any(item["code"] == "C172" for item in payload)
    assert len(payload) <= 5


def test_search_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = run(capsys, "search", "zzzznotarealplanename")
    assert code == 2
    assert "no matches" in err


def test_search_limit(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "search", "cessna", "--limit", "3")
    assert code == 0
    assert len([line for line in out.splitlines() if line.strip()]) == 3


def test_emergency_listing(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "emergency")
    assert code == 0
    for code_text in ("7500", "7600", "7700"):
        assert code_text in out


def test_emergency_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "emergency", "--json")
    assert code == 0
    payload = json.loads(out)
    assert [item["code"] for item in payload] == ["7500", "7600", "7700"]


def test_info_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "info", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["types"] >= 350
    assert payload["emergency_codes"] == ["7500", "7600", "7700"]
    assert payload["version"]


def test_info_text(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _err = run(capsys, "info")
    assert code == 0
    assert "bundled ICAO types" in out
    assert "7700" in out


def test_watch_with_stub_fetch(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    from squawk_decoder.cli import _cmd_watch

    frames = [
        [{"hex": "abc123", "squawk": "1200"}],
        [{"hex": "abc123", "squawk": "7700"}],
    ]
    calls = {"n": 0}

    def fetch(_url: str, timeout: float = 0.0) -> list[dict[str, object]]:
        idx = min(calls["n"], len(frames) - 1)
        calls["n"] += 1
        return frames[idx]

    args = argparse.Namespace(
        url="http://stub",
        interval=0.0,
        timeout=1.0,
        all=True,
        json=False,
        max_polls=3,
    )
    code = _cmd_watch(args, fetch=fetch)
    captured = capsys.readouterr()
    assert code == 0
    assert "7700" in captured.out
    assert "polls=3" in captured.err


def test_watch_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    from squawk_decoder.cli import _cmd_watch

    frames = [
        [],
        [{"hex": "abc123", "squawk": "7500", "flight": "UAL1  "}],
    ]
    calls = {"n": 0}

    def fetch(_url: str, timeout: float = 0.0) -> list[dict[str, object]]:
        idx = min(calls["n"], len(frames) - 1)
        calls["n"] += 1
        return frames[idx]

    args = argparse.Namespace(
        url="http://stub",
        interval=0.0,
        timeout=1.0,
        all=True,
        json=True,
        max_polls=2,
    )
    code = _cmd_watch(args, fetch=fetch)
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out.splitlines()[0])
    assert payload["squawk"] == "7500"
    assert payload["emergency"] is True
    assert payload["callsign"] == "UAL1"
    assert isinstance(payload["ts"], float)


def test_watch_fetch_error_is_tolerated(capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    from squawk_decoder.cli import _cmd_watch
    from squawk_decoder.monitor import FetchError

    def fetch(_url: str, timeout: float = 0.0) -> list[dict[str, object]]:
        raise FetchError("boom")

    args = argparse.Namespace(
        url="http://stub",
        interval=0.0,
        timeout=1.0,
        all=True,
        json=False,
        max_polls=2,
    )
    code = _cmd_watch(args, fetch=fetch)
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert "errors=2" in captured.err
    # Every attempt failed, so the monitor never produced a good poll.
    assert code == 1


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_unknown_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense"])
    assert excinfo.value.code == 2
