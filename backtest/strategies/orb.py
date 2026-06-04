"""开盘区间突破策略 (Opening Range Breakout / ORB)

基于菲阿里"等市场告诉你方向"的哲学——不等回调，不等均线，只用
开盘30分钟的高低点区间作为当日锚点。突破区间+成交量确认后顺势入场。

核心规则：
  1. 夜盘 OR: 21:00-21:30  |  日盘 OR: 09:00-09:30
  2. 突破区间上沿 + 放量(>2.0x均量) → 做多
  3. 跌破区间下沿 + 放量 → 做空
  4. 止损: 价格回到区间中线以下(多)/以上(空) = 突破失败
  5. 止盈: 无固定目标，浮盈1R后激活移动止损（区间宽度/2 跟踪）
  6. 每时段最多一单（夜盘一单、日盘一单）
  7. 午后/夜尾不开新仓（14:00+/22:00+）
  8. 开盘区间过窄（<0.3x ATR）跳过该时段
  9. 时段末前5分钟强平（22:55 / 14:55）

────────────────────────────────────────────────────────────────
回测记录 v2 (螺纹钢2610, 5m, 2026-03-01 ~ 2026-06-03)
────────────────────────────────────────────────────────────────
v2 优化: vol_ratio 1.5→2.0, 止损从OR边界→OR中线, 夜盘ATR 0.3→0.2
配置: initial_capital=100000, tick_size=1, tick_value=10,
      margin_rate=0.10, fee_per_lot=3, slippage=1, intraday_only=True

结果:
  最终权益:   99,492.00     (v1: 99,062.00)
  总收益率:       -0.51%    (v1: -0.94%)
  最大回撤:     -691.00     (v1: -1,199.00 / -1.20%)
  总交易数:      33         (v1: 43, vol_ratio 2.0 过滤了10笔)
  胜率:          39.39%     (v1: 20.93%, 几乎翻倍)
  盈亏比:         0.58      (v1: 0.43)
  平均盈利:      54.00      (v1: 77.33)
  平均亏损:      60.50      (v1: 48.06)
  Sharpe:        -4.43      (v1: -9.58)

v2 分析:
  胜率从21%→39%说明止损放宽是正确的方向——突破后回踩是正常行为，
  给回踩留空间让真正有效的突破有足够时间发育。
  交易数 33 笔(日均0.37笔)频率合理，不像 Mode C 过度交易。

  剩余问题:
  1. 盈亏比虽从0.43升到0.58但仍<1.0——平均亏损60.5略高于平均盈利54。
     亏损端有一笔 -196 异常值(5/14 做空被反拉)，拉高了平均亏损。
  2. eod_force 平仓占比较高——方向对了但幅度不够。
  3. 缺少方向偏好——多头和空头信号平等对待。

────────────────────────────────────────────────────────────────
回测记录 v2 (PVC2609, 5m, 2026-03-01 ~ 2026-06-03)
────────────────────────────────────────────────────────────────
配置: initial_capital=50000, tick_size=1, tick_value=5,
      margin_rate=0.09, fee_per_lot=2, slippage=1, intraday_only=True

结果:
  最终权益:   50,630.00     (+1.26%)
  最大回撤:     -993.00     (-1.95%)
  总交易数:      25         (胜11 / 负14)
  胜率:          44.00%
  盈亏比:         1.33      ← 盈利!
  平均盈利:     232.36
  平均亏损:     137.57
  Sharpe:         2.87      ← 正数!

亮点: +1091(3/11做多), +346(5/7做多), +251(5/14做多), +246(3/6做空)

品种差异分析:
  PVC 在 ORB 上显著优于螺纹钢——盈亏比 1.33 vs 0.58，Sharpe +2.87 vs -4.43。
  原因: PVC 日内趋势延续性更强，开盘区间突破后假突破更少。
  ORB 是有品种偏好的——适合趋势性强、假突破少的品种，不适合震荡品种。
交易结构分析:
  时段分布: 日盘39笔 / 夜盘4笔（夜盘OR区间通常较窄被ATR过滤跳过）
  平仓原因: OR_stop 28笔(65%), eod_force 9笔, trail_stop 4笔, eod 0笔

已确认的问题:
  1. OR_stop 触发过密: 65%的交易被"价格回到区间"止损，且多在
     入场后1-2根K线内触发。突破后回踩突破位是正常行为，
     当前在 orb_high(多)/orb_low(空) 处止损太紧。
     建议: 止损设在区间中线或区间对边，给回踩留空间。

  2. 夜盘信号过少: 4笔仅占9%。夜盘OR区间窄(21:00-21:30波动小)，
     被 min_range_atr_ratio=0.3 过滤了大量机会。
     建议: 夜盘降低最低区间比例至0.2。

  3. 成交量1.5x可能仍不够: 在活跃时段，突破bar经常天然放量，
     未有效过滤假突破。
     建议: 提至2.0x或改用"连续2根放量bar"确认。

  4. 做多优于做空: 9笔盈利中有6笔是做多。螺纹钢这段时期偏多头，
     但策略没有做方向偏好。
     建议: 加入日线趋势过滤，趋势向上时只做多。

  5. 胜率20.93%是突破策略的正常范围: 突破策略天然胜率低(25-35%)，
     依赖高盈亏比盈利。当前盈亏比0.43远不达标，核心原因是平均亏损
     48太接近平均盈利77——止损太紧导致盈利端无法充分发育。

对比 Mode A / Mode C:
  | 指标     | ORB     | Mode A  | Mode C  |
  |----------|---------|---------|---------|
  | 交易数   | 43      | 13      | 313     |
  | 胜率     | 20.93%  | 15.38%  | 14.38%  |
  | 收益率   | -0.94%  | -0.28%  | -9.16%  |
  | 盈亏比   | 0.43    | 0.33    | 0.12    |
  ORB 交易频率合理(日均0.5笔)，亏损缓慢，逻辑清晰，是最有优化潜力的版本。
"""
from __future__ import annotations

