"""通道突破策略（5/15 分钟，趋势跟随型）。

逻辑：
1. 计算 N 周期 Donchian 通道（最高/最低）
2. 收盘突破通道上轨 → 做多，突破下轨 → 做空
3. 止损放在通道另一端，止盈 2R

注意：这是趋势跟随策略，和均值回归反向。
"""
from backtest.indicators import atr, is_bull_bar, is_bear_bar
from backtest.registry import register_strategy


@register_strategy("donchian_breakout")
def on_bar(
    bar, ctx,
    channel_period: int = 20,
    atr_period: int = 14,
    stop_atr: float = 2.0,
    tp_rr: float = 2.0,
    min_stop_ticks: float = 5.0,
    max_hold: int = 16,
    qty: int = 1,
):
    st = ctx.state
    n = len(ctx.history)
    if n < max(channel_period + 1, atr_period + 2):
        return

    cur_atr = atr(ctx.history, atr_period)
    if cur_atr != cur_atr or cur_atr <= 0:
        return

    # Donchian 通道
    channel_highs = [b.high for b in ctx.history[-channel_period:]]
    channel_lows = [b.low for b in ctx.history[-channel_period:]]
    dc_high = max(channel_highs)
    dc_low = min(channel_lows)

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

    # 多头突破
    if bar.close > dc_high and is_bull_bar(bar):
        stop = dc_low - max(stop_atr * cur_atr, min_stop_ticks)
        target = bar.close + (bar.close - stop) * tp_rr
        st["stop"] = stop
        st["target"] = target
        st["hold"] = 0
        ctx.buy(qty, reason="dc_breakout_long")
        return

    # 空头突破
    if bar.close < dc_low and is_bear_bar(bar):
        stop = dc_high + max(stop_atr * cur_atr, min_stop_ticks)
        target = bar.close - (stop - bar.close) * tp_rr
        st["stop"] = stop
        st["target"] = target
        st["hold"] = 0
        ctx.sell(qty, reason="dc_breakout_short")
        return
