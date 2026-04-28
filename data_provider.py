"""
TradeSense 数据适配层 —— 使用 mootdx 本地文件读取替代掘金 SDK

mootdx ExtQuotes 网络接口已失效，改用 tdxpy reader 直接读取通达信本地数据文件。
支持扩展市场（期货/商品）的日线、1分钟线(lc1)、5分钟线(lc5)。
"""
import traceback
import pandas as pd
from datetime import timedelta
from pathlib import Path
from tdxpy.reader.exhq_daily_bar_reader import TdxExHqDailyBarReader
from tdxpy.reader.lc_min_bar_reader import TdxLCMinBarReader

# 通达信安装目录
TDX_DIR = Path("C:/new_tdx")
VIPDOC = TDX_DIR / "vipdoc" / "ds"

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


def _get_file_path(market: int, symbol: str, period: str) -> Path:
    """根据 market、symbol、period 构造通达信数据文件路径"""
    symbol_upper = symbol.upper()
    if period == "1d":
        subdir = "lday"
        suffix = "day"
        filename = f"{market}#{symbol_upper}.{suffix}"
    elif period in ("1m",):
        subdir = "minline"
        suffix = "lc1"
        filename = f"{market}#{symbol_upper}.{suffix}"
    elif period in ("5m", "15m", "30m", "60m", "1h"):
        # 通达信5分钟线文件同时包含 5/15/30/60 分钟数据
        subdir = "fzline"
        suffix = "lc5"
        filename = f"{market}#{symbol_upper}.{suffix}"
    else:
        raise ValueError(f"不支持的周期: {period}")

    path = VIPDOC / subdir / filename
    return path


def _read_daily(path: Path) -> pd.DataFrame:
    """读取扩展市场日线数据"""
    reader = TdxExHqDailyBarReader()
    df = reader.get_df(str(path))
    df = df.reset_index()
    df.rename(columns={"index": "bob"}, inplace=True)
    df["bob"] = pd.to_datetime(df["bob"])
    # 日线只需要 open/high/low/close/volume
    return df[["bob", "open", "high", "low", "close", "volume"]]


def _read_minute(path: Path, period: str = "5m") -> pd.DataFrame:
    """读取扩展市场分钟线数据（lc1 或 lc5）"""
    reader = TdxLCMinBarReader()
    df = reader.get_df(str(path))
    df = df.reset_index()
    df.rename(columns={"date": "bob"}, inplace=True)
    df["bob"] = pd.to_datetime(df["bob"])

    # lc5 文件包含 5/15/30/60 分钟数据，需要按 period 重采样
    if period in ("15m", "30m", "60m", "1h"):
        rule = period.replace("1h", "60T")
        df = df.set_index("bob")
        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        resampled = resampled.reset_index()
        return resampled[["bob", "open", "high", "low", "close", "volume"]]

    return df[["bob", "open", "high", "low", "close", "volume"]]


def fetch_kline(market: int, symbol: str, period: str, count: int = 800) -> pd.DataFrame:
    """
    获取 K 线数据

    :param market: 扩展市场ID（如 30=上期所, 29=大商所）
    :param symbol: 品种代码（如 'RB2610'）
    :param period: 周期字符串（1m, 5m, 15m, 30m, 60m, 1h, 1d）
    :param count: 获取条数（从末尾取最新 count 条）
    :return: DataFrame（统一列名: bob, open, high, low, close, volume）
    """
    path = _get_file_path(market, symbol, period)
    if not path.exists():
        return pd.DataFrame()

    try:
        if period == "1d":
            df = _read_daily(path)
        else:
            df = _read_minute(path, period)
    except Exception:
        traceback.print_exc()
        return pd.DataFrame()

    if df.empty:
        return df

    # 取最新 count 条
    df = df.sort_values("bob").tail(count).reset_index(drop=True)
    return df


def fetch_kline_by_date(
    market: int,
    symbol: str,
    period: str,
    start_date: str = None,
    end_date: str = None,
    count: int = 800,
) -> pd.DataFrame:
    """获取 K 线并按日期过滤"""
    df = fetch_kline(market, symbol, period, count=count)

    if df.empty:
        return df

    if start_date:
        start_ts = pd.Timestamp(start_date)
        df = df[df["bob"] >= start_ts]

    if end_date:
        end_ts = pd.Timestamp(end_date) + timedelta(days=1)
        df = df[df["bob"] < end_ts]

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
    display_minutes = PERIOD_MINUTES.get(display_period, 5)
    step_minutes = PERIOD_MINUTES.get(step_period, 1)
    multiplier = max(1, display_minutes // step_minutes)

    display_df = fetch_kline_by_date(
        market, symbol, display_period,
        start_date=start_date, end_date=end_date, count=count,
    )
    step_df = fetch_kline_by_date(
        market, symbol, step_period,
        start_date=start_date, end_date=end_date, count=count * multiplier,
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
            (display_df["bob"] >= rs) & (display_df["bob"] <= re)
        ].copy()

        if display_df.empty:
            return {"error": "指定时间范围内无显示周期数据"}

        step_upper = re + pd.Timedelta(minutes=display_minutes)
        step_df = step_df[
            (step_df["bob"] >= rs) & (step_df["bob"] < step_upper)
        ].copy()

        if step_df.empty:
            return {"error": "指定时间范围内无步进周期数据"}
    else:
        display_df = display_df.tail(count)
        step_df = step_df.tail(count * multiplier)

    return {
        "display": display_df.sort_values("bob").reset_index(drop=True),
        "step": step_df.sort_values("bob").reset_index(drop=True),
    }
