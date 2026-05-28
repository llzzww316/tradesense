"""上升旗型策略单元测试。"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.registry import _STRATEGIES, register_strategy
from backtest.strategies.bull_flag import (
    _calc_atr,
    _find_bull_pole,
    _linreg,
    detect_bull_flag,
)


# ============================================================================
# 造 Bar / Engine 辅助
# ============================================================================

def _bar(o: float, c: float, h: float, l: float,
         t: str = "2025-10-01 09:30:00", v: float = 100000.0) -> dict:
    return {"bob": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _make_engine(df: pd.DataFrame, strategy: str = "bull_flag",
                 strategy_params: dict | None = None,
                 initial_capital: float = 100000.0) -> BacktestEngine:
    params = {
        "pole_lookback": 20, "pole_min_pct": 5.0, "pole_max_retrace_pct": 30.0,
        "flag_min_bars": 3, "flag_atr_shrink_ratio": 0.8,
        "flag_max_height_ratio": 0.5,
        "atr_stop_mult": 1.0, "fixed_qty": 100,
    }
    if strategy_params:
        params.update(strategy_params)
    cfg = BacktestConfig(
        symbol="test", contract=None, period="5min",
        start_date=None, end_date=None,
        strategy=strategy, strategy_params=params,
        initial_capital=initial_capital,
        tick_size=0.01, slippage_ticks=0, margin_rate=1.0,
        tick_value=1.0, fee_per_lot=3.0, instrument_type="stock",
        commission_rate=0.0, stamp_tax_rate=0.0, transfer_fee_rate=0.0,
        lot_size=100, intraday_only=False,
    )
    return BacktestEngine(cfg, df)


@pytest.fixture(autouse=True)
def _isolated_registry():
    snapshot = dict(_STRATEGIES)
    yield
    _STRATEGIES.clear()
    _STRATEGIES.update(snapshot)


# ============================================================================
# _linreg
# ============================================================================

def test_linreg_perfect_line():
    s, i, r2 = _linreg([1, 2, 3], [10, 20, 30])
    assert s == pytest.approx(10.0)
    assert i == pytest.approx(0.0)
    assert r2 == pytest.approx(1.0)


def test_linreg_horizontal():
    s, i, r2 = _linreg([1, 2, 3], [5, 5, 5])
    assert s == pytest.approx(0.0)
    assert i == pytest.approx(5.0)
    assert r2 == pytest.approx(1.0)


def test_linreg_negative_slope():
    s, i, r2 = _linreg([1, 2, 3], [30, 20, 10])
    assert s == pytest.approx(-10.0)
    assert i == pytest.approx(40.0)


def test_linreg_single_point():
    s, i, r2 = _linreg([1], [42.0])
    assert s == 0.0
    assert i == pytest.approx(42.0)
    assert r2 == 0.0


# ============================================================================
# _calc_atr
# ============================================================================

def test_calc_atr_empty():
    assert _calc_atr([]) == 0.0


def test_calc_atr_single_bar():
    from backtest.models import Bar
    assert _calc_atr([Bar(time="t", open=10, high=12, low=9, close=11, volume=100)]) == 0.0


def test_calc_atr_two_bars():
    from backtest.models import Bar
    bars = [
        Bar(time="t1", open=10, high=12, low=9, close=11, volume=100),
        Bar(time="t2", open=11, high=13, low=10, close=12, volume=100),
    ]
    assert _calc_atr(bars) == pytest.approx(3.0)


# ============================================================================
# _find_bull_pole
# ============================================================================

def test_pole_insufficient_rise():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.3, low=9.9, close=10.1, volume=100)
            for i in range(25)]
    assert _find_bull_pole(bars, pole_lookback=20, pole_min_pct=5.0,
                           pole_max_retrace_pct=30.0) is None


def test_pole_too_much_retrace():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(10)]
    bars.append(Bar(time="t10", open=10, high=12.0, low=10, close=11.5, volume=100))
    bars.append(Bar(time="t11", open=11.5, high=11.5, low=10.1, close=10.3, volume=100))
    bars.extend([Bar(time=f"t{i}", open=10.3, high=10.4, low=10.2, close=10.3, volume=100)
                 for i in range(12, 20)])
    assert _find_bull_pole(bars, pole_lookback=20, pole_min_pct=5.0,
                           pole_max_retrace_pct=30.0) is None


def test_pole_standalone_success():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(10)]
    bars.append(Bar(time="t10", open=10, high=11.0, low=10, close=11.0, volume=100))
    bars.extend([Bar(time=f"t{i}", open=10.95, high=10.99, low=10.90, close=10.95, volume=100)
                 for i in range(11, 15)])
    result = _find_bull_pole(bars, pole_lookback=20, pole_min_pct=5.0,
                             pole_max_retrace_pct=30.0)
    assert result is not None
    _, _, pole_low, pole_high, pole_atr = result
    assert pole_low == pytest.approx(9.9)
    assert pole_high == pytest.approx(11.0)
    assert pole_atr > 0


# ============================================================================
# 标准旗型场景构造
# ============================================================================

def _make_bull_flag_scenario() -> pd.DataFrame:
    """构造完整的上升旗型场景 K 线序列。

    Bars 0-4:   基线横盘
    Bars 5-10:  旗杆上涨 (pole_low=9.90, pole_high=12.40)
    Bars 11-16: 旗帜收敛 (highs 递减, lows 走平)
    """
    rows = []
    t = 0

    def _add(o, h, l, c):
        nonlocal t
        rows.append(_bar(o, c, h, l,
                         t=f"2025-10-{10 + t // 8:02d} {9 + t % 8:02d}:00:00"))
        t += 1

    for _ in range(5):
        _add(10.0, 10.15, 9.90, 10.05)

    _add(10.05, 10.40, 10.00, 10.35)
    _add(10.35, 10.80, 10.30, 10.75)
    _add(10.75, 11.20, 10.70, 11.15)
    _add(11.15, 11.60, 11.10, 11.55)
    _add(11.55, 12.00, 11.50, 11.95)
    _add(11.95, 12.40, 11.90, 12.35)  # pole peak

    _add(12.35, 12.40, 12.10, 12.15)
    _add(12.15, 12.32, 12.08, 12.12)
    _add(12.12, 12.25, 12.05, 12.18)
    _add(12.18, 12.20, 12.06, 12.10)
    _add(12.10, 12.15, 12.04, 12.08)
    _add(12.08, 12.12, 12.03, 12.06)

    return pd.DataFrame(rows)


def _scenario_bars():
    """返回标准旗型场景的 Bar 列表。"""
    from backtest.models import Bar
    df = _make_bull_flag_scenario()
    return [Bar(time=row["bob"], open=row["open"], high=row["high"],
                low=row["low"], close=row["close"], volume=row["volume"])
            for _, row in df.iterrows()]


# ============================================================================
# detect_bull_flag
# ============================================================================

def test_detect_bull_flag_happy_path():
    df = _make_bull_flag_scenario()
    engine = _make_engine(df)
    engine.run()

    bars = engine.ctx.history
    info = detect_bull_flag(
        bars,
        pole_lookback=20, pole_min_pct=5.0, pole_max_retrace_pct=30.0,
        flag_min_bars=3, flag_atr_shrink_ratio=0.8, flag_max_height_ratio=0.5,
    )
    assert info is not None, "应检测到旗型形态"
    assert info["pole_low"] == pytest.approx(9.90, rel=0.01)
    assert info["pole_high"] == pytest.approx(12.40, rel=0.01)
    assert info["pole_height"] > 0
    assert info["flag_low"] > info["pole_low"]
    assert info["flag_upper_slope"] < 0, "上轨必须下行"
    assert info["breakout_price"] > 0


def test_detect_bull_flag_not_enough_data():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.1, low=9.9, close=10, volume=100)
            for i in range(10)]
    assert detect_bull_flag(bars, pole_lookback=20) is None


def test_detect_bull_flag_pole_too_small():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.3, low=9.9, close=10.1, volume=100)
            for i in range(100)]
    assert detect_bull_flag(bars, pole_lookback=20, pole_min_pct=10.0) is None


def test_detect_bull_flag_pole_retrace_too_deep():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(15)]
    bars.append(Bar(time="t15", open=10, high=11.5, low=10, close=11.5, volume=100))
    bars.append(Bar(time="t16", open=11.5, high=11.5, low=10.6, close=10.6, volume=100))
    bars.extend([Bar(time=f"t{i}", open=10.6, high=10.7, low=10.5, close=10.6, volume=100)
                 for i in range(17, 25)])
    info = detect_bull_flag(bars, pole_lookback=20, pole_min_pct=5.0,
                            pole_max_retrace_pct=40.0, flag_min_bars=3)
    assert info is None, "回撤超过 40% 应返回 None"


def test_detect_bull_flag_few_bull_bars_in_pole():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(15)]
    bars.append(Bar(time="t15", open=10.0, high=11.0, low=10.0, close=10.8, volume=100))
    bars.extend([Bar(time=f"t{i}", open=10.8, high=10.9, low=10.75, close=10.8, volume=100)
                 for i in range(16, 30)])
    info = detect_bull_flag(bars, pole_lookback=20, pole_min_pct=5.0,
                            pole_high_min_bull_bars=3)
    assert info is None, "只有 1 根真阳线，需要 >=3"


def test_detect_bull_flag_flag_not_descent():
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(10)]
    bars.append(Bar(time="t10", open=10, high=11.5, low=10, close=11.5, volume=100))
    for hi in [11.6, 11.7, 11.8, 11.9, 12.0, 12.1]:
        bars.append(Bar(time=f"t{len(bars)}", open=hi - 0.1, high=hi,
                        low=hi - 0.3, close=hi - 0.05, volume=100))
    info = detect_bull_flag(bars, pole_lookback=20, pole_min_pct=5.0, flag_min_bars=3)
    assert info is None, "上轨上行 → 不是旗帜"


def test_detect_bull_flag_breakout_price():
    df = _make_bull_flag_scenario()
    engine = _make_engine(df)
    engine.run()
    bars = engine.ctx.history
    info = detect_bull_flag(bars, pole_lookback=20, pole_min_pct=5.0, flag_min_bars=3)
    assert info is not None

    flag_bars = bars[info["pole_end_idx"] + 1:]
    flag_body = flag_bars[:-1]
    expected_x = len(flag_body) + 1
    expected = info["flag_upper_slope"] * expected_x + info["flag_upper_intercept"]
    assert info["breakout_price"] == pytest.approx(expected)


def test_detect_bull_flag_atr_shrink_excludes_breakout_bar():
    """ATR 收缩应只看旗帜本体，不应把突破大阳线算进去。"""
    from backtest.models import Bar

    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(10)]
    bars.append(Bar(time="t10", open=10, high=12.0, low=10, close=12.0, volume=100))
    # 旗帜本体：窄幅收敛
    for h in [11.9, 11.85, 11.8]:
        bars.append(Bar(time=f"t{len(bars)}", open=h - 0.05, high=h, low=11.5,
                        close=h - 0.05, volume=100))
    # 突破 bar：极宽振幅（若 ATR 误算进来会显著抬高）
    bars.append(Bar(time=f"t{len(bars)}", open=11.6, high=13.2, low=11.55,
                    close=13.0, volume=100))

    info = detect_bull_flag(
        bars,
        pole_lookback=20,
        pole_min_pct=5.0,
        flag_min_bars=3,
        flag_atr_shrink_ratio=0.6,
        flag_max_height_ratio=1.0,
    )
    assert info is not None, "突破大阳线不应破坏旗帜 ATR 收缩判定"


# ============================================================================
# on_bar — 策略行为测试（用 StrategyContext 直接驱动）
# ============================================================================

def _make_manual_flag_bars():
    """构造手动旗型场景：pole + flag(lows 走平) + breakout。"""
    from backtest.models import Bar
    bars = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
            for i in range(10)]
    bars.append(Bar(time="t10", open=10, high=12.0, low=10, close=12.0, volume=100))
    for h in [11.9, 11.85, 11.8, 11.75, 11.7]:
        bars.append(Bar(time=f"t{len(bars)}", open=h - 0.05, high=h, low=11.50,
                        close=h - 0.05, volume=100))
    bars.append(Bar(time=f"t{len(bars)}", open=11.7, high=12.5, low=11.65,
                    close=12.3, volume=100))
    return bars


def test_on_bar_buy_on_breakout():
    """突破收盘 → 生成买入订单。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()
    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    assert len(orders) == 1
    assert orders[0].action == "open_long"


