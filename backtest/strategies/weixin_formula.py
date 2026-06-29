"""微信选股公式策略（日线级别）。

源公式：公众号「程序化指标」2026-06-11
原始通达信代码：

ZBRSQS:=(CLOSE-LLV(LOW,55))/(HHV(HIGH,55)-LLV(LOW,55))*100;
KPRSQS:=(OPEN-LLV(LOW,55))/(HHV(HIGH,55)-LLV(LOW,55))*100;
ZBDL:=100-(100-3*SMA(SMA(ZBRSQS,20,1),15,1)+2*SMA(SMA(ZBRSQS,20,1),15,1));
KPDL:=100-(100-3*SMA(SMA(KPRSQS,20,1),15,1)+2*SMA(SMA(KPRSQS,20,1),15,1));
DNFZ:=ZBDL>REF(KPDL,1);
MA20:=MA(CLOSE,20);
MA60:=MA(CLOSE,60);
QSXH:=CLOSE>MA20 AND MA60>=REF(MA60,1);
ZF:=(CLOSE-REF(CLOSE,1))/REF(CLOSE,1)*100;
YXQR:=CLOSE>OPEN AND ZF>=2 AND ZF<=7;
LNQR:=VOL>MA(VOL,5)*1.5 AND REF(VOL,1)<MA(VOL,5);
XDWZ:=(CLOSE-LLV(LOW,55))/(HHV(HIGH,55)-LLV(LOW,55))*100;
WZGL:=XDWZ<80 AND (CLOSE/REF(CLOSE,2))<1.1;
高胜率:DNFZ AND LNQR AND YXQR AND QSXH AND WZGL;

回测转换逻辑（单股票日线）：
- 每个交易日在 on_bar 判断是否满足全部 5 个条件
- 满足则次日开盘买入，持有 hold_days 天后卖出
- 统计胜率、盈亏比等
"""
from __future__ import annotations

from math import isnan

from backtest.indicators import atr, ema_inc
from backtest.registry import register_strategy


def _sma(values: list[float], period: int) -> float:
    """简单移动平均。"""
    if len(values) < period:
        return float("nan")
    return sum(values[-period:]) / period


def _llv(values: list[float], period: int) -> float:
    """N 周期最低值。"""
    return min(values[-period:])


def _hhv(values: list[float], period: int) -> float:
    """N 周期最高值。"""
    return max(values[-period:])


def _ref(values: list[float], offset: int) -> float:
    """前 N 根的值。"""
    if len(values) < offset + 1:
        return float("nan")
    return values[-(offset + 1)]


