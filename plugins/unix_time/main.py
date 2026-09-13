#!/usr/bin/env python3
"""Convert Unix timestamps to UTC and UTC date-times to Unix timestamps."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_EVEN
import sys


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ConversionError(ValueError):
    pass


def unix_to_utc(value: str) -> str:
    try:
        timestamp = Decimal(value)
    except InvalidOperation as error:
        raise ConversionError(f"invalid Unix timestamp: {value}") from error
    if not timestamp.is_finite():
        raise ConversionError("Unix timestamp must be finite")

    whole_seconds = int(timestamp.to_integral_value(rounding=ROUND_FLOOR))
    microseconds = int(
        ((timestamp - whole_seconds) * 1_000_000).to_integral_value(
            rounding=ROUND_HALF_EVEN
        )
    )
    try:
        converted = EPOCH + timedelta(
            seconds=whole_seconds, microseconds=microseconds
        )
    except (OverflowError, ValueError) as error:
        raise ConversionError("Unix timestamp is outside the supported date range") from error
    return converted.isoformat().replace("+00:00", "Z")


def utc_to_unix(value: str) -> str:
    normalized = value.strip()
    if normalized.upper().endswith(" UTC"):
        normalized = normalized[:-4] + "+00:00"
    elif normalized.endswith("Z") or normalized.endswith("z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        converted = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ConversionError(f"invalid UTC date-time: {value}") from error
    if converted.tzinfo is None or converted.utcoffset() != timedelta(0):
        raise ConversionError("date-time must use UTC (Z, UTC, or +00:00)")

    delta = converted - EPOCH
    timestamp = (
        delta.days * 86_400
        + delta.seconds
        + Decimal(delta.microseconds) / 1_000_000
    )
    formatted = format(timestamp, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def convert(request: str) -> str:
    command, separator, value = request.strip().partition(" ")
    value = value.strip()
    if not separator or not value:
        raise ConversionError("usage: to-utc <timestamp> or to-unix <UTC date-time>")
    if command == "to-utc":
        return unix_to_utc(value)
    if command == "to-unix":
        return utc_to_unix(value)
    raise ConversionError(f"unknown conversion: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "request",
        nargs="+",
        help="to-utc <timestamp> or to-unix <UTC date-time>",
    )
    args = parser.parse_args(argv)
    try:
        print(convert(" ".join(args.request)))
        return 0
    except ConversionError as error:
        print(f"unix_time: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