def test_on_bar_stop_loss_triggers():
    """持仓后价格跌破止损 → 平仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()
    crash = bars[-1]  # hmm, the last bar IS the breakout bar

    ctx = StrategyContext()
    for b in bars[:-1]:
        ctx._push_bar(b)
    # 突破 bar 触发入场
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    ctx.drain_pending()

    # 追加 crashes bar 并模拟持仓
    crash_bar = _scenario_crash_bar()
    ctx._push_bar(crash_bar)
    ctx._set_position("long", 100)
    on_bar(crash_bar, ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    assert len(orders) >= 1
    assert any(o.action == "close" and "SL" in o.reason for o in orders)


def _scenario_crash_bar():
    from backtest.models import Bar
    return Bar(time="t_crash", open=12.30, high=12.30, low=7.00, close=8.50, volume=100000)


def test_on_bar_take_profit_triggers():
    """持仓后价格触及止盈 → 平仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()

    ctx = StrategyContext()
    for b in bars[:-1]:
        ctx._push_bar(b)
    # 突破 bar 触发入场
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    assert len(orders) == 1
    assert orders[0].action == "open_long"

    # 追加止盈 bar
    tp_bar = _scenario_tp_bar()
    ctx._push_bar(tp_bar)
    ctx._set_position("long", 100)
    on_bar(tp_bar, ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    assert any(o.action == "close" and "TP" in o.reason for o in orders)


def _scenario_tp_bar():
    from backtest.models import Bar
    return Bar(time="t_tp", open=12.30, high=16.00, low=12.30, close=16.00, volume=100000)


def test_on_bar_no_entry_without_breakout():
    """没有突破 → 不开仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _scenario_bars()  # 纯场景 bars，最后 bar 未突破
    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    assert len(orders) == 0, "无突破不应开仓"


def test_on_bar_no_entry_few_flag_bars():
    """flag_min_bars 太大 → 不开仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()
    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=20, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    assert len(orders) == 0


def test_on_bar_no_duplicate_entry():
    """已持仓时不再重复开仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()

    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    # 模拟已持仓
    ctx._set_position("long", 100)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)
    orders = ctx.drain_pending()
    opens = [o for o in orders if o.action == "open_long"]
    assert len(opens) == 0, "已持仓不应再开仓"


def test_on_bar_risk_params_flow_through():
    """验证 atr_stop_mult 影响止损位。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()

    # atr_stop_mult=2.0
    ctx2 = StrategyContext()
    for b in bars:
        ctx2._push_bar(b)
    on_bar(bars[-1], ctx2, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, atr_stop_mult=2.0, fixed_qty=100, trend_filter=False)
    assert ctx2.state.get("bf_stop") is not None

    # atr_stop_mult=0.5
    ctx05 = StrategyContext()
    for b in bars:
        ctx05._push_bar(b)
    on_bar(bars[-1], ctx05, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, atr_stop_mult=0.5, fixed_qty=100, trend_filter=False)
    assert ctx05.state.get("bf_stop") is not None

    assert ctx2.state["bf_stop"] < ctx05.state["bf_stop"], (
        f"mult=2.0 stop={ctx2.state['bf_stop']} 应 < mult=0.5 stop={ctx05.state['bf_stop']}"
    )


def test_on_bar_take_profit_uses_measured_move():
    """止盈目标 == 入场价 + 旗杆高度（等幅测量）。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()

    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, fixed_qty=100, trend_filter=False)

    target = ctx.state.get("bf_target")
    entry = ctx.state.get("bf_entry")
    pole_high = ctx.state.get("bf_pole_high")
    assert target is not None, "应有止盈目标"
    assert entry is not None, "应有入场价"
    pole_height = pole_high - 9.9
    assert target == pytest.approx(entry + pole_height, rel=0.02)


def test_on_bar_target_uses_filled_price_not_signal_close():
    """入场后应基于实际成交价重算止盈，而不是信号K收盘价。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_manual_flag_bars()
    breakout = bars[-1]
    fill_bar = type(breakout)(
        time="t_fill",
        open=12.8,   # 刻意高于 breakout.close=12.3，模拟跳空成交
        high=12.9,
        low=12.7,
        close=12.82,
        volume=1000,
    )

    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(
        breakout,
        ctx,
        pole_lookback=20,
        pole_min_pct=5.0,
        flag_min_bars=3,
        fixed_qty=100,
        trend_filter=False,
    )
    assert ctx.state.get("bf_pole_height") is not None

    # 模拟下一根开盘已成交，策略再次被调用进入持仓管理。
    ctx._set_position("long", 100, avg_price=fill_bar.open)
    ctx._push_bar(fill_bar)
    on_bar(
        fill_bar,
        ctx,
        pole_lookback=20,
        pole_min_pct=5.0,
        flag_min_bars=3,
        fixed_qty=100,
        trend_filter=False,
    )

    expected_target = fill_bar.open + ctx.state["bf_pole_height"]
    assert ctx.state["bf_entry"] == pytest.approx(fill_bar.open)
    assert ctx.state["bf_target"] == pytest.approx(expected_target)

# ============================================================================
# trend_filter 测试
# ============================================================================

def _make_trend_before_flag(uptrend: bool) -> list:
    """在已知旗型数据前加 200 根趋势 bar，旗型价格对齐趋势末端。"""
    from backtest.models import Bar

    trend = []
    price = 15.0 if uptrend else 30.0
    factor = 1.003 if uptrend else 0.997
    for i in range(200):
        trend.append(Bar(time=f"t{i}", open=price, high=price*1.01,
                         low=price*0.99, close=price, volume=100))
        price *= factor

    trend_end = price
    # 用 _make_manual_flag_bars 的模板，但价格对齐 trend_end
    # poles: O=10 -> trend_end, pole_high=12 -> trend_end*1.2
    ratio = trend_end / 10.0
    bars = []
    # 基线 10 根
    for i in range(10):
        p = trend_end * (1 - i * 0.001)  # 略低于 trend_end
        bars.append(Bar(time=f"t{200+i}", open=p, high=p*1.015, low=p*0.99,
                        close=p*1.005, volume=100))
    # 旗杆：从基线上升到 +20%
    base = bars[-1].close if bars else trend_end * 1.01
    pole_high = base * 1.20
    bars.append(Bar(time=f"t{210}", open=base, high=pole_high, low=base,
                    close=pole_high*0.98, volume=100))
    # 旗帜（lows 走平，远高于旗杆起点）
    flag_low = base * 1.10  # 旗杆低点上方 10%，确保回撤 < 50%
    for idx, h in enumerate([pole_high*0.995, pole_high*0.992, pole_high*0.990]):
        bars.append(Bar(time=f"t{211+idx}", open=h*0.998, high=h,
                        low=flag_low, close=h*0.998, volume=100))
    # 突破 bar
    bars.append(Bar(time=f"t{214}", open=flag_low*1.005, high=pole_high*1.02,
                    low=flag_low, close=pole_high*1.02, volume=100))

    return trend + bars


def test_on_bar_trend_filter_blocks_downtrend():
    """下降趋势 + 旗型 = trend_filter 阻止开仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_trend_before_flag(uptrend=False)
    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=2.0,
           pole_max_retrace_pct=50.0,
           flag_min_bars=3, flag_atr_shrink_ratio=3.0,
           trend_filter=True,
           trend_ema_fast=50, trend_ema_slow=150, fixed_qty=100)
    orders = ctx.drain_pending()
    assert len(orders) == 0, "下降趋势应阻止开仓"


def test_on_bar_trend_filter_allows_uptrend():
    """上升趋势 + 旗型 = trend_filter 允许开仓。"""
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar

    bars = _make_trend_before_flag(uptrend=True)
    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    on_bar(bars[-1], ctx, pole_lookback=20, pole_min_pct=2.0,
           pole_max_retrace_pct=50.0,
           flag_min_bars=3, flag_atr_shrink_ratio=3.0, trend_filter=True,
           trend_ema_fast=50, trend_ema_slow=150, fixed_qty=100)
    orders = ctx.drain_pending()
    assert len(orders) == 1
    assert orders[0].action == "open_long", "上升趋势应允许开仓"

def test_r2_filter_blocks_noisy_flag():
    from backtest.models import Bar
    from backtest.context import StrategyContext
    from backtest.strategies.bull_flag import on_bar, detect_bull_flag

    # 标准旗型：R² 很高
    bars = _make_manual_flag_bars()
    ctx = StrategyContext()
    for b in bars:
        ctx._push_bar(b)
    info = detect_bull_flag(bars, pole_lookback=20, pole_min_pct=5.0, flag_min_bars=3)
    assert info is not None
    assert info["flag_upper_r2"] > 0.9, "标准旗型 R² 应很高"

    # 松散旗型：highs 不线性 → R² 低
    bars2 = [Bar(time=f"t{i}", open=10, high=10.2, low=9.9, close=10.1, volume=100)
             for i in range(10)]
    bars2.append(Bar(time="t10", open=10, high=12.0, low=10, close=12.0, volume=100))
    for h in [11.9, 11.6, 11.7]:  # 下降但非线性
        bars2.append(Bar(time=f"t{len(bars2)}", open=h-0.05, high=h, low=11.5,
                        close=h-0.05, volume=100))
    bars2.append(Bar(time=f"t{len(bars2)}", open=11.7, high=12.5, low=11.65,
                    close=12.3, volume=100))
    ctx2 = StrategyContext()
    for b in bars2:
        ctx2._push_bar(b)
    info2 = detect_bull_flag(bars2, pole_lookback=20, pole_min_pct=5.0,
                             flag_min_bars=3, flag_atr_shrink_ratio=1.0,
                             flag_max_height_ratio=1.0)
    assert info2 is not None, "松散旗型应被检测"
    r2 = info2["flag_upper_r2"]
    assert r2 < 0.8, f"松散旗型 R² 应低，实际 {r2:.4f}"

    # r2_min=0.8 应阻止
    on_bar(bars2[-1], ctx2, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, flag_atr_shrink_ratio=1.0,
           r2_filter=True, r2_min=0.8, fixed_qty=100,
           trend_filter=False)
    assert len(ctx2.drain_pending()) == 0

    # r2_min=0.1 应允许
    ctx3 = StrategyContext()
    for b in bars2:
        ctx3._push_bar(b)
    on_bar(bars2[-1], ctx3, pole_lookback=20, pole_min_pct=5.0,
           flag_min_bars=3, flag_atr_shrink_ratio=1.0,
           r2_filter=True, r2_min=0.1, fixed_qty=100,
           trend_filter=False)
    assert len(ctx3.drain_pending()) == 1
