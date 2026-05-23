"""回测账户：权益/可用/持仓/爆仓判定。所有 fill 都通过 apply_fill 入账。"""
from __future__ import annotations

from typing import Optional

from backtest.models import Fill, Position


class Account:
    def __init__(self, initial_capital: float, margin_rate: float,
                 tick_size: float, tick_value: float,
                 instrument_type: str = "futures",
                 commission_rate: float = 0.00025,
                 stamp_tax_rate: float = 0.001,
                 transfer_fee_rate: float = 0.00001,
                 lot_size: int = 100,
                 fee_per_lot: float = 3.0):
        self.initial_capital = initial_capital
        self.margin_rate = margin_rate
        self.tick_size = tick_size
        self.tick_value = tick_value
        self.instrument_type = instrument_type
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.transfer_fee_rate = transfer_fee_rate
        self.lot_size = lot_size
        self._fee_per_lot = fee_per_lot

        self.position: Optional[Position] = None
        self.realized_pnl: float = 0.0
        self.total_fee: float = 0.0
        self.unrealized_pnl: float = 0.0

    @property
    def equity(self) -> float:
        return self.initial_capital + self.realized_pnl - self.total_fee + self.unrealized_pnl

    @property
    def available(self) -> float:
        margin = self.position.margin if self.position else 0.0
        return self.equity - margin

    def _price_to_pnl(self, entry: float, exit_: float, qty: int, side: str) -> float:
        """计算盈亏。
        
        期货：按跳数折算，每跳价值 tick_value。
        股票：直接价差 × 股数（qty × lot_size）。
        """
        if self.instrument_type == "stock":
            return (exit_ - entry) * qty * self.lot_size
        ticks = (exit_ - entry) / self.tick_size
        sign = 1 if side == "long" else -1
        return ticks * self.tick_value * qty * sign

    def _calc_stock_fee(self, price: float, qty: int, action: str) -> float:
        """计算 A 股交易费用。
        
        佣金：买卖双向，commission_rate × 成交额
        印花税：仅卖出，stamp_tax_rate × 成交额
        过户费：买卖双向，transfer_fee_rate × 成交额（沪市深市均收）
        """
        turnover = price * qty * self.lot_size
        commission = turnover * self.commission_rate
        # 佣金最低 5 元（交易所规定，部分券商可议价；这里简单实现）
        commission = max(commission, 5.0)
        stamp = 0.0
        if action == "close":
            stamp = turnover * self.stamp_tax_rate
        transfer = turnover * self.transfer_fee_rate
        return commission + stamp + transfer

    def _calc_futures_fee(self, price: float, qty: int, action: str) -> float:
        """期货按手固定手续费。"""
        return self._fee_per_lot * qty

    def apply_fill(self, fill: Fill) -> None:
        if fill.action in ("open_long", "open_short"):
            if self.position is not None:
                raise ValueError("持仓中，不支持反手 / 加仓；请先平仓")
            side = "long" if fill.action == "open_long" else "short"

            if self.instrument_type == "stock":
                fee = self._calc_stock_fee(fill.price, fill.qty, "open")
                # 股票全款买入
                margin = fill.price * fill.qty * self.lot_size * self.margin_rate
            else:
                fee = self._calc_futures_fee(fill.price, fill.qty, fill.action)
                margin = fill.price * fill.qty * self.tick_value * self.margin_rate / self.tick_size

            self.position = Position(
                side=side, qty=fill.qty, avg_price=fill.price,
                opened_time=fill.time, margin=margin,
            )
            self.total_fee += fee
            fill.fee = fee
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

            if self.instrument_type == "stock":
                fee = self._calc_stock_fee(fill.price, fill.qty, "close")
            else:
                fee = self._calc_futures_fee(fill.price, fill.qty, fill.action)

            self.position = None
            self.unrealized_pnl = 0.0
            self.total_fee += fee
            fill.fee = fee
            return

        raise ValueError(f"未知 fill.action: {fill.action}")

    def update_on_close(self, close_price: float) -> None:
        if self.position is None:
            self.unrealized_pnl = 0.0
            return
        self.unrealized_pnl = self._price_to_pnl(
            entry=self.position.avg_price, exit_=close_price,
            qty=self.position.qty, side=self.position.side,
        )

    def is_liquidated(self) -> bool:
        return self.available < 0
