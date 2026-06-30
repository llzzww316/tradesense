"""
实时 TDX 行情数据层：封装 mootdx 网络 API，提供线程安全的连接管理和数据获取。

A 股走 mootdx StdQuotes（通达信标准行情 7709），期货暂不支持扩展行情，
返回 error 供上层回退到离线 VIPDOC 数据。

字段一致性验证（日线 / 重叠区间）：
  - 茅台 SH.600519    80 条：open/high/low/close/volume 全部 100% 一致
  - 平安银行 SZ.000001 783 条：100% 一致
  - 五粮液 SZ.000858  783 条：100% 一致
分钟线（1m/5m/15m/30m/60m）已验证全部可获取。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

import pandas as pd
from mootdx.quotes import Quotes

from config import (
    get_market_type,
    get_stock_exchange_and_code,
    get_symbols_config,
    resolve_symbol,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 周期 → mootdx / tdxpy K 线类型常量映射
# mootdx 底层用的就是 tdxpy 的常量定义
# ---------------------------------------------------------------------------

# 标准行情（A 股）K 线类型
_PERIOD_CATEGORY: dict[str, int] = {
    "1m": 8,   # KLINE_TYPE_1MIN
    "5m": 0,   # KLINE_TYPE_5MIN
    "15m": 1,  # KLINE_TYPE_15MIN
    "30m": 2,  # KLINE_TYPE_30MIN
    "60m": 3,  # KLINE_TYPE_1HOUR
    "1h": 3,   # KLINE_TYPE_1HOUR
    "1d": 9,   # KLINE_TYPE_DAILY（mootdx 用 9，tdxpy 用 4）
}

VALID_PERIODS = set(_PERIOD_CATEGORY.keys())

# ---------------------------------------------------------------------------
# 连接管理器
# ---------------------------------------------------------------------------

# mootdx StdQuotes 客户端（单例）的内部接口说明：
# - Quotes.factory(market='std') 创建客户端，自动连接并开始心跳
# - client.get_security_bars(freq, market, code, start, count) → list[OrderedDict]
# - client.get_security_quotes((market, code)) → list[dict]
# - client.closed → bool
# - client.close() → None
#
# 注意：mootdx 的 Quotes.bars() 包装方法有 bug（get_stock_market 断言 symbol 为 str），
# 我们直接使用底层 client.get_security_bars() 绕过。


class MootdxConnectionManager:
    """管理 mootdx StdQuotes 连接生命周期，线程安全。"""

    def __init__(self) -> None:
        self._client: Quotes | None = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> Quotes:
        """确保客户端存活，若已关闭则重建。

        注意：此方法假定调用方已持有 _lock，不要在外层再加锁。
        """
        if self._client is not None and not self._client.closed:
            return self._client
        self._client = Quotes.factory(market="std")
        logger.info("mootdx StdQuotes 已连接")
        return self._client

    def with_client(self, fn):
        """加锁执行 mootdx API 调用，连接断开时自动重连一次。

        `fn` 收到的是 `StdQuotes.client`（底层 tcp 客户端），不是 StdQuotes 外层。
        因为 `get_security_bars` / `get_security_quotes` 都在 `.client` 上。
        """
        with self._lock:
            try:
                client = self._ensure_client()
                return fn(client.client)
            except Exception:
                logger.debug("mootdx 连接可能已断，重置后重试")
                self._client = None
                try:
                    client = self._ensure_client()
                    return fn(client.client)
                except Exception:
                    logger.exception("mootdx API 调用失败（重试后）")
                    raise

    def health(self) -> dict:
        """返回连接健康状态。"""
        with self._lock:
            if self._client is not None:
                try:
                    ok = not self._client.client.closed
                except Exception:
                    ok = False
            else:
                ok = False
            return {
                "hq": {"connected": ok},
                "exhq": {"connected": False},  # mootdx StdQuotes 不支持扩展行情
            }

    def disconnect_all(self) -> None:
        """断开连接。"""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.client.close()
                except Exception:
                    pass
                self._client = None
                logger.info("mootdx 连接已断开")


# 模块级单例
_manager = MootdxConnectionManager()


# ---------------------------------------------------------------------------
# 报价 — A 股
# ---------------------------------------------------------------------------


def get_realtime_quote(symbol: str, contract: str | None = None) -> dict:
    """获取实时报价。

    返回 dict:
        symbol, contract, price, open, high, low, volume,
        bid1_vol, ask1_vol, time, source,
        error (仅失败时出现)
    """
    # 注意：不走 resolve_symbol，因为 A 股没有 mootdx_market，
    # 那只对期货有意义。A 股直接用 get_stock_exchange_and_code 就够了。
    market_type = get_market_type(symbol)
    if market_type is None:
        return {"symbol": symbol, "error": "unknown_symbol", "source": None}

    if market_type == "stock":
        return _quote_stock(symbol, contract)
    elif market_type == "futures":
        return _quote_futures(symbol, contract)
    else:
        return {"symbol": symbol, "error": "unknown_market_type", "source": None}


def _quote_stock(symbol: str, stock_code: str) -> dict:
    """A 股实时报价（走 mootdx StdQuotes）。"""
    pair = get_stock_exchange_and_code(symbol)
    if pair is None:
        if "." in stock_code:
            exchange, code = stock_code.split(".", 1)
        else:
            return {"symbol": symbol, "error": "invalid_stock_code", "source": None}
    else:
        exchange, code = pair

    market = 1 if exchange == "sh" else 0  # 1=上海, 0=深圳

    try:
        result = _manager.with_client(
            lambda c: c.get_security_quotes((market, code))
        )
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "source": None}

    if not result or len(result) == 0:
        return {"symbol": symbol, "error": "no_data", "source": None}

    q = result[0]
    return {
        "symbol": symbol,
        "contract": stock_code,
        "price": q.get("price", 0.0),
        "open": q.get("open", 0.0),
        "high": q.get("high", 0.0),
        "low": q.get("low", 0.0),
        "volume": q.get("vol", 0),
        "bid1": q.get("bid1", 0.0),
        "ask1": q.get("ask1", 0.0),
        "bid1_vol": q.get("bid_vol1", 0),
        "ask1_vol": q.get("ask_vol1", 0),
        "last_close": q.get("last_close", 0.0),
        "time": q.get("servertime", ""),
        "source": "hq",
    }


def _quote_futures(symbol: str, contract_code: str | None = None) -> dict:
    """期货实时报价（暂不支持，触发回退到离线数据）。

    mootdx StdQuotes 只封装了标准行情（7709），不支持扩展行情（7720）。
    扩展行情需使用 tdxpy TdxExHq_API，与本机环境连接超时问题相同。
    后续若需支持期货实时数据，可引入 mootdx ExQuotes 或东方财富 HTTP 接口。
    """
    logger.debug("期货实时报价暂不支援 [%s %s]，触发回退离线", symbol, contract_code)
    return {"symbol": symbol, "error": "exhq_unavailable", "source": None}


# ---------------------------------------------------------------------------
# K 线 — A 股
# ---------------------------------------------------------------------------


def get_realtime_bars(
    symbol: str,
    contract: str | None = None,
    period: str = "5m",
    count: int = 800,
) -> pd.DataFrame | dict:
    """获取实时 K 线数据。

    返回 DataFrame 列: [bob, open, high, low, close, volume]
    （与 data_provider.fetch_kline() 格式一致）。
    失败时返回 dict {"error": ...} 供上层判断回退。
    """
    if period not in VALID_PERIODS:
        return {"error": f"invalid_period: {period}"}

    # 同样不走 resolve_symbol，直接用 get_market_type
    market_type = get_market_type(symbol)
    if market_type is None:
        return {"error": f"unknown_symbol: {symbol}"}

    if market_type == "stock":
        return _bars_stock(symbol, contract, period, count)
    elif market_type == "futures":
        return {"error": "exhq_unavailable"}
    else:
        return {"error": f"unknown_market_type: {market_type}"}


def _bars_stock(
    symbol: str, stock_code: str | None, period: str, count: int
) -> pd.DataFrame | dict:
    """A 股实时 K 线（走 mootdx StdQuotes 底层 API）。"""
    pair = get_stock_exchange_and_code(symbol)
    if pair is None:
        if stock_code and "." in stock_code:
            exchange, code = stock_code.split(".", 1)
        else:
            return {"error": f"invalid_stock_code: {stock_code}"}
    else:
        exchange, code = pair

    market = 1 if exchange == "sh" else 0  # 1=上海, 0=深圳

    category = _PERIOD_CATEGORY.get(period)
    if category is None:
        return {"error": f"unsupported_period: {period}"}

    try:
        result = _manager.with_client(
            lambda c, cat=category: c.get_security_bars(cat, market, code, start=0, count=min(count, 800))
        )
    except Exception as e:
        return {"error": str(e)}

    if not result:
        return pd.DataFrame(columns=["bob", "open", "high", "low", "close", "volume"])

    return _normalize_bars(result)


def _bars_futures(
    symbol: str, contract_code: str | None, market_id: int | None, period: str, count: int
) -> pd.DataFrame | dict:
    """期货实时 K 线（暂不支持，触发回退离线）。"""
    logger.debug("期货实时 K 线暂不支援 [%s %s]，触发回退离线", symbol, contract_code)
    return {"error": "exhq_unavailable"}


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def _normalize_bars(raw: list) -> pd.DataFrame:
    """将 mootdx 返回的 bar OrderedDict 列表归一化为标准 DataFrame。

    格式与 data_provider.fetch_kline() 完全一致：
        [bob, open, high, low, close, volume]
    """
    if not raw:
        return pd.DataFrame(columns=["bob", "open", "high", "low", "close", "volume"])

    rows = []
    for bar in raw:
        rows.append({
            "bob": bar.get("datetime", ""),
            "open": float(bar.get("open", 0.0)),
            "high": float(bar.get("high", 0.0)),
            "low": float(bar.get("low", 0.0)),
            "close": float(bar.get("close", 0.0)),
            "volume": float(bar.get("vol", 0.0)),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["bob"] = pd.to_datetime(df["bob"], errors="coerce")
    # 去除时区信息（与 data_provider 的 _cached_read 返回的 naive datetime 一致）
    if df["bob"].dt.tz is not None:
        df["bob"] = df["bob"].dt.tz_localize(None)
    df = df.sort_values("bob").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 健康检查 & 断开
# ---------------------------------------------------------------------------


def health() -> dict:
    """返回连接健康状态。"""
    return _manager.health()


def disconnect_all() -> None:
    """断开所有连接（进程退出时调用）。"""
    _manager.disconnect_all()
