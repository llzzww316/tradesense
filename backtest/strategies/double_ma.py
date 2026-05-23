"""双均线策略：fast/slow EMA 金叉开多，死叉平多；不做空。"""
from __future__ import annotations

from backtest.context import StrategyContext
from backtest.indicators import ema_inc
from backtest.models import Bar
from backtest.registry import register_strategy


@register_strategy("double_ma")
def on_bar(bar: Bar, ctx: StrategyContext, *, fast: int = 5, slow: int = 20, **_: object) -> None:
    n = len(ctx.history)
    if n < slow + 1:
        return

    ema_fast_prev = ctx.state.get("ema_fast", float("nan"))
    ema_slow_prev = ctx.state.get("ema_slow", float("nan"))
    ema_fast_now = ema_inc(ctx.state, "ema_fast", bar.close, fast)
    ema_slow_now = ema_inc(ctx.state, "ema_slow", bar.close, slow)

    golden = ema_fast_prev <= ema_slow_prev and ema_fast_now > ema_slow_now
    death = ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now

    if golden and ctx.position_side is None:
        ctx.buy(1, reason=f"golden cross fast={fast} slow={slow}")
    elif death and ctx.position_side == "long":
        ctx.close(reason=f"death cross fast={fast} slow={slow}")
