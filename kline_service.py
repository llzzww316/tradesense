"""
K 线业务服务层：把品种解析 / 合约校验 / replay_data 编排 / EMA / JSON 友好输出
集中在这里，由 server.py 与 mcp_server.py 共同调用。

异常语义：
- UnknownSymbolError  —— 品种未知（server 映射 404）
- ContractMismatchError —— 合约代码与品种前缀不符（server 映射 400）
- ContractNotFoundError —— 本机 vipdoc 中没有该合约数据（server 映射 404）
- NoDataError         —— 数据为空 / 区间无数据（server 映射 404）
- InvalidRequestError —— 其他入参错误（server 映射 400）
"""
from __future__ import annotations

import logging
import pandas as pd

from config import get_symbols_config, resolve_symbol
from data_provider import (
    VALID_PERIODS,
    KlineReadError,
    contract_has_any_data_file,
    fetch_replay_data,
    futures_prefix_from_mootdx_code,
    scan_contract_codes,
    fetch_kline,
)
import realtime_provider as rp

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """业务层错误基类。"""


class UnknownSymbolError(ServiceError):
    pass


class ContractMismatchError(ServiceError):
    pass


class ContractNotFoundError(ServiceError):
    pass


class NoDataError(ServiceError):
    pass


class InvalidRequestError(ServiceError):
    pass


class DataReadError(ServiceError):
    pass


class RealtimeDataError(ServiceError):
    """实时数据不可用（网络错误、扩展行情失效等）。"""
    pass


def _calculate_ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False).mean()


def _validate_timestamp(value: str | None, field_name: str) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        return pd.Timestamp(value)
    except Exception as exc:
        raise InvalidRequestError(f"{field_name} 格式无效: {value}") from exc


def list_contracts(symbol: str) -> dict:
    """扫描本机 vipdoc，返回该品种下可选的通达信合约代码。"""
    market_id, configured_code, _ = resolve_symbol(symbol)
    if market_id is None or not configured_code:
        raise UnknownSymbolError(f"未知品种: {symbol}")

    prefix = futures_prefix_from_mootdx_code(configured_code)
    if not prefix:
        raise InvalidRequestError("无法从配置推导合约前缀，请检查 symbols.json 中的 mootdx_code")

    scanned = scan_contract_codes(market_id, prefix)
    cfg_u = configured_code.upper().strip()
    merged = sorted(set(scanned + ([cfg_u] if cfg_u else [])))

    if cfg_u in merged:
        default_contract = cfg_u
    elif merged:
        default_contract = merged[-1]
    else:
        default_contract = cfg_u

    return {
        "symbol": symbol,
        "market": market_id,
        "prefix": prefix,
        "contracts": merged,
        "default_contract": default_contract,
    }


def _resolve_effective_contract(symbol: str, contract: str | None) -> tuple[int, str, str]:
    """返回 (market_id, default_code, effective_code)；做完所有品种 / 合约校验。"""
    market_id, default_code, _display = resolve_symbol(symbol)
    if market_id is None or default_code is None:
        raise UnknownSymbolError(f"未知品种: {symbol}，请使用中文名（如 螺纹钢、PVC）")

    prefix = futures_prefix_from_mootdx_code(default_code)
    effective_code = str(default_code).strip().upper()
    if contract is not None and str(contract).strip():
        effective_code = str(contract).strip().upper()
        if prefix and not effective_code.startswith(prefix):
            raise ContractMismatchError(
                f"合约 {effective_code} 与品种前缀 {prefix} 不符，"
                "请重新选择或检查 symbols.json 中的 mootdx_code"
            )
        if not contract_has_any_data_file(market_id, effective_code):
            raise ContractNotFoundError(
                f"本机 vipdoc 中未找到合约 {effective_code} 的数据文件（请先下载扩展行情）"
            )
    return market_id, default_code, effective_code


