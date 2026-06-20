"""量价确认价格行为策略（5/15 分钟，多空对称）。

逻辑：
1. 检测价格形态（Pin Bar / 吞没）
2. 成交量确认：当前 K 成交量 > 前 N 根均量 * vol_ratio
3. 量价共振才入场
4. 止损/止盈同 pinbar_structure

成交量是期货市场的关键确认——没有量的 Pin Bar 是噪音。
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


@register_strategy("volume_pa")
def on_bar(
    bar, ctx,
    atr_period: int = 14,
    vol_lookback: int = 10,
    vol_ratio: float = 1.3,
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
    if n < max(atr_period + 2, vol_lookback + 2, 30):
        return

    cur_atr = atr(ctx.history, atr_period)
    if cur_atr != cur_atr or cur_atr <= 0:
        return

    # 均量
    avg_vol = sum(b.volume for b in ctx.history[-vol_lookback - 1:-1]) / vol_lookback
    if avg_vol <= 0:
        return

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

    # 量价确认：当前量必须放大
    vol_ok = bar.volume >= avg_vol * vol_ratio
    if not vol_ok:
        return

    # 形态检测
    pin = pin_bar(bar)
    engulf = engulfing(ctx.history[-2], bar) if n >= 2 else None
    if pin is None and engulf is None:
        return
    signal = pin or engulf

    # 找结构点
    swings = detect_swings(ctx.history, window=swing_window)
    if not swings:
        return
    # 过滤
    filtered = []
    for i, (idx, price, kind) in enumerate(swings):
        if i == 0:
            filtered.append((idx, price, kind))
            continue
        if abs(price - filtered[-1][1]) >= min_swing_atr * cur_atr:
            filtered.append((idx, price, kind))
    if not filtered:
        return
    recent = filtered[-min(len(filtered), 2):]

    if signal == "long":
        for _, price, kind in recent:
            if kind == "low" and abs(bar.low - price) <= cur_atr * 0.5:
                stop = bar.low - max(stop_atr * cur_atr, min_stop_ticks)
                target = bar.high + (bar.high - stop) * tp_rr
                st["stop"] = stop
                st["target"] = target
                st["hold"] = 0
                ctx.buy(qty, reason="vol_pa_long")
                return

    if signal == "short":
        for _, price, kind in recent:
            if kind == "high" and abs(bar.high - price) <= cur_atr * 0.5:
                stop = bar.high + max(stop_atr * cur_atr, min_stop_ticks)
                target = bar.low - (stop - bar.low) * tp_rr
                st["stop"] = stop
                st["target"] = target
                st["hold"] = 0
                ctx.sell(qty, reason="vol_pa_short")
                return
