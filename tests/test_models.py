"""验证数据类的基本构造与默认值。"""
from backtest.models import (
    Bar, Order, Fill, Trade, Position, BacktestConfig, EquityPoint,
)


def test_bar_construction():
    bar = Bar(time="2025-10-01 09:00:00", open=3000.0, high=3002.0,
              low=2998.0, close=3001.0, volume=100)
    assert bar.close == 3001.0


def test_order_default_reason():
    o = Order(action="open_long", qty=1)
    assert o.reason == ""


def test_fill_fields():
    f = Fill(time="t", action="open_long", qty=1, price=3001.0, fee=3.0)
    assert f.fee == 3.0


def test_trade_net_pnl_field():
    t = Trade(
        open_time="t1", close_time="t2", side="long", qty=1,
        open_price=3000.0, close_price=3005.0,
        fee=6.0, pnl=50.0, net_pnl=44.0, holding_bars=3,
    )
    assert t.net_pnl == 44.0


def test_position_margin_stored():
    p = Position(side="long", qty=2, avg_price=3000.0,
                 opened_time="t", margin=6000.0)
    assert p.margin == 6000.0


def test_config_defaults(rb_config_kwargs):
    cfg = BacktestConfig(**rb_config_kwargs)
    assert cfg.strategy_params == {"fast": 5, "slow": 20}
    assert cfg.intraday_only is False


def test_equity_point():
    ep = EquityPoint(time="t", equity=10000.0, drawdown=0.0)
    assert ep.equity == 10000.0
