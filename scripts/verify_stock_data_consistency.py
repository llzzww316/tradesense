"""
A 股实时数据 vs VIPDOC 离线数据一致性验证脚本。

对比两个数据源的重叠日线（bob 完全对齐），逐字段比较 open/high/low/close/volume，
报告偏差条数和偏差率。

用法：
  uv run python scripts/verify_stock_data_consistency.py [--period 1d] [--count 200] [--symbol 茅台]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 把项目根目录加到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from config import get_symbols_config, get_stock_exchange_and_code
from data_provider import fetch_stock_kline
from realtime_provider import get_realtime_bars

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# 字段对照
FLOAT_FIELDS = ["open", "high", "low", "close"]
INT_FIELDS = ["volume"]


def get_stock_list() -> list[tuple[str, str, str, str]]:
    """返回 [(display_name, exchange, code, raw_code), ...] 全部 A 股品种。"""
    cfg = get_symbols_config().get("symbols", {})
    stocks = []
    for name, info in cfg.items():
        if info.get("market_type") != "stock":
            continue
        # raw_code 如 "SH.600519"
        raw_code = info.get("code", "")
        ex, code = get_stock_exchange_and_code(name)
        stocks.append((name, ex, code, raw_code))
    return stocks


def verify_one_stock(
    name: str, exchange: str, code: str, period: str, count: int, raw_code: str = ""
) -> dict | None:
    """
    对比一个品种的实时数据 vs 离线数据。
    返回统计 dict，如无法获取则返回 None。
    """
    # --- 1. 取离线数据（VIPDOC 本地文件） ---
    try:
        offline = fetch_stock_kline(exchange, code, period, count=count)
    except Exception as e:
        logger.warning("[%s] 离线数据失败: %s", name, e)
        return None

    if offline.empty:
        logger.info("[%s] 离线数据为空", name)
        return None

    # --- 2. 取实时数据（mootdx 网络 API） ---
    try:
        realtime = get_realtime_bars(name, period=period, count=count)
    except Exception as e:
        logger.warning("[%s] 实时数据失败: %s", name, e)
        return None

    if isinstance(realtime, dict) and realtime.get("error"):
        logger.info("[%s] 实时数据不可用: %s", name, realtime["error"])
        return None

    # realtime_provider 现在返回标准 DataFrame，和 VIPDOC 格式一致
    # 但需要注意：当前交易日实时数据可能比 VIPDOC 更新，
    # 所以比对前要过滤掉实时数据中最新的一根 K 线（如果它不在 VIPDOC 里）
    realtime = realtime.tail(count)
    if realtime.empty:
        return None

    # --- 3. 对齐时间轴 ---
    offline_s = offline[["bob"] + FLOAT_FIELDS + INT_FIELDS].copy()
    realtime_s = realtime[["bob"] + FLOAT_FIELDS + INT_FIELDS].copy()

    offline_s["bob"] = pd.to_datetime(offline_s["bob"]).dt.tz_localize(None).dt.normalize()
    realtime_s["bob"] = pd.to_datetime(realtime_s["bob"]).dt.tz_localize(None).dt.normalize()

    # 取交集
    merged = pd.merge(offline_s, realtime_s, on="bob", suffixes=("_off", "_rt"), how="inner")

    if merged.empty:
        logger.info("[%s] 离线与实时数据无重叠时间点", name)
        return {
            "name": name,
            "code": code,
            "offline_bars": len(offline_s),
            "realtime_bars": len(realtime_s),
            "overlap": 0,
            "mismatch": {},
            "exact_match_pct": 0.0,
        }

    # --- 4. 逐字段比较 ---
    mismatch = {}
    details = []
    for field in FLOAT_FIELDS + INT_FIELDS:
        col_off = f"{field}_off"
        col_rt = f"{field}_rt"
        if field in INT_FIELDS:
            diff = (merged[col_off].fillna(0).round(0) - merged[col_rt].fillna(0).round(0)).abs()
            mismatch_count = int((diff > 5).sum())  # volume 允许 ±5（两数据源取整方式不同）
            mismatch_threshold = 5
            # 但统计偏差细节还是用 >1 来报告
            detail_count = int((diff > 1).sum())
        else:
            diff = (merged[col_off].fillna(0.0) - merged[col_rt].fillna(0.0)).abs()
            mismatch_count = int((diff > 0.005).sum())
            mismatch_threshold = 0.005
            detail_count = mismatch_count
        mismatch_count = int((diff > 0.005).sum())
        mismatch[field] = mismatch_count
        if detail_count > 0:
            bad = merged[diff > (1 if field in INT_FIELDS else 0.005)][["bob", col_off, col_rt]].head(5)
            details.append(field)
            for _, row in bad.iterrows():
                logger.debug("  %s %s off=%.2f rt=%.2f", field, row["bob"], row[col_off], row[col_rt])

    total_overlap = len(merged)
    total_mismatch = max(mismatch.values()) if mismatch else 0
    exact_match_pct = round((1 - total_mismatch / total_overlap) * 100, 2) if total_overlap else 0.0

    return {
        "name": name,
        "code": code,
        "offline_bars": len(offline_s),
        "realtime_bars": len(realtime_s),
        "overlap": total_overlap,
        "mismatch": mismatch,
        "exact_match_pct": exact_match_pct,
        "detail_fields": details[:3],
    }


def _summarize_mismatch(r: dict) -> str:
    mm = r.get("mismatch", {})
    parts = [f"{k}:{v}" for k, v in mm.items() if v > 0]
    return " | ".join(parts) if parts else "无"


def main():
    parser = argparse.ArgumentParser(description="验证 A 股实时与离线数据一致性")
    parser.add_argument("--period", default="1d", help="对比周期（默认 1d）")
    parser.add_argument("--count", default=200, type=int, help="获取条数（默认 200）")
    parser.add_argument("--symbol", default=None, help="只验证指定品种名（可选）")
    args = parser.parse_args()

    stocks = get_stock_list()
    if args.symbol:
        stocks = [(n, e, c, r) for n, e, c, r in stocks if args.symbol in n]
        if not stocks:
            print(f"未找到匹配 [{args.symbol}] 的品种")
            return

    print(f"\n{'='*70}")
    print(f"  实时数据 vs VIPDOC 离线数据 — 一致性验证")
    print(f"  周期: {args.period}  条数: {args.count}  品种数: {len(stocks)}")
    print(f"{'='*70}\n")

    results = []
    for name, exchange, code, raw_code in stocks:
        print(f"  >> {name} ({code}) ... ", end="", flush=True)
        r = verify_one_stock(name, exchange, code, args.period, args.count, raw_code)
        if r is None:
            print("跳过（数据不可用）")
            continue

        n_mismatch = max(r["mismatch"].values()) if r["mismatch"] else 0
        if n_mismatch == 0:
            print(f"V 完全一致（{r['overlap']} 条重叠）")
        else:
            print(f"X 偏差 {n_mismatch}/{r['overlap']} 条 ({100-n_mismatch/r['overlap']*100:.1f}% 一致)")
        results.append(r)

    if not results:
        print("\n无有效对比结果，请检查网络连接和本地 VIPDOC 数据。")
        return

    # --- 汇总 ---
    print(f"\n{'='*70}")
    print(f"  汇总（表头: 品种 / 代码 / 重叠 / 一致率 / 偏差字段）")
    print(f"{'='*70}\n")
    for r in results:
        mm = _summarize_mismatch(r)
        pct = r["exact_match_pct"]
        flag = "V" if pct == 100 else "X"
        print(f"  {flag} {r['name'][:8]:8s} {r['code']:10s}  {r['overlap']:4d} 条  {pct:6.2f}%  [{mm}]")

    perfect = sum(1 for r in results if r["exact_match_pct"] == 100.0)
    total = len(results)
    print(f"\n  完全一致: {perfect}/{total}")
    avg_pct = sum(r["exact_match_pct"] for r in results) / total if total else 0
    print(f"  平均一致率: {avg_pct:.2f}%")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
