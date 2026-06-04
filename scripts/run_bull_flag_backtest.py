"""随机选取60只沪深主板股票，回测上升旗型策略 2024-01-01 至 2026-05-22"""
import random
import sys
from pathlib import Path

# 确保 backtest 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest.strategies  # noqa: F401
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from data_provider import fetch_stock_kline_by_date

# ---------------------------------------------------------------------------
# 扫描本地可用的沪深主板股票
# ---------------------------------------------------------------------------
def scan_main_board() -> list[tuple[str, str]]:
    """返回 [(exchange, code), ...] 沪深主板（排除创业板 30xxxx、科创板 68xxxx）。"""
    sh_dir = Path("C:/new_tdx/vipdoc/sh/lday")
    sz_dir = Path("C:/new_tdx/vipdoc/sz/lday")
    result = []
    for base_dir, exchange in [(sh_dir, "sh"), (sz_dir, "sz")]:
        if not base_dir.exists():
            continue
        for f in base_dir.iterdir():
            if not f.is_file() or f.suffix.lower() != ".day":
                continue
            code = f.stem.lower().replace(exchange, "")
            if not code.isdigit() or len(code) != 6:
                continue
            code_int = int(code)
            if exchange == "sh" and 600000 <= code_int <= 605999:
                result.append((exchange, code))
            elif exchange == "sz":
                if 300000 <= code_int <= 301999:
                    continue
                if 0 <= code_int <= 3999:
                    result.append((exchange, code))
    return result


def main():
    random.seed(42)
    all_stocks = scan_main_board()
    print(f"本地沪深主板股票总数: {len(all_stocks)}")

    # 随机取 60 只
    selected = random.sample(all_stocks, min(60, len(all_stocks)))
    print(f"随机选取 {len(selected)} 只回测")

    # 策略参数（默认参数，开启 EMA 趋势过滤）
    params = {
        "pole_lookback": 15,
        "pole_min_pct": 1.5,
        "pole_max_retrace_pct": 60.0,
        "pole_high_min_bull_bars": 2,
        "flag_min_bars": 3,
        "flag_atr_shrink_ratio": 1.0,
        "flag_max_height_ratio": 0.7,
        "atr_stop_mult": 1.5,
        "fixed_qty": 100,
        "trend_filter": True,
        "trend_ema_fast": 50,
        "trend_ema_slow": 150,
        "r2_filter": False,
    }

    total_tr = 0
    total_pnl = 0.0
    all_trades = []
    success = 0
    skipped = 0
    errors = 0

    for exchange, code in selected:
        try:
            df = fetch_stock_kline_by_date(exchange, code, "1d", "2024-01-01", "2026-05-22")
            if len(df) < 200:
                skipped += 1
                continue

            cfg = BacktestConfig(
                symbol=f"{exchange}{code}",
                contract=None,
                period="1d",
                start_date="2024-01-01",
                end_date="2026-05-22",
                strategy="bull_flag",
                strategy_params=dict(params),
                initial_capital=1_000_000.0,
                tick_size=0.01,
                slippage_ticks=0,
                margin_rate=1.0,
                tick_value=0.01,
                fee_per_lot=0,
                instrument_type="stock",
                commission_rate=0.00025,
                stamp_tax_rate=0.001,
                transfer_fee_rate=0.00001,
                lot_size=100,
                intraday_only=False,
            )

            result = BacktestEngine(cfg, df).run()
            success += 1

            for t in result.trades:
                t._stock = f"{exchange}{code}"
                total_tr += 1
                total_pnl += t.net_pnl
                all_trades.append(t)

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  [ERROR] {exchange}{code}: {e}")

    # -----------------------------------------------------------------------
    # 汇总统计
    # -----------------------------------------------------------------------
    print()
    print("=" * 80)
    print(f"回测完成: 成功={success}, 数据不足={skipped}, 错误={errors}")
    print(f"总交易次数: {total_tr}")
    print(f"总盈亏:     {total_pnl:+,.0f}")
    if all_trades:
        wins = [t for t in all_trades if t.net_pnl > 0]
        losses = [t for t in all_trades if t.net_pnl <= 0]
        nw, nl = len(wins), len(losses)
        wr = nw / total_tr * 100 if total_tr else 0
        avg_w = sum(t.net_pnl for t in wins) / nw if nw else 0
        avg_l = abs(sum(t.net_pnl for t in losses) / nl) if nl else 0
        rr = avg_w / avg_l if avg_l > 0 else 0

        tp_hits = sum(1 for t in all_trades if "TP" in t.close_reason)
        sl_hits = sum(1 for t in all_trades if "SL" in t.close_reason)

        print(f"胜率:       {wr:.1f}% ({nw}W / {nl}L)")
        print(f"平均盈亏:   W={avg_w:+.0f}  L={avg_l:+.0f}")
        print(f"盈亏比:     {rr:.2f}")
        print(f"止盈/止损:  {tp_hits} / {sl_hits}")

        top3 = sorted(all_trades, key=lambda t: -t.net_pnl)[:3]
        bot3 = sorted(all_trades, key=lambda t: t.net_pnl)[:3]
        print(f"Top3 盈利:  {', '.join(f'{t._stock} {t.net_pnl:+.0f}' for t in top3)}")
        print(f"Top3 亏损:  {', '.join(f'{t._stock} {t.net_pnl:+.0f}' for t in bot3)}")
    else:
        print("无交易信号。")

if __name__ == "__main__":
    main()
