#!/usr/bin/env python3
"""形态扫描主脚本：批量拉取日线K线 → 识别看涨形态 → 输出结果。

用法：
    uv run python -m patterns.scanner
    uv run python -m patterns.scanner --max-workers 4 --count 120
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from tdxpy.hq import TdxHq_API
from tdxpy.constants import hq_hosts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.models import Bar
from patterns.geometry import detect_bull_flag, detect_asc_triangle
from patterns.breakout import detect_breakout, detect_breakout_pullback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 股票列表解析
# ---------------------------------------------------------------------------

_STOCK_ROW_RE = re.compile(
    r"^\|\s*\d+\s*\|\s*(SH\.\d{6}|SZ\.\d{6})\s*\|\s*(.+?)\s*\|\s*([\d.]+)\s*\|$",
    re.MULTILINE,
)


def parse_stock_list(md_path: str) -> list[dict]:
    """从 Markdown 表格中解析股票列表。"""
    text = Path(md_path).read_text(encoding="utf-8")
    stocks = []
    for m in _STOCK_ROW_RE.finditer(text):
        code = m.group(1)
        name = m.group(2).strip()
        price = float(m.group(3))
        exchange, symbol = code.split(".")
        stocks.append({
            "code": code,
            "name": name,
            "price": price,
            "market": 1 if exchange == "SH" else 0,  # tdxpy: 1=上海, 0=深圳
            "symbol": symbol,
        })
    return stocks


# ---------------------------------------------------------------------------
# K 线拉取
# ---------------------------------------------------------------------------

def _create_hq_connection() -> Optional[TdxHq_API]:
    """创建一个 HQ 连接，返回 TdxHq_API 或 None。"""
    api = TdxHq_API(multithread=True, heartbeat=True, auto_retry=True)
    import random
    indices = list(range(len(hq_hosts)))
    random.shuffle(indices)
    for idx in indices[:5]:
        name, ip, port = hq_hosts[idx]
        try:
            api.connect(ip, port, time_out=5.0)
            # 测试连接
            test = api.get_security_count(0)
            if test and test > 0:
                logger.debug("HQ 已连接: %s", name)
                return api
        except Exception:
            continue
    return None


def fetch_daily_bars(
    api: TdxHq_API, market: int, symbol: str, count: int = 120
) -> Optional[list[Bar]]:
    """用单个 HQ 连接拉取日线 K 线，返回 Bar 列表。"""
    try:
        result = api.get_security_bars(
            4,  # KLINE_TYPE_DAILY
            market,
            symbol,
            0,
            count,
        )
    except Exception:
        return None

    if not result:
        return None

    bars = []
    for r in result:
        try:
            bars.append(Bar(
                time=r.get("datetime", ""),
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=float(r.get("vol", 0)),
            ))
        except (ValueError, TypeError):
            continue
    return bars if bars else None


def fetch_bars_batch(
    stocks: list[dict],
    count: int = 120,
    max_workers: int = 4,
) -> dict[str, list[Bar]]:
    """并发拉取多只股票的日线 K 线。

    Returns:
        {code: [Bar, ...]} — 成功拉取的股票
    """
    results: dict[str, list[Bar]] = {}
    total = len(stocks)
    done = [0]

    def _worker(batch: list[dict]) -> list[tuple[str, Optional[list[Bar]]]]:
        api = _create_hq_connection()
        if api is None:
            return [(s["code"], None) for s in batch]
        out = []
        for s in batch:
            bars = fetch_daily_bars(api, s["market"], s["symbol"], count)
            out.append((s["code"], bars))
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == total:
                pct = done[0] / total * 100
                print(f"\r  拉取进度: {done[0]}/{total} ({pct:.0f}%)", end="", flush=True)
        try:
            api.disconnect()
        except Exception:
            pass
        return out

    # 分片
    chunk_size = max(1, total // max_workers)
    chunks = [stocks[i:i + chunk_size] for i in range(0, total, chunk_size)]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, chunk) for chunk in chunks]
        for future in as_completed(futures):
            try:
                for code, bars in future.result():
                    if bars:
                        results[code] = bars
            except Exception:
                logger.exception("批次拉取异常")

    print()  # 换行
    return results


# ---------------------------------------------------------------------------
# 形态检测
# ---------------------------------------------------------------------------

def scan_patterns(bars: list[Bar]) -> list[dict]:
    """对单只股票的 K 线列表检测所有看涨形态。"""
    hits = []

    for detector in [detect_bull_flag, detect_asc_triangle,
                     detect_breakout, detect_breakout_pullback]:
        result = detector(bars)
        if result and result.get("detected"):
            hits.append(result)

    return hits


def score_hits(hits: list[dict]) -> int:
    """多形态叠加评分。"""
    if not hits:
        return 0
    total = max(h["score"] for h in hits)
    # 多形态叠加
    total += 10 * (len(hits) - 1)
    return min(100, total)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def write_result(
    results: list[dict],
    total_scanned: int,
    output_path: str,
    elapsed: float,
) -> None:
    """将扫描结果写入 Markdown 文档。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("# 看涨形态扫描结果\n")
    lines.append(f"**扫描时间**: {now}")
    lines.append(f"**扫描范围**: 沪深主板 < 30 元（非ST/退市/周期）共 {total_scanned} 只")
    lines.append(f"**成功拉取K线**: {total_scanned} 只")
    lines.append(f"**命中数量**: {len(results)} 只")
    lines.append(f"**耗时**: {elapsed:.1f} 秒\n")

    # 统计各形态命中数
    pattern_counts: dict[str, int] = {}
    for r in results:
        for h in r["hits"]:
            p = h["pattern"]
            pattern_counts[p] = pattern_counts.get(p, 0) + 1

    lines.append("## 形态分布\n")
    lines.append("| 形态 | 命中数 |")
    lines.append("|------|--------|")
    for p, c in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {p} | {c} 只 |")
    lines.append("")

    # 结果表
    lines.append("## 扫描结果\n")
    lines.append("| 排名 | 代码 | 名称 | 现价 | 命中形态 | 评分 | 说明 |")
    lines.append("|------|------|------|------|----------|------|------|")

    for i, r in enumerate(results, 1):
        pattern_names = " + ".join(h["pattern"] for h in r["hits"])
        details = "; ".join(h["detail"] for h in r["hits"])
        lines.append(
            f"| {i} | {r['code']} | {r['name']} | {r['price']:.2f} "
            f"| {pattern_names} | {r['score']} | {details} |"
        )

    lines.append("")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n结果已保存: {output_path}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="看涨形态扫描器")
    parser.add_argument(
        "--stock-list",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "stock_scan_low_price.md"),
        help="股票列表 Markdown 文件路径",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "pattern_scan_result.md"),
        help="输出结果 Markdown 文件路径",
    )
    parser.add_argument("--count", type=int, default=120, help="每只股票拉取的日线K线数量")
    parser.add_argument("--max-workers", type=int, default=4, help="并发线程数（=HQ连接数）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # 1. 解析股票列表
    print(f"正在解析股票列表: {args.stock_list}")
    stocks = parse_stock_list(args.stock_list)
    print(f"共 {len(stocks)} 只股票\n")

    # 2. 并发拉取K线
    print(f"正在拉取日线K线（{args.max_workers}线程并发）...")
    t0 = time.time()
    bars_data = fetch_bars_batch(stocks, count=args.count, max_workers=args.max_workers)
    fetch_time = time.time() - t0
    print(f"成功拉取 {len(bars_data)}/{len(stocks)} 只（{fetch_time:.1f}s）\n")

    # 3. 形态检测
    print("正在识别看涨形态...")
    stock_info = {s["code"]: s for s in stocks}
    scan_results = []
    t1 = time.time()
    for code, bars in bars_data.items():
        hits = scan_patterns(bars)
        if hits:
            info = stock_info.get(code, {})
            scan_results.append({
                "code": code,
                "name": info.get("name", ""),
                "price": info.get("price", bars[-1].close),
                "hits": hits,
                "score": score_hits(hits),
            })

    # 按评分排序
    scan_results.sort(key=lambda x: -x["score"])
    scan_time = time.time() - t1
    print(f"扫描完成: {len(scan_results)} 只命中（{scan_time:.1f}s）")

    # 4. 输出
    write_result(scan_results, len(stocks), args.output, fetch_time + scan_time)


if __name__ == "__main__":
    main()