def get_replay_payload(
    symbol: str,
    contract: str | None = None,
    display_period: str = "5m",
    step_period: str = "1m",
    count: int = 2000,
    ma_period: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    range_start: str | None = None,
    range_end: str | None = None,
) -> dict:
    """
    与旧 /api/replay_data 完全兼容的返回结构；失败抛业务异常由上层映射。
    """
    market_id, _default_code, effective_code = _resolve_effective_contract(symbol, contract)

    if count < 1:
        raise InvalidRequestError("count 必须大于 0")
    if ma_period < 1 or ma_period > 500:
        raise InvalidRequestError("ma_period 必须在 1 到 500 之间")
    if display_period not in VALID_PERIODS:
        raise InvalidRequestError(f"不支持的显示周期: {display_period}")
    if step_period not in VALID_PERIODS:
        raise InvalidRequestError(f"不支持的步进周期: {step_period}")
    if (range_start and not range_end) or (range_end and not range_start):
        raise InvalidRequestError("range_start 与 range_end 必须同时传入")

    start_ts = _validate_timestamp(start_date, "start_date")
    end_ts = _validate_timestamp(end_date, "end_date")
    range_start_ts = _validate_timestamp(range_start, "range_start")
    range_end_ts = _validate_timestamp(range_end, "range_end")

    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise InvalidRequestError("start_date 不能晚于 end_date")
    if range_start_ts is not None and range_end_ts is not None and range_start_ts > range_end_ts:
        raise InvalidRequestError("range_start 不能晚于 range_end")

    try:
        result = fetch_replay_data(
            market=market_id,
            symbol=effective_code,
            display_period=display_period,
            step_period=step_period,
            count=count,
            start_date=start_date,
            end_date=end_date,
            range_start=range_start,
            range_end=range_end,
        )
    except KlineReadError as exc:
        raise DataReadError("K 线文件读取/解析失败，请检查本地 VIPDOC 数据文件") from exc

    if "error" in result:
        # data_provider 的业务错误：空数据 / 区间无数据 / range 顺序错误
        raise NoDataError(result["error"])

    display_bars = result["display"]
    step_bars = result["step"]

    if display_bars.empty:
        raise NoDataError("No display period data")
    if step_bars.empty:
        raise NoDataError("No step period data")

    display_bars["ema"] = _calculate_ema(display_bars["close"], ma_period)
    display_bars["time"] = display_bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")
    step_bars["time"] = step_bars["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # NaN 转 None 以便 JSON 序列化为 null
    display_out = display_bars[["time", "open", "high", "low", "close", "ema"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "ema": object}
    )
    display_out = display_out.where(display_out.notna(), None)
    display_data = display_out.to_dict(orient="records")

    step_out = step_bars[["time", "open", "high", "low", "close"]].astype(
        {"open": float, "high": float, "low": float, "close": float}
    )
    step_data = step_out.to_dict(orient="records")

    symbols_map = get_symbols_config().get("symbols", {})
    symbol_code = symbols_map.get(symbol, {}).get("code", symbol)

    return {
        "symbol": symbol,
        "symbol_code": symbol_code,
        "contract": effective_code,
        "display": display_data,
        "step": step_data,
        "displayPeriod": display_period,
        "stepPeriod": step_period,
        "maPeriod": ma_period,
    }


def get_latest_price(symbol: str, contract: str | None = None) -> dict:
    """获取品种最新价（轻量，只读 1 根 1m K 线，不跑 EMA/双周期等重操作）。"""
    market_id, _default_code, effective_code = _resolve_effective_contract(symbol, contract)
    try:
        df = fetch_kline(market_id, effective_code, "1m", count=1)
    except KlineReadError as exc:
        raise DataReadError("K 线文件读取/解析失败，请检查本地 VIPDOC 数据文件") from exc
    if df.empty:
        raise NoDataError(f"无最新 K 线数据: {symbol}")
    row = df.iloc[-1]
    t = row["bob"]
    ts = t.strftime("%Y-%m-%d %H:%M:%S") if hasattr(t, "strftime") else str(t)

    symbols_map = get_symbols_config().get("symbols", {})
    symbol_code = symbols_map.get(symbol, {}).get("code", symbol)

    return {
        "symbol": symbol,
        "symbol_code": symbol_code,
        "contract": effective_code,
        "price": float(row["close"]),
        "time": ts,
    }


def search_symbols(q: str) -> list[dict]:
    q = (q or "").lower()
    results = []
    for name, info in get_symbols_config().get("symbols", {}).items():
        code = info["code"]
        if q in name.lower() or q in code.lower():
            results.append({
                "name": name,
                "code": code,
                "tick_value": info.get("tick_value", 10),
            })
    return results


# ---------------------------------------------------------------------------
# 实时数据服务
# ---------------------------------------------------------------------------


def get_realtime_price(symbol: str, contract: str | None = None) -> dict:
    """获取实时报价（走网络）。ExHQ 不可用时回退到离线 VIPDOC 最新数据。"""
    from config import get_market_type

    market_id, _default_code, effective_code = _resolve_effective_contract(symbol, contract)
    market_type = get_market_type(symbol)

    # 尝试网络实时报价
    quote = rp.get_realtime_quote(symbol, contract=effective_code)
    if not quote.get("error"):
        quote["symbol_code"] = _get_symbol_code(symbol)
        return quote

    # 回退到离线
    if market_type == "futures" and quote.get("error") in ("exhq_unavailable", "exhq_no_data"):
        logger.info("实时报价不可用，回退离线数据: %s", symbol)
        return get_latest_price(symbol, contract=effective_code)

    # A 股或未知错误：仍尝试抛出
    if quote.get("error") == "unknown_symbol":
        raise UnknownSymbolError(f"未知品种: {symbol}")
    raise RealtimeDataError(f"实时报价失败: {quote.get('error')}")


def get_realtime_payload(
    symbol: str,
    contract: str | None = None,
    period: str = "5m",
    count: int = 200,
    ma_period: int = 20,
) -> dict:
    """获取实时 K 线 + EMA。ExHQ 不可用时回退到离线 VIPDOC。"""
    from config import get_market_type

    if period not in VALID_PERIODS:
        raise InvalidRequestError(f"不支持的周期: {period}")
    if count < 1:
        raise InvalidRequestError("count 必须大于 0")

    market_id, _default_code, effective_code = _resolve_effective_contract(symbol, contract)
    market_type = get_market_type(symbol)

    # 尝试网络实时 K 线
    bars = rp.get_realtime_bars(symbol, contract=effective_code, period=period, count=count)

    is_error = isinstance(bars, dict) and bars.get("error")
    if is_error:
        err = bars["error"]
        if market_type == "futures" and err == "exhq_unavailable":
            logger.info("实时 K 线不可用，回退离线数据: %s", symbol)
            df = fetch_kline(market_id, effective_code, period, count=count)
        else:
            raise RealtimeDataError(f"实时 K 线失败: {err}")
    else:
        df = bars

    if df.empty:
        raise NoDataError(f"无实时 K 线数据: {symbol}")

    # 计算 EMA
    df["ema"] = _calculate_ema(df["close"], ma_period)
    df["time"] = df["bob"].dt.strftime("%Y-%m-%d %H:%M:%S")

    bars_out = df[["time", "open", "high", "low", "close", "ema"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "ema": object}
    )
    bars_out = bars_out.where(bars_out.notna(), None)
    display_data = bars_out.to_dict(orient="records")

    return {
        "symbol": symbol,
        "symbol_code": _get_symbol_code(symbol),
        "contract": effective_code,
        "display": display_data,
        "period": period,
        "maPeriod": ma_period,
        "source": "realtime" if not is_error else "offline_fallback",
    }


def _get_symbol_code(symbol: str) -> str:
    symbols_map = get_symbols_config().get("symbols", {})
    return symbols_map.get(symbol, {}).get("code", symbol)
