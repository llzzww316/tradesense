"""双周期方向过滤 + 形态入场策略（5/15 分钟）。

逻辑：
1. 长周期趋势判定：计算 N 期 EMA，比较当前值 vs 前 M 根值
   来判断长周期方向（模拟 15m/60m 方向）
2. 短周期形态：Pin Bar / 吞没在结构点出现 → 入场
3. 方向一致才入场（顺势而为）

不要加 from __future__ import annotations
"""
from backtest.indicators import (
    atr, ema_inc, pin_bar, engulfing, detect_swings,
)
from backtest.registry import register_strategy


def _entry_allowed(time_str: str) -> bool:
    hm = time_str[11:16]
    if "14:30" <= hm < "21:00":
        return False
    if hm >= "22:30":
        return False
    return True


def _filter_swings(raw_swings, cur_atr, min_swing_atr):
    if not raw_swings:
        return []
    filtered = [raw_swings[0]]
    for _, price, kind in raw_swings[1:]:
        if abs(price - filtered[-1][1]) >= min_swing_atr * cur_atr:
            filtered.append((_, price, kind))
    return filtered


@register_strategy("dual_tf_pa")
def on_bar(
    bar, ctx,
    atr_period: int = 14,
    trend_ema: int = 60,
    trend_lookback: int = 5,
    swing_window: int = 3,
    stop_atr: float = 1.8,
    tp_rr: float = 1.8,
    min_swing_atr: float = 1.0,
    min_stop_ticks: float = 5.0,
    max_hold: int = 12,
    qty: int = 1,
):
    st = ctx.state
    n = len(ctx.history)
    warmup = max(atr_period + 2, trend_ema + trend_lookback + 2, 30)
    if n < warmup:
        return

    cur_atr = atr(ctx.history, atr_period)
    if cur_atr != cur_atr or cur_atr <= 0:
        return

    # 趋势方向：长周期 EMA 斜率
    cur_ema = ema_inc(st, "trend_ema", bar.close, trend_ema)
    if st.get("prev_ema") is not None:
        trend_up = cur_ema > st["prev_ema"]
    else:
        trend_up = None
    st["prev_ema"] = cur_ema

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
    if not _entry_allowed(bar.time):
        return
    if trend_up is None:
        return

    raw_swings = detect_swings(ctx.history, window=swing_window)
    if not raw_swings:
        return
    swings = _filter_swings(raw_swings, cur_atr, min_swing_atr)
    if not swings:
        return
    recent = swings[-min(len(swings), 2):]

    pin = pin_bar(bar)
    engulf = engulfing(ctx.history[-2], bar) if n >= 2 else None
    if pin is None and engulf is None:
        return
    signal = pin or engulf

    # 趋势过滤：EMA 向上只做多，向下只做空
    if signal == "long" and not trend_up:
        return
    if signal == "short" and trend_up:
        return

    if signal == "long":
        for _, price, kind in recent:
            if kind == "low" and abs(bar.low - price) <= cur_atr * 0.5:
                stop = bar.low - max(stop_atr * cur_atr, min_stop_ticks)
                target = bar.high + (bar.high - stop) * tp_rr
                st["stop"] = stop
                st["target"] = target
                st["hold"] = 0
                ctx.buy(qty, reason="dual_tf_long")
                return

    if signal == "short":
        for _, price, kind in recent:
            if kind == "high" and abs(bar.high - price) <= cur_atr * 0.5:
                stop = bar.high + max(stop_atr * cur_atr, min_stop_ticks)
                target = bar.low - (stop - bar.low) * tp_rr
                st["stop"] = stop
                st["target"] = target
                st["hold"] = 0
                ctx.sell(qty, reason="dual_tf_short")
                return
