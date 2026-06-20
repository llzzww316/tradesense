"""价格行为形态检测单元测试。"""
from backtest.models import Bar
from backtest.indicators import (
    pin_bar,
    engulfing,
    harami,
    inside_bar,
    fake_breakout,
    three_bar_push,
    doji,
    marubozu,
    trend_bar_side,
    bullish_reversal_setup,
    bearish_reversal_setup,
    exhaustion_pattern,
    squeeze_detection,
)


def _bar(open_, high, low, close, time="2025-10-01 09:00:00", volume=100):
    """helper: open 用 'open_' 避免冲突。"""
    return Bar(time=time, open=open_, high=high, low=low, close=close, volume=volume)


# ── 单 K 形态 ──────────────────────────────────────────────


def test_pin_bar_bullish():
    """下影线长、实体小 → long 信号。"""
    b = _bar(100, 101, 95, 100.5)
    assert pin_bar(b) == "long"


def test_pin_bar_bearish():
    """上影线长、实体小 → short 信号。"""
    b = _bar(100, 105, 99, 100.5)
    assert pin_bar(b) == "short"


def test_pin_bar_not_enough_wick():
    """影线不够长 → None。"""
    b = _bar(100, 103, 97, 101)
    assert pin_bar(b) == "long"


def test_engulfing_bullish():
    """阳线完全覆盖前一根阴线 → long。"""
    prev = _bar(102, 103, 98, 100)
    cur = _bar(99, 104, 98, 103)
    assert engulfing(prev, cur) == "long"


def test_engulfing_bearish():
    """阴线完全覆盖前一根阳线 → short。"""
    prev = _bar(98, 103, 98, 102)
    cur = _bar(103, 104, 97, 98)
    assert engulfing(prev, cur) == "short"


def test_engulfing_same_direction():
    """同向吞没 —— 不算反转形态，返回 None。"""
    prev = _bar(98, 103, 98, 102)
    cur = _bar(100, 105, 99, 104)
    assert engulfing(prev, cur) is None


def test_engulfing_too_small():
    """当前实体未完全覆盖前一个实体 → None。"""
    prev = _bar(100, 103, 97, 102)
    cur = _bar(101, 104, 99, 103)
    assert engulfing(prev, cur) is None


def test_harami_bullish():
    """下跌后孕线（阴 -> 小阳）→ 可能转多。"""
    prev = _bar(104, 105, 96, 98)
    cur = _bar(99, 101, 98, 100)
    assert harami(prev, cur) == "long"


def test_harami_bearish():
    """上涨后孕线（阳 -> 小阴）→ 可能转空。"""
    prev = _bar(96, 105, 96, 104)
    cur = _bar(103, 104, 100, 102)
    assert harami(prev, cur) == "short"


def test_harami_outer_not_contained():
    """当前 K 实体超出前一根范围 → 不是孕线。"""
    prev = _bar(100, 104, 98, 102)
    cur = _bar(101, 106, 97, 103)
    assert harami(prev, cur) is None


def test_inside_bar_true():
    outer = _bar(100, 105, 95, 102)
    inner = _bar(101, 103, 97, 102)
    assert inside_bar(outer, inner)


def test_inside_bar_false():
    outer = _bar(100, 105, 95, 102)
    inner = _bar(101, 106, 96, 103)
    assert not inside_bar(outer, inner)


def test_doji_true():
    b = _bar(100, 105, 95, 100.5)
    assert doji(b)


def test_doji_false():
    b = _bar(100, 105, 95, 103)
    assert not doji(b)


def test_marubozu_bullish():
    b = _bar(95, 105, 95, 105)
    assert marubozu(b) == "long"


def test_marubozu_bearish():
    b = _bar(105, 105, 95, 95)
    assert marubozu(b) == "short"


# ── 多 K 组合 ──────────────────────────────────────────────


def test_three_bar_push_long():
    a = _bar(100, 105, 99, 104)
    b = _bar(104, 108, 103, 107)
    c = _bar(107, 110, 106, 109)
    assert three_bar_push(a, b, c, "long")


def test_three_bar_push_not_strict():
    """B 的收盘没比 A 高 → 不成立。"""
    a = _bar(100, 105, 99, 104)
    b = _bar(104, 107, 102, 103)
    c = _bar(103, 108, 102, 107)
    assert not three_bar_push(a, b, c, "long")


def test_fake_breakout_long():
    """假突破多头：突破前高后收回，确认 K 继续走低。"""
    prior = _bar(100, 103, 98, 101)
    breakout = _bar(101, 106, 99, 102)
    confirm = _bar(101, 102, 96, 97)
    assert fake_breakout(prior, breakout, confirm, "long")

def test_fake_breakout_not_break():
    """根本没突破 → 不算假突破。"""
    prior = _bar(100, 103, 98, 101)
    breakout = _bar(101, 103, 99, 102)  # high=103 <= prior.high=103, 没突破
    confirm = _bar(102, 104, 100, 101)
    assert not fake_breakout(prior, breakout, confirm, "long")


def test_bullish_reversal_pin_bar():
    """低点出现 Pin Bar，前几根偏空。"""
    bars = [
        _bar(105, 106, 104, 104.5, time="09:05:00"),
        _bar(104.5, 105, 103, 103, time="09:10:00"),
        _bar(103, 104, 100, 101, time="09:15:00"),
    ]
    pin = _bar(101, 102, 97, 101, time="09:20:00")
    assert pin_bar(pin) == "long"
    signal = bullish_reversal_setup(bars + [pin])
    assert signal == "pin_bar"


def test_squeeze_detection():
    """连续 inside bar → True。"""
    outer = _bar(100, 105, 95, 102)
    bars = [outer]
    for _ in range(7):
        bars.append(_bar(101, 103, 97, 102))
    assert squeeze_detection(bars, lookback=8)


    """连续阳线实体缩小 + 上影线 → 多头衰竭，返回 short。"""
    a = _bar(100, 105, 99, 104)
    b = _bar(104, 108, 103, 107)
    c = _bar(107, 112, 106, 108)  # 实体缩小，上影线长
    bars = [_bar(95, 97, 94, 96) for _ in range(3)] + [a, b, c]
    assert exhaustion_pattern(bars) == "short"


def test_bearish_reversal_engulfing():
    bars = [
        _bar(100, 103, 99, 102, time="09:05:00"),
        _bar(102, 104, 101, 103, time="09:10:00"),
    ]
    cur = _bar(104, 107, 100, 101, time="09:15:00")
    assert engulfing(bars[-1], cur) == "short"
    assert bearish_reversal_setup(bars + [cur]) == "engulfing"
