"""
TradeSense 数据适配层 —— tdxpy 读取通达信扩展市场 VIPDOC 离线文件。

历史说明：曾计划经 mootdx 网络扩展行情拉取；现仅支持本地 vipdoc（lc1 / lc5 / 日线）。
"""
import logging
import os
import re
import pandas as pd
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from tdxpy.reader.exhq_daily_bar_reader import TdxExHqDailyBarReader
from tdxpy.reader.lc_min_bar_reader import TdxLCMinBarReader

logger = logging.getLogger(__name__)

# 通达信安装目录（可通过环境变量 TRADESENSE_TDX_DIR 覆盖；换机器不用改代码）
TDX_DIR = Path(os.getenv("TRADESENSE_TDX_DIR", "C:/new_tdx"))
VIPDOC = TDX_DIR / "vipdoc" / "ds"


def futures_prefix_from_mootdx_code(mootdx_code: str) -> str:
    """从配置中的示例合约推导品种字母前缀（如 RB2610 -> RB，V2609 -> V）。"""
    if not mootdx_code:
        return ""
    m = re.match(r"^([A-Za-z]+)", str(mootdx_code).strip())
    return (m.group(1) if m else "").upper()


def scan_contract_codes(market: int, prefix: str) -> list[str]:
    """
    扫描 VIPDOC 下 fzline/minline/lday，列出该市场与前缀匹配的合约代码（去重大写排序）。
    文件名形如: {market}#RB2610.lc5
    """
    prefix = (prefix or "").upper().strip()
    if not prefix or not VIPDOC.exists():
        return []

    found: set[str] = set()
    contract_re = re.compile(rf"^{re.escape(prefix)}\d+$")
    subdir_globs = [
        ("fzline", f"{market}#{prefix}*.lc5"),
        ("minline", f"{market}#{prefix}*.lc1"),
        ("lday", f"{market}#{prefix}*.day"),
    ]
    for subdir, pattern in subdir_globs:
        d = VIPDOC / subdir
        if not d.is_dir():
            continue
        for path in d.glob(pattern):
            stem = path.stem
            if "#" not in stem:
                continue
            _, tail = stem.split("#", 1)
            code = tail.upper().strip()
            if contract_re.match(code):
                found.add(code)

    return sorted(found)


def contract_has_any_data_file(market: int, symbol_code: str) -> bool:
    """是否至少存在 日线 / 1m / 5m（lc5）中任一类本地文件。"""
    code_upper = symbol_code.upper().strip()
    for period in ("1d", "1m", "5m"):
        if _get_file_path(market, code_upper, period).exists():
            return True
    return False


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


@lru_cache(maxsize=64)
def _cached_read(path_str: str, mtime: float, period: str) -> pd.DataFrame:
    """
    以 (path, mtime, period) 为 key 缓存完整文件读取 + 排序 + 重采样结果。
    文件被通达信覆写后 mtime 变化，旧 key 自动失效（条目仍在 LRU 里，不会再命中）。
    返回值是缓存的引用：调用方必须 .copy() 再加列或改行，避免污染缓存。
    """
    path = Path(path_str)
    if period == "1d":
        df = _read_daily(path)
    else:
        df = _read_minute(path, period)
    if df.empty:
        return df
    # tdxpy 原始读出并非严格时间升序，统一排序后落入缓存，后续直接 tail 即可
    return df.sort_values("bob").reset_index(drop=True)


def clear_kline_cache() -> None:
    """手动清缓存（测试或强制刷新时用）。"""
    _cached_read.cache_clear()


def fetch_kline(market: int, symbol: str, period: str, count: int | None = 800) -> pd.DataFrame:
    """
    获取 K 线数据

    :param market: 扩展市场ID（如 30=上期所, 29=大商所）
    :param symbol: 品种代码（如 'RB2610'）
    :param period: 周期字符串（1m, 5m, 15m, 30m, 60m, 1h, 1d）
    :param count: 获取条数（从末尾取最新 count 条）；传 None 表示不截断
    :return: DataFrame（统一列名: bob, open, high, low, close, volume）
    """
    path = _get_file_path(market, symbol, period)
    if not path.exists():
        return pd.DataFrame()

    try:
        mtime = path.stat().st_mtime
        df = _cached_read(str(path), mtime, period)
    except Exception:
        logger.exception("读取 K 线失败: market=%s symbol=%s period=%s path=%s", market, symbol, period, path)
        return pd.DataFrame()

    if df.empty:
        return df

    # 缓存的 DataFrame 已经是按 bob 升序的（_read_daily / _read_minute 读完就是升序）。
    # 这里只做 tail + copy，copy 保证调用方后续赋值不会污染缓存。
    if count is not None:
        df = df.tail(count)
    return df.copy().reset_index(drop=True)


def fetch_kline_by_date(
    market: int,
    symbol: str,
    period: str,
    start_date: str = None,
    end_date: str = None,
    count: int = 800,
) -> pd.DataFrame:
    """获取 K 线并按日期过滤。有日期时先过滤全量再 tail(count)，避免 tail 先截掉了所选日期。"""
    has_date = bool(start_date or end_date)
    df = fetch_kline(market, symbol, period, count=None if has_date else count)

    if df.empty:
        return df

    if start_date:
        start_ts = pd.Timestamp(start_date)
        df = df[df["bob"] >= start_ts]

    if end_date:
        end_ts = pd.Timestamp(end_date) + timedelta(days=1)
        df = df[df["bob"] < end_ts]

    if has_date:
        df = df.tail(count)

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
    返回结构与 GET /api/replay_data JSON 约定一致（display + step 两个 DataFrame）:
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
        re_ts = pd.Timestamp(range_end)

        if rs > re_ts:
            return {"error": "range_start 不能晚于 range_end"}

        display_df = display_df[
            (display_df["bob"] >= rs) & (display_df["bob"] <= re_ts)
        ].copy()

        if display_df.empty:
            return {"error": "指定时间范围内无显示周期数据"}

        step_upper = re_ts + pd.Timedelta(minutes=display_minutes)
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
