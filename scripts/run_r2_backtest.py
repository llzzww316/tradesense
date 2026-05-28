"""60 ???????:?? vs +EMA vs EMA+R22"""
import backtest.strategies  # noqa: F401
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from data_provider import fetch_stock_kline_by_date

ALL_STOCKS = [
    ("????","sz","000001"),("??A","sz","000002"),("????","sz","000333"),
    ("????","sz","000568"),("????","sz","000651"),("???A","sz","000725"),
    ("???","sz","000858"),("????","sz","000895"),("????","sz","002027"),
    ("????","sz","002142"),("????","sz","002230"),("????","sz","002415"),
    ("????","sz","002460"),("???","sz","002594"),("????","sz","300059"),
    ("????","sz","300750"),("????","sh","600000"),("????","sh","600030"),
    ("????","sh","600031"),("????","sh","600036"),("????","sh","600276"),
    ("????","sh","600585"),("????","sh","600809"),("????","sh","600887"),
    ("????","sh","600900"),("????","sh","601012"),("????","sh","601088"),
    ("????","sh","601166"),("????","sh","601318"),("????","sh","601398"),
    ("????","sh","601668"),("????","sh","601857"),("????","sh","603259"),
    ("????","sh","600519"),("????","sh","600309"),("????","sh","600690"),
    ("????","sh","601888"),("????","sh","603288"),("????","sz","300760"),
    ("????","sz","300274"),("????","sz","300124"),("????","sh","601899"),
    ("????","sh","688981"),("????","sh","688111"),("????","sh","603501"),
    ("????","sh","603986"),("????","sh","600941"),("????","sh","600941"),
    ("????","sz","002714"),("????","sh","600438"),("???","sh","600436"),
    ("????","sh","600196"),("????","sh","600660"),("????","sh","600104"),
    ("????","sh","600019"),("????","sh","601006"),("????","sh","601766"),
    ("????","sh","601988"),("????","sh","601288"),("????","sh","601939"),
]

COMMON = {
    "pole_lookback": 15, "pole_min_pct": 1.5, "pole_max_retrace_pct": 60.0,
    "pole_high_min_bull_bars": 2, "flag_min_bars": 3,
    "flag_atr_shrink_ratio": 1.0, "flag_max_height_ratio": 0.7,
    "fixed_qty": 1,
}

CONFIGS = [
    ("baseline", {
        **COMMON, "atr_stop_mult": 1.5,
        "trend_filter": False, "r2_filter": False,
    }),
    ("EMA", {
        **COMMON, "atr_stop_mult": 1.5,
        "trend_filter": True, "trend_ema_fast": 50, "trend_ema_slow": 150,
        "r2_filter": False,
    }),
    ("EMA+R22", {
        **COMMON,
        "r2_filter": True, "r2_min": 0.30, "r2_stop_tight": 0.5, "r2_stop_loose": 2.5,
        "trend_filter": True, "trend_ema_fast": 50, "trend_ema_slow": 150,
    }),
]

def run(label, params):
    total_tr = 0; total_pnl = 0; tp_hits = 0; sl_hits = 0
    all_t = []
    for name, ex, code in ALL_STOCKS:
        try:
            df = fetch_stock_kline_by_date(ex, code, "1d", "2024-01-01", "2026-05-22")
            if len(df) < 200:
                continue
            cfg = BacktestConfig(
                symbol=name, contract=None, period="1d",
                start_date="2024-01-01", end_date="2026-05-22",
                strategy="bull_flag", strategy_params=dict(params),
                initial_capital=1000000.0, tick_size=0.01,
                slippage_ticks=0, margin_rate=1.0, tick_value=0.01,
                fee_per_lot=0, instrument_type="stock",
                commission_rate=0.00025, stamp_tax_rate=0.001,
                transfer_fee_rate=0.00001, lot_size=100,
                intraday_only=False,
            )
            result = BacktestEngine(cfg, df).run()
            for t in result.trades:
                total_tr += 1; total_pnl += t.net_pnl; all_t.append(t)
                if 'TP' in t.close_reason: tp_hits += 1
                elif 'SL' in t.close_reason: sl_hits += 1
        except:
            pass

    wins = sum(1 for t in all_t if t.net_pnl > 0)
    losses = sum(1 for t in all_t if t.net_pnl <= 0)
    avg_w = sum(t.net_pnl for t in all_t if t.net_pnl > 0) / max(wins, 1)
    avg_l = abs(sum(t.net_pnl for t in all_t if t.net_pnl <= 0) / max(losses, 1))
    rr = avg_w / avg_l if avg_l > 0 else 0
    t3w = ', '.join(f'{t.net_pnl:+.0f}' for t in sorted(all_t, key=lambda x: -x.net_pnl)[:3])
    t3l = ', '.join(f'{t.net_pnl:+.0f}' for t in sorted(all_t, key=lambda x: x.net_pnl)[:3])

    print(f"{label:>16s}: {total_tr:>3d} trades | PnL={total_pnl:>+8,.0f} | "
          f"win={wins/total_tr*100:>3.0f}% | TP/SL={tp_hits}/{sl_hits} | "
          f"avgW/L={avg_w:>.0f}/{avg_l:>.0f} | RR={rr:.2f}")
    print(f"                 Top3: -  {t3w}")
    print(f"                        -  {t3l}")
    return total_pnl, total_tr, wins, losses, tp_hits, sl_hits

results = []
for label, params in CONFIGS:
    results.append(run(label, params))

print()
print("=" * 70)
print(f"{'':>16s} {'??':>5s} {'??':>10s} {'??':>5s} {'TP/SL':>7s} {'???':>6s}")
print("=" * 52)
for (label, _), (pnl, tr, w, l, tp, sl) in zip(CONFIGS, results):
    wr = w/tr*100 if tr else 0
    print(f"{label:>16s} {tr:>5d} {pnl:>+9,.0f} {wr:>4.0f}% {tp}/{sl}  {'' if not tr else '':>5s}")
