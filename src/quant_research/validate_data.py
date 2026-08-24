from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Bar

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}


def parse_timestamp(value: str) -> datetime:
    try:
        numeric = float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        if numeric > 1_000_000_000_000:
            numeric /= 1000
        parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_ohlcv(path: Path) -> list[Bar]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        normalized = {name.lower().strip(): name for name in reader.fieldnames}
        missing = REQUIRED_COLUMNS - normalized.keys()
        if missing:
            raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
        bars: list[Bar] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                bar = Bar(
                    timestamp=parse_timestamp(row[normalized["timestamp"]]),
                    open=float(row[normalized["open"]]),
                    high=float(row[normalized["high"]]),
                    low=float(row[normalized["low"]]),
                    close=float(row[normalized["close"]]),
                    volume=float(row[normalized["volume"]]),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid value at CSV line {line_number}: {exc}") from exc
            bars.append(bar)
    validate_bars(bars)
    return bars


def validate_bars(bars: Iterable[Bar], expected_interval_seconds: int | None = None) -> None:
    values = list(bars)
    if len(values) < 2:
        raise ValueError("at least two bars are required")
    previous: Bar | None = None
    for bar in values:
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise ValueError(f"non-positive price at {bar.timestamp.isoformat()}")
        if bar.volume < 0:
            raise ValueError(f"negative volume at {bar.timestamp.isoformat()}")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.low > bar.high:
            raise ValueError(f"invalid OHLC relationship at {bar.timestamp.isoformat()}")
        if previous is not None:
            delta = (bar.timestamp - previous.timestamp).total_seconds()
            if delta <= 0:
                raise ValueError(f"timestamps are not strictly increasing at {bar.timestamp.isoformat()}")
            if expected_interval_seconds is not None and delta != expected_interval_seconds:
                raise ValueError(f"missing or irregular bar between {previous.timestamp.isoformat()} and {bar.timestamp.isoformat()}")
        previous = bar


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an OHLCV CSV without modifying it.")
    parser.add_argument("data", type=Path)
    parser.add_argument("--expected-interval-seconds", type=int)
    args = parser.parse_args()
    bars = load_ohlcv(args.data)
    if args.expected_interval_seconds is not None:
        validate_bars(bars, args.expected_interval_seconds)
    print(f"valid bars={len(bars)} start={bars[0].timestamp.isoformat()} end={bars[-1].timestamp.isoformat()} sha256={sha256(args.data)}")


if __name__ == "__main__":
    main()
