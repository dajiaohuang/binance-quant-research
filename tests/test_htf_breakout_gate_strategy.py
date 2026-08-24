from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd
from pandas import DataFrame
from pandas.testing import assert_series_equal

from freqtrade.strategy import merge_informative_pair


STRATEGY_DIRECTORY = Path(__file__).resolve().parents[1] / "strategies"
if str(STRATEGY_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIRECTORY))

from DryRunSmaCrossHtfBreakoutGateStrategy import (  # noqa: E402
    DryRunSmaCrossHtfBreakoutGateStrategy,
)
from DryRunSmaCrossStrategy import DryRunSmaCrossStrategy  # noqa: E402


def strategy_without_runtime(strategy_class):
    """Construct a strategy for pure dataframe-method tests without exchange state."""
    return object.__new__(strategy_class)


def hourly_frame(periods: int) -> DataFrame:
    dates = pd.date_range("2024-01-01T00:00:00Z", periods=periods, freq="1h")
    high = pd.Series([100.0 + index for index in range(periods)])
    close = high - 0.5
    if periods > 24:
        close.iloc[24] = 124.0
        high.iloc[24] = 124.5
    return DataFrame(
        {
            "date": dates,
            "open": close - 0.25,
            "high": high,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )


def entry_frame(include_gate: bool = True) -> DataFrame:
    dataframe = DataFrame(
        {
            "volume": [1.0, 1.0, 1.0, 1.0, 1.0],
            "fast_sma": [0.0, 2.0, 0.0, 2.0, 0.0],
            "slow_sma": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )
    if include_gate:
        dataframe["htf_breakout_gate_1h"] = [False, True, True, False, None]
    return dataframe


class HtfBreakoutGateStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parent = strategy_without_runtime(DryRunSmaCrossStrategy)
        self.candidate = strategy_without_runtime(DryRunSmaCrossHtfBreakoutGateStrategy)

    def test_informative_history_prefix_is_unchanged_by_future_rows(self) -> None:
        prefix = hourly_frame(30)
        future = hourly_frame(36)
        future.loc[30:, "high"] = 10_000.0
        future.loc[30:, "close"] = 9_999.0

        prefix_result = self.candidate.populate_indicators_1h(prefix.copy(), {})
        future_result = self.candidate.populate_indicators_1h(future.copy(), {})

        assert_series_equal(
            prefix_result["htf_breakout_gate"],
            future_result.loc[: len(prefix) - 1, "htf_breakout_gate"],
        )
        self.assertTrue(bool(prefix_result.loc[24, "htf_breakout_gate"]))

    def test_completed_hour_maps_to_last_5m_close_then_next_open(self) -> None:
        base = DataFrame(
            {
                "date": pd.to_datetime(
                    [
                        "2024-01-01T14:50:00Z",
                        "2024-01-01T14:55:00Z",
                        "2024-01-01T15:00:00Z",
                        "2024-01-01T15:05:00Z",
                    ]
                ),
                "close": [1.0, 2.0, 3.0, 4.0],
            }
        )
        informative = DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01T14:00:00Z"]),
                "htf_breakout_gate": [True],
            }
        )

        merged = merge_informative_pair(base, informative, "5m", "1h", ffill=True)
        self.assertTrue(pd.isna(merged.loc[0, "htf_breakout_gate_1h"]))
        self.assertTrue(bool(merged.loc[1, "htf_breakout_gate_1h"]))

        signal_at_completed_close = merged["htf_breakout_gate_1h"].fillna(False).astype(int)
        executable_signal = signal_at_completed_close.shift(1).fillna(0).astype(int)
        self.assertEqual(executable_signal.loc[1], 0)
        self.assertEqual(executable_signal.loc[2], 1)

    def test_gate_truth_and_candidate_entries_are_parent_subset(self) -> None:
        dataframe = entry_frame()
        parent_result = self.parent.populate_entry_trend(dataframe.copy(), {})
        candidate_result = self.candidate.populate_entry_trend(dataframe.copy(), {})

        parent_entries = parent_result["enter_long"].fillna(0).eq(1)
        candidate_entries = candidate_result["enter_long"].fillna(0).eq(1)

        self.assertTrue(bool(candidate_result.loc[1, "enter_long"] == 1))
        self.assertFalse(bool(candidate_entries.loc[3]))
        self.assertTrue(bool((~candidate_entries | parent_entries).all()))

    def test_missing_or_nan_gate_never_enters(self) -> None:
        missing_result = self.candidate.populate_entry_trend(entry_frame(False), {})
        self.assertFalse(bool(missing_result["enter_long"].fillna(0).eq(1).any()))

        nan_frame = entry_frame()
        nan_frame.loc[1, "htf_breakout_gate_1h"] = None
        nan_result = self.candidate.populate_entry_trend(nan_frame, {})
        self.assertFalse(bool(nan_result["enter_long"].fillna(0).eq(1).any()))

    def test_exit_column_is_identical_to_parent(self) -> None:
        dataframe = entry_frame()
        parent_result = self.parent.populate_exit_trend(dataframe.copy(), {})
        candidate_result = self.candidate.populate_exit_trend(dataframe.copy(), {})
        assert_series_equal(parent_result["exit_long"], candidate_result["exit_long"])

    def test_long_only_startup_and_parent_methods_are_preserved(self) -> None:
        self.assertFalse(DryRunSmaCrossHtfBreakoutGateStrategy.can_short)
        self.assertEqual(DryRunSmaCrossHtfBreakoutGateStrategy.timeframe, "5m")
        self.assertEqual(DryRunSmaCrossHtfBreakoutGateStrategy.htf_breakout_lookback_hours, 24)
        self.assertEqual(DryRunSmaCrossHtfBreakoutGateStrategy.startup_candle_count, 300)
        self.assertIs(
            DryRunSmaCrossHtfBreakoutGateStrategy.populate_indicators,
            DryRunSmaCrossStrategy.populate_indicators,
        )
        self.assertIs(
            DryRunSmaCrossHtfBreakoutGateStrategy.populate_exit_trend,
            DryRunSmaCrossStrategy.populate_exit_trend,
        )


if __name__ == "__main__":
    unittest.main()
