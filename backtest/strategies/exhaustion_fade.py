"""衰竭反转策略（5 分钟日内，均值回归型，多空对称）。

设计依据（RB2610/V2609 真实 5m 数据统计，2025-09 ~ 2026-06）：
- 5m 收益 lag-1 自相关为负（RB -0.10），强趋势 K 后下一根同向概率仅 32~39%，
  20 根新高/新低突破后顺向概率 <50% —— 该尺度下市场均值回归主导；
- 唯一期望为正的信号族：连续 N 根同向 + 累计幅度 >= X*ATR 后做反向。

逻辑（以做空衰竭上涨为例，做多镜像）：
1. 入场：连续 run_bars 根同向 K 且该段累计幅度 >= burst_atr * ATR
   → 收盘反向开仓，下一根开盘 ± 滑点成交（吃"冲过头"的回归）
2. 止盈：入场后回归 target_atr * ATR 即走——回归行情走不远，不贪
3. 止损：衰竭段极值之外 stop_atr * ATR——极值被刷穿说明不是衰竭是延续
4. 超时：持仓满 max_hold 根还没到目标，期望已耗尽，市价离场
5. 时间过滤：临近收盘不开新仓，配合 intraday_only 日末强平

注意：broker 无盘中条件触发，止损/止盈按"K 线触及 → 收盘发单 →
下一根开盘成交"，统计中该信号 MAE 约 1.5 ATR，止损不能设太近。

不要加 from __future__ import annotations——registry 的参数反射依赖
真实类型对象（anno is bool），字符串化注解会让 bool 参数被识别成 number。
"""

from backtest.indicators import atr, is_bear_bar, is_bull_bar
from backtest.registry import register_strategy


def _entry_allowed(time_str: str) -> bool:
    """临近收盘禁止开新仓：日盘 14:30 后、夜盘 22:30 后（RB/V 夜盘 23:00 收）。"""
    hm = time_str[11:16]
    if "14:30" <= hm < "21:00":
        return False
    if hm >= "22:30":
        return False
    return True


@register_strategy("exhaustion_fade")
def on_bar(
    bar,
    ctx,
    run_bars: int = 4,
    burst_atr: float = 2.5,
    atr_period: int = 14,
    target_atr: float = 1.0,
    stop_atr: float = 1.0,
    max_hold: int = 8,
    qty: int = 1,
):
    st = ctx.state
    n = len(ctx.history)

    # 预热：ATR 需要 period+1 根
    if n < atr_period + 2:
        return

    # --- 空仓时清理上一笔遗留状态 ---
    if ctx.position_side is None:
        if st.get("stop") is not None:
            st["stop"] = None
            st["target"] = None
        st["exiting"] = False

    # ============ 持仓管理 ============
    if ctx.position_side is not None:
        if st.get("stop") is None:
            # 成交后第一根：按真实成交价 + 信号时缓存的 ATR/极值 设置止损止盈
            entry = ctx.position_avg_price
            a = st.get("signal_atr", 0.0)
            if ctx.position_side == "long":
                # 做多衰竭下跌：止损放在衰竭段最低点下方
                st["stop"] = min(st.get("signal_extreme", entry), entry) - stop_atr * a
                st["target"] = entry + target_atr * a
            else:
                st["stop"] = max(st.get("signal_extreme", entry), entry) + stop_atr * a
                st["target"] = entry - target_atr * a
            st["hold"] = 0

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
                # 回归没来，期望耗尽，不跟它耗
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

    # ============ 空仓找入场 ============
    if not _entry_allowed(bar.time):
        return

    a = atr(ctx.history, atr_period)
    if a != a or a <= 0:  # NaN 防护
        return

    # 统计当前连续同向段：从最后一根往回数
    if is_bull_bar(bar):
        direction = 1
    elif is_bear_bar(bar):
        direction = -1
    else:
        return

    run = 1
    i = n - 2
    while i >= 0:
        b = ctx.history[i]
        if (direction == 1 and is_bull_bar(b)) or (direction == -1 and is_bear_bar(b)):
            run += 1
            i -= 1
        else:
            break
    if run < run_bars:
        return

    seg_start = ctx.history[n - run]
    burst = direction * (bar.close - seg_start.open)
    if burst < burst_atr * a:
        return

    # 衰竭段极值：做反向时止损参考点
    seg = ctx.history[n - run:]
    if direction == 1:
        st["signal_extreme"] = max(b.high for b in seg)
        st["signal_atr"] = a
        ctx.sell(qty, reason="fade_up_burst")  # 衰竭上涨 → 做空
    else:
        st["signal_extreme"] = min(b.low for b in seg)
        st["signal_atr"] = a
        ctx.buy(qty, reason="fade_down_burst")  # 衰竭下跌 → 做多
