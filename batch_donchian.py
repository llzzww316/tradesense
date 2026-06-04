"""批量 Donchian 策略回测 — 30 只沪深主板股票。"""
import os
import sys
import random
import json
from datetime import datetime

import pandas as pd

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(__file__))

import backtest.strategies  # noqa: F401  触发策略注册
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from data_provider import fetch_stock_kline_by_date

TDX_DIR = os.environ.get("TRADESENSE_TDX_DIR", "C:/new_tdx")
os.environ["TRADESENSE_TDX_DIR"] = TDX_DIR

# ── 选股 ──────────────────────────────────────────────────────

def select_main_board_stocks(n: int = 30, seed: int = 42) -> list[str]:
    """随机选取 n 只沪深主板股票，返回 ['SH.600519', ...] 格式。"""
    sh_dir = f"{TDX_DIR}/vipdoc/sh/lday"
    sz_dir = f"{TDX_DIR}/vipdoc/sz/lday"

    sh_main = [f.replace(".day", "") for f in os.listdir(sh_dir)
               if f.startswith("sh6") and f.endswith(".day")
               and not f.startswith("sh688")]  # 排除科创板
    sz_main = [f.replace(".day", "") for f in os.listdir(sz_dir)
               if (f.startswith("sz000") or f.startswith("sz001"))
               and f.endswith(".day")]

    all_stocks = []
    for s in sh_main:
        all_stocks.append(f"SH.{s[2:]}")
    for s in sz_main:
        all_stocks.append(f"SZ.{s[2:]}")

    random.seed(seed)
    return random.sample(all_stocks, min(n, len(all_stocks)))


# ── 回测 ──────────────────────────────────────────────────────

def run_donchian_backtest(
    stock_code: str,
    start_date: str = "2024-01-01",
    end_date: str = "2026-06-04",
    initial_capital: float = 100_000.0,
    strategy_params: dict | None = None,
) -> dict:
    """对单只股票跑 Donchian 回测，返回结果摘要。"""
    exchange, symbol = stock_code.split(".")

    try:
        df = fetch_stock_kline_by_date(
            exchange=exchange.lower(), symbol=symbol, period="1d",
            start_date=start_date, end_date=end_date, count=None,
        )
    except Exception as e:
        return {"code": stock_code, "error": f"数据获取失败: {e}"}

    if df.empty:
        return {"code": stock_code, "error": "无数据"}

    cfg = BacktestConfig(
        symbol=stock_code, contract=stock_code, period="1d",
        start_date=start_date, end_date=end_date,
        initial_capital=initial_capital,
        tick_size=0.01, tick_value=0.01,
        margin_rate=1.0, fee_per_lot=0.0,
        slippage_ticks=0, intraday_only=False,
        strategy="donchian",
        strategy_params=strategy_params or {},
        instrument_type="stock",
        commission_rate=0.00025, stamp_tax_rate=0.001,
        transfer_fee_rate=0.00001, lot_size=100,
    )

    engine = BacktestEngine(cfg, df)
    result = engine.run()

    m = result.metrics
    return {
        "code": stock_code,
        "bars": len(df),
        "trades": m["total_trades"],
        "wins": m["winning_trades"],
        "losses": m["losing_trades"],
        "win_rate": round(m["win_rate"] * 100, 1),
        "profit_factor": round(m["profit_factor"], 2) if m["profit_factor"] != float("inf") else "∞",
        "total_return": round(m["total_return"] * 100, 2),
        "max_dd_pct": round(m["max_drawdown_pct"] * 100, 2),
        "sharpe": round(m["sharpe"], 2) if m["sharpe"] is not None else None,
        "avg_holding": round(m["avg_holding_bars"], 1),
        "final_equity": round(m["final_equity"], 0),
    }


# ── 主程序 ─────────────────────────────────────────────────────

def main():
    stocks = select_main_board_stocks(30)
    print(f"随机选取 {len(stocks)} 只沪深主板股票")
    print(f"回测区间: 2024-01-01 ~ 2026-06-04")
    print(f"策略: Donchian (entry=20, exit=10, atr_stop=2.0, trend_ema=50)")
    print(f"初始资金: 100,000")
    print("=" * 90)

    results = []
    for i, code in enumerate(stocks):
        sys.stdout.write(f"[{i+1:2d}/{len(stocks)}] {code} ... ")
        sys.stdout.flush()
        r = run_donchian_backtest(code)
        results.append(r)
        if "error" in r:
            print(f"[FAIL] {r['error']}")
        else:
            nt, wr, ret, pf = r["trades"], r["win_rate"], r["total_return"], r["profit_factor"]
            print(f"[OK]   {nt}笔 | 胜率{wr}% | 收益{ret}% | PF={pf}")

    # ── 汇总 ──
    print("=" * 90)
    valid = [r for r in results if "error" not in r and r["trades"] > 0]
    no_trade = [r for r in results if "error" not in r and r["trades"] == 0]
    errors = [r for r in results if "error" in r]

    print(f"\n=== 汇总统计 ===")
    print(f"  总计: {len(results)} 只 | 有交易: {len(valid)} | 无交易: {len(no_trade)} | 错误: {len(errors)}")

    if valid:
        avg_return = sum(r["total_return"] for r in valid) / len(valid)
        avg_winrate = sum(r["win_rate"] for r in valid) / len(valid)
        avg_pf = sum(r["profit_factor"] for r in valid if isinstance(r["profit_factor"], (int, float))) / max(1, len([r for r in valid if isinstance(r["profit_factor"], (int, float))]))
        avg_holding = sum(r["avg_holding"] for r in valid) / len(valid)
        profitable = [r for r in valid if r["total_return"] > 0]

        print(f"\n  有交易的 {len(valid)} 只股票:")
        print(f"    盈利: {len(profitable)} 只 ({len(profitable)/len(valid)*100:.0f}%)")
        print(f"    平均收益率: {avg_return:.2f}%")
        print(f"    平均胜率: {avg_winrate:.1f}%")
        print(f"    平均盈亏比(PF): {avg_pf:.2f}")
        print(f"    平均持仓天数: {avg_holding:.1f}")

        # Top 5 / Bottom 5
        valid_sorted = sorted(valid, key=lambda x: x["total_return"], reverse=True)
        print(f"\n  [TOP 5]")
        for r in valid_sorted[:5]:
            code, ret, wr, pf, nt = r["code"], r["total_return"], r["win_rate"], r["profit_factor"], r["trades"]
            print(f"    {code:12s} | 收益 {ret:>7.2f}% | 胜率 {wr:>5.1f}% | PF {pf} | {nt}笔")
        print(f"\n  [BOTTOM 5]")
        for r in valid_sorted[-5:]:
            code, ret, wr, pf, nt = r["code"], r["total_return"], r["win_rate"], r["profit_factor"], r["trades"]
            print(f"    {code:12s} | 收益 {ret:>7.2f}% | 胜率 {wr:>5.1f}% | PF {pf} | {nt}笔")

    if no_trade:
        print(f"\n  [NO TRADE] ({len(no_trade)}):")
        codes = ", ".join(r["code"] for r in no_trade)
        print(f"    {codes}")

    # 保存详细结果到 JSON
    out_path = os.path.join(os.path.dirname(__file__), "donchian_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {out_path}")


if __name__ == "__main__":
    main()
