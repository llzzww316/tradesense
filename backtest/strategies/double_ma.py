"""双均线策略：fast/slow EMA 金叉开多，死叉平多；不做空。"""
from __future__ import annotations

import pandas as pd

from backtest.context import StrategyContext
from backtest.models import Bar
from backtest.registry import register_strategy


def _ema(values: list[float], span: int) -> float:
    if len(values) < span:
        return float("nan")
    return float(pd.Series(values).ewm(span=span, adjust=False).mean().iloc[-1])


@register_strategy("double_ma")
def on_bar(bar: Bar, ctx: StrategyContext, *, fast: int = 5, slow: int = 20, **_: object) -> None:
    closes = ctx.closes
    if len(closes) < slow + 1:
        return

    ema_fast_now = _ema(closes, fast)
    ema_slow_now = _ema(closes, slow)
    ema_fast_prev = _ema(closes[:-1], fast)
    ema_slow_prev = _ema(closes[:-1], slow)

    golden = ema_fast_prev <= ema_slow_prev and ema_fast_now > ema_slow_now
    death = ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now

    if golden and ctx.position_side is None:
        ctx.buy(1, reason=f"golden cross fast={fast} slow={slow}")
    elif death and ctx.position_side == "long":
        ctx.close(reason=f"death cross fast={fast} slow={slow}")
