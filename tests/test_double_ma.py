"""验证双均线策略的 on_bar 行为：金叉开多、死叉平仓。"""
from backtest.context import StrategyContext
from backtest.models import Bar
from backtest.strategies.double_ma import on_bar


def _feed(ctx: StrategyContext, closes: list[float]):
    for i, c in enumerate(closes):
        bar = Bar(time=f"t{i}", open=c, high=c + 1, low=c - 1, close=c, volume=100)
        ctx._push_bar(bar)
        on_bar(bar, ctx, fast=3, slow=5)


def test_no_signal_before_slow_warmup():
    ctx = StrategyContext()
    _feed(ctx, [100.0, 101.0, 102.0, 103.0])   # 不到 slow=5 根
    assert ctx.pending_orders == []


def test_golden_cross_triggers_open_long():
    ctx = StrategyContext()
    _feed(ctx, [100.0, 99.0, 98.0, 97.0, 96.0, 97.0, 99.0, 102.0, 105.0, 110.0])
    assert any(o.action == "open_long" for o in ctx.pending_orders)


def test_death_cross_closes_long():
    """构造先上涨后暴跌的序列，应当先开多后平仓。"""
    ctx = StrategyContext()
    closes = list(range(100, 120)) + list(range(120, 100, -1))
    for i, c in enumerate(closes):
        bar = Bar(time=f"t{i}", open=float(c), high=float(c) + 1,
                  low=float(c) - 1, close=float(c), volume=100)
        ctx._push_bar(bar)
        on_bar(bar, ctx, fast=3, slow=5)
        # 模拟引擎：下单后把持仓状态回灌 ctx
        for o in ctx.drain_pending():
            if o.action == "open_long":
                ctx._set_position("long", o.qty)
            elif o.action == "close":
                ctx._set_position(None, 0)
    assert ctx.position_side is None   # 最后应当已平仓
