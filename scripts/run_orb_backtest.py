"""螺纹钢2610回测：ORB 策略"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest.strategies  # noqa: F401
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from data_provider import fetch_kline_by_date

RB_MKT = 30
RB_SYM = "RB2610"
TICK = 1
MARGIN = 0.10
FEE = 3
TICK_VALUE = 10

def main():
    df = fetch_kline_by_date(
        market=RB_MKT, symbol=RB_SYM,
        period="5m", start_date="2026-03-01", end_date="2026-06-03",
        count=None,
    )
    if df.empty:
        print("未找到数据")
        return

    print(f"数据: {df['bob'].iloc[0]} ~ {df['bob'].iloc[-1]}  ({len(df)} 根K线)")

    cfg = BacktestConfig(
        symbol="螺纹钢2610", contract=RB_SYM, period="5m",
        start_date="2026-03-01", end_date="2026-06-03",
        strategy="orb", strategy_params={},
        initial_capital=100000.0, tick_size=TICK, tick_value=TICK_VALUE,
        margin_rate=MARGIN, fee_per_lot=FEE, slippage_ticks=1,
        intraday_only=True, instrument_type="futures",
    )
    result = BacktestEngine(cfg, df).run()

    m = result.metrics
    print()
    print("=" * 60)
    print("回测结果: ORB 开盘区间突破 (螺纹钢2610, 5m)")
    print("=" * 60)
    print(f"最终权益:   {m['final_equity']:>12,.2f}")
    print(f"总收益率:   {m['total_return']*100:>11.2f}%")
    print(f"最大回撤:   {m['max_drawdown']:>12,.2f}  ({m['max_drawdown_pct']*100:.2f}%)")
    print("-" * 60)
    print(f"总交易数:   {m['total_trades']:>12d}  (胜{m['winning_trades']} / 负{m['losing_trades']})")
    print(f"胜率:       {m['win_rate']*100:>11.2f}%")
    print(f"盈亏比:     {m['profit_factor']:>12.2f}")
    print(f"平均盈利:   {m['avg_win']:>12,.2f}")
    print(f"平均亏损:   {m['avg_loss']:>12,.2f}")
    print(f"Sharpe:     {m['sharpe'] or 0:>12.2f}")
    print("=" * 60)
    if result.trades:
        print("\n交易明细:")
        for t in result.trades:
            side = "多" if t.side == "long" else "空"
            print(f"  {t.open_time} {side} {t.qty}手 @{t.open_price:.0f}"
                  f" -> {t.close_time} @{t.close_price:.0f}"
                  f"  PnL={t.net_pnl:+,.0f}  ({t.close_reason})")

if __name__ == "__main__":
    main()