@register_strategy("weixin_formula")
def on_bar(
    bar, ctx,
    # --- 公式参数 ---
    rsv_period: int = 55,
    sma1_period: int = 20,
    sma2_period: int = 15,
    vol_ma_period: int = 5,
    vol_ratio: float = 1.5,
    ma_period_short: int = 20,
    ma_period_long: int = 60,
    min_pct: float = 2.0,
    max_pct: float = 7.0,
    wzgl_max_pct_2d: float = 1.1,
    wzgl_max_level: float = 80.0,
    # --- 交易参数 ---
    hold_days: int = 5,
    qty: int = 1,
    stop_loss_pct: float = 5.0,
    take_profit_pct: float = 15.0,
):
    """微信选股公式策略。

    参数：
        hold_days: 持仓天数（持有 bar 数）
        stop_loss_pct: 止损百分比
        take_profit_pct: 止盈百分比
    """
    st = ctx.state
    n = len(ctx.history)

    # --- 需要足够的 K 线数据 ---
    needed = max(rsv_period + sma1_period + sma2_period + 5,
                 ma_period_long + 2,
                 vol_ma_period + 5)
    if n < needed:
        return

    closes = [b.close for b in ctx.history]
    highs = [b.high for b in ctx.history]
    lows = [b.low for b in ctx.history]
    opens_ = [b.open for b in ctx.history]
    volumes = [b.volume for b in ctx.history]

    # --- 条件 1: DNFZ - 动量向上转折 ---
    close_rsv = (closes[-1] - _llv(lows, rsv_period)) / \
                (_hhv(highs, rsv_period) - _llv(lows, rsv_period)) * 100 \
                if _hhv(highs, rsv_period) != _llv(lows, rsv_period) else 0.0

    open_rsv = (opens_[-1] - _llv(lows, rsv_period)) / \
               (_hhv(highs, rsv_period) - _llv(lows, rsv_period)) * 100 \
               if _hhv(highs, rsv_period) != _llv(lows, rsv_period) else 0.0

    # 构建 RSV 序列用于 SMA 计算（简化的双 SMA 逻辑）
    # 原始：SMA(SMA(ZBRSQS,20,1),15,1)
    # 我们用最近 50 根做 SMA 近似
    zb_vals = []
    kp_vals = []
    for i in range(-min(len(closes), 50), 0):
        c = closes[i]
        o = opens_[i]
        cv = closes[:i+1]
        hv = highs[:i+1]
        lv = lows[:i+1]
        rng = _hhv(hv, rsv_period) - _llv(lv, rsv_period)
        if rng == 0:
            zb_vals.append(50.0)
            kp_vals.append(50.0)
        else:
            zb_vals.append((c - _llv(lv, rsv_period)) / rng * 100)
            kp_vals.append((o - _llv(lv, rsv_period)) / rng * 100)

    if len(zb_vals) < sma1_period + sma2_period:
        return

    sma1_zb = _sma(zb_vals, sma1_period)
    sma1_kp = _sma(kp_vals, sma1_period)

    # 再套一层 SMA
    # 但我们要 REF 前一根的 KPDL，所以需要计算两个时间点的值
    # 简化实现：用当前和之前的 zb_vals/kp_vals 切片
    def _compute_dl(rsv_list, period1, period2):
        if len(rsv_list) < period1 + period2:
            return float("nan")
        s1 = []
        for i in range(len(rsv_list) - period2, len(rsv_list)):
            s1.append(_sma(rsv_list[:i+1], period1) if len(rsv_list[:i+1]) >= period1 else float("nan"))
        s1 = [x for x in s1 if not isnan(x)]
        if len(s1) < period2:
            return float("nan")
        return _sma(s1, period2)

    # 用简化版本：如果 zb_vals 和 kp_vals 长度够，用当前段计算
    # 原始：ZBDL = 100-(100-3*SMA(SMA(ZBRSQS,20,1),15,1)+2*SMA(SMA(ZBRSQS,20,1),15,1))
    # 简化：ZBDL ≈ SMA(SMA(ZBRSQS,20,1),15,1)
    # 因为 100 - (100 - 3*sma2 + 2*sma2) = 100 - (100 - sma2) = sma2
    # 不对，重看：100-(100-3*SMA(SMA(...,20,1),15,1)+2*SMA(SMA(...,20,1),15,1))
    # = 100 - (100 - 3*X + 2*X) = 100 - (100 - X) = X
    # 所以 ZBDL = SMA(SMA(ZBRSQS,20,1),15,1) = 双 SMA
    if len(zb_vals) < sma1_period + sma2_period:
        return

    # 当前 ZBDL
    sma_zb = []
    sma_kp = []
    for i in range(sma2_period, len(zb_vals)):
        s1_zb = _sma(zb_vals[:i+1], sma1_period) if len(zb_vals[:i+1]) >= sma1_period else float("nan")
        s1_kp = _sma(kp_vals[:i+1], sma1_period) if len(kp_vals[:i+1]) >= sma1_period else float("nan")
        if not isnan(s1_zb) and not isnan(s1_kp):
            sma_zb.append(s1_zb)
            sma_kp.append(s1_kp)

    if len(sma_zb) < sma2_period:
        return

    zbdl_now = _sma(sma_zb, sma2_period)
    kpdl_now = _sma(sma_kp, sma2_period)
    kpdl_prev = _ref(sma_kp, 1) if len(sma_kp) >= 2 else float("nan")

    if isnan(zbdl_now) or isnan(kpdl_prev):
        return

    dnfz = zbdl_now > kpdl_prev

    # --- 条件 2: QSXH - 多头趋势 ---
    ma20 = sum(closes[-ma_period_short:]) / ma_period_short
    ma60_vals = closes[-ma_period_long - 1:]
    ma60 = sum(ma60_vals[-ma_period_long:]) / ma_period_long
    ma60_prev = sum(ma60_vals[:-1][-ma_period_long:]) / ma_period_long if len(ma60_vals) > ma_period_long else ma60

    qsxh = (closes[-1] > ma20) and (ma60 >= ma60_prev)

    # --- 条件 3: YXQR - 阳线 + 涨幅 2%~7% ---
    if n < 2:
        return
    zf = (closes[-1] - closes[-2]) / closes[-2] * 100
    yxqr = (closes[-1] > opens_[-1]) and (min_pct <= zf <= max_pct)

    # --- 条件 4: LNQR - 放量 + 昨日缩量 ---
    vol_ma5 = sum(volumes[-vol_ma_period:]) / vol_ma_period
    vol_ma5_prev = _ref(volumes, 1) if len(volumes) >= vol_ma_period + 1 else float("nan")
    # 原文：VOL>MA(VOL,5)*1.5 AND REF(VOL,1)<MA(VOL,5)
    lnqr = (volumes[-1] > vol_ma5 * vol_ratio) and \
           (_ref(volumes, 1) < vol_ma5_prev if len(volumes) >= 2 else False)

    # --- 条件 5: WZGL - 位置管理 ---
    xdwz = (closes[-1] - _llv(lows, rsv_period)) / \
           (_hhv(highs, rsv_period) - _llv(lows, rsv_period)) * 100 \
           if _hhv(highs, rsv_period) != _llv(lows, rsv_period) else 100.0
    close_2d_ago = _ref(closes, 2) if len(closes) >= 3 else float("nan")
    wzgl = (xdwz < wzgl_max_level) and \
           (closes[-1] / close_2d_ago < wzgl_max_pct_2d if len(closes) >= 3 else False)

    # --- 持仓管理 ---
    if ctx.position_side is not None:
        # 检查止损止盈
        entry_price = ctx.position_avg_price
        pnl_pct = (bar.close - entry_price) / entry_price * 100

        if ctx.position_side == "long":
            if pnl_pct <= -stop_loss_pct:
                ctx.close(reason="stop_loss")
                return
            if pnl_pct >= take_profit_pct:
                ctx.close(reason="take_profit")
                return

        # 检查持有天数
        hold_count = st.get("hold_count", 0) + 1
        st["hold_count"] = hold_count
        if hold_count >= hold_days:
            ctx.close(reason="time_exit")
            return
        return

    # --- 开仓条件：全部 5 个条件满足 ---
    if dnfz and qsxh and yxqr and lnqr and wzgl:
        # 次日开盘买入
        ctx.buy(qty, reason="weixin_formula_signal")
        st["hold_count"] = 0
        return
