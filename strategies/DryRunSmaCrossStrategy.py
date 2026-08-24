"""Conservative, explanatory Freqtrade dry-run baseline.

Signals use completed candles. Freqtrade receives them after candle close and
simulates market-order execution; this strategy does not use future rows.
"""

from __future__ import annotations

from pandas import DataFrame

from freqtrade.strategy import IStrategy


class DryRunSmaCrossStrategy(IStrategy):
    """Long-only SMA crossover baseline for dry-run plumbing validation."""

    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    minimal_roi = {"0": 0.03}
    stoploss = -0.03

    fast_window = 20
    slow_window = 50

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["fast_sma"] = dataframe["close"].rolling(self.fast_window, min_periods=self.fast_window).mean()
        dataframe["slow_sma"] = dataframe["close"].rolling(self.slow_window, min_periods=self.slow_window).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["volume"] > 0)
                & (dataframe["fast_sma"] > dataframe["slow_sma"])
                & (dataframe["fast_sma"].shift(1) <= dataframe["slow_sma"].shift(1))
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["volume"] > 0)
                & (dataframe["fast_sma"] < dataframe["slow_sma"])
                & (dataframe["fast_sma"].shift(1) >= dataframe["slow_sma"].shift(1))
            ),
            "exit_long",
        ] = 1
        return dataframe
