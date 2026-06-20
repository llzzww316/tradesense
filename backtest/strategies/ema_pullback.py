"""EMA 回调突破策略（5 分钟日内，趋势跟随型，多空对称）。

核心结构（以多头为例，空头镜像）：
1. 趋势过滤：收盘在 EMA 之上 且 EMA 向上 → 只做多
2. 回调标记：趋势中价格回踩触及 EMA（bar.low <= EMA）
3. 入场信号：回调后出现趋势阳线（实体占比 >= 50%、收在区间上沿），
   且收盘升破前一根 K 高点 → 收盘发信号，下一根开盘 ± 滑点成交
4. 止损：入场价 ∓ max(stop_atr_mult * ATR, min_stop_points)
5. 止盈：止损距离的 take_profit_rr 倍（默认 2R）
6. 保护性离场：收盘穿越 EMA（趋势失效）提前平仓
7. 时间过滤：日盘 14:30 后、夜盘 22:30 后不开新仓，
   配合 intraday_only=True 的日末强平，避免临收盘开仓被动挨打
8. 趋势时段过滤（trend_filter）：按"日期+日盘/夜盘"切分时段，追踪时段
   开盘以来的价格包络与净位移；只有当 包络 >= trend_min_atr * ATR 且
   净位移/包络 >= trend_strength 时才认定本时段有趋势，且只做位移方向。
   未达标的时段（震荡日）整段不入场——5 分钟级别 EMA 过滤太弱，
   真实数据回测显示绝大多数亏损来自震荡时段的反复假信号

注意：broker 不支持盘中条件触发，止损/止盈是"K 线触及 → 收盘发单 →
下一根开盘成交"，实际成交价会比触发价多滑一根 K，因此止损距离
不宜设得过近（min_stop_points 兜底）。

不要加 from __future__ import annotations——registry 的参数反射依赖
真实类型对象（anno is bool），字符串化注解会让 bool 参数被识别成 number。
"""

from backtest.indicators import atr, ema_inc, trend_bar_side
from backtest.registry import register_strategy


def _entry_allowed(time_str: str) -> bool:
    """临近收盘禁止开新仓：日盘 14:30 后、夜盘 22:30 后（RB/V 夜盘 23:00 收）。"""
    hm = time_str[11:16]
    if "14:30" <= hm < "21:00":
        return False
    if hm >= "22:30":
        return False
    return True


def _session_key(time_str: str) -> str:
    """时段标识：'日期@day'（9:00-15:00）或 '日期@night'（21:00-23:00）。"""
    hm = time_str[11:16]
    return time_str[:10] + ("@night" if hm >= "21:00" else "@day")


def _update_session(st: dict, bar, time_str: str) -> None:
    """维护当前时段的开盘价/最高/最低，跨时段自动重置（含趋势判定状态）。"""
    key = _session_key(time_str)
    if st.get("sess_key") != key:
        st["sess_key"] = key
        st["sess_open"] = bar.open
        st["sess_high"] = bar.high
        st["sess_low"] = bar.low
        st["sess_dir"] = None  # 本时段趋势方向：None / "long" / "short"
    else:
        st["sess_high"] = max(st["sess_high"], bar.high)
        st["sess_low"] = min(st["sess_low"], bar.low)


def _session_trend_dir(st: dict, bar, cur_atr: float,
                       trend_min_atr: float, trend_strength: float):
    """判定当前时段是否为趋势时段，返回允许交易的方向（None=不许交易）。

    包络 = 时段最高 - 时段最低；净位移 = 现价 - 时段开盘。
    包络够大（>= trend_min_atr * ATR）且位移占包络比例够高（>= trend_strength）
    → 趋势时段。一旦认定方向就锁定，时段内不反向（避免追反转被双向打脸）。
    """
    if st.get("sess_dir") is not None:
        return st["sess_dir"]
    if cur_atr != cur_atr or cur_atr <= 0:  # NaN 防护
        return None
    envelope = st["sess_high"] - st["sess_low"]
    if envelope < trend_min_atr * cur_atr:
        return None
    displacement = bar.close - st["sess_open"]
    if abs(displacement) / envelope < trend_strength:
        return None
    st["sess_dir"] = "long" if displacement > 0 else "short"
    return st["sess_dir"]


