"""SMA baseline with a fixed minimum holding period for regular exits."""

from __future__ import annotations

from datetime import timedelta

from freqtrade.exchange import timeframe_to_minutes
from freqtrade.persistence import Trade

from DryRunSmaCrossStrategy import DryRunSmaCrossStrategy


class DryRunSmaCrossMinHoldStrategy(DryRunSmaCrossStrategy):
    """Reuse the SMA baseline and delay non-stop exits for six candles."""

    min_hold_bars = 6

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time,
        **kwargs,
    ) -> bool:
        """Allow stop-loss protection immediately, but delay regular exits."""
        if exit_reason in {"stop_loss", "stoploss_on_exchange", "emergency_exit", "force_exit"}:
            return True

        hold_minutes = timeframe_to_minutes(self.timeframe) * self.min_hold_bars
        return current_time - trade.open_date_utc >= timedelta(minutes=hold_minutes)
