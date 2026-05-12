"""回测事件循环。"""
from __future__ import annotations

import pandas as pd

from backtest.account import Account
from backtest.broker import Broker
from backtest.context import StrategyContext
from backtest.metrics import compute_metrics
from backtest.models import (
    Bar, BacktestConfig, BacktestResult, EquityPoint, Fill, Trade,
)
from backtest.registry import get_strategy


class BacktestEngine:
    def __init__(self, config: BacktestConfig, bars_df: pd.DataFrame):
        self.config = config
        self.bars_df = bars_df.copy()
        self.account = Account(
            initial_capital=config.initial_capital,
            margin_rate=config.margin_rate,
            tick_size=config.tick_size,
            tick_value=config.tick_value,
        )
        self.broker = Broker(
            tick_size=config.tick_size,
            slippage_ticks=config.slippage_ticks,
            fee_per_lot=config.fee_per_lot,
        )
        self.ctx = StrategyContext()
        self.strategy_fn = get_strategy(config.strategy)

    @staticmethod
    def _df_to_bars(df: pd.DataFrame) -> list[Bar]:
        bars = []
        for _, row in df.iterrows():
            t = row["bob"]
            if hasattr(t, "strftime"):
                ts = t.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts = str(t)
            bars.append(Bar(time=ts, open=float(row["open"]),
                            high=float(row["high"]), low=float(row["low"]),
                            close=float(row["close"]), volume=float(row["volume"])))
        return bars

    def run(self) -> BacktestResult:
        bars = self._df_to_bars(self.bars_df)
        fills: list[Fill] = []
        trades: list[Trade] = []
        equity_curve: list[EquityPoint] = []
        peak_equity = self.config.initial_capital
        liquidated_at: str | None = None

        # 记录"开仓信息"用来在平仓时合成 Trade
        open_ctx: dict | None = None

        def _close_fill_to_trade(close_fill: Fill, opened: dict) -> Trade:
            entry = opened["price"]
            exit_ = close_fill.price
            side = opened["side"]
            ticks = (exit_ - entry) / self.config.tick_size
            sign = 1 if side == "long" else -1
            pnl = ticks * self.config.tick_value * close_fill.qty * sign
            fee_total = opened["fee"] + close_fill.fee
            holding = opened["bar_index_to_close"] - opened["bar_index_open"]
            return Trade(
                open_time=opened["time"], close_time=close_fill.time,
                side=side, qty=close_fill.qty,
                open_price=entry, close_price=exit_,
                fee=fee_total, pnl=pnl, net_pnl=pnl - fee_total,
                holding_bars=holding,
                open_reason=opened["reason"], close_reason=close_fill.reason,
            )

        prev_date = None

        for i, bar in enumerate(bars):
            # --- 先让待成交单在当根开盘成交 ---
            fill = self.broker.execute_on_open(bar)
            if fill is not None:
                self.account.apply_fill(fill)
                fills.append(fill)
                if fill.action in ("open_long", "open_short"):
                    self.ctx._set_position(
                        "long" if fill.action == "open_long" else "short",
                        fill.qty,
                    )
                    open_ctx = {
                        "time": fill.time, "price": fill.price, "qty": fill.qty,
                        "side": "long" if fill.action == "open_long" else "short",
                        "fee": fill.fee, "reason": fill.reason,
                        "bar_index_open": i,
                    }
                elif fill.action == "close":
                    if open_ctx is not None:
                        open_ctx["bar_index_to_close"] = i
                        trades.append(_close_fill_to_trade(fill, open_ctx))
                        open_ctx = None
                    self.ctx._set_position(None, 0)

            # --- 策略产生新信号（可能塞 broker） ---
            self.ctx._push_bar(bar)
            self.strategy_fn(bar, self.ctx, **self.config.strategy_params)
            for order in self.ctx.drain_pending():
                if order.action in ("open_long", "open_short"):
                    if self.account.position is None:
                        self.broker.submit(order)
                elif order.action == "close":
                    if self.account.position is not None:
                        self.broker.submit(order, close_side=self.account.position.side)

            # --- 收盘结算 + 权益点（以 close 价做 mark-to-market） ---
            self.account.update_on_close(bar.close)
            peak_equity = max(peak_equity, self.account.equity)
            dd = self.account.equity - peak_equity   # ≤ 0
            equity_curve.append(EquityPoint(time=bar.time, equity=self.account.equity, drawdown=dd))

            # --- 日末强平（仅 intraday_only）---
            # 注意：必须放在上面的权益点记录之后，这样当根 EquityPoint.equity
            # 反映的是 close 价下的浮盈权益，而不是 flatten 后的现金。
            if self.config.intraday_only:
                cur_date = bar.time.split(" ")[0]
                is_last_of_day = (i == len(bars) - 1) or (
                    bars[i + 1].time.split(" ")[0] != cur_date
                )
                if is_last_of_day and self.account.position is not None:
                    force = self.broker.force_close(
                        time=bar.time, qty=self.account.position.qty,
                        side=self.account.position.side, price=bar.close, reason="eod",
                    )
                    self.account.apply_fill(force)
                    fills.append(force)
                    if open_ctx is not None:
                        open_ctx["bar_index_to_close"] = i
                        trades.append(_close_fill_to_trade(force, open_ctx))
                        open_ctx = None
                    self.ctx._set_position(None, 0)

            # --- 爆仓 → 下一根开盘强平，随后停止新开仓 ---
            if self.account.is_liquidated() and liquidated_at is None:
                liquidated_at = bar.time
                if i + 1 < len(bars) and self.account.position is not None:
                    next_bar = bars[i + 1]
                    force = self.broker.force_close(
                        time=next_bar.time, qty=self.account.position.qty,
                        side=self.account.position.side, price=next_bar.open, reason="liquidate",
                    )
                    self.account.apply_fill(force)
                    fills.append(force)
                    if open_ctx is not None:
                        open_ctx["bar_index_to_close"] = i + 1
                        trades.append(_close_fill_to_trade(force, open_ctx))
                        open_ctx = None
                    self.ctx._set_position(None, 0)
                break

        metrics = compute_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=self.config.initial_capital,
            final_position="flat" if self.account.position is None else self.account.position.side,
        )

        return BacktestResult(
            config=self.config, bars=bars, fills=fills, trades=trades,
            equity_curve=equity_curve, metrics=metrics,
            liquidated=liquidated_at is not None, liquidated_at=liquidated_at,
        )
