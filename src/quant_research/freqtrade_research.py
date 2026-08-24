"""Safe Freqtrade launcher restricted to research and inspection commands."""

from __future__ import annotations

import sys
from collections.abc import Sequence


ALLOWED_COMMANDS = frozenset(
    {
        "backtesting",
        "backtesting-analysis",
        "backtesting-show",
        "download-data",
        "list-data",
        "list-markets",
        "list-pairs",
        "list-strategies",
        "lookahead-analysis",
        "recursive-analysis",
        "show-config",
    }
)


def build_freqtrade_argv(arguments: Sequence[str]) -> list[str]:
    """Validate a pass-through command and prepend Freqtrade's program name."""
    if not arguments:
        raise ValueError("a Freqtrade research command is required")
    command = arguments[0]
    if command not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        raise ValueError(f"command {command!r} is not allowed; choose one of: {allowed}")
    return ["freqtrade", *arguments]


def main() -> int:
    try:
        freqtrade_argv = build_freqtrade_argv(sys.argv[1:])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    # The optional aiodns resolver cannot reach this Windows environment's
    # local DNS service. ThreadedResolver uses the verified system resolver.
    import aiohttp.connector
    from aiohttp.resolver import ThreadedResolver

    aiohttp.connector.DefaultResolver = ThreadedResolver

    from freqtrade.main import main as freqtrade_main

    sys.argv = freqtrade_argv
    return freqtrade_main()
