"""Safe local launcher for the workspace's Freqtrade dry-run baseline."""

from __future__ import annotations

import sys
from pathlib import Path


def build_command(root: Path) -> list[str]:
    """Build a fixed dry-run invocation; this launcher never accepts live mode."""
    config = root / "config" / "freqtrade-dry-run.json"
    strategy_path = root / "strategies"
    if not config.is_file():
        raise FileNotFoundError(f"dry-run configuration not found: {config}")
    if not strategy_path.is_dir():
        raise FileNotFoundError(f"strategy directory not found: {strategy_path}")
    return [
        "freqtrade",
        "trade",
        "--config",
        str(config),
        "--strategy",
        "DryRunSmaCrossStrategy",
        "--strategy-path",
        str(strategy_path),
        "--db-url",
        f"sqlite:///{root / 'freqtrade-state' / 'trades.dryrun.sqlite'}",
        "--logfile",
        str(root / "logs" / "freqtrade-dry-run.log"),
        "--dry-run",
    ]


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("quant-dry-run accepts no arguments; its invocation is fixed to dry-run mode")

    # aiohttp defaults to aiodns when installed. In this Windows environment,
    # aiodns cannot reach the system resolver while the threaded resolver can.
    import aiohttp.connector
    from aiohttp.resolver import ThreadedResolver

    aiohttp.connector.DefaultResolver = ThreadedResolver

    from freqtrade.main import main as freqtrade_main

    sys.argv = build_command(Path.cwd())
    return freqtrade_main()
