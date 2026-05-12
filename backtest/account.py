"""回测账户：权益/可用/持仓/爆仓判定。所有 fill 都通过 apply_fill 入账。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Fill, Position


class Account:
    def __init__(self, initial_capital: float, margin_rate: float,
                 tick_size: float, tick_value: float):
        self.initial_capital = initial_capital
        self.margin_rate = margin_rate
        self.tick_size = tick_size
        self.tick_value = tick_value

        self.position: Optional[Position] = None
        self.realized_pnl: float = 0.0
        self.total_fee: float = 0.0
        self.unrealized_pnl: float = 0.0
        self._last_close: Optional[float] = None

    @property
    def equity(self) -> float:
        return self.initial_capital + self.realized_pnl - self.total_fee + self.unrealized_pnl

    @property
    def available(self) -> float:
        margin = self.position.margin if self.position else 0.0
        return self.equity - margin

    def _price_to_pnl(self, entry: float, exit_: float, qty: int, side: str) -> float:
        """按跳数折算金额：每跳价值 tick_value，每跳 = tick_size。"""
        ticks = (exit_ - entry) / self.tick_size
        sign = 1 if side == "long" else -1
        return ticks * self.tick_value * qty * sign

    def apply_fill(self, fill: Fill) -> None:
        self.total_fee += fill.fee

        if fill.action in ("open_long", "open_short"):
            if self.position is not None:
                raise ValueError("持仓中，不支持反手 / 加仓；请先平仓")
            side = "long" if fill.action == "open_long" else "short"
            margin = fill.price * fill.qty * self.tick_value * self.margin_rate / self.tick_size
            self.position = Position(
                side=side, qty=fill.qty, avg_price=fill.price,
                opened_time=fill.time, margin=margin,
            )
            return

        if fill.action == "close":
            if self.position is None:
                raise ValueError("无持仓，无法平仓")
            if fill.qty != self.position.qty:
                raise ValueError(
                    f"本期不支持部分平仓：持仓 {self.position.qty} 手，平仓 {fill.qty} 手"
                )
            pnl = self._price_to_pnl(
                entry=self.position.avg_price, exit_=fill.price,
                qty=self.position.qty, side=self.position.side,
            )
            self.realized_pnl += pnl
            self.position = None
            self.unrealized_pnl = 0.0
            return

        raise ValueError(f"未知 fill.action: {fill.action}")

    def update_on_close(self, close_price: float) -> None:
        self._last_close = close_price
        if self.position is None:
            self.unrealized_pnl = 0.0
            return
        self.unrealized_pnl = self._price_to_pnl(
            entry=self.position.avg_price, exit_=close_price,
            qty=self.position.qty, side=self.position.side,
        )

    def is_liquidated(self) -> bool:
        return self.available < 0
