"""
实时 TDX 行情数据层：封装 tdxpy 网络 API，提供线程安全的连接管理和数据获取。

A 股走 TdxHq_API（端口 7709），期货走 TdxExHq_API（端口 7720）。
扩展行情接口可能不稳定，失败时返回 error 供上层回退到离线数据。
"""
import logging
import random
import threading
from datetime import datetime

import pandas as pd
from tdxpy.exhq import TdxExHq_API
from tdxpy.hq import TdxHq_API
from tdxpy.constants import TDXParams, hq_hosts

from config import (
    get_market_type,
    get_stock_exchange_and_code,
    get_symbols_config,
    resolve_symbol,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 周期 → tdxpy K 线类型常量映射
# ---------------------------------------------------------------------------

_PERIOD_CATEGORY_HQ: dict[str, int] = {
    "1m": TDXParams.KLINE_TYPE_1MIN,       # 8
    "5m": TDXParams.KLINE_TYPE_5MIN,       # 0
    "15m": TDXParams.KLINE_TYPE_15MIN,     # 1
    "30m": TDXParams.KLINE_TYPE_30MIN,     # 2
    "60m": TDXParams.KLINE_TYPE_1HOUR,     # 3
    "1h": TDXParams.KLINE_TYPE_1HOUR,      # 3
    "1d": TDXParams.KLINE_TYPE_DAILY,      # 4
}

_PERIOD_CATEGORY_EXHQ: dict[str, int] = {
    "1m": TDXParams.KLINE_TYPE_EXHQ_1MIN,  # 7（扩展行情 1 分钟专用）
    "5m": TDXParams.KLINE_TYPE_5MIN,       # 0
    "15m": TDXParams.KLINE_TYPE_15MIN,     # 1
    "30m": TDXParams.KLINE_TYPE_30MIN,     # 2
    "60m": TDXParams.KLINE_TYPE_1HOUR,     # 3
    "1h": TDXParams.KLINE_TYPE_1HOUR,      # 3
    "1d": TDXParams.KLINE_TYPE_DAILY,      # 4
}

# 默认扩展行情服务器列表（tdxpy 没有内置 exhq_hosts）
_DEFAULT_EXHQ_HOSTS: list[tuple[str, str, int]] = [
    ("扩展行情1", "112.74.214.43", 7720),
    ("扩展行情2", "124.160.88.252", 7720),
]

VALID_PERIODS = set(_PERIOD_CATEGORY_HQ.keys())


# ---------------------------------------------------------------------------
# 连接管理器
# ---------------------------------------------------------------------------

class TdxConnectionManager:
    """管理 TdxHq_API（A 股）和 TdxExHq_API（期货）的连接生命周期。"""

    def __init__(self) -> None:
        # A 股标准行情
        self._hq_api: TdxHq_API | None = None
        self._hq_lock = threading.Lock()
        self._hq_server_idx = random.randint(0, len(hq_hosts) - 1)

        # 期货扩展行情
        self._exhq_api: TdxExHq_API | None = None
        self._exhq_lock = threading.Lock()
        self._exhq_hosts = self._load_exhq_hosts()
        self._exhq_server_idx = random.randint(0, max(0, len(self._exhq_hosts) - 1))

    # -- A 股 (HQ) ----------------------------------------------------------

    def _ensure_hq(self) -> TdxHq_API:
        """确保 HQ 连接存活，失败时轮转服务器重连。"""
        if self._hq_api is not None:
            try:
                # 检查 socket 是否已关闭
                if not self._hq_api.client._closed:
                    return self._hq_api
            except Exception:
                pass
            self._hq_api = None

        # 轮转尝试连接
        tried = 0
        while tried < min(5, len(hq_hosts)):
            name, ip, port = hq_hosts[self._hq_server_idx % len(hq_hosts)]
            try:
                api = TdxHq_API(multithread=True, heartbeat=True, auto_retry=True)
                api.connect(ip, port, time_out=5.0)
                logger.info("HQ 已连接: %s (%s:%d)", name, ip, port)
                self._hq_api = api
                return api
            except Exception:
                logger.debug("HQ 连接失败: %s (%s:%d)，尝试下一个", name, ip, port)
                self._hq_server_idx = (self._hq_server_idx + 1) % len(hq_hosts)
                tried += 1

        raise ConnectionError("所有 HQ 服务器均不可用")

    def _with_hq(self, fn):
        """加锁执行 HQ API 调用，异常时尝试重连一次。"""
        with self._hq_lock:
            try:
                api = self._ensure_hq()
                return fn(api)
            except Exception:
                # 连接可能已断，重置后重试一次
                self._hq_api = None
                try:
                    api = self._ensure_hq()
                    return fn(api)
                except Exception:
                    logger.exception("HQ API 调用失败（重试后）")
                    raise

    # -- 期货 (ExHQ) --------------------------------------------------------

    def _load_exhq_hosts(self) -> list[tuple[str, str, int]]:
        """从 symbols.json 或默认值加载扩展行情服务器列表。"""
        cfg = get_symbols_config()
        hosts = cfg.get("exhq_hosts")
        if hosts and isinstance(hosts, list):
            return [tuple(h) for h in hosts]
        return list(_DEFAULT_EXHQ_HOSTS)

    def _ensure_exhq(self) -> TdxExHq_API:
        """确保 ExHQ 连接存活，失败时轮转服务器重连。"""
        if self._exhq_api is not None:
            try:
                if not self._exhq_api.client._closed:
                    return self._exhq_api
            except Exception:
                pass
            self._exhq_api = None

        if not self._exhq_hosts:
            raise ConnectionError("未配置扩展行情服务器（exhq_hosts）")

        tried = 0
        while tried < min(5, len(self._exhq_hosts)):
            name, ip, port = self._exhq_hosts[self._exhq_server_idx % len(self._exhq_hosts)]
            try:
                api = TdxExHq_API(multithread=True, heartbeat=True, auto_retry=True)
                api.connect(ip, port, time_out=5.0)
                logger.info("ExHQ 已连接: %s (%s:%d)", name, ip, port)
                self._exhq_api = api
                return api
            except Exception:
                logger.debug("ExHQ 连接失败: %s (%s:%d)，尝试下一个", name, ip, port)
                self._exhq_server_idx = (self._exhq_server_idx + 1) % len(self._exhq_hosts)
                tried += 1

        raise ConnectionError("所有 ExHQ 服务器均不可用")

    def _with_exhq(self, fn):
        """加锁执行 ExHQ API 调用，异常时尝试重连一次。"""
        with self._exhq_lock:
            try:
                api = self._ensure_exhq()
                return fn(api)
            except Exception:
                self._exhq_api = None
                try:
                    api = self._ensure_exhq()
                    return fn(api)
                except Exception:
                    logger.exception("ExHQ API 调用失败（重试后）")
                    raise

    # -- 公开方法 ------------------------------------------------------------

    def health(self) -> dict:
        """返回连接健康状态。"""
        hq_ok = False
        exhq_ok = False
        hq_info = {}
        exhq_info = {}

        with self._hq_lock:
            if self._hq_api is not None:
                try:
                    hq_ok = not self._hq_api.client._closed
                except Exception:
                    pass
            hq_info = {"connected": hq_ok}

        with self._exhq_lock:
            if self._exhq_api is not None:
                try:
                    exhq_ok = not self._exhq_api.client._closed
                except Exception:
                    pass
            exhq_info = {"connected": exhq_ok, "hosts_count": len(self._exhq_hosts)}

        return {"hq": hq_info, "exhq": exhq_info}

    def disconnect_all(self) -> None:
        """断开所有连接（进程退出时调用）。"""
        with self._hq_lock:
            if self._hq_api is not None:
                try:
                    self._hq_api.disconnect()
                except Exception:
                    pass
                self._hq_api = None

        with self._exhq_lock:
            if self._exhq_api is not None:
                try:
                    self._exhq_api.disconnect()
                except Exception:
                    pass
                self._exhq_api = None


# 模块级单例
_manager = TdxConnectionManager()


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def get_realtime_quote(symbol: str, contract: str | None = None) -> dict:
    """获取实时报价。

    返回 dict:
        symbol, contract, price, open, high, low, volume,
        bid1, ask1, bid1_vol, ask1_vol, time, source,
        error (仅失败时出现)
    """
    market_id, default_code, _ = resolve_symbol(symbol)
    if market_id is None:
        return {"symbol": symbol, "error": "unknown_symbol", "source": None}

    market_type = get_market_type(symbol)
    effective_code = contract or default_code

    if market_type == "stock":
        return _quote_stock(symbol, effective_code)
    elif market_type == "futures":
        return _quote_futures(symbol, effective_code, market_id)
    else:
        return {"symbol": symbol, "error": "unknown_market_type", "source": None}


def _quote_stock(symbol: str, stock_code: str) -> dict:
    """A 股实时报价（标准行情 7709）。"""
    pair = get_stock_exchange_and_code(symbol)
    if pair is None:
        # fallback：用 contract 参数作为 code
        if "." in stock_code:
            exchange, code = stock_code.split(".", 1)
        else:
            return {"symbol": symbol, "error": "invalid_stock_code", "source": None}
    else:
        exchange, code = pair

    market = 1 if exchange == "sh" else 0  # tdxpy: 1=上海, 0=深圳

    try:
        result = _manager._with_hq(
            lambda api: api.get_security_quotes((market, code))
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


def _quote_futures(symbol: str, contract_code: str, market_id: int) -> dict:
    """期货实时报价（扩展行情 7720）。失败返回 error 供上层回退。"""
    try:
        result = _manager._with_exhq(
            lambda api: api.get_instrument_quote(market_id, contract_code)
        )
    except Exception as e:
        logger.warning("ExHQ 报价失败 [%s]: %s", symbol, e)
        return {"symbol": symbol, "error": "exhq_unavailable", "source": None}

    if result is None:
        return {"symbol": symbol, "error": "exhq_no_data", "source": None}

    q = result
    return {
        "symbol": symbol,
        "contract": contract_code,
        "price": q.get("price", 0.0),
        "open": q.get("open", 0.0),
        "high": q.get("high", 0.0),
        "low": q.get("low", 0.0),
        "volume": q.get("zongliang", 0),
        "bid1": q.get("bid1", 0.0),
        "ask1": q.get("ask1", 0.0),
        "bid1_vol": q.get("bid_vol1", 0),
        "ask1_vol": q.get("ask_vol1", 0),
        "last_close": q.get("pre_close", 0.0),
        "time": datetime.now().strftime("%H:%M:%S"),
        "source": "exhq",
    }


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

    market_id, default_code, _ = resolve_symbol(symbol)
    if market_id is None:
        return {"error": f"unknown_symbol: {symbol}"}

    market_type = get_market_type(symbol)
    effective_code = contract or default_code
    count = min(count, TDXParams.MAX_KLINE_COUNT)  # tdxpy 限制 800

    if market_type == "stock":
        return _bars_stock(symbol, effective_code, period, count)
    elif market_type == "futures":
        return _bars_futures(symbol, effective_code, market_id, period, count)
    else:
        return {"error": f"unknown_market_type: {market_type}"}


def _bars_stock(symbol: str, stock_code: str, period: str, count: int) -> pd.DataFrame | dict:
    """A 股实时 K 线。"""
    pair = get_stock_exchange_and_code(symbol)
    if pair is None:
        if "." in stock_code:
            exchange, code = stock_code.split(".", 1)
        else:
            return {"error": f"invalid_stock_code: {stock_code}"}
    else:
        exchange, code = pair

    market = 1 if exchange == "sh" else 0
    category = _PERIOD_CATEGORY_HQ.get(period)
    if category is None:
        return {"error": f"unsupported_period_for_hq: {period}"}

    try:
        result = _manager._with_hq(
            lambda api: api.get_security_bars(category, market, code, start=0, count=count)
        )
    except Exception as e:
        return {"error": str(e)}

    if not result:
        return pd.DataFrame(columns=["bob", "open", "high", "low", "close", "volume"])

    return _normalize_bars(result)


def _bars_futures(
    symbol: str, contract_code: str, market_id: int, period: str, count: int,
) -> pd.DataFrame | dict:
    """期货实时 K 线。失败返回 error 供回退。"""
    category = _PERIOD_CATEGORY_EXHQ.get(period)
    if category is None:
        return {"error": f"unsupported_period_for_exhq: {period}"}

    try:
        result = _manager._with_exhq(
            lambda api: api.get_instrument_bars(category, market_id, contract_code, start=0, count=count)
        )
    except Exception as e:
        logger.warning("ExHQ K 线失败 [%s]: %s", symbol, e)
        return {"error": "exhq_unavailable"}

    if not result:
        return pd.DataFrame(columns=["bob", "open", "high", "low", "close", "volume"])

    return _normalize_bars(result)


def _normalize_bars(raw: list) -> pd.DataFrame:
    """将 tdxpy 返回的 bar 列表归一化为 DataFrame。"""
    if not raw:
        return pd.DataFrame(columns=["bob", "open", "high", "low", "close", "volume"])
    rows = []
    for bar in raw:
        rows.append({
            "bob": bar.get("datetime", ""),
            "open": bar.get("open", 0.0),
            "high": bar.get("high", 0.0),
            "low": bar.get("low", 0.0),
            "close": bar.get("close", 0.0),
            "volume": bar.get("vol", 0.0),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["bob"] = pd.to_datetime(df["bob"], errors="coerce")
        df = df.sort_values("bob").reset_index(drop=True)
    return df


def health() -> dict:
    """返回连接健康状态（供 /api/realtime/health 调用）。"""
    return _manager.health()


def disconnect_all() -> None:
    """断开所有连接（进程退出时调用）。"""
    _manager.disconnect_all()
