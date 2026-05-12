"""策略在 on_bar 里收到的上下文：历史 K、持仓只读视图、下单 API、用户 state。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Order, Side


class StrategyContext:
    def __init__(self):
        self.history: list[Bar] = []
        self.pending_orders: list[Order] = []
        self.state: dict = {}
        self._position_side: Optional[Side] = None
        self._position_qty: int = 0

    def _push_bar(self, bar: Bar) -> None:
        self.history.append(bar)

    def _set_position(self, side: Optional[Side], qty: int) -> None:
        self._position_side = side
        self._position_qty = qty

    @property
    def current_bar(self) -> Bar:
        return self.history[-1]

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.history]

    @property
    def position_side(self) -> Optional[Side]:
        return self._position_side

    @property
    def position_qty(self) -> int:
        return self._position_qty

    def buy(self, qty: int, reason: str = "") -> None:
        self.pending_orders.append(Order(action="open_long", qty=qty, reason=reason))

    def sell(self, qty: int, reason: str = "") -> None:
        self.pending_orders.append(Order(action="open_short", qty=qty, reason=reason))

    def close(self, reason: str = "") -> None:
        self.pending_orders.append(Order(action="close", qty=self._position_qty or 0, reason=reason))

    def drain_pending(self) -> list[Order]:
        out = self.pending_orders
        self.pending_orders = []
        return out
