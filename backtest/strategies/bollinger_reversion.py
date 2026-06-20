"""布林带均值回归策略（5/15 分钟，多空对称）。

逻辑：
1. 计算布林带（SMA + K * StdDev）
2. 价格触及/突破上下轨时，等待确认形态（Pin Bar / 吞没）
3. 确认后反向入场，止损放 K 线极值 + ATR，止盈回归中轨

不要加 from __future__ import annotations
"""
import math
from backtest.indicators import (
    atr, pin_bar, engulfing, is_bull_bar, is_bear_bar,
)
from backtest.registry import register_strategy


@register_strategy("bollinger_reversion")
def on_bar(
    bar, ctx,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    stop_atr: float = 1.5,
    min_stop_ticks: float = 5.0,
    max_hold: int = 12,
    entry_confirmation: bool = True,
    qty: int = 1,
):
    st = ctx.state
    n = len(ctx.history)
    if n < max(bb_period + 1, atr_period + 2):
        return

    cur_atr = atr(ctx.history, atr_period)
    if cur_atr != cur_atr or cur_atr <= 0:
        return

    # 计算布林带
    closes = [b.close for b in ctx.history[-bb_period:]]
    sma = sum(closes) / len(closes)
    variance = sum((c - sma) ** 2 for c in closes) / len(closes)
    std = math.sqrt(variance)
    upper = sma + bb_std * std
    lower = sma - bb_std * std

    # 空仓清理
    if ctx.position_side is None:
        for k in ("stop", "target", "hold", "exiting"):
            st.pop(k, None)

    # 持仓管理
    if ctx.position_side is not None:
        if st.get("exiting"):
            return
        st["hold"] = st.get("hold", 0) + 1
        if ctx.position_side == "long":
            if bar.low <= st["stop"]:
                ctx.close(reason="stop_loss")
                st["exiting"] = True
            elif bar.high >= st["target"]:
                ctx.close(reason="take_profit")
                st["exiting"] = True
            elif st["hold"] >= max_hold:
                ctx.close(reason="timeout")
                st["exiting"] = True
        else:
            if bar.high >= st["stop"]:
                ctx.close(reason="stop_loss")
                st["exiting"] = True
            elif bar.low <= st["target"]:
                ctx.close(reason="take_profit")
                st["exiting"] = True
            elif st["hold"] >= max_hold:
                ctx.close(reason="timeout")
                st["exiting"] = True
        return

    # 入场
    if entry_confirmation:
        pin_sig = pin_bar(bar)
        engulf_sig = engulfing(ctx.history[-2], bar) if n >= 2 else None
        confirmed = pin_sig is not None or engulf_sig is not None
    else:
        confirmed = True

    # 超卖区做多
    if bar.low <= lower and confirmed:
        stop = bar.low - max(stop_atr * cur_atr, min_stop_ticks)
        target = sma  # 回归中轨
        st["stop"] = stop
        st["target"] = target
        st["hold"] = 0
        ctx.buy(qty, reason="bb_oversold")
        return

    # 超买区做空
    if bar.high >= upper and confirmed:
        stop = bar.high + max(stop_atr * cur_atr, min_stop_ticks)
        target = sma
        st["stop"] = stop
        st["target"] = target
        st["hold"] = 0
        ctx.sell(qty, reason="bb_overbought")
        return
