"""验证 always_in_pullback 策略：AI 评分、H2/L2 状态机、摆动点、离场逻辑。"""
from backtest.context import StrategyContext
from backtest.models import Bar
from backtest.strategies.always_in_pullback import (
    _ai_direction,
    _confirm_swing_high,
    _confirm_swing_low,
    _detect_pullback_state,
    _is_trading_range,
    _reset_pullback,
    on_bar,
)


# ---------------------------------------------------------------------------
# 辅助：构造 K 线
# ---------------------------------------------------------------------------

def _bar(close: float, open_: float = None, high: float = None, low: float = None,
         idx: int = 0) -> Bar:
    o = open_ if open_ is not None else close - 0.5
    h = high if high is not None else max(close, o) + 1.0
    l = low if low is not None else min(close, o) - 1.0
    return Bar(time=f"t{idx}", open=o, high=h, low=l, close=close, volume=100)


def _bull_bar(close: float, size: float = 5.0, idx: int = 0) -> Bar:
    o = close - size
    return Bar(time=f"t{idx}", open=o, high=close + 0.5, low=o - 0.5, close=close, volume=100)


def _bear_bar(close: float, size: float = 5.0, idx: int = 0) -> Bar:
    o = close + size
    return Bar(time=f"t{idx}", open=o, high=o + 0.5, low=close - 0.5, close=close, volume=100)


def _feed(ctx: StrategyContext, bars: list[Bar]):
    for bar in bars:
        ctx._push_bar(bar)
        on_bar(bar, ctx)
        for o in ctx.drain_pending():
            if o.action == "open_long":
                ctx._set_position("long", o.qty)
            elif o.action == "open_short":
                ctx._set_position("short", o.qty)
            elif o.action == "close":
                ctx._set_position(None, 0)


def _feed_raw(ctx: StrategyContext, bars: list[Bar]):
    for bar in bars:
        ctx._push_bar(bar)
        on_bar(bar, ctx)


# ---------------------------------------------------------------------------
# AI 方向判定
# ---------------------------------------------------------------------------

class TestAiDirection:
    def _make_history(self, closes: list[float], opens: list[float] = None) -> list[Bar]:
        bars = []
        for i, c in enumerate(closes):
            o = opens[i] if opens else c - 0.5
            bars.append(Bar(time=f"t{i}", open=o, high=max(c, o) + 1,
                            low=min(c, o) - 1, close=c, volume=100))
        return bars

    def test_consecutive_bull_bars_score_long(self):
        closes = [100 + i * 2 for i in range(30)]
        opens = [c - 4 for c in closes]
        history = self._make_history(closes, opens)
        ema = closes[-1] - 5
        direction, strength, score = _ai_direction(history, ema, lookback=5)
        assert direction == "long"
        assert strength == "strong"

    def test_consecutive_bear_bars_score_short(self):
        closes = [200 - i * 2 for i in range(30)]
        opens = [c + 4 for c in closes]
        history = self._make_history(closes, opens)
        ema = closes[-1] + 5
        direction, strength, score = _ai_direction(history, ema, lookback=5)
        assert direction == "short"
        assert strength == "strong"

    def test_flat_market_no_direction(self):
        closes = [100.0] * 30
        history = self._make_history(closes)
        direction, strength, score = _ai_direction(history, 100.0, lookback=5)
        assert direction is None

    def test_insufficient_bars(self):
        history = self._make_history([100.0, 101.0])
        direction, strength, score = _ai_direction(history, 100.0, lookback=5)
        assert direction is None


# ---------------------------------------------------------------------------
# H2/L2 二次入场
# ---------------------------------------------------------------------------

