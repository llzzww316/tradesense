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
    contract_has_any_data_file,
    fetch_replay_data,
    futures_prefix_from_mootdx_code,
    scan_contract_codes,
)

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


def _calculate_ema(closes: pd.Series, period: int) -> pd.Series:
    if len(closes) < period:
        return pd.Series([None] * len(closes), index=closes.index)
    return closes.ewm(span=period, adjust=False).mean()


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
