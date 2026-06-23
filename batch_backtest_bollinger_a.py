"""批量回测脚本：60只沪深主板股票，bollinger_reversion_a 策略，2024-01~2026-05"""
import json
import sys
import time
from pathlib import Path

import pandas as pd

# 确保项目在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest.strategies.bollinger_reversion_a  # 确保策略注册

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from data_provider import fetch_stock_kline_by_date, clear_kline_cache

# ---- 60只股票池（33只配置文件已有 + 27只随机补充）----
STOCKS = [
    # 已配置33只
    ("贵州茅台", "sh", "600519"),
    ("宁德时代", "sz", "300750"),
    ("招商银行", "sh", "600036"),
    ("浦发银行", "sh", "600000"),
    ("中信证券", "sh", "600030"),
    ("三一重工", "sh", "600031"),
    ("恒瑞医药", "sh", "600276"),
    ("海螺水泥", "sh", "600585"),
    ("山西汾酒", "sh", "600809"),
    ("伊利股份", "sh", "600887"),
    ("长江电力", "sh", "600900"),
    ("隆基绿能", "sh", "601012"),
    ("中国神华", "sh", "601088"),
    ("兴业银行", "sh", "601166"),
    ("中国平安", "sh", "601318"),
    ("工商银行", "sh", "601398"),
    ("中国建筑", "sh", "601668"),
    ("中国石油", "sh", "601857"),
    ("药明康德", "sh", "603259"),
    ("平安银行", "sz", "000001"),
    ("万科A", "sz", "000002"),
    ("美的集团", "sz", "000333"),
    ("泸州老窖", "sz", "000568"),
    ("格力电器", "sz", "000651"),
    ("京东方A", "sz", "000725"),
    ("五粮液", "sz", "000858"),
    ("双汇发展", "sz", "000895"),
    ("分众传媒", "sz", "002027"),
    ("宁波银行", "sz", "002142"),
    ("科大讯飞", "sz", "002230"),
    ("海康威视", "sz", "002415"),
    ("赣锋锂业", "sz", "002460"),
    ("比亚迪", "sz", "002594"),
    # 随机补充27只（沪深主板）
    ("首创环保", "sh", "600008"),
    ("中闽能源", "sh", "601026"),
    ("联明股份", "sh", "603006"),
    ("华创云信", "sh", "600155"),
    ("渤海轮渡", "sh", "603167"),
    ("公牛集团", "sh", "603195"),
    ("庚星股份", "sh", "600753"),
    ("中直股份", "sh", "600038"),
    ("凯盛科技", "sh", "600552"),
    ("电魂网络", "sh", "603258"),
    ("淳中科技", "sh", "603516"),
    ("海正药业", "sh", "600267"),
    ("建研院", "sh", "603183"),
    ("顾家家居", "sh", "603816"),
    ("东方航空", "sh", "600115"),
    ("德豪润达", "sz", "002005"),
    ("欣龙控股", "sz", "000955"),
    ("奥美医疗", "sz", "002950"),
    ("证通电子", "sz", "002197"),
    ("航发控制", "sz", "000738"),
    ("华宏科技", "sz", "002645"),
    ("智光电气", "sz", "002169"),
    ("大北农", "sz", "002385"),
    ("实丰文化", "sz", "002862"),
    ("海欣食品", "sz", "002702"),
    ("七匹狼", "sz", "002029"),
    ("中洲控股", "sz", "000042"),
]

PERIOD = "1d"
START_DATE = "2024-01-01"
END_DATE = "2026-05-22"
INITIAL_CAPITAL = 100_000.0

STRATEGY = "bollinger_reversion_a"
# 日线参数：两周=10天布林带，中等置信度，5%止损，持股上限30天
PARAMS = {
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_period": 14,
    "stop_pct": 0.05,
    "max_hold": 30,
    "entry_confirmation": True,
    "position_pct": 0.3,
}

# A股手续费
COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.001
TRANSFER_FEE_RATE = 0.00001
LOT_SIZE = 100