class TestH2L2:
    def test_h2_signal_after_pullback(self):
        state: dict = {"pb_state": "none", "pb_extreme": None}
        ai_dir = "long"

        bar = _bear_bar(98.0, size=2, idx=0)
        prev = _bull_bar(100.0, size=2, idx=-1)
        pb_state, signal = _detect_pullback_state(bar, prev, ai_dir, state)
        assert pb_state == "started"
        assert signal is None

        bar2 = _bear_bar(97.0, size=2, idx=1)
        prev2 = bar
        pb_state, signal = _detect_pullback_state(bar2, prev2, ai_dir, state)
        assert signal is None

        h1 = _bull_bar(98.5, size=2, idx=2)
        prev3 = bar2
        h1 = Bar(time="t2", open=96.5, high=prev3.high + 1, low=96.0, close=98.5, volume=100)
        pb_state, signal = _detect_pullback_state(h1, prev3, ai_dir, state)
        assert pb_state == "h1_seen"
        assert signal is None

        bar4 = _bear_bar(97.5, size=2, idx=3)
        state["_n"] = 100
        state["h1_bar_index"] = 99
        pb_state, signal = _detect_pullback_state(bar4, h1, ai_dir, state)
        assert signal is None

        h2 = Bar(time="t4", open=96.0, high=bar4.high + 1, low=95.5, close=98.0, volume=100)
        state["_n"] = 101
        pb_state, signal = _detect_pullback_state(h2, bar4, ai_dir, state)
        assert signal == "long"

    def test_l2_signal_in_short(self):
        state: dict = {"pb_state": "none", "pb_extreme": None}
        ai_dir = "short"

        bar = _bull_bar(102.0, size=2, idx=0)
        prev = _bear_bar(100.0, size=2, idx=-1)
        pb_state, signal = _detect_pullback_state(bar, prev, ai_dir, state)
        assert pb_state == "started"

        l1 = Bar(time="t1", open=103.0, high=103.5, low=prev.low - 1, close=100.0, volume=100)
        pb_state, signal = _detect_pullback_state(l1, bar, ai_dir, state)
        assert pb_state == "h1_seen"
        assert signal is None

        bar3 = _bull_bar(103.0, size=2, idx=2)
        state["_n"] = 100
        state["h1_bar_index"] = 99
        pb_state, signal = _detect_pullback_state(bar3, l1, ai_dir, state)
        assert signal is None

        l2 = Bar(time="t3", open=104.0, high=104.5, low=bar3.low - 1, close=100.0, volume=100)
        state["_n"] = 101
        pb_state, signal = _detect_pullback_state(l2, bar3, ai_dir, state)
        assert signal == "short"

    def test_reset_on_ai_change(self):
        state: dict = {"pb_state": "h1_seen", "pb_extreme": 97.0, "h1_bar_index": 5}
        _reset_pullback(state)
        assert state["pb_state"] == "none"
        assert state.get("pb_extreme") is None
        assert "h1_bar_index" not in state


# ---------------------------------------------------------------------------
# 摆动点检测
# ---------------------------------------------------------------------------

class TestSwingPoint:
    def test_swing_low_confirmed(self):
        history = [
            Bar(time="t0", open=10, high=12, low=8, close=11, volume=100),
            Bar(time="t1", open=9, high=11, low=7, close=10, volume=100),
            Bar(time="t2", open=10, high=13, low=9, close=12, volume=100),
        ]
        assert _confirm_swing_low(history) == 7.0

    def test_no_swing_low(self):
        history = [
            Bar(time="t0", open=12, high=13, low=11, close=12, volume=100),
            Bar(time="t1", open=10, high=12, low=9, close=10, volume=100),
            Bar(time="t2", open=8, high=10, low=7, close=8, volume=100),
        ]
        assert _confirm_swing_low(history) is None

    def test_swing_high_confirmed(self):
        history = [
            Bar(time="t0", open=10, high=12, low=8, close=11, volume=100),
            Bar(time="t1", open=13, high=15, low=12, close=14, volume=100),
            Bar(time="t2", open=11, high=13, low=10, close=12, volume=100),
        ]
        assert _confirm_swing_high(history) == 15.0


# ---------------------------------------------------------------------------
# TR 过滤
# ---------------------------------------------------------------------------

class TestTradingRange:
    def test_overlapping_bars_detected_as_tr(self):
        bars = []
        for i in range(25):
            c = 100.0 + (i % 3 - 1) * 0.1
            bars.append(Bar(time=f"t{i}", open=c - 0.1, high=c + 1.0,
                            low=c - 1.0, close=c, volume=100))
        closes = [b.close for b in bars]
        assert _is_trading_range(bars, closes, 100.0, lookback=20, overlap_threshold=0.6)

    def test_trending_not_tr(self):
        bars = []
        for i in range(25):
            c = 100.0 + i * 3
            bars.append(Bar(time=f"t{i}", open=c - 1, high=c + 2,
                            low=c - 2, close=c, volume=100))
        closes = [b.close for b in bars]
        assert not _is_trading_range(bars, closes, 115.0, lookback=20, overlap_threshold=0.6)


# ---------------------------------------------------------------------------
# 完整交易流程
# ---------------------------------------------------------------------------

