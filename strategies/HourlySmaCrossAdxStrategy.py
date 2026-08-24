"""One-hour version of the rejected 5-minute SMA+ADX candidate.

This strategy intentionally changes only the candle timeframe to test whether
the same trend filter behaves better with lower market microstructure noise.
"""

from __future__ import annotations

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class HourlySmaCrossAdxStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 50

    minimal_roi = {"0": 0.03}
    stoploss = -0.03

    fast_window = 20
    slow_window = 50
    adx_window = 14
    adx_threshold = 25.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["fast_sma"] = dataframe["close"].rolling(self.fast_window, min_periods=self.fast_window).mean()
        dataframe["slow_sma"] = dataframe["close"].rolling(self.slow_window, min_periods=self.slow_window).mean()
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=self.adx_window)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["volume"] > 0)
                & (dataframe["adx"] >= self.adx_threshold)
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
