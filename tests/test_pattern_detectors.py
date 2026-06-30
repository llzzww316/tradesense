from backtest.models import Bar
from patterns.breakout import detect_breakout, detect_breakout_pullback
from patterns.geometry import detect_asc_triangle, detect_bull_flag


def _bar(open_, high, low, close, volume=100, time="2026-01-01"):
    return Bar(time=time, open=open_, high=high, low=low, close=close, volume=volume)


def test_breakout_requires_volume_expansion():
    bars = [_bar(10.0, 10.5, 9.7, 10.1, volume=100) for _ in range(24)]
    bars.append(_bar(10.2, 11.2, 10.1, 11.1, volume=120))

    assert detect_breakout(bars) is None


def test_breakout_requires_large_body():
    bars = [_bar(10.0, 10.5, 9.7, 10.1, volume=100) for _ in range(24)]
    bars.append(_bar(10.95, 11.2, 10.9, 11.1, volume=250))

    assert detect_breakout(bars) is None


def test_breakout_pullback_uses_recent_valid_breakout():
    bars = [_bar(10.0, 10.4, 9.8, 10.1, volume=100) for _ in range(20)]

    # Older breakout is invalidated by a deep pullback.
    bars.extend([
        _bar(10.2, 11.4, 10.1, 11.2, volume=250),
        _bar(11.0, 11.1, 9.2, 9.5, volume=120),
        _bar(9.6, 10.0, 9.4, 9.8, volume=110),
        _bar(9.8, 10.2, 9.6, 10.0, volume=105),
    ])

    # Recent breakout remains valid after a shallow, shrinking-volume pullback.
    bars.extend([
        _bar(10.1, 11.9, 10.0, 11.7, volume=300),
        _bar(11.7, 11.85, 11.55, 11.68, volume=120),
        _bar(11.68, 11.8, 11.5, 11.62, volume=110),
        _bar(11.62, 11.75, 11.45, 11.6, volume=105),
    ])

    result = detect_breakout_pullback(bars)

    assert result is not None
    assert result["pattern"] == "突破回调"


def test_bull_flag_requires_price_near_upper_edge():
    bars = [_bar(10.0, 10.4, 9.8, 10.1) for _ in range(10)]
    bars.extend([
        _bar(10.0, 10.4, 9.8, 10.2, volume=300),
        _bar(10.2, 11.0, 10.1, 10.8, volume=320),
        _bar(10.8, 12.2, 10.7, 12.0, volume=340),
        _bar(12.0, 13.5, 11.9, 13.2, volume=360),
        _bar(13.2, 14.8, 13.0, 14.5, volume=380),
    ])
    bars.extend([
        _bar(14.3, 14.5, 13.8, 14.0, volume=80),
        _bar(14.0, 14.3, 13.7, 13.9, volume=75),
        _bar(13.9, 14.2, 13.6, 13.8, volume=70),
        _bar(13.8, 14.1, 13.5, 13.7, volume=65),
        _bar(13.7, 13.9, 13.4, 13.5, volume=60),
        _bar(13.5, 13.7, 13.2, 13.3, volume=55),
        _bar(13.3, 13.5, 13.0, 13.1, volume=50),
        _bar(13.1, 13.3, 12.8, 12.9, volume=45),
        _bar(12.9, 13.1, 12.7, 12.8, volume=40),
    ])
    bars.extend([_bar(12.8, 13.1, 12.7, 12.9) for _ in range(6)])

    assert detect_bull_flag(bars) is None


def test_ascending_triangle_requires_narrowing_range():
    bars = [_bar(10.0, 10.2, 9.8, 10.0) for _ in range(10)]
    pattern = [
        _bar(10.0, 12.0, 9.8, 11.5),
        _bar(11.4, 11.7, 10.0, 10.2),
        _bar(10.2, 10.8, 9.6, 10.0),
        _bar(10.0, 11.8, 10.2, 11.5),
        _bar(11.5, 11.6, 10.7, 10.9),
        _bar(10.9, 10.9, 10.2, 10.5),
        _bar(10.5, 12.0, 10.6, 11.6),
        _bar(11.6, 11.8, 10.9, 11.1),
        _bar(11.1, 11.2, 10.4, 10.7),
        _bar(10.7, 11.9, 10.8, 11.5),
        _bar(11.5, 11.7, 11.0, 11.3),
        _bar(11.3, 11.5, 10.6, 11.0),
        _bar(11.0, 12.0, 10.7, 11.7),
        _bar(11.7, 11.9, 11.1, 11.6),
        _bar(11.6, 11.8, 10.5, 11.4),
        _bar(11.4, 11.9, 10.8, 11.7),
        _bar(11.7, 11.9, 11.0, 11.6),
        _bar(11.6, 11.8, 10.6, 11.7),
        _bar(11.7, 11.95, 11.0, 11.85),
        _bar(11.8, 11.95, 10.7, 11.9),
    ]
    bars.extend(pattern)

    assert detect_asc_triangle(bars, max_narrowing_ratio=0.7) is None
