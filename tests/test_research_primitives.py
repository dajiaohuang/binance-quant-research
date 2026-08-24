from datetime import datetime, timedelta, timezone
import unittest

from quant_research.backtest import run_backtest, sma_cross_signals
from quant_research.freqtrade_dry_run import build_command
from quant_research.freqtrade_research import build_freqtrade_argv
from quant_research.models import Bar
from quant_research.validate_data import validate_bars


def bars_from_closes(closes: list[float]) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [Bar(start + timedelta(days=index), close, close + 1, close - 1, close, 100.0) for index, close in enumerate(closes)]


class ResearchPrimitivesTests(unittest.TestCase):
    def test_validator_rejects_duplicate_timestamps(self) -> None:
        bars = bars_from_closes([10, 11])
        invalid = [bars[0], Bar(bars[0].timestamp, 11, 12, 10, 11, 1)]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            validate_bars(invalid)

    def test_sma_cross_uses_only_available_closes(self) -> None:
        signals = sma_cross_signals(bars_from_closes([10, 9, 8, 9, 10, 11]), 2, 3)
        self.assertEqual(signals[:3], [0, 0, 0])
        self.assertIn(1, signals)

    def test_backtest_delays_signal_to_next_open_and_charges_costs(self) -> None:
        bars = bars_from_closes([10, 9, 8, 9, 10, 11, 10, 9, 8])
        metrics, trades = run_backtest(bars, {"initial_cash": 1000, "fee_rate": 0.001, "slippage_rate": 0.001, "position_fraction": 1, "fast_window": 2, "slow_window": 3})
        self.assertGreaterEqual(metrics["trade_count"], 1)
        self.assertGreater(trades[0].fees, 0)

    def test_dry_run_launcher_cannot_start_live_mode(self) -> None:
        command = build_command(__import__("pathlib").Path.cwd())
        self.assertEqual(command[1], "trade")
        self.assertIn("--dry-run", command)
        self.assertNotIn("--dry-run=false", command)

    def test_research_launcher_rejects_trade_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            build_freqtrade_argv(["trade", "--dry-run"])

    def test_research_launcher_allows_backtesting(self) -> None:
        self.assertEqual(
            build_freqtrade_argv(["backtesting", "--help"]),
            ["freqtrade", "backtesting", "--help"],
        )


if __name__ == "__main__":
    unittest.main()