from typing import Optional

from backtest.models import Bar, Side
from backtest.registry import register_strategy


# ── 工具函数 ──────────────────────────────────────────────────

def _bar_time_minutes(t: str) -> int:
    try:
        parts = t.split(" ")[1].split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def _bar_date(t: str) -> str:
    return t.split(" ")[0]


def _avg_volume(history: list[Bar], n: int = 10) -> float:
    """最近 n 根 bar 的均量。"""
    if len(history) < n:
        return 0.0
    recent = history[-n:]
    return sum(b.volume for b in recent) / n


def _atr(history: list[Bar], period: int = 14) -> float:
    """简单 ATR（用 |close - close| 替代 true range，减少复杂度）。"""
    if len(history) < period + 1:
        return 0.0
    closes = [b.close for b in history[-period - 1:]]
    tr_sum = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return tr_sum / period


def _adx(history: list[Bar], period: int = 14) -> float:
    """Wilder's ADX —— 趋势强度指标（>25=趋势, <20=震荡）。"""
    n = min(len(history), period * 3)
    if n < period + 1:
        return 0.0

    bars = history[-n:]
    tr_list, plus_dm, minus_dm = [], [], []

    for i in range(1, len(bars)):
        high, low = bars[i].high, bars[i].low
        prev_high, prev_low = bars[i - 1].high, bars[i - 1].low
        prev_close = bars[i - 1].close

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    if len(tr_list) < period:
        return 0.0

    # Wilder's 初始平滑
    atr_s = sum(tr_list[:period]) / period
    p_dm = sum(plus_dm[:period]) / period
    m_dm = sum(minus_dm[:period]) / period

    dx_list = []
    for i in range(period, len(tr_list)):
        atr_s = atr_s + (tr_list[i] - atr_s) / period
        p_dm = p_dm + (plus_dm[i] - p_dm) / period
        m_dm = m_dm + (minus_dm[i] - m_dm) / period
        if atr_s > 0:
            p_di = 100 * p_dm / atr_s
            m_di = 100 * m_dm / atr_s
            denom = p_di + m_di
            dx = 100 * abs(p_di - m_di) / denom if denom > 0 else 0.0
        else:
            dx = 0.0
        dx_list.append(dx)

    return sum(dx_list) / len(dx_list) if dx_list else 0.0


