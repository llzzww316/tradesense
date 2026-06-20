"""Pin Bar 结构突破策略 v2（5/15 分钟日内，多空对称）。

v2 优化：
1. 跟踪止损（保守）：浮盈 >= 2.0 * ATR 时，止损移到保本附近
2. 固定 max_hold（无缩放）

不要加 from __future__ import annotations——registry 的参数反射依赖
真实类型对象（anno is bool），字符串化注解会让 bool 参数被识别成 number。
"""

from backtest.indicators import (
    atr, pin_bar, engulfing, detect_swings,
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
        prev_price = filtered[-1][1]
        if abs(price - prev_price) >= min_swing_atr * cur_atr:
            filtered.append((_, price, kind))
    return filtered


@register_strategy("pinbar_structure")
def on_bar(
    bar, ctx,
    atr_period: int = 14,
    swing_window: int = 2,
    pin_body_ratio: float = 0.4,
    pin_wick_ratio: float = 2.5,
    stop_atr: float = 2.0,
    tp_rr: float = 2.0,
    min_swing_atr: float = 1.5,
    min_stop_ticks: float = 5.0,
    min_rr: float = 1.5,
    trailing_activate: float = 2.0,
    trail_offset: float = 1.0,
    max_hold: int = 14,
    qty: int = 1,
):
    st = ctx.state
    n = len(ctx.history)

    warmup = max(atr_period + 2, 30)
    if n < warmup:
        return

    cur_atr = atr(ctx.history, atr_period)
    if cur_atr != cur_atr or cur_atr <= 0:
        return

    if ctx.position_side is None:
        for k in ("stop", "target", "hold", "exiting", "entry_price", "trailed"):
            st.pop(k, None)

    # ========= 持仓管理 =========
    if ctx.position_side is not None:
        if st.get("exiting"):
            return
        st["hold"] = st.get("hold", 0) + 1

        # 跟踪止损：浮盈 >= trailing_activate * ATR 时，止损移到保本附近
        if not st.get("trailed", False):
            if ctx.position_side == "long":
                if bar.high - st["entry_price"] >= trailing_activate * cur_atr:
                    st["stop"] = max(st["stop"], st["entry_price"] + trail_offset * cur_atr)
                    st["trailed"] = True
            else:
                if st["entry_price"] - bar.low >= trailing_activate * cur_atr:
                    st["stop"] = min(st["stop"], st["entry_price"] - trail_offset * cur_atr)
                    st["trailed"] = True

        if ctx.position_side == "long":
            if bar.low <= st["stop"]:
                ctx.close(reason="trailing_stop" if st.get("trailed") else "stop_loss")
                st["exiting"] = True
            elif bar.high >= st["target"]:
                ctx.close(reason="take_profit")
                st["exiting"] = True
            elif st["hold"] >= max_hold:
                ctx.close(reason="timeout")
                st["exiting"] = True
        else:
            if bar.high >= st["stop"]:
                ctx.close(reason="trailing_stop" if st.get("trailed") else "stop_loss")
                st["exiting"] = True
            elif bar.low <= st["target"]:
                ctx.close(reason="take_profit")
                st["exiting"] = True
            elif st["hold"] >= max_hold:
                ctx.close(reason="timeout")
                st["exiting"] = True
        return

    # ========= 空仓找入场 =========
    if not _entry_allowed(bar.time):
        return

    raw_swings = detect_swings(ctx.history, window=swing_window)
    if not raw_swings:
        return
    swings = _filter_swings(raw_swings, cur_atr, min_swing_atr)
    if not swings:
        return
    recent = swings[-min(len(swings), 2):]

    pin_signal = pin_bar(bar, body_ratio_max=pin_body_ratio, wick_to_body=pin_wick_ratio)
    engulf_signal = engulfing(ctx.history[-2], bar) if len(ctx.history) >= 2 else None

    if pin_signal is None and engulf_signal is None:
        return
    signal = pin_signal or engulf_signal

    if signal == "long":
        for _, price, kind in recent:
            if kind == "low" and abs(bar.low - price) <= cur_atr * 0.5:
                stop = bar.low - max(stop_atr * cur_atr, min_stop_ticks)
                target = bar.high + (bar.high - stop) * tp_rr
                if (target - stop) <= min_rr * (stop - bar.low):
                    continue
                st["stop"] = stop
                st["target"] = target
                st["entry_price"] = bar.close
                st["hold"] = 0
                st["trailed"] = False
                ctx.buy(qty, reason="pinbar_long")
                return

    if signal == "short":
        for _, price, kind in recent:
            if kind == "high" and abs(bar.high - price) <= cur_atr * 0.5:
                stop = bar.high + max(stop_atr * cur_atr, min_stop_ticks)
                target = bar.low - (stop - bar.low) * tp_rr
                if (stop - target) <= min_rr * (stop - bar.high):
                    continue
                st["stop"] = stop
                st["target"] = target
                st["entry_price"] = bar.close
                st["hold"] = 0
                st["trailed"] = False
                ctx.sell(qty, reason="pinbar_short")
                return
