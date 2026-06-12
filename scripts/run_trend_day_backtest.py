"""趋势日策略回测：RB（螺纹钢）+ PVC，对比不同参数组合。

用法：uv run python scripts/run_trend_day_backtest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest.strategies  # noqa: F401
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from data_provider import fetch_kline_by_date


# ── 品种配置 ──────────────────────────────────────────────────

INSTRUMENTS = {
    "RB2610": {"market": 30, "tick_size": 1, "tick_value": 10, "margin": 0.10, "fee": 3},
    "V2609":  {"market": 29, "tick_size": 1, "tick_value": 5,  "margin": 0.09, "fee": 2},
}

# ── 参数组合 ──────────────────────────────────────────────────

PARAM_SETS = {
    "默认": {},
    "关闭移动止损": {"enable_trailing": False},
    "关闭回调入场": {"enable_pullback": False},
    "关闭两者":     {"enable_trailing": False, "enable_pullback": False},
}


def run_single(symbol: str, cfg_dict: dict, params: dict, label: str) -> dict | None:
    """跑单次回测，返回 metrics 或 None。"""
    df = fetch_kline_by_date(
        market=cfg_dict["market"], symbol=symbol,
        period="5m", start_date="2026-03-01", end_date="2026-06-10",
        count=None,
    )
    if df.empty:
        print(f"  [{symbol}] 未找到数据，跳过")
        return None

    cfg = BacktestConfig(
        symbol=symbol, contract=symbol, period="5m",
        start_date="2026-03-01", end_date="2026-06-10",
        strategy="trend_day", strategy_params=params,
        initial_capital=100000.0,
        tick_size=cfg_dict["tick_size"], tick_value=cfg_dict["tick_value"],
        margin_rate=cfg_dict["margin"], fee_per_lot=cfg_dict["fee"],
        slippage_ticks=1, intraday_only=True, instrument_type="futures",
    )
    result = BacktestEngine(cfg, df).run()
    return result


def print_metrics(symbol: str, label: str, result) -> None:
    m = result.metrics
    trades = result.trades
    print(f"\n{'─'*60}")
    print(f"  {symbol} | {label}")
    print(f"{'─'*60}")
    print(f"  数据范围:    {result.bars[0].time if result.bars else 'N/A'} ~ {result.bars[-1].time if result.bars else 'N/A'}")
    print(f"  最终权益:    {m['final_equity']:>12,.2f}")
    print(f"  总收益率:    {m['total_return']*100:>11.2f}%")
    print(f"  最大回撤:    {m['max_drawdown']:>12,.2f}  ({m['max_drawdown_pct']*100:.2f}%)")
    print(f"  总交易数:    {m['total_trades']:>12d}  (胜{m['winning_trades']} / 负{m['losing_trades']})")
    print(f"  胜率:        {m['win_rate']*100:>11.2f}%")
    print(f"  盈亏比:      {m['profit_factor']:>12.2f}")
    print(f"  平均盈利:    {m['avg_win']:>12,.2f}")
    print(f"  平均亏损:    {m['avg_loss']:>12,.2f}")
    print(f"  Sharpe:      {m['sharpe'] or 0:>12.2f}")
    print(f"  盈利天数:    {m.get('winning_days', 0)}")
    print(f"  最大连胜:    {m.get('max_consecutive_wins', 0)}")
    print(f"  最大连亏:    {m.get('max_consecutive_losses', 0)}")

    # 打印交易明细（最多20笔）
    if trades:
        print(f"\n  交易明细 (共{len(trades)}笔，显示前20):")
        for t in trades[:20]:
            side = "多" if t.side == "long" else "空"
            pnl_sign = "+" if t.net_pnl >= 0 else ""
            print(f"    {t.open_time} {side} {t.qty}手 @{t.open_price:.0f}"
                  f" -> {t.close_time} @{t.close_price:.0f}"
                  f"  {pnl_sign}{t.net_pnl:,.0f}  ({t.close_reason})")


def main():
    print("=" * 60)
    print("趋势日策略回测 (5m, 2026-03-01 ~ 2026-06-10)")
    print("=" * 60)

    for symbol, cfg_dict in INSTRUMENTS.items():
        print(f"\n{'#'*60}")
        print(f"# {symbol}")
        print(f"{'#'*60}")

        for label, params in PARAM_SETS.items():
            result = run_single(symbol, cfg_dict, params, label)
            if result:
                print_metrics(symbol, label, result)

    print("\n" + "=" * 60)
    print("回测完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