def _is_night_session(t: str) -> bool:
    m = _bar_time_minutes(t)
    return m >= 21 * 60


def _in_opening_range(t: str, session: str) -> bool:
    """是否在开盘区间内。"""
    m = _bar_time_minutes(t)
    if session == "night":
        return 21 * 60 <= m < 21 * 60 + 30
    else:
        return 9 * 60 <= m < 9 * 60 + 30


def _session_end_forced_close(t: str, session: str) -> bool:
    """是否需要强制平仓：时段结束前 5 分钟。"""
    m = _bar_time_minutes(t)
    if session == "night":
        return m >= 22 * 60 + 55  # 22:55+
    else:
        return m >= 14 * 60 + 55  # 14:55+


def _should_skip_entry(t: str) -> bool:
    """禁止开仓的时段。"""
    m = _bar_time_minutes(t)
    # 14:00-15:00 午后尾盘不新开
    if 14 * 60 <= m < 15 * 60:
        return True
    # 22:00 后夜盘不新开
    if m >= 22 * 60:
        return True
    return False


# ── 策略入口 ──────────────────────────────────────────────────

@register_strategy("orb")
def on_bar(
    bar: Bar,
    ctx,
    *,
    # OR 参数
    orb_minutes: int = 30,
    # 成交量过滤
    vol_ratio: float = 2.0,
    vol_lookback: int = 10,
    # 区间最小宽度（ATR 比例）
    min_range_atr_ratio: float = 0.3,
    # ATR 参数
    atr_period: int = 14,
    # 仓位
    fixed_qty: int = 1,
    # tick 规格
    tick_size: float = 1.0,
    # v3: 市场状态过滤
    use_regime_filter: bool = True,
    adx_period: int = 14,
    adx_threshold: float = 25.0,
) -> None:
    s = ctx.state

    # ── 初始化 ──
    if not s.get("orb_init"):
        s["orb_init"] = True
        s["orb_session"] = None
        s["orb_phase"] = "idle"
        s["orb_high"] = float("-inf")
        s["orb_low"] = float("inf")
        s["orb_mid"] = 0.0
        s["orb_range"] = 0.0
        s["orb_entry_price"] = 0.0
        s["orb_best_price"] = 0.0

    # ── 持仓管理 ──
    if ctx.position_side is not None:
        _manage_position(bar, ctx, s, tick_size)
        return

    if ctx.pending_orders:
        return

    # 检测是否新时段开始：进入 OR 收集区间
    if _in_opening_range(bar.time, "night"):
        if s["orb_session"] != "night" or s["orb_phase"] == "idle":
            s["orb_session"] = "night"
            s["orb_phase"] = "collecting"
            s["orb_high"] = float("-inf")
            s["orb_low"] = float("inf")
            s["orb_mid"] = 0.0
            s["orb_range"] = 0.0
            s["orb_entry_price"] = 0.0
            s["orb_best_price"] = 0.0
            s["orb_trailing_activated"] = False

    elif _in_opening_range(bar.time, "day"):
        if s["orb_session"] != "day" or s["orb_phase"] == "idle":
            s["orb_session"] = "day"
            s["orb_phase"] = "collecting"
            s["orb_high"] = float("-inf")
            s["orb_low"] = float("inf")
            s["orb_mid"] = 0.0
            s["orb_range"] = 0.0
            s["orb_entry_price"] = 0.0
            s["orb_best_price"] = 0.0
            s["orb_trailing_activated"] = False

    # ── 收集 OR ──
    if s["orb_phase"] == "collecting":
        s["orb_high"] = max(s["orb_high"], bar.high)
        s["orb_low"] = min(s["orb_low"], bar.low)

        # 是否收集完毕？
        if not _in_opening_range(bar.time, s["orb_session"] or ""):
            s["orb_range"] = s["orb_high"] - s["orb_low"]
            s["orb_mid"] = (s["orb_high"] + s["orb_low"]) / 2.0
            # 检查最小区间宽度（夜盘更宽松，因夜盘波动天然较小）
            atr_val = _atr(ctx.history, atr_period)
            if atr_val > 0:
                ratio = min_range_atr_ratio * 0.67 if s["orb_session"] == "night" else min_range_atr_ratio
                if s["orb_range"] < ratio * atr_val:
                    s["orb_phase"] = "traded"
                    return

            # v3: 计算趋势强度
            if use_regime_filter:
                s["orb_adx"] = _adx(ctx.history, adx_period)

            s["orb_phase"] = "armed"

    # ── 入场检测 ──
    if s["orb_phase"] != "armed":
        return

    if _should_skip_entry(bar.time):
        return

    # v3: 趋势强度过滤（ADX < 阈值 = 震荡日，跳过）
    if use_regime_filter and s.get("orb_adx", 0) < adx_threshold:
        s["orb_phase"] = "traded"
        return

    orb_high = s["orb_high"]
    orb_low = s["orb_low"]
    avg_vol = _avg_volume(ctx.history, vol_lookback)

    # 成交量确认：当前 bar 成交量 > vol_ratio * 均量
    vol_ok = avg_vol > 0 and bar.volume > vol_ratio * avg_vol

    # 做多：突破区间上沿
    if bar.close > orb_high and vol_ok:
        s["orb_entry_price"] = bar.close
        s["orb_best_price"] = bar.close
        s["orb_trailing_activated"] = False
        s["orb_phase"] = "traded"
        ctx.buy(fixed_qty, reason=f"ORB_L@{bar.close:.1f}")

    # 做空：跌破区间下沿
    elif bar.close < orb_low and vol_ok:
        s["orb_entry_price"] = bar.close
        s["orb_best_price"] = bar.close
        s["orb_trailing_activated"] = False
        s["orb_phase"] = "traded"
        ctx.sell(fixed_qty, reason=f"ORB_S@{bar.close:.1f}")


