from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

from .models import Bar
from .validate_data import load_ohlcv, sha256

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    net_pnl: float


def moving_average(values: list[float], window: int, index: int) -> float | None:
    if index + 1 < window:
        return None
    return mean(values[index + 1 - window : index + 1])


def sma_cross_signals(bars: list[Bar], fast_window: int, slow_window: int) -> list[int]:
    if not 0 < fast_window < slow_window:
        raise ValueError("fast_window must be positive and less than slow_window")
    closes = [bar.close for bar in bars]
    signals = [0] * len(bars)
    previous_fast: float | None = None
    previous_slow: float | None = None
    for index in range(len(bars)):
        fast = moving_average(closes, fast_window, index)
        slow = moving_average(closes, slow_window, index)
        if fast is not None and slow is not None and previous_fast is not None and previous_slow is not None:
            if fast > slow and previous_fast <= previous_slow:
                signals[index] = 1
            elif fast < slow and previous_fast >= previous_slow:
                signals[index] = -1
        previous_fast, previous_slow = fast, slow
    return signals


def run_backtest(bars: list[Bar], config: dict[str, object]) -> tuple[dict[str, float | int | None], list[Trade]]:
    initial_cash = float(config["initial_cash"])
    fee_rate = float(config["fee_rate"])
    slippage_rate = float(config["slippage_rate"])
    position_fraction = float(config["position_fraction"])
    if initial_cash <= 0 or fee_rate < 0 or slippage_rate < 0 or not 0 < position_fraction <= 1:
        raise ValueError("initial_cash must be positive; costs non-negative; position_fraction in (0, 1]")
    signals = sma_cross_signals(bars, int(config["fast_window"]), int(config["slow_window"]))
    cash = initial_cash
    quantity = 0.0
    entry_time: str | None = None
    entry_price = 0.0
    entry_fee = 0.0
    trades: list[Trade] = []
    equity_curve: list[float] = [initial_cash]
    for index in range(1, len(bars)):
        # The decision from bar index-1 executes at this bar's open.
        signal = signals[index - 1]
        bar = bars[index]
        if signal == 1 and quantity == 0:
            fill_price = bar.open * (1 + slippage_rate)
            capital = cash * position_fraction
            quantity = capital / (fill_price * (1 + fee_rate))
            entry_fee = quantity * fill_price * fee_rate
            cash -= quantity * fill_price + entry_fee
            entry_time, entry_price = bar.timestamp.isoformat(), fill_price
        elif signal == -1 and quantity > 0:
            fill_price = bar.open * (1 - slippage_rate)
            exit_fee = quantity * fill_price * fee_rate
            proceeds = quantity * fill_price - exit_fee
            gross_pnl = quantity * (fill_price - entry_price)
            trade = Trade(entry_time or "", bar.timestamp.isoformat(), entry_price, fill_price, quantity, gross_pnl, entry_fee + exit_fee, gross_pnl - entry_fee - exit_fee)
            trades.append(trade)
            cash += proceeds
            quantity = 0.0
        equity_curve.append(cash + quantity * bar.close)
    if quantity > 0:
        bar = bars[-1]
        fill_price = bar.close * (1 - slippage_rate)
        exit_fee = quantity * fill_price * fee_rate
        proceeds = quantity * fill_price - exit_fee
        gross_pnl = quantity * (fill_price - entry_price)
        trades.append(Trade(entry_time or "", bar.timestamp.isoformat(), entry_price, fill_price, quantity, gross_pnl, entry_fee + exit_fee, gross_pnl - entry_fee - exit_fee))
        cash += proceeds
        equity_curve[-1] = cash
    peak = initial_cash
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    returns = [equity_curve[i] / equity_curve[i - 1] - 1 for i in range(1, len(equity_curve)) if equity_curve[i - 1] > 0]
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = (mean(returns) / volatility * (252**0.5)) if volatility else None
    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    return ({"initial_cash": initial_cash, "final_equity": cash, "net_return": cash / initial_cash - 1, "max_drawdown": max_drawdown, "sharpe_daily_equivalent": sharpe, "trade_count": len(trades), "win_rate": len(wins) / len(trades) if trades else None, "profit_factor": gross_profit / gross_loss if gross_loss else None, "total_fees": sum(trade.fees for trade in trades)}, trades)


def next_experiment_directory(root: Path) -> Path:
    prefix = f"exp_{datetime.now(timezone.utc).strftime('%Y%m%d')}_"
    existing = [int(path.name.rsplit("_", 1)[-1]) for path in root.glob(f"{prefix}*") if path.name.rsplit("_", 1)[-1].isdigit()]
    directory = root / f"{prefix}{max(existing, default=0) + 1:03d}"
    directory.mkdir(parents=True)
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local next-bar SMA baseline and save an experiment artifact.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--experiments-dir", type=Path, default=Path("experiments"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    bars = load_ohlcv(args.data)
    metrics, trades = run_backtest(bars, config)
    output = next_experiment_directory(args.experiments_dir)
    manifest = {"experiment_id": output.name, "created_at": datetime.now(timezone.utc).isoformat(), "strategy": config.get("strategy"), "data_path": str(args.data), "data_sha256": sha256(args.data), "data_start": bars[0].timestamp.isoformat(), "data_end": bars[-1].timestamp.isoformat(), "status": "completed", "execution_assumption": "signal at prior bar close, fill at next bar open; final open position closes at final close"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "parameters.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (output / "trades.json").write_text(json.dumps([asdict(trade) for trade in trades], indent=2), encoding="utf-8")
    (output / "backtest.log").write_text(" ".join(["command=quant-backtest", f"config={args.config}", f"data={args.data}"]) + "\n", encoding="utf-8")
    print(f"experiment={output} net_return={metrics['net_return']:.6f} max_drawdown={metrics['max_drawdown']:.6f} trades={metrics['trade_count']}")


if __name__ == "__main__":
    main()
