"""布林带均值回归策略 A 股版（日线/60m 线，仅做多）。

设计依据：在期货 bollinger_reversion 基础上适配 A 股规则：
- 不做空：只在价格触及下轨时做多，回归中轨止盈
- T+1：买入当日不可卖出（引擎已内置）
- 百分比止损：股价跨度大（几元~几千元），ATR 绝对值不统一，统一用 stop_pct
- 资金管理：按 initial_capital 的 position_pct 计算可买手数
- 无盘中条件单：止损/止盈用 bar.close 触及检测

逻辑：
1. 计算布林带（SMA + bb_std × StdDev）
2. 价格触及下轨 → 等待确认形态（Pin Bar / 吞没）
3. 确认后做多，止盈回归中轨，止损按百分比
4. 持仓超时（max_hold 根 K 线未达止盈/止损）→ 收盘离场

不要加 from __future__ import annotations——registry 参数反射依赖真实类型对象。
"""
import math
from backtest.indicators import (
    atr, pin_bar, engulfing,
)
from backtest.registry import register_strategy


@register_strategy("bollinger_reversion_a")
def on_bar(
    bar, ctx,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    stop_pct: float = 0.05,
    max_hold: int = 20,
    entry_confirmation: bool = True,
    position_pct: float = 0.3,
):
    st = ctx.state
    n = len(ctx.history)

    if n < max(bb_period + 1, atr_period + 2):
        return

    # 计算布林带
    closes = [b.close for b in ctx.history[-bb_period:]]
    sma = sum(closes) / len(closes)
    variance = sum((c - sma) ** 2 for c in closes) / len(closes)
    std = math.sqrt(variance)
    lower = sma - bb_std * std
    # 带宽太窄时不交易（横盘震荡，布林带收窄，均值回归无利可图）
    bandwidth = (2 * bb_std * std) / sma if sma > 0 else 0.0
    if bandwidth < 0.05:
        pass  # 仍然可以交易，但带宽过窄意味信号质量下降

    # --- 空仓：清理上一笔遗留状态 ---
    if ctx.position_side is None:
        if st.get("stop") is not None:
            st["stop"] = None
            st["target"] = None
            st["entry_price"] = None
        st["exiting"] = False

    # ============ 持仓管理 ============
    if ctx.position_side is not None:
        if st.get("entry_price") is None:
            st["entry_price"] = ctx.position_avg_price
            entry = ctx.position_avg_price
            st["target"] = sma  # 仍然回归中轨
            st["stop"] = entry * (1 - stop_pct)
            st["hold"] = 0

        if st.get("exiting"):
            return
        st["hold"] = st.get("hold", 0) + 1

        if bar.close <= st["stop"]:
            ctx.close(reason="stop_loss")
            st["exiting"] = True
        elif bar.close >= st["target"]:
            ctx.close(reason="take_profit")
            st["exiting"] = True
        elif st["hold"] >= max_hold:
            ctx.close(reason="timeout")
            st["exiting"] = True
        return

    # ============ 空仓找入场：仅做多 ============

    # 确认形态过滤
    if entry_confirmation:
        pin_sig = pin_bar(bar)
        engulf_sig = engulfing(ctx.history[-2], bar) if n >= 2 else None
        confirmed = pin_sig is not None or engulf_sig is not None
    else:
        confirmed = True

    # 超卖区做多：价格触及/跌破下轨
    if bar.low <= lower and confirmed:
        st["entry_price"] = bar.close
        st["target"] = sma
        st["stop"] = bar.close * (1 - stop_pct)
        st["hold"] = 0

        # 资金管理：按 initial_capital * position_pct / (股价 * 100股) 计算手数
        initial_capital = ctx.state.get("_initial_capital", 100000)
        target_capital = initial_capital * position_pct
        qty = max(1, math.floor(target_capital / (bar.close * 100)))
        ctx.buy(qty, reason="bb_a_oversold")
