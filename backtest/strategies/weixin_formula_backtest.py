#!/usr/bin/env python3
"""微信选股公式 - 回测脚本 v2（复用 scanner 批量拉取，本地跑公式检测）。

用法：
    cd /root/tradesense
    .venv/bin/python backtest/strategies/weixin_formula_backtest.py --samples 50
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from patterns.scanner import parse_stock_list, fetch_bars_batch


# ---------------------------------------------------------------------------
# 公式条件函数
# ---------------------------------------------------------------------------

def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return float("nan")
    return sum(values[-period:]) / period


def _llv(values: list[float], period: int) -> float:
    return min(values[-period:])


def _hhv(values: list[float], period: int) -> float:
    return max(values[-period:])


def _ref(values: list[float], offset: int) -> float:
    if len(values) < offset + 1:
        return float("nan")
    return values[-(offset + 1)]


def check_formula_on_bar(bars: list, bar_idx: int, rsv_period=55,
                         sma1_period=20, sma2_period=15,
                         vol_ma_period=5, ma_period_short=20, ma_period_long=60) -> bool:
    """判断 bars[:bar_idx+1] 中最后一根 bar 是否满足公式条件。"""
    n = bar_idx + 1
    if n < 120:
        return False
    
    sub = bars[:n]
    closes = [b.close for b in sub]
    highs = [b.high for b in sub]
    lows = [b.low for b in sub]
    opens_ = [b.open for b in sub]
    volumes = [b.volume for b in sub]
    
    # 构建 RSV 序列（只用最近 50 根）
    lookback = min(50, n)
    zb_vals = []
    kp_vals = []
    for i in range(n - lookback, n):
        c = closes[i]
        o = opens_[i]
        cv = closes[:i+1]
        hv = highs[:i+1]
        lv = lows[:i+1]
        rng = max(hv[-rsv_period:]) - min(lv[-rsv_period:])
        if rng == 0:
            zb_vals.append(50.0)
            kp_vals.append(50.0)
        else:
            zb_vals.append((c - min(lv[-rsv_period:])) / rng * 100)
            kp_vals.append((o - min(lv[-rsv_period:])) / rng * 100)
    
    if len(zb_vals) < sma1_period + sma2_period:
        return False
    
    # 计算双 SMA
    sma_zb = []
    sma_kp = []
    for i in range(sma2_period, len(zb_vals)):
        s1_zb = _sma(zb_vals[:i+1], sma1_period)
        s1_kp = _sma(kp_vals[:i+1], sma1_period)
        if not (math.isnan(s1_zb) or math.isnan(s1_kp)):
            sma_zb.append(s1_zb)
            sma_kp.append(s1_kp)
    
    if len(sma_zb) < sma2_period:
        return False
    
    zbdl_now = _sma(sma_zb, sma2_period)
    kpdl_prev = sma_kp[-2] if len(sma_kp) >= 2 else float("nan")
    if math.isnan(zbdl_now) or math.isnan(kpdl_prev):
        return False
    dnfz = zbdl_now > kpdl_prev
    
    # QSXH
    ma20 = sum(closes[-ma_period_short:]) / ma_period_short
    ma60_vals = closes[-ma_period_long - 1:]
    ma60 = sum(ma60_vals[-ma_period_long:]) / ma_period_long
    ma60_prev = sum(ma60_vals[:-1][-ma_period_long:]) / ma_period_long if len(ma60_vals) > ma_period_long else ma60
    qsxh = (closes[-1] > ma20) and (ma60 >= ma60_prev)
    
    # YXQR
    if n < 2:
        return False
    zf = (closes[-1] - closes[-2]) / closes[-2] * 100
    yxqr = (closes[-1] > opens_[-1]) and (2.0 <= zf <= 7.0)
    
    # LNQR
    vol_ma5 = sum(volumes[-vol_ma_period:]) / vol_ma_period
    vol_ref = _ref(volumes, 1) if len(volumes) >= 2 else float("nan")
    lnqr = (volumes[-1] > vol_ma5 * 1.5) and \
           (vol_ref < _sma(volumes, vol_ma_period) if len(volumes) >= vol_ma_period + 1 else False)
    
    # WZGL
    xdwz = (closes[-1] - min(lows[-rsv_period:])) / \
           (max(highs[-rsv_period:]) - min(lows[-rsv_period:])) * 100 \
           if max(highs[-rsv_period:]) != min(lows[-rsv_period:]) else 100.0
    close_2d_ago = _ref(closes, 2) if len(closes) >= 3 else float("nan")
    wzgl = (xdwz < 80.0) and \
           (closes[-1] / close_2d_ago < 1.1 if len(closes) >= 3 else False)
    
    return dnfz and qsxh and yxqr and lnqr and wzgl


# ---------------------------------------------------------------------------
# 回测核心
# ---------------------------------------------------------------------------

def backtest_stock(code: str, name: str, bars: list, min_bars=150, hold_days=5) -> dict:
    """对单只股票跑滚动回测。"""
    total = len(bars)
    if total < min_bars:
        return {"code": code, "name": name, "signals": 0, "error": "insufficient_data"}
    
    signals = []
    
    for i in range(min_bars, total):
        if not check_formula_on_bar(bars, i):
            continue
        
        entry_price = bars[i].close
        exit_idx = min(i + hold_days, total - 1)
        exit_price = bars[exit_idx].close
        
        future = bars[i+1:exit_idx+1]
        highest = max(b.high for b in future) if future else exit_price
        lowest = min(b.low for b in future) if future else exit_price
        
        ret = (exit_price - entry_price) / entry_price * 100
        max_gain = (highest - entry_price) / entry_price * 100
        max_loss = (lowest - entry_price) / entry_price * 100
        
        signals.append({
            "date": bars[i].time,
            "entry": entry_price,
            "exit": exit_price,
            "ret_pct": ret,
            "max_gain_pct": max_gain,
            "max_loss_pct": max_loss,
        })
    
    if not signals:
        return {"code": code, "name": name, "signals": 0, "error": "no_signals"}
    
    returns = [s["ret_pct"] for s in signals]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    
    return {
        "code": code,
        "name": name,
        "signals": len(signals),
        "win_rate": len(wins) / len(returns) * 100 if returns else 0,
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "avg_return": sum(returns) / len(returns) if returns else 0,
        "max_return": max(returns),
        "min_return": min(returns),
        "total_return": sum(returns),
    }


def print_report(results: list[dict], elapsed: float):
    """打印回测报告。"""
    has_signal = [r for r in results if r.get("signals", 0) > 0]
    no_signal = [r for r in results if r.get("signals", 0) == 0]
    errors = [r for r in results if r.get("error", "") and r["signals"] == 0]
    
    print("\n" + "=" * 68)
    print(f"  📊 微信选股公式回测报告")
    print(f"     时间: {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"     测试股票: {len(results)} 只")
    print(f"     产生信号: {len(has_signal)} 只")
    print(f"     无信号:   {len(no_signal)} 只")
    print(f"     数据不足: {sum(1 for r in errors if r.get('error') == 'insufficient_data')} 只")
    print(f"     耗时:     {elapsed:.1f} 秒")
    print("=" * 68)
    
    if not has_signal:
        print("\n  ❌ 测试股票中无一产生选股信号。")
        return
    
    # 汇总
    all_returns = []
    all_signals = 0
    for r in has_signal:
        all_signals += r["signals"]
    
    # 重新计算所有信号的收益率列表
    all_rets = []
    for r in has_signal:
        if "signals_detail" in r:
            all_rets.extend([s["ret_pct"] for s in r["signals_detail"]])
    
    if all_rets:
        wins = [x for x in all_rets if x > 0]
        losses = [x for x in all_rets if x <= 0]
        
        print(f"\n  📈 整体统计")
        print(f"     总信号数:    {all_signals}")
        print(f"     胜率:        {len(wins)/len(all_rets)*100:.1f}% ({len(wins)}/{len(all_rets)})")
        print(f"     平均收益率:  {sum(all_rets)/len(all_rets):+.2f}%")
        if wins:  print(f"     平均盈利:    {sum(wins)/len(wins):+.2f}%")
        if losses: print(f"     平均亏损:    {sum(losses)/len(losses):+.2f}%")
        if wins and losses:
            print(f"     盈亏比:      {abs(sum(wins)/len(wins))/abs(sum(losses)/len(losses)):.2f}")
        print(f"     最大收益:    {max(all_rets):+.2f}%")
        print(f"     最大亏损:    {min(all_rets):+.2f}%")
        
        # 分布
        print(f"\n  📉 收益分布（持仓5日）")
        ranges = [(-100, -10), (-10, -5), (-5, -2), (-2, 0), (0, 2), (2, 5), (5, 10), (10, 100)]
        for lo, hi in ranges:
            cnt = sum(1 for r in all_rets if lo < r <= hi)
            bar = "█" * max(1, int(cnt / max(1, len(all_rets)) * 50))
            print(f"     {lo:>+5}% ~ {hi:>+4}%: {cnt:>4} 次 {bar}")
    
    # Top 10 信号最多的
    sorted_stocks = sorted(has_signal, key=lambda r: -r["signals"])[:10]
    print(f"\n  🏆 信号最多 Top 10")
    for r in sorted_stocks:
        print(f"     {r['code']} {r['name']:>6s}: {r['signals']:>3d} 次  胜率{r['win_rate']:.0f}%  平均{r['avg_return']:+.2f}%")
    
    # 胜率最高的 Top 10（至少3次信号）
    wr_sorted = sorted(
        [r for r in has_signal if r["signals"] >= 3],
        key=lambda r: -r["win_rate"]
    )[:10]
    print(f"\n  🎯 高胜率 Top 10（≥3次信号）")
    for r in wr_sorted:
        print(f"     {r['code']} {r['name']:>6s}: 胜率{r['win_rate']:.0f}%  ({r['signals']}次)  均收{r['avg_return']:+.2f}%")
    
    print(f"\n  ⚠️ 数据来源 tdxpy 实时行情，仅供参考，不构成投资建议")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="微信选股公式回测")
    parser.add_argument("--samples", type=int, default=50, help="随机抽样股票数")
    parser.add_argument("--count", type=int, default=200, help="每只股票拉取 K 线数")
    args = parser.parse_args()
    
    print("📥 加载股票列表...")
    stocks = parse_stock_list(str(Path(__file__).resolve().parent.parent.parent / "docs" / "stock_scan_low_price.md"))
    print(f"   共 {len(stocks)} 只股票")
    
    import random
    random.seed(42)
    sample = random.sample(stocks, min(args.samples, len(stocks)))
    print(f"\n🔍 随机抽取 {len(sample)} 只，拉取日线数据（count={args.count}）...")
    
    t0 = time.time()
    bars_map = fetch_bars_batch(sample, count=args.count, max_workers=4)
    fetch_time = time.time() - t0
    print(f"   成功拉取: {len(bars_map)} / {len(sample)} 只（{fetch_time:.1f}s）")
    
    # 建立 code -> (name, bars) 映射
    stock_info = {s["code"]: (s["name"], s) for s in sample}
    
    print(f"\n🧮 运行公式检测...")
    results = []
    for code, bars in bars_map.items():
        name = stock_info.get(code, ("?", {}))[0]
        result = backtest_stock(code, name, bars)
        results.append(result)
    
    total_time = time.time() - t0
    print_report(results, total_time)


if __name__ == "__main__":
    main()
