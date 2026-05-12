"""pytest 通用 fixtures：合成 K 线、合成 symbol 配置"""
import pandas as pd
import pytest
from datetime import datetime, timedelta


def _make_bars(n: int, start: str = "2025-10-01 09:00:00", period_minutes: int = 5,
               base_price: float = 3000.0, trend: float = 0.0) -> pd.DataFrame:
    """生成 n 根合成 K：open=close=base+i*trend，high/low 在其基础上 ±2。"""
    rows = []
    t = pd.Timestamp(start)
    for i in range(n):
        price = base_price + i * trend
        rows.append({
            "bob": t,
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price,
            "volume": 100,
        })
        t += timedelta(minutes=period_minutes)
    return pd.DataFrame(rows)


@pytest.fixture
def flat_bars():
    return _make_bars(50, trend=0.0)


@pytest.fixture
def up_bars():
    return _make_bars(50, trend=1.0)


@pytest.fixture
def down_bars():
    return _make_bars(50, trend=-1.0)


@pytest.fixture
def rb_config_kwargs():
    """螺纹钢典型参数：1 跳 = 1 元，每跳价值 10 元/手，保证金 10%，手续费 3 元/手"""
    return dict(
        symbol="螺纹钢",
        contract=None,
        period="5m",
        start_date=None,
        end_date=None,
        initial_capital=100000.0,
        tick_size=1.0,
        tick_value=10.0,
        margin_rate=0.10,
        fee_per_lot=3.0,
        slippage_ticks=1,
        intraday_only=False,
        strategy="double_ma",
        strategy_params={"fast": 5, "slow": 20},
    )
