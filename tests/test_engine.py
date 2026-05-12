"""Engine 把 Bar 序列 + 策略 + 账户 + broker 串起来跑完一轮回测。"""
import pandas as pd
import pytest
from datetime import timedelta

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.registry import register_strategy, _clear_registry_for_test


def _make_df(closes: list[float], start="2025-10-01 09:00:00", minutes=5) -> pd.DataFrame:
    rows = []
    t = pd.Timestamp(start)
    for c in closes:
        rows.append({"bob": t, "open": c, "high": c + 1, "low": c - 1,
                     "close": c, "volume": 100})
        t += timedelta(minutes=minutes)
    return pd.DataFrame(rows)


def _cfg(strategy="buy_and_hold", **overrides):
    base = dict(
        symbol="X", contract=None, period="5m",
        start_date=None, end_date=None,
        initial_capital=100000.0,
        tick_size=1.0, tick_value=10.0,
        margin_rate=0.10, fee_per_lot=3.0,
        slippage_ticks=0, intraday_only=False,
        strategy=strategy, strategy_params={},
    )
    base.update(overrides)
    return BacktestConfig(**base)


def setup_function():
    _clear_registry_for_test()


def test_single_buy_then_close_pnl():
    """先注册一个"第二根买、第五根平"的傻策略，验证端到端盈亏。"""
    @register_strategy("buy_and_hold")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 2 and ctx.position_side is None:
            ctx.buy(1, reason="test-open")
        if len(ctx.history) == 5 and ctx.position_side == "long":
            ctx.close(reason="test-close")

    df = _make_df([3000, 3001, 3002, 3003, 3004, 3005, 3006])
    eng = BacktestEngine(_cfg(), df)
    result = eng.run()

    # 第 2 根产生 open → 第 3 根开盘价 3002 成交
    # 第 5 根产生 close → 第 6 根开盘价 3005 成交
    # 毛利 = (3005 - 3002) * 10 = 30；手续费 = 3 * 2 = 6；净 = 24
    assert len(result.trades) == 1
    assert result.trades[0].pnl == pytest.approx(30.0)
    assert result.trades[0].net_pnl == pytest.approx(24.0)
    assert result.liquidated is False


def test_open_signal_without_next_bar_is_ignored():
    """最后一根才产生信号，没有下一根可成交，fill 应为空。"""
    @register_strategy("late_signal")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 3 and ctx.position_side is None:
            ctx.buy(1)

    df = _make_df([3000, 3001, 3002])   # 只有 3 根
    result = BacktestEngine(_cfg(strategy="late_signal"), df).run()
    assert result.fills == []
    assert result.trades == []


def test_equity_curve_length_matches_bars():
    @register_strategy("noop")
    def strat(bar, ctx, **kw):
        pass

    df = _make_df([3000, 3001, 3002, 3003, 3004])
    result = BacktestEngine(_cfg(strategy="noop"), df).run()
    assert len(result.equity_curve) == 5
    assert result.equity_curve[0].equity == pytest.approx(100000.0)
    # 最大回撤永远 ≤ 0
    assert all(p.drawdown <= 0 for p in result.equity_curve)


def test_liquidation_stops_further_trades():
    """初始资金小 + 持多逢暴跌 → 应爆仓。"""
    @register_strategy("hold_long")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 1 and ctx.position_side is None:
            ctx.buy(1, reason="all in")

    closes = [3000, 3000, 3000, 2000]   # 第 4 根暴跌 33%
    df = _make_df(closes)
    result = BacktestEngine(_cfg(strategy="hold_long", initial_capital=4000.0), df).run()
    assert result.liquidated is True
    assert result.liquidated_at is not None


def test_intraday_force_close_at_day_end():
    """intraday_only=True 时，日末必须平仓。"""
    @register_strategy("hold_long2")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 1 and ctx.position_side is None:
            ctx.buy(1, reason="open")

    # 两天数据，每天 3 根
    rows = []
    for day in ("2025-10-01", "2025-10-02"):
        for hh in ("09:00", "10:00", "14:00"):
            t = pd.Timestamp(f"{day} {hh}:00")
            rows.append({"bob": t, "open": 3000.0, "high": 3001.0,
                         "low": 2999.0, "close": 3000.0, "volume": 100})
    df = pd.DataFrame(rows)
    result = BacktestEngine(_cfg(strategy="hold_long2", intraday_only=True), df).run()
    # 至少有一条 close fill 标注 "eod"
    assert any(f.action == "close" and "eod" in f.reason for f in result.fills)
    # 最终不持仓
    assert result.metrics["final_position"] == "flat"


def test_intraday_equity_point_uses_close_not_post_flatten():
    """日末强平时，当根的 EquityPoint.equity 应等于 close 价下的浮盈权益，
    而不是已经扣完手续费、退完保证金的 flatten 后权益。"""
    import pandas as pd

    @register_strategy("hold_long3")
    def strat(bar, ctx, **kw):
        if len(ctx.history) == 1 and ctx.position_side is None:
            ctx.buy(1, reason="open")

    # 一天两根：open=3000, close=3005（第二根）
    # 开仓在第 2 根 open=3000（第 1 根信号，第 2 根成交），fee=3
    # 第 2 根是当日最后一根：close=3005，浮盈 = 5*10 = 50
    # 预期 equity_curve[-1].equity = 100000 - 3 + 50 = 100047
    # 错误顺序会得到 flatten 后的 equity = 100000 + 50 - 6 = 100044
    rows = [
        {"bob": pd.Timestamp("2025-10-01 09:00:00"), "open": 3000.0, "high": 3001.0,
         "low": 2999.0, "close": 3000.0, "volume": 100},
        {"bob": pd.Timestamp("2025-10-01 14:00:00"), "open": 3000.0, "high": 3006.0,
         "low": 2999.0, "close": 3005.0, "volume": 100},
    ]
    df = pd.DataFrame(rows)
    result = BacktestEngine(
        _cfg(strategy="hold_long3", intraday_only=True), df
    ).run()

    assert result.equity_curve[-1].equity == pytest.approx(100047.0)
    # force close 事件存在且有 eod 原因
    assert any(f.action == "close" and "eod" in f.reason for f in result.fills)
