"""data_provider 重采样规则回归：15m/30m/60m/1h 必须按分钟聚合。

历史 bug（2026-05）：rule 用小写 'm' —— pandas 把 '15m'/'30m'/'60m' 解析为
N*MonthEnd，把整段行情塞进一根月末 K。修复后统一用 'Nmin'。
"""
from __future__ import annotations

import pandas as pd
import pytest

import data_provider as dp


class _FakeReader:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def get_df(self, _path: str) -> pd.DataFrame:
        # 真实 TdxLCMinBarReader 返回时以 'date' 为 index
        return self._df.set_index("date")


def _synth_5m(n: int, start: str = "2025-10-01 09:00:00") -> pd.DataFrame:
    rows = []
    t = pd.Timestamp(start)
    for i in range(n):
        rows.append({
            "date": t,
            "open": 100.0 + i, "high": 101.0 + i,
            "low": 99.0 + i, "close": 100.0 + i, "volume": 10,
        })
        t += pd.Timedelta(minutes=5)
    return pd.DataFrame(rows)


@pytest.mark.parametrize("period,expected_rows", [
    # 20 根 5m 覆盖 09:00 ~ 10:35（共 100 分钟）
    ("15m", 7),
    ("30m", 4),
    ("60m", 2),
    ("1h", 2),
])
def test_read_minute_resamples_by_minute_not_month(monkeypatch, period, expected_rows):
    df5 = _synth_5m(20)
    monkeypatch.setattr(dp, "TdxLCMinBarReader", lambda: _FakeReader(df5))

    out = dp._read_minute(dp.Path("/not/used"), period=period)

    # 若 rule 被解析成 MonthEnd，len(out) 会退化到 1
    assert len(out) == expected_rows, (
        f"{period}: expected {expected_rows} bars, got {len(out)} — "
        "rule 可能被 pandas 当成 MonthEnd"
    )

    first = out["bob"].iloc[0]
    assert first == pd.Timestamp("2025-10-01 09:00:00"), (
        f"{period}: 首根时间应对齐到开盘 09:00，实际 {first}"
    )

    # OHLC 合理性：high >= low、volume 累加
    assert (out["high"] >= out["low"]).all()
    assert out["volume"].sum() == 20 * 10


def test_read_minute_5m_passthrough(monkeypatch):
    """5m 不走 resample 分支，应原样返回 20 根。"""
    df5 = _synth_5m(20)
    monkeypatch.setattr(dp, "TdxLCMinBarReader", lambda: _FakeReader(df5))

    out = dp._read_minute(dp.Path("/not/used"), period="5m")

    assert len(out) == 20
    assert list(out.columns) == ["bob", "open", "high", "low", "close", "volume"]