@register_strategy("ema_pullback")
def on_bar(
    bar,
    ctx,
    ema_period: int = 20,
    atr_period: int = 14,
    stop_atr_mult: float = 1.5,
    take_profit_rr: float = 2.0,
    min_stop_points: float = 4.0,
    ema_exit: bool = True,
    trend_filter: bool = True,
    trend_min_atr: float = 4.0,
    trend_strength: float = 0.6,
    qty: int = 1,
):
    st = ctx.state

    # --- 增量 EMA，并保留上一根值用于判断 EMA 方向 ---
    prev_ema = st.get("ema")
    cur_ema = ema_inc(st, "ema", bar.close, ema_period)

    # 时段包络追踪要从时段第一根开始，必须放在预热返回之前
    _update_session(st, bar, bar.time)

    # 预热期：EMA / ATR 都没意义，直接返回
    warmup = max(ema_period, atr_period + 1)
    if len(ctx.history) < warmup or prev_ema is None:
        return

    up_trend = bar.close > cur_ema and cur_ema > prev_ema
    down_trend = bar.close < cur_ema and cur_ema < prev_ema

    # --- 空仓且无遗留状态时，清理上一笔交易的止损/止盈 ---
    if ctx.position_side is None:
        if st.get("stop") is not None:
            st["stop"] = None
            st["target"] = None
        st["exiting"] = False

    # ============ 持仓管理 ============
    if ctx.position_side is not None:
        # 刚成交的第一根：按真实成交价初始化止损/止盈
        if st.get("stop") is None:
            entry = ctx.position_avg_price
            risk = max(st.get("signal_atr", 0.0) * stop_atr_mult, min_stop_points)
            if ctx.position_side == "long":
                st["stop"] = entry - risk
                st["target"] = entry + risk * take_profit_rr
            else:
                st["stop"] = entry + risk
                st["target"] = entry - risk * take_profit_rr

        # 已发过平仓单（等下一根开盘成交），不重复发
        if st.get("exiting"):
            return

        if ctx.position_side == "long":
            if bar.low <= st["stop"]:
                ctx.close(reason="stop_loss")
                st["exiting"] = True
            elif bar.high >= st["target"]:
                ctx.close(reason="take_profit")
                st["exiting"] = True
            elif ema_exit and bar.close < cur_ema:
                # 收盘跌破 EMA：趋势失效，不等止损直接走
                ctx.close(reason="ema_exit")
                st["exiting"] = True
        else:  # short
            if bar.high >= st["stop"]:
                ctx.close(reason="stop_loss")
                st["exiting"] = True
            elif bar.low <= st["target"]:
                ctx.close(reason="take_profit")
                st["exiting"] = True
            elif ema_exit and bar.close > cur_ema:
                ctx.close(reason="ema_exit")
                st["exiting"] = True
        return

    # ============ 空仓找入场 ============
    if not _entry_allowed(bar.time):
        # 临近收盘清掉回调标记，避免隔节/隔日的"陈旧回调"触发入场
        st["pb_long"] = False
        st["pb_short"] = False
        return

    # 趋势时段过滤：未认定趋势的时段不交易；认定后只做趋势方向
    if trend_filter:
        cur_atr = atr(ctx.history, atr_period)
        sess_dir = _session_trend_dir(st, bar, cur_atr, trend_min_atr, trend_strength)
        if sess_dir is None:
            st["pb_long"] = False
            st["pb_short"] = False
            return
        if sess_dir == "short":
            up_trend = False
        if sess_dir == "long":
            down_trend = False

    if up_trend:
        st["pb_short"] = False  # 趋势翻多，作废空头回调标记
        if bar.low <= cur_ema:
            st["pb_long"] = True  # 回踩 EMA，标记多头回调成立
        if (
            st.get("pb_long")
            and trend_bar_side(bar) == "long"
            and bar.close > ctx.history[-2].high  # 收盘升破前一根高点：回调结束确认
        ):
            a = atr(ctx.history, atr_period)
            if a == a and a > 0:  # 排除 NaN
                st["signal_atr"] = a
                st["pb_long"] = False
                ctx.buy(qty, reason="pullback_long")
    elif down_trend:
        st["pb_long"] = False
        if bar.high >= cur_ema:
            st["pb_short"] = True
        if (
            st.get("pb_short")
            and trend_bar_side(bar) == "short"
            and bar.close < ctx.history[-2].low
        ):
            a = atr(ctx.history, atr_period)
            if a == a and a > 0:
                st["signal_atr"] = a
                st["pb_short"] = False
                ctx.sell(qty, reason="pullback_short")
    else:
        # 震荡 / 趋势不明：清空所有回调标记，等待新趋势
        st["pb_long"] = False
        st["pb_short"] = False
