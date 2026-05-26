"""Broker：把 Order 在下一根 K 按规则成交（支持市价单和 Stop 单）。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Fill, Order, Side


class Broker:
    def __init__(self, tick_size: float, slippage_ticks: int, fee_per_lot: float,
                 instrument_type: str = "futures"):
        self.tick_size = tick_size
        self.slippage_ticks = slippage_ticks
        self.fee_per_lot = fee_per_lot
        self.instrument_type = instrument_type
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

    def _try_fill_stop(self, order: Order, bar: Bar, close_side: Optional[Side]) -> Optional[Fill]:
        """Stop 单触发逻辑：
        - 做多 Stop：trigger_price 被 bar.high 触及或突破 → 按 trigger_price + 滑点成交
        - 做空 Stop：trigger_price 被 bar.low 触及或突破 → 按 trigger_price - 滑点成交
        返回 Fill 表示成交，None 表示未触发（保留待成交）。
        """
        if order.trigger_price <= 0:
            # 非 Stop 单，按原逻辑在开盘成交
            price = self._slip(bar.open, order.action, close_side)
            return Fill(
                time=bar.time, action=order.action, qty=order.qty,
                price=price, fee=0, reason=order.reason,
            )

        tp = order.trigger_price
        if order.action == "open_long":
            # 买入 Stop：价格向上突破 trigger_price 时触发
            if bar.high >= tp:
                # 如果开盘就跳空越过 trigger，按开盘价成交；否则按 trigger
                fill_price = bar.open if bar.open >= tp else tp
                price = self._slip(fill_price, order.action, close_side)
                return Fill(
                    time=bar.time, action=order.action, qty=order.qty,
                    price=price, fee=0, reason=order.reason,
                )
        elif order.action == "open_short":
            # 卖出 Stop：价格向下突破 trigger_price 时触发
            if bar.low <= tp:
                fill_price = bar.open if bar.open <= tp else tp
                price = self._slip(fill_price, order.action, close_side)
                return Fill(
                    time=bar.time, action=order.action, qty=order.qty,
                    price=price, fee=0, reason=order.reason,
                )

        # 未触发，保留
        return None

    def execute_on_open(self, bar: Bar) -> Optional[Fill]:
        if self._pending is None:
            return None
        order = self._pending
        close_side = self._pending_close_side

        fill = self._try_fill_stop(order, bar, close_side)
        if fill is not None:
            # 成交了，清空待成交
            self._pending = None
            self._pending_close_side = None
        # 未触发时 fill 为 None，pending 保留到下一根 K
        return fill

    def force_close(self, time: str, qty: int, side: Side, price: float,
                    reason: str = "") -> Fill:
        exec_price = self._slip(price, "close", side)
        self._pending = None
        self._pending_close_side = None
        # fee 由 Account.apply_fill 计算，此处传 0
        return Fill(
            time=time, action="close", qty=qty,
            price=exec_price, fee=0, reason=reason,
        )

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    @property
    def pending_order(self) -> Optional[Order]:
        return self._pending
