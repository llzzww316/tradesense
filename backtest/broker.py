"""Broker：把 Order 在下一根 K 开盘按规则成交。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Fill, Order, Side


class Broker:
    def __init__(self, tick_size: float, slippage_ticks: int, fee_per_lot: float):
        self.tick_size = tick_size
        self.slippage_ticks = slippage_ticks
        self.fee_per_lot = fee_per_lot
        self._pending: Optional[Order] = None
        self._pending_close_side: Optional[Side] = None

    def submit(self, order: Order, close_side: Optional[Side] = None) -> None:
        if self._pending is not None:
            raise ValueError("已有待成交订单，不支持同根 K 多单")
        if order.action == "close" and close_side is None:
            raise ValueError("提交 close 订单时必须提供当前 side")
        self._pending = order
        self._pending_close_side = close_side

    def _slip(self, price: float, action: str, close_side: Optional[Side]) -> float:
        slip_amt = self.slippage_ticks * self.tick_size
        if action == "open_long":
            return price + slip_amt
        if action == "open_short":
            return price - slip_amt
        if action == "close":
            # 平多=卖出→负滑点；平空=买回→正滑点
            return price - slip_amt if close_side == "long" else price + slip_amt
        raise ValueError(f"未知 action: {action}")

    def execute_on_open(self, bar: Bar) -> Optional[Fill]:
        if self._pending is None:
            return None
        order = self._pending
        close_side = self._pending_close_side
        price = self._slip(bar.open, order.action, close_side)
        fill = Fill(
            time=bar.time,
            action=order.action,
            qty=order.qty,
            price=price,
            fee=self.fee_per_lot * order.qty,
            reason=order.reason,
        )
        self._pending = None
        self._pending_close_side = None
        return fill

    def force_close(self, time: str, qty: int, side: Side, price: float,
                    reason: str = "") -> Fill:
        exec_price = self._slip(price, "close", side)
        self._pending = None
        self._pending_close_side = None
        return Fill(
            time=time, action="close", qty=qty,
            price=exec_price, fee=self.fee_per_lot * qty, reason=reason,
        )
