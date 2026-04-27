"""
TradeSense 数据适配层 —— 使用 mootdx 替代掘金 SDK
"""
import math
import traceback
import pandas as pd
from datetime import timedelta
from mootdx.quotes import Quotes
from mootdx.consts import (
    KLINE_1MIN, KLINE_5MIN, KLINE_15MIN, KLINE_30MIN,
    KLINE_1HOUR, KLINE_DAILY, MAX_KLINE_COUNT,
)

# mootdx 客户端（扩展市场 = 期货/商品）
_client = None


def get_client():
    """获取 mootdx ExtQuotes 客户端（延迟初始化 + 单例）"""
    global _client
    if _client is None:
        try:
            _client = Quotes.factory(market='ext', quiet=True)
        except Exception:
            traceback.print_exc()
            raise RuntimeError("无法连接 mootdx 扩展市场服务器")
    return _client


# 周期映射: display_period -> mootdx frequency int
PERIOD_MAP = {
    "1m": KLINE_1MIN,
    "5m": KLINE_5MIN,
    "15m": KLINE_15MIN,
    "30m": KLINE_30MIN,
    "60m": KLINE_1HOUR,
    "1h": KLINE_1HOUR,
    "1d": KLINE_DAILY,
}

# 各周期分钟数（用于步进数据量估算）
PERIOD_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1h": 60,
    "1d": 1440,
}


def _fetch_chunk(market: int, symbol: str, frequency: int, start: int, offset: int) -> pd.DataFrame:
    """底层调用 mootdx ExtQuotes.bars，处理列名标准化"""
    client = get_client()
    result = client.bars(
        frequency=frequency,
        market=market,
        symbol=symbol,
        start=start,
        offset=offset,
    )

    if result is None or result.empty:
        return pd.DataFrame()

    df = result.copy()

    # 时间列 -> bob
    if 'datetime' in df.columns:
        df['bob'] = pd.to_datetime(df['datetime'])
    elif 'date' in df.columns:
        df['bob'] = pd.to_datetime(df['date'])
    else:
        df['bob'] = pd.to_datetime(df.index)

    # 成交量 -> volume
    if 'vol' in df.columns and 'volume' not in df.columns:
        df['volume'] = df['vol']

    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col not in df.columns:
            df[col] = 0.0

    df = df.sort_values('bob').reset_index(drop=True)
    return df[['bob', 'open', 'high', 'low', 'close', 'volume']]


def fetch_kline(market: int, symbol: str, frequency: int, count: int = 800) -> pd.DataFrame:
    """
    获取 K 线数据，支持超过 800 条的分页拉取。
    mootdx 单次上限 MAX_KLINE_COUNT(800)，倒序 start 偏移获取。
    """
    if count <= 0:
        return pd.DataFrame()

    offset = min(count, MAX_KLINE_COUNT)
    dfs = []

    # 先取最新 offset 条
    df = _fetch_chunk(market, symbol, frequency, start=0, offset=offset)
    if df.empty:
        return df
    dfs.append(df)

    remaining = count - len(df)
    page = 1
    while remaining > 0 and not df.empty:
        page_offset = min(remaining, MAX_KLINE_COUNT)
        df = _fetch_chunk(market, symbol, frequency, start=page * MAX_KLINE_COUNT, offset=page_offset)
        if df.empty:
            break
        dfs.append(df)
        remaining -= len(df)
        page += 1

    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.sort_values('bob').drop_duplicates(subset=['bob']).reset_index(drop=True)
    return combined


def fetch_kline_by_date(
    market: int,
    symbol: str,
    frequency: int,
    start_date: str = None,
    end_date: str = None,
    count: int = 800,
) -> pd.DataFrame:
    """先取 count 条，再按日期过滤"""
    df = fetch_kline(market, symbol, frequency, count=count)

    if df.empty:
        return df

    if start_date:
        start_ts = pd.Timestamp(start_date)
        df = df[df['bob'] >= start_ts]

    if end_date:
        end_ts = pd.Timestamp(end_date) + timedelta(days=1)
        df = df[df['bob'] < end_ts]

    return df.reset_index(drop=True)


def fetch_replay_data(
    market: int,
    symbol: str,
    display_period: str,
    step_period: str,
    count: int = 2000,
    start_date: str = None,
    end_date: str = None,
    range_start: str = None,
    range_end: str = None,
) -> dict:
    """
    获取回放数据：显示周期 + 步进周期
    返回结构与掘金版本一致:
    { "display": DataFrame, "step": DataFrame }
    """
    display_freq = PERIOD_MAP.get(display_period, KLINE_5MIN)
    step_freq = PERIOD_MAP.get(step_period, KLINE_1MIN)
    display_minutes = PERIOD_MINUTES.get(display_period, 5)
    step_minutes = PERIOD_MINUTES.get(step_period, 1)

    # 步进数据量约为显示数据的 multiplier 倍
    multiplier = max(1, display_minutes // step_minutes)
    step_count = min(count * multiplier, 800)

    display_df = fetch_kline_by_date(
        market, symbol, display_freq,
        start_date=start_date, end_date=end_date, count=min(count, 800),
    )
    step_df = fetch_kline_by_date(
        market, symbol, step_freq,
        start_date=start_date, end_date=end_date, count=step_count,
    )

    if display_df.empty:
        return {"error": "No display period data"}
    if step_df.empty:
        return {"error": "No step period data"}

    if range_start and range_end:
        rs = pd.Timestamp(range_start)
        re = pd.Timestamp(range_end)

        if rs > re:
            return {"error": "range_start 不能晚于 range_end"}

        display_df = display_df[
            (display_df['bob'] >= rs) & (display_df['bob'] <= re)
        ].copy()

        if display_df.empty:
            return {"error": "指定时间范围内无显示周期数据"}

        step_upper = re + pd.Timedelta(minutes=display_minutes)
        step_df = step_df[
            (step_df['bob'] >= rs) & (step_df['bob'] < step_upper)
        ].copy()

        if step_df.empty:
            return {"error": "指定时间范围内无步进周期数据"}
    else:
        display_df = display_df.tail(count)
        step_df = step_df.tail(count * multiplier)

    return {
        "display": display_df.sort_values('bob').reset_index(drop=True),
        "step": step_df.sort_values('bob').reset_index(drop=True),
    }
