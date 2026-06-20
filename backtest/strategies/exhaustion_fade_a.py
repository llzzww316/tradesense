"""衰竭反转策略 A 股版（30 分钟 K 线，均值回归型，仅做多）。

设计依据：在期货 exhaustion_fade（run=3, burst=2.0, tp=1.5, sl=1.5）的
基础上适配 A 股规则——不支持做空、T+1 锁定、三重手续费成本。

A 股差异点：
- 不做空：只在下跌衰竭（连续阴线+累计跌幅>=X*ATR）后做多
- T+1：买入当日不可卖出（引擎已内置，策略无需额外处理）
- 无止损价挂单（A 股无盘中条件单），持仓期间用 K 线触及检测模拟止损
- 手续费：佣金 min(0.025%, 5元) + 卖出印花税 0.1% + 过户费 0.001%
- 不设 intraday_only：持仓可隔夜，配合引擎 T+1 即可
- 资金管理：按 initial_capital 的 position_pct 计算可买手数，避免高价股爆仓

逻辑：
1. 入场：连续 run_bars 根阴线 + 累计跌幅 >= burst_atr * ATR
   → 收盘买入，下一根开盘 ± 滑点成交
2. 止盈：收盘价达到 买入价*(1 + target_atr * ATR / 买入价) 即卖
3. 止损：收盘价跌破 买入价*(1 - stop_pct) 即卖
4. 超时：持仓满 max_hold 根 K 还没走完，收盘离场

不要加 from __future__ import annotations——registry 参数反射依赖
真实类型对象（anno is bool），字符串化注解会让 bool 参数被识别成 number。
"""

import math
from backtest.indicators import atr, is_bear_bar
from backtest.registry import register_strategy


@register_strategy("exhaustion_fade_a")
def on_bar(
    bar,
    ctx,
    run_bars: int = 3,
    burst_atr: float = 2.0,
    atr_period: int = 14,
    target_atr: float = 1.5,
    stop_pct: float = 0.02,
    max_hold: int = 10,
    position_pct: float = 0.3,
):
    st = ctx.state
    n = len(ctx.history)

    # 预热
    if n < atr_period + 2:
        return

    a = atr(ctx.history, atr_period)
    if a != a or a <= 0:
        return

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
            st["target"] = entry * (1 + target_atr * a / entry)
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

    # ============ 空仓找入场：只做多 ============
    if not is_bear_bar(bar):
        return

    # 统计当前连续阴线段
    run = 1
    i = n - 2
    while i >= 0:
        if is_bear_bar(ctx.history[i]):
            run += 1
            i -= 1
        else:
            break
    if run < run_bars:
        return

    seg_start = ctx.history[n - run]
    burst = seg_start.open - bar.close  # 累计跌幅
    if burst < burst_atr * a:
        return

    # 按资金计算可买手数：position_pct 可用资金 / (股价 * 100股)
    # 用收盘价估算（实际成交价是下一根开盘，会略不同但安全边际足够）
    initial_capital = ctx.state.get("_initial_capital", 100000)
    target_capital = initial_capital * position_pct
    qty = max(1, math.floor(target_capital / (bar.close * 100)))
    ctx.buy(qty, reason="fade_down_burst")