def run_one(name: str, exchange: str, sym: str) -> dict | None:
    """对单只股票回测，返回 metrics 或 None。"""
    try:
        clear_kline_cache()
        df = fetch_stock_kline_by_date(
            exchange=exchange, symbol=sym, period=PERIOD,
            start_date=START_DATE, end_date=END_DATE,
            count=None,
        )
    except Exception as e:
        print(f"  [{name}] 数据获取失败: {e}")
        return None

    if df.empty:
        print(f"  [{name}] 回测区间无数据")
        return None

    code = f"{exchange.upper()}.{sym}"
    cfg = BacktestConfig(
        symbol=name, contract=code, period=PERIOD,
        start_date=START_DATE, end_date=END_DATE,
        initial_capital=INITIAL_CAPITAL,
        tick_size=0.01, tick_value=0.01,
        margin_rate=1.0, fee_per_lot=0,
        slippage_ticks=1, intraday_only=False,
        strategy=STRATEGY, strategy_params=PARAMS,
        instrument_type="stock",
        commission_rate=COMMISSION_RATE,
        stamp_tax_rate=STAMP_TAX_RATE,
        transfer_fee_rate=TRANSFER_FEE_RATE,
        lot_size=LOT_SIZE,
    )

    try:
        result = BacktestEngine(cfg, df).run()
    except Exception as e:
        print(f"  [{name}] 回测异常: {e}")
        return None

    m = result.metrics
    return {
        "name": name,
        "code": code,
        "bars": len(result.bars),
        "total_return": m["total_return"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "sharpe": m["sharpe"],
        "calmar": m["calmar"],
        "win_rate": m["win_rate"],
        "profit_factor": m["profit_factor"],
        "total_trades": m["total_trades"],
        "avg_holding_bars": m["avg_holding_bars"],
        "final_equity": m["final_equity"],
    }


def main():
    print(f"=" * 80)
    print(f"批量回测: {STRATEGY} | 周期={PERIOD} | {START_DATE} ~ {END_DATE}")
    print(f"股票池: {len(STOCKS)} 只沪深主板 | 初始资金: {INITIAL_CAPITAL:,.0f}")
    print(f"参数: {json.dumps(PARAMS, ensure_ascii=False)}")
    print(f"=" * 80)

    results = []
    start_time = time.time()
    for i, (name, ex, sym) in enumerate(STOCKS):
        print(f"\n[{i+1}/{len(STOCKS)}] {name} ({ex}{sym}) ...", end=" ", flush=True)
        r = run_one(name, ex, sym)
        if r:
            results.append(r)
            rr = r["total_return"] * 100
            sh = r["sharpe"] or 0
            print(f"收益={rr:+.2f}% Sharpe={sh:.2f} 交易={r['total_trades']}")
        else:
            print("SKIP")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 80}")
    print(f"回测完成！耗时 {elapsed:.0f}s | 有效结果: {len(results)}/{len(STOCKS)}")
    print(f"{'=' * 80}")

    if not results:
        print("无有效回测结果！")
        return

    # ---- 排序（按总收益）----
    results.sort(key=lambda x: x["total_return"], reverse=True)

    # ---- 汇总表格 ----
    print(f"\n{'名称':<10s} {'代码':<10s} {'K线':>5s} {'收益率':>8s} {'最大回撤':>8s} {'Sharpe':>7s} {'Calmar':>7s} {'胜率':>7s} {'盈亏比':>7s} {'交易':>5s} {'持K':>5s}")
    print("-" * 100)

    total_return_sum = 0.0
    sharpe_sum = 0.0
    sharpe_count = 0
    win_count = 0

    for r in results:
        rr = r["total_return"] * 100
        dd = r["max_drawdown_pct"] * 100
        sh = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "N/A"
        ca = f"{r['calmar']:.2f}" if r["calmar"] is not None else "N/A"
        wr = r["win_rate"] * 100

        print(f"{r['name']:<10s} {r['code']:<10s} {r['bars']:>5d} {rr:>7.2f}% {dd:>7.2f}% {sh:>7s} {ca:>7s} {wr:>6.1f}% {r['profit_factor']:>6.2f} {r['total_trades']:>5d} {r['avg_holding_bars']:>4.0f}")

        total_return_sum += r["total_return"]
        if r["sharpe"] is not None:
            sharpe_sum += r["sharpe"]
            sharpe_count += 1
        if r["total_return"] > 0:
            win_count += 1

    print("-" * 100)

    avg_return = (total_return_sum / len(results)) * 100
    avg_sharpe = sharpe_sum / sharpe_count if sharpe_count else 0
    print(f"{'均值':<10s} {'':<10s} {'':>5s} {avg_return:>7.2f}% {'':>8s} {avg_sharpe:>7.2f} {'':>7s} {'':>7s} {'':>7s} {'':>5s}")
    print(f"盈利股票: {win_count}/{len(results)}")

    # ---- 保存结果 ----
    out = {
        "strategy": STRATEGY,
        "period": PERIOD,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "params": PARAMS,
        "initial_capital": INITIAL_CAPITAL,
        "results": results,
        "summary": {
            "avg_return": avg_return,
            "avg_sharpe": avg_sharpe,
            "win_count": win_count,
            "total_count": len(results),
        },
    }
    out_path = Path(__file__).resolve().parent / "batch_results_bollinger_a.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
