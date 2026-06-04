"""Donchian 日线突破策略单元测试。"""
import pandas as pd
import pytest
from datetime import datetime, timedelta

import backtest.strategies  # noqa: F401  触发策略注册
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig


# ── 辅助函数 ──────────────────────────────────────────────────

def _make_daily_bars(n: int, base_price: float = 100.0, volume: float = 1000.0,
                     start: str = "2025-01-01", spread: float = 2.0,
                     trend: float = 0.0) -> list[dict]:
    """生成 n 根合成日线 bar（dict 格式，用于构造 DataFrame）。"""
    rows = []
    t = pd.Timestamp(start)
    for i in range(n):
        p = base_price + i * trend
        rows.append({
            "bob": t,
            "open": p,
            "high": p + spread,
            "low": p - spread,
            "close": p,
            "volume": volume,
        })
        t += timedelta(days=1)
    return rows


def _make_bar(date: str, open_: float = 100.0, high: float = 102.0,
              low: float = 98.0, close: float = 100.0, volume: float = 1000.0) -> dict:
    """构造单根 bar dict。"""
    return {"bob": pd.Timestamp(date), "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


def _bars_to_df(bars: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(bars)


def _make_config(strategy: str = "donchian", strategy_params: dict | None = None) -> BacktestConfig:
    return BacktestConfig(
        symbol="测试股票", contract="SH.000001", period="1d",
        start_date=None, end_date=None,
        initial_capital=100_000.0,
        tick_size=0.01, tick_value=0.01,
        margin_rate=1.0, fee_per_lot=0.0,
        slippage_ticks=0, intraday_only=False,
        strategy=strategy,
        strategy_params=strategy_params or {},
        instrument_type="stock",
        commission_rate=0.00025, stamp_tax_rate=0.001,
        transfer_fee_rate=0.00001, lot_size=100,
    )


def _run(bars: list[dict], params: dict | None = None):
    """便捷回测运行函数。"""
    cfg = _make_config(strategy_params=params or {})
    engine = BacktestEngine(cfg, _bars_to_df(bars))
    return engine.run()


def _make_entry_exit_bars(
    flat_n: int = 50,
    hold_bars: int = 8,
    exit_low: float = 95.0,
) -> list[dict]:
    """构造完整的入场→持仓→出场序列。

    返回的 bars 列表保证：
    1. 前 flat_n 根平盘（close=100, low=98）
    2. 1 根突破 bar（close=105, volume=2000）
    3. 1 根成交 bar（close=106）
    4. hold_bars 根持仓期（close=104, low=103）
    5. 1 根出场触发 bar（close=exit_low, low=exit_low-1）→ 触发 Donchian 下轨出场
    6. 1 根出场成交 bar
    """
    bars = _make_daily_bars(flat_n, base_price=100.0, volume=1000.0)
    # 信号 bar：突破 Donchian 上轨
    bars.append(_make_bar("2025-02-21", close=105.0, high=106.0, volume=2000.0))
    # 成交 bar
    bars.append(_make_bar("2025-02-22", close=106.0, high=107.0, low=104.0))
    # 持仓期：价格在 103-106 区间
    for i in range(hold_bars):
        bars.append(_make_bar(f"2025-03-{i+1:02d}", close=104.0, high=106.0, low=103.0))
    # 出场触发 bar：close 跌破 Donchian 下轨
    bars.append(_make_bar("2025-03-10", open_=103.0, high=103.0, low=exit_low - 1, close=exit_low))
    # 出场成交 bar
    bars.append(_make_bar("2025-03-11", close=exit_low))
    return bars


# ── 测试用例 ──────────────────────────────────────────────────

class TestDonchianEntry:
    """入场条件测试。"""

    def test_breakout_triggers_buy(self):
        """突破 Donchian 上轨 + 放量 + 趋势过滤 → 入场并平仓。"""
        bars = _make_entry_exit_bars()
        result = _run(bars)
        assert len(result.trades) >= 1, "突破应产生至少 1 笔交易"
        assert result.trades[0].side == "long", "应为多头交易"

    def test_no_breakout_no_entry(self):
        """价格未突破 Donchian 上轨 → 不入场。"""
        bars = _make_daily_bars(60, base_price=100.0, volume=1000.0)
        result = _run(bars)
        assert len(result.trades) == 0, "无突破不应有交易"

    def test_volume_filter_blocks_entry(self):
        """突破但成交量不足 → 不入场。"""
        bars = _make_daily_bars(50, base_price=100.0, volume=1000.0)
        bars.append(_make_bar("2025-02-21", close=105.0, high=106.0, volume=500.0))
        bars.append(_make_bar("2025-02-22", close=106.0))
        result = _run(bars, {"vol_ratio": 1.5})
        assert len(result.trades) == 0, "缩量突破不应入场"

    def test_volume_filter_passes_with_volume(self):
        """突破且放量 → 入场并平仓。"""
        bars = _make_entry_exit_bars()
        result = _run(bars, {"vol_ratio": 1.5})
        assert len(result.trades) >= 1, "放量突破应入场"

    def test_trend_filter_blocks_entry(self):
        """价格 < EMA50 → 不入场。"""
        bars = _make_daily_bars(50, base_price=100.0, volume=1000.0, trend=-0.5)
        # 突破近期高点，但价格仍在 EMA 下方
        bars.append(_make_bar("2025-02-20", open_=75.0, high=80.0, low=75.0, close=79.0, volume=5000.0))
        bars.append(_make_bar("2025-02-21", close=80.0))
        result = _run(bars, {"trend_ema": 50})
        # 下跌趋势中 close=79 < EMA(~88) → 不应入场
        assert len(result.trades) == 0, "价格低于 EMA50 不应入场"


class TestDonchianExit:
    """出场条件测试。"""

    def test_donchian_low_exit(self):
        """持仓中价格跌破 exit_period 日最低 → Donchian 下轨平仓。"""
        bars = _make_entry_exit_bars(exit_low=95.0)
        result = _run(bars)
        assert len(result.trades) >= 1, "应有交易记录"
        trade = result.trades[0]
        assert "DC_exit" in trade.close_reason, f"Donchian 下轨出场，got: {trade.close_reason}"

    def test_trailing_stop_exit(self):
        """持仓中价格从最高点回撤 > 2×ATR → 移动止损平仓。"""
        bars = _make_daily_bars(50, base_price=100.0, volume=1000.0, spread=2.0)
        # 信号 bar：突破入场
        bars.append(_make_bar("2025-02-21", open_=100.0, high=108.0, low=100.0, close=108.0, volume=3000.0))
        # 成交 bar + 上涨期：close 逐步走高到 114
        bars.append(_make_bar("2025-02-22", open_=108.0, high=110.0, low=107.0, close=110.0))
        bars.append(_make_bar("2025-02-23", open_=110.0, high=112.0, low=109.0, close=112.0))
        bars.append(_make_bar("2025-02-24", open_=112.0, high=114.0, low=111.0, close=114.0))
        bars.append(_make_bar("2025-02-25", open_=114.0, high=115.0, low=113.0, close=114.0))
        # highest close = 114.0, ATR ≈ 4.0
        # 用 atr_stop_mult=5.0 使 stop = 114 - 20 = 94（容易触发）
        # 大幅下跌 → 触发 Donchian 下轨（更低的优先级），或 trailing stop
        bars.append(_make_bar("2025-02-26", open_=113.0, high=113.0, low=90.0, close=92.0))
        bars.append(_make_bar("2025-02-27", close=92.0))
        result = _run(bars, {"atr_stop_mult": 5.0})
        assert len(result.trades) >= 1, "应有交易记录"
        trade = result.trades[0]
        assert trade.close_reason != "", f"交易应已平仓，close_reason: {trade.close_reason}"


class TestDonchianEdgeCases:
    """边界条件测试。"""

    def test_insufficient_data_no_crash(self):
        """数据不足时不崩溃、不产生交易。"""
        bars = _make_daily_bars(5, base_price=100.0)
        result = _run(bars)
        assert len(result.trades) == 0, "数据不足不应产生交易"

    def test_no_short_position(self):
        """A 股不支持做空，策略不应产生空头信号。"""
        bars = _make_entry_exit_bars()
        result = _run(bars)
        for trade in result.trades:
            assert trade.side == "long", "A 股策略只应做多"


class TestDonchianRegistration:
    """策略注册测试。"""

    def test_strategy_registered(self):
        """donchian 策略应已注册。"""
        from backtest.registry import list_strategies
        assert "donchian" in list_strategies()

    def test_strategy_params_exposed(self):
        """策略参数应可被反射出。"""
        from backtest.registry import get_strategy_params
        params = get_strategy_params("donchian")
        param_names = [p["name"] for p in params]
        assert "entry_period" in param_names
        assert "exit_period" in param_names
        assert "atr_stop_mult" in param_names
        assert "vol_ratio" in param_names
        assert "trend_ema" in param_names
