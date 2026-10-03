# squawk-decoder

[![CI](https://github.com/technothatch/squawk-decoder/actions/workflows/ci.yml/badge.svg)](https://github.com/technothatch/squawk-decoder/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/technothatch/squawk-decoder/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/technothatch/squawk-decoder)](https://github.com/technothatch/squawk-decoder/releases)
[![Issues](https://img.shields.io/github/issues/technothatch/squawk-decoder)](https://github.com/technothatch/squawk-decoder/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/technothatch/squawk-decoder/pulls)
[![Code size](https://img.shields.io/github/languages/code-size/technothatch/squawk-decoder)](https://github.com/technothatch/squawk-decoder)
[![Last commit](https://img.shields.io/github/last-commit/technothatch/squawk-decoder)](https://github.com/technothatch/squawk-decoder/commits/main)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen.svg)](https://github.com/technothatch/squawk-decoder)

Offline decoder for **Mode A squawk codes**, **ADS-B emitter categories** and
**ICAO aircraft type designators**, plus a live squawk monitor for
[dump1090](https://github.com/flightaware/dump1090) and
[readsb](https://github.com/wiedehopf/readsb).

Every ADS-B feed carries a squawk, and most tools just show you the four
digits. This one tells you what they *mean* — that `7700` is a general
emergency, that `1222` is US Coast Guard, that `D5` is a marshaller standing on
the apron — and can watch a live feed and tell you the moment any of it changes.
Everything runs locally against bundled reference data; nothing is queried from
the network unless you explicitly ask.

## Features

- **Squawk decoding** — 142 reference codes with names, notes and ICAO
  semantics, plus reserved-range awareness (7040-7074, 4400-4475, …).
- **Machine-readable flags** — `emergency`, `hijack`, `radio-failure`,
  `intercept`, `military`, `government`, `vfr`, `ifr`, `reserved`, `unknown`.
  Build filters on flags rather than string-matching names.
- **Emitter categories** — all 34 ADS-B categories `A0`–`D7` (air, surface,
  vehicle, fixed ground) decoded to label + description.
- **ICAO type lookup** — 385 type designators (`B738`, `A320`, `BCS3`, …) with
  accent- and punctuation-insensitive fuzzy search.
- **Live squawk monitor** — polls `aircraft.json`, emits **only on change**,
  with a baseline first poll so a restart never replays history. Exit codes and
  `--json` make it usable in scripts and cron.
- **Fully offline** — reference data ships inside the package. No API keys, no
  network, no telemetry.
- **Zero runtime dependencies** — Python standard library only.

## Install

```bash
pip install squawk-decoder
```

Or from source:

```bash
git clone https://github.com/technothatch/squawk-decoder.git
cd squawk-decoder
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Quickstart

Decode a squawk:

```console
$ squawk-decoder decode 7700
7700  General emergency
  digits : 7 7 0 0 (decimal 4032)
  kind   : emergency
  flags  : emergency, general-emergency
  note   : ICAO standard. Any emergency: failure, medical, fuel, security.

  *** EMERGENCY SQUAWK ***        (on stderr)
```

Emergency squawks exit with status `2`, everything else `0`, malformed input
also `2` — so it drops straight into shell scripts:

```bash
squawk-decoder decode "$SQUAWK" --json | jq -r .kind
```

Look up a type and search the database:

```console
$ squawk-decoder type B738
B738  Boeing 737-800

$ squawk-decoder search "737-800" --limit 3
B738    Boeing 737-800
B737    Boeing 737-700
B739    Boeing 737-900
```

Decode an emitter category:

```console
$ squawk-decoder category A3
A3  Large  (air)
  70 000 lb or more MTOW, or a heavy aircraft.
```

Watch a live decoder and get alerted on transitions:

```console
$ squawk-decoder watch --url http://localhost:8080/data/aircraft.json
# watching http://localhost:8080/data/aircraft.json every 2s — ctrl-c to stop
!  3C673A  BAW117   7700  emergency  General emergency
# polls=148 events=1 errors=0
```

`--all` reports every squawk transition instead of just the interesting ones.
`--json` emits one JSON object per event for piping into `jq`, log shippers or
alert rules:

```bash
squawk-decoder watch --json --interval 1 | while read -r line; do
  jq -e '.emergency' <<<"$line" && notify-send "ADS-B emergency" "$line"
done
```

## Configuration reference

| Command | Flag | Env / input | Meaning | Default |
| --- | --- | --- | --- | --- |
| all | `--json` | — | Emit JSON instead of text | off |
| all | `--version` | — | Print version and exit | — |
| `decode` | `--strict` | — | Fail on codes missing from the table | off |
| `category` | `code` | arg | Emitter category, case-insensitive | — |
| `type` | `code` | arg | ICAO type designator, case-insensitive | — |
| `search` | `--limit N` | arg | Maximum results | `15` |
| `watch` | `--url` | `DUMP1090_URL` | `aircraft.json` endpoint | `http://localhost:8080/data/aircraft.json` |
| `watch` | `--interval` | — | Poll interval, seconds | `2.0` |
| `watch` | `--timeout` | — | HTTP timeout, seconds | `5.0` |
| `watch` | `--all` | — | Report non-emergency squawks too | off |
| `watch` | `--max-polls` | — | Stop after N polls (`0` = forever) | `0` |
| `emergency` | — | — | List the ICAO emergency squawks | — |
| `info` | — | — | Show bundled dataset statistics | — |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | No command given, or watch never completed a poll |
| `2` | Unknown/invalid code, unknown type, or **the decoded squawk is an emergency** |

## How it works

```
                 ┌────────────────────────────────────────┐
   aircraft.json │ monitor.py                             │
   ─────────────▶│  fetch → _event_for → SquawkMonitor    │──┐
                 │  (poll, baseline, change detection)    │  │
                 └────────────────────────────────────────┘  │
                                                             ▼
                 ┌────────────────────────────────────────┐  printed /
   "7700"        │ core.py                                │  JSON event
   ─────────────▶│  _normalise_squawk → table lookup      │──▶
                 │  → _flags_for / _range_for             │
                 └───────────────┬────────────────────────┘
                                 │
                 ┌───────────────▼────────────────────────┐
                 │ data/  (bundled, read via importlib)   │
                 │  squawks.json     142 codes + ranges   │
                 │  categories.json  34 ADS-B categories  │
                 │  types.csv        385 ICAO designators │
                 └────────────────────────────────────────┘
```

`core.py` is pure — no I/O, no clock, no globals beyond memoised tables — which
is why decoding is trivially testable and safe to import anywhere.
`monitor.py` owns all interaction with a decoder, and injects the fetcher so
tests never need a socket.

**Why octal?** Mode A codes are four octal digits (0-7). `8` and `9` cannot
appear; `squawk-decoder` rejects them outright rather than silently coercing,
because a `9` in a squawk almost always means a parsing bug upstream.

## Development

```bash
git clone https://github.com/technothatch/squawk-decoder.git
cd squawk-decoder
pip install -e ".[dev]"

make lint     # ruff check + ruff format --check + mypy
make test     # pytest with coverage (fail-under 80)
make check    # everything, the same gate CI runs
```

The test suite covers decoding, flag derivation, category/type lookup, fuzzy
search, monitor change-detection, HTTP failure handling and every CLI command —
139 tests at 95% line coverage.

## Related projects

- [dump1090-fa](https://github.com/flightaware/dump1090) — FlightAware's decoder
- [readsb](https://github.com/wiedehopf/readsb) — high-performance fork, JSON docs in-repo
- [tar1090](https://github.com/wiedehopf/tar1090) — web UI for readsb
- [dump1090-alerts](https://github.com/technothatch/dump1090-alerts) — rule-based notifications
- [dump1090-cli](https://github.com/technothatch/dump1090-cli) — pretty CLI for live data

## License

MIT © technothatch. See [LICENSE](LICENSE).
