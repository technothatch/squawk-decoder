"""Command-line interface for squawk-decoder."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any

from squawk_decoder import __version__
from squawk_decoder.core import (
    UnknownCodeError,
    decode_category,
    decode_squawk,
    emergency_codes,
    fuzzy_type,
    lookup_type,
    type_count,
)
from squawk_decoder.monitor import (
    DEFAULT_URL,
    FetchError,
    SquawkMonitor,
    fetch_aircraft,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNKNOWN = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squawk-decoder",
        description=(
            "Offline Mode A squawk, emitter category and ICAO type decoder, "
            "plus a live squawk monitor for dump1090 / readsb."
        ),
        epilog="ADS-B data is public by design; nothing here contacts the network unless asked to.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_decode = sub.add_parser("decode", help="decode a Mode A squawk code")
    p_decode.add_argument("code", help="1-4 octal digits, e.g. 7700")
    p_decode.add_argument("--json", action="store_true", help="emit JSON")
    p_decode.add_argument("--strict", action="store_true", help="fail on unknown codes")

    p_cat = sub.add_parser("category", help="decode an ADS-B emitter category")
    p_cat.add_argument("code", help="emitter category, e.g. A3 or B1")
    p_cat.add_argument("--json", action="store_true", help="emit JSON")

    p_type = sub.add_parser("type", help="look up an ICAO aircraft type designator")
    p_type.add_argument("code", help="ICAO type code, e.g. A321")
    p_type.add_argument("--json", action="store_true", help="emit JSON")

    p_search = sub.add_parser("search", help="fuzzy-search bundled aircraft types")
    p_search.add_argument("query", help="text to search for, e.g. 'airbus 320'")
    p_search.add_argument("--limit", type=int, default=15, help="maximum results (default 15)")
    p_search.add_argument("--json", action="store_true", help="emit JSON")

    p_watch = sub.add_parser("watch", help="live squawk monitor against a decoder")
    p_watch.add_argument(
        "--url", default=DEFAULT_URL, help=f"aircraft.json URL (default {DEFAULT_URL})"
    )
    p_watch.add_argument(
        "--interval", type=float, default=2.0, help="poll interval seconds (default 2)"
    )
    p_watch.add_argument(
        "--timeout", type=float, default=5.0, help="HTTP timeout seconds (default 5)"
    )
    p_watch.add_argument(
        "--all", action="store_true", help="report every squawk, not just interesting ones"
    )
    p_watch.add_argument("--json", action="store_true", help="emit one JSON object per event")
    p_watch.add_argument(
        "--max-polls",
        type=int,
        default=0,
        help="stop after N polls (0 = run forever; used by tests)",
    )

    p_emerg = sub.add_parser("emergency", help="list the ICAO emergency squawks")
    p_emerg.add_argument("--json", action="store_true", help="emit JSON")

    p_stats = sub.add_parser("info", help="show bundled dataset statistics")
    p_stats.add_argument("--json", action="store_true", help="emit JSON")

    return parser


def _print_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _cmd_decode(args: argparse.Namespace) -> int:
    try:
        squawk = decode_squawk(args.code, strict=args.strict)
    except UnknownCodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    if args.json:
        _print_json(
            {
                "code": squawk.code,
                "name": squawk.name,
                "note": squawk.note,
                "kind": squawk.kind,
                "emergency": squawk.emergency,
                "flags": list(squawk.flags),
                "in_range": squawk.in_range,
            }
        )
        return EXIT_OK if not squawk.emergency else EXIT_UNKNOWN
    digits = " ".join(str(d) for d in squawk.digits)
    decimal = sum(d * 8**i for i, d in enumerate(reversed(squawk.digits)))
    print(f"{squawk.code}  {squawk.name}")
    print(f"  digits : {digits} (decimal {decimal})")
    print(f"  kind   : {squawk.kind}")
    print(f"  flags  : {', '.join(squawk.flags)}")
    if squawk.in_range:
        print(f"  range  : {squawk.in_range}")
    print(f"  note   : {squawk.note}")
    if squawk.emergency:
        print("\n  *** EMERGENCY SQUAWK ***", file=sys.stderr)
        return EXIT_UNKNOWN
    return EXIT_OK


def _cmd_category(args: argparse.Namespace) -> int:
    category = decode_category(args.code)
    if category is None:
        print(f"error: unknown emitter category {args.code!r}", file=sys.stderr)
        return EXIT_UNKNOWN
    if args.json:
        _print_json(
            {
                "code": category.code,
                "label": category.label,
                "group": category.group,
                "description": category.description,
            }
        )
        return EXIT_OK
    print(f"{category.code}  {category.label}  ({category.group})")
    print(f"  {category.description}")
    return EXIT_OK


def _cmd_type(args: argparse.Namespace) -> int:
    name = lookup_type(args.code)
    if name is None:
        print(f"error: unknown ICAO type {args.code!r}", file=sys.stderr)
        suggestions = fuzzy_type(args.code, limit=5)
        if suggestions:
            print("did you mean:", file=sys.stderr)
            for code, sugg_name in suggestions:
                print(f"  {code:<6} {sugg_name}", file=sys.stderr)
        return EXIT_UNKNOWN
    if args.json:
        _print_json({"code": args.code.strip().upper(), "name": name})
        return EXIT_OK
    print(f"{args.code.strip().upper()}  {name}")
    return EXIT_OK


def _cmd_search(args: argparse.Namespace) -> int:
    results = fuzzy_type(args.query, limit=max(1, args.limit))
    if args.json:
        _print_json([{"code": c, "name": n} for c, n in results])
        return EXIT_OK if results else EXIT_UNKNOWN
    if not results:
        print(f"no matches for {args.query!r}", file=sys.stderr)
        return EXIT_UNKNOWN
    for code, name in results:
        print(f"{code:<8} {name}")
    return EXIT_OK


def _cmd_emergency(args: argparse.Namespace) -> int:
    codes = sorted(emergency_codes())
    if args.json:
        _print_json([asdict(decode_squawk(c)) for c in codes])
        return EXIT_OK
    for code in codes:
        squawk = decode_squawk(code)
        print(f"{code}  {squawk.name}")
        print(f"         {squawk.note}")
    return EXIT_OK


def _cmd_info(args: argparse.Namespace) -> int:
    n_types = type_count()
    codes = sorted(emergency_codes())
    if args.json:
        _print_json({"types": n_types, "emergency_codes": codes, "version": __version__})
        return EXIT_OK
    print(f"squawk-decoder {__version__}")
    print(f"  bundled ICAO types : {n_types}")
    print(f"  emergency squawks  : {', '.join(codes)}")
    return EXIT_OK


def _json_event(event: Any) -> dict[str, Any]:
    return {
        "hex": event.hex,
        "squawk": event.squawk,
        "callsign": event.callsign,
        "kind": event.kind,
        "name": event.name,
        "emergency": event.is_emergency,
        "flags": list(event.flags),
        "ts": time.time(),
    }


def _cmd_watch(args: argparse.Namespace, *, fetch: Callable[..., list[Any]] | None = None) -> int:
    fetcher = fetch if fetch is not None else fetch_aircraft
    emitted: list[Any] = []

    def handler(event: Any) -> None:
        emitted.append(event)
        if args.json:
            print(json.dumps(_json_event(event)), flush=True)
        else:
            print(event.render(), flush=True)

    monitor = SquawkMonitor(
        lambda: fetcher(args.url, timeout=args.timeout),
        on_event=handler,
        include_normal=args.all,
    )
    polls = 0
    print(f"# watching {args.url} every {args.interval}s — ctrl-c to stop", file=sys.stderr)
    try:
        while True:
            try:
                monitor.poll()
            except FetchError as exc:
                monitor.errors += 1
                print(f"# {exc}", file=sys.stderr)
            polls += 1
            if args.max_polls and polls >= args.max_polls:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n# stopped", file=sys.stderr)
    print(
        f"# polls={monitor.polls} events={monitor.events} errors={monitor.errors}", file=sys.stderr
    )
    return EXIT_ERROR if monitor.polls == 0 else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help()
        return EXIT_ERROR
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "decode": _cmd_decode,
        "category": _cmd_category,
        "type": _cmd_type,
        "search": _cmd_search,
        "emergency": _cmd_emergency,
        "info": _cmd_info,
        "watch": _cmd_watch,
    }
    try:
        return handlers[args.command](args)
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