# ── 持仓管理 ──────────────────────────────────────────────────

def _manage_position(bar: Bar, ctx, s: dict, tick_size: float):
    side: Optional[Side] = ctx.position_side
    entry = s.get("orb_entry_price", 0.0)
    orb_range = s.get("orb_range", 0.0)
    orb_mid = s.get("orb_mid", 0.0)
    session = s.get("orb_session")

    if not side or not entry or orb_range <= 0:
        return

    # 更新最佳价
    if side == "long" and bar.close > s["orb_best_price"]:
        s["orb_best_price"] = bar.close
    elif side == "short" and bar.close < s["orb_best_price"]:
        s["orb_best_price"] = bar.close

    # ── 止损：价格回到区间内（突破失败） ──
    # 用区间中线而非边界，给回踩留出空间
    if side == "long" and bar.low <= orb_mid:
        ctx.close(reason="OR_stop")
        return
    if side == "short" and bar.high >= orb_mid:
        ctx.close(reason="OR_stop")
        return

    # ── 移动止损 ──
    half_range = orb_range * 0.5
    best = s["orb_best_price"]

    if side == "long":
        profit = bar.close - entry
        if profit >= orb_range and not s["orb_trailing_activated"]:
            # 浮盈 1R，激活移动止损（止损提到保本）
            s["orb_trailing_activated"] = True
        if s["orb_trailing_activated"]:
            stop = best - half_range
            if bar.low <= stop:
                ctx.close(reason="trail_stop")
                return
    else:
        profit = entry - bar.close
        if profit >= orb_range and not s["orb_trailing_activated"]:
            s["orb_trailing_activated"] = True
        if s["orb_trailing_activated"]:
            stop = best + half_range
            if bar.high >= stop:
                ctx.close(reason="trail_stop")
                return

    # ── 时段末强平 ──
    if session and _session_end_forced_close(bar.time, session):
        ctx.close(reason="eod_force")