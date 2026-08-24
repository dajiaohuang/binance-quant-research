"""SMA baseline gated by a completed same-pair one-hour breakout."""

from __future__ import annotations

from pandas import DataFrame

from freqtrade.strategy import informative

from DryRunSmaCrossStrategy import DryRunSmaCrossStrategy


class DryRunSmaCrossHtfBreakoutGateStrategy(DryRunSmaCrossStrategy):
    """Allow parent entries only during a completed one-hour breakout."""

    htf_breakout_lookback_hours = 24
    startup_candle_count = max(
        DryRunSmaCrossStrategy.startup_candle_count,
        (htf_breakout_lookback_hours + 1) * 12,
    )

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Mark closes above the preceding 24 completed one-hour highs."""
        preceding_high = (
            dataframe["high"]
            .shift(1)
            .rolling(
                self.htf_breakout_lookback_hours,
                min_periods=self.htf_breakout_lookback_hours,
            )
            .max()
        )
        dataframe["htf_breakout_gate"] = dataframe["close"] > preceding_high
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Mask the parent's entries without changing its 5m alpha definition."""
        dataframe = super().populate_entry_trend(dataframe, metadata)
        gate = dataframe.get("htf_breakout_gate_1h")
        if gate is None:
            dataframe.loc[:, "enter_long"] = 0
            return dataframe

        gate = gate.fillna(False).astype(bool)
        dataframe.loc[~gate, "enter_long"] = 0
        return dataframe