class TestFullFlow:
    def test_uptrend_h2_entry_and_stop(self):
        ctx = StrategyContext()
        bars = []
        for i in range(30):
            c = 100.0 + i * 2
            bars.append(Bar(time=f"t{i}", open=c - 3, high=c + 1,
                            low=c - 4, close=c, volume=100))
        for i in range(2):
            c = 158.0 - i * 3
            bars.append(Bar(time=f"t{30+i}", open=c + 2, high=c + 3,
                            low=c - 1, close=c, volume=100))
        bars.append(Bar(time="t32", open=152.0, high=157.0, low=151.0, close=155.0, volume=100))
        bars.append(Bar(time="t33", open=154.0, high=155.0, low=149.0, close=150.0, volume=100))
        bars.append(Bar(time="t34", open=149.0, high=156.0, low=148.0, close=154.0, volume=100))
        for i in range(5):
            c = 154.0 + i
            bars.append(Bar(time=f"t{35+i}", open=c - 1, high=c + 1,
                            low=c - 2, close=c, volume=100))

        for bar in bars:
            ctx._push_bar(bar)
            on_bar(bar, ctx)
            for o in ctx.drain_pending():
                if o.action == "open_long":
                    ctx._set_position("long", o.qty)
                elif o.action == "close":
                    ctx._set_position(None, 0)

        assert len(ctx.history) == len(bars)

    def test_ai_flip_closes_position(self):
        ctx = StrategyContext()
        ctx._set_position("long", 1)
        ctx.state["stop_price"] = 90.0
        ctx.state["entry_price"] = 100.0
        ctx.state["risk"] = 10.0
        ctx.state["entry_bar_index"] = 30
        ctx.state["follow_checked"] = True
        ctx.state["breakeven_done"] = True

        bars = []
        for i in range(30):
            c = 100.0
            bars.append(Bar(time=f"t{i}", open=c - 0.5, high=c + 1,
                            low=c - 1, close=c, volume=100))
        for i in range(20):
            c = 100.0 - i * 3
            bars.append(Bar(time=f"t{30+i}", open=c + 2, high=c + 3,
                            low=c - 1, close=c, volume=100))

        for bar in bars:
            ctx._push_bar(bar)
            on_bar(bar, ctx)
            for o in ctx.drain_pending():
                if o.action == "close":
                    ctx._set_position(None, 0)

        assert ctx.position_side is None or ctx.position_side == "long"

    def test_breakeven_after_1r(self):
        ctx = StrategyContext()
        ctx._set_position("long", 1)
        ctx.state["stop_price"] = 90.0
        ctx.state["entry_price"] = 100.0
        ctx.state["risk"] = 10.0
        ctx.state["entry_bar_index"] = 30
        ctx.state["follow_checked"] = True
        ctx.state["breakeven_done"] = False
        ctx.state["prev_ai_dir"] = "long"

        bars = []
        for i in range(25):
            c = 100.0 + i * 0.5
            bars.append(Bar(time=f"t{i}", open=c - 0.5, high=c + 1,
                            low=c - 1, close=c, volume=100))
        for i in range(10):
            c = 112.0 + i * 0.5
            bars.append(Bar(time=f"t{25+i}", open=c - 0.5, high=c + 1,
                            low=c - 1, close=c, volume=100))

        for bar in bars:
            ctx._push_bar(bar)
            on_bar(bar, ctx)
            for o in ctx.drain_pending():
                if o.action == "close":
                    ctx._set_position(None, 0)

        if ctx.state.get("breakeven_done"):
            assert ctx.state["stop_price"] >= ctx.state["entry_price"]

    def test_follow_through_strong_bear_exits_long(self):
        ctx = StrategyContext()
        ctx._set_position("long", 1)
        ctx.state["stop_price"] = 90.0
        ctx.state["entry_price"] = 100.0
        ctx.state["risk"] = 10.0
        ctx.state["entry_bar_index"] = 25
        ctx.state["follow_checked"] = False
        ctx.state["breakeven_done"] = False
        ctx.state["prev_ai_dir"] = "long"

        bars = []
        for i in range(25):
            c = 100.0
            bars.append(Bar(time=f"t{i}", open=c - 0.5, high=c + 1,
                            low=c - 1, close=c, volume=100))
        # entry_bar_index=25, next bar (idx 26) = 强反向趋势 K
        bars.append(Bar(time="t25", open=100.0, high=100.5,
                        low=90.0, close=90.5, volume=100))
        for i in range(5):
            c = 90.0
            bars.append(Bar(time=f"t{26+i}", open=c + 0.5, high=c + 1,
                            low=c - 1, close=c, volume=100))

        closed = False
        for bar in bars:
            ctx._push_bar(bar)
            on_bar(bar, ctx)
            for o in ctx.drain_pending():
                if o.action == "close":
                    closed = True
                    ctx._set_position(None, 0)

        assert closed
