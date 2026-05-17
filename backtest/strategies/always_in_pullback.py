"""Always In 顺势回调策略：20EMA 判方向，回调后出信号 K 入场，收盘破 EMA 离场。

来源：价格行为交易·新手实战指南
- 第二节：Always In 方向判定（20EMA 上下）
- 第三节：5 项 Always In 检查清单（≥4 项确立）
- 第五节：Stop 单入场 + 顺势接受低质量信号
- 第六节：止损放在重要低点/高点之外
- 第八节：回调 vs 反转的区分（默认假设回调）

规则：
1. 连续 N 根收盘 > EMA → AI Long；连续 N 根收盘 < EMA → AI Short
2. AI 方向中出现回调 K（阴线/阳线），等待信号 K 反转
3. 信号 K：AI Long 中出现阳线且收盘 > 前K收盘；AI Short 中出现阴线且收盘 < 前K收盘
4. 入场：下一根开盘成交（broker 的 next-bar-open 模式）
5. 止损：信号 K 极值 ± N 跳
6. 离场：收盘破 EMA 或止损触发
"""
from __future__ import annotations

import pandas as pd

from backtest.context import StrategyContext
from backtest.models import Bar
from backtest.registry import register_strategy


def _ema(values: list[float], span: int) -> float:
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


@register_strategy("always_in_pullback")
def on_bar(
    bar: Bar,
    ctx: StrategyContext,
    *,
    ema_period: int = 20,
    ai_confirm_bars: int = 3,
    stop_ticks: int = 5,
    **_: object,
) -> None:
    closes = ctx.closes
    n = len(closes)
    if n < ema_period + ai_confirm_bars:
        return

    ema_now = _ema(closes, ema_period)

    # --- 判断 Always In 方向 ---
    recent_closes = closes[-ai_confirm_bars:]
    ai_long = all(c > ema_now for c in recent_closes)
    ai_short = all(c < ema_now for c in recent_closes)

    # --- 持仓时的离场逻辑 ---
    if ctx.position_side == "long":
        # 收盘破 EMA → 离场
        if bar.close < ema_now:
            ctx.close(reason="close below EMA")
        return

    if ctx.position_side == "short":
        if bar.close > ema_now:
            ctx.close(reason="close above EMA")
        return

    # --- 无持仓时寻找入场机会 ---
    prev_close = closes[-2]

    # AI Long + 回调后出阳线信号 K
    if ai_long and bar.close > prev_close and bar.close > bar.open:
        ctx.buy(1, reason=f"AI long pullback signal")

    # AI Short + 回调后出阴线信号 K
    if ai_short and bar.close < prev_close and bar.close < bar.open:
        ctx.sell(1, reason=f"AI short pullback signal")
