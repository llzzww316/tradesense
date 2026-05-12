// ---- API 基址与 index.html 逻辑对齐 ----
function resolveApiBase() {
    const root = document.documentElement;
    if (root.hasAttribute("data-api-base")) {
        return root.getAttribute("data-api-base").trim().replace(/\/$/, "");
    }
    try {
        const ls = localStorage.getItem("tradesense_api_base");
        if (ls) return ls.trim().replace(/\/$/, "");
    } catch (_) { /* ignore */ }
    const { protocol, hostname, port } = window.location;
    const effectivePort = port || (protocol === "https:" ? "443" : "80");
    const onHttp = protocol === "http:" || protocol === "https:";
    if (onHttp && effectivePort === "8765") return "/api";
    if ((hostname === "localhost" || hostname === "127.0.0.1") && effectivePort !== "8765") {
        return `${protocol}//${hostname}:8765/api`.replace(/\/$/, "");
    }
    return "";
}
const API_BASE = resolveApiBase();

// ---- DOM refs ----
const $ = id => document.getElementById(id);
const refs = {
    symbol: $("bt-symbol"), period: $("bt-period"), start: $("bt-start"), end: $("bt-end"),
    strategy: $("bt-strategy"), params: $("bt-params"),
    capital: $("bt-capital"), slippage: $("bt-slippage"),
    tickSize: $("bt-tick-size"), tickValue: $("bt-tick-value"),
    fee: $("bt-fee"), margin: $("bt-margin"), intraday: $("bt-intraday"),
    run: $("bt-run"), status: $("bt-status"),
    chart: $("bt-chart"), equityChart: $("bt-equity-chart"),
    metricsBox: $("bt-metrics"), tradeBody: $("bt-trade-body"),
    exportBtn: $("bt-export"),
};

let chart = null, candleSeries = null, equityChart = null, equitySeries = null;
let lastResult = null;

// ---- 初始化品种 + 策略下拉 ----
async function initLists() {
    try {
        const [symResp, stratResp] = await Promise.all([
            fetch(`${API_BASE}/symbols`).then(r => r.ok ? r.json() : Promise.reject(new Error(`GET /symbols HTTP ${r.status}`))),
            fetch(`${API_BASE}/backtest/strategies`).then(r => r.ok ? r.json() : Promise.reject(new Error(`GET /backtest/strategies HTTP ${r.status}`))),
        ]);
        for (const name of Object.keys(symResp.symbols || {})) {
            refs.symbol.appendChild(new Option(name, name));
        }
        for (const s of stratResp.strategies || []) {
            refs.strategy.appendChild(new Option(s, s));
        }
    } catch (e) {
        refs.status.textContent = "加载品种/策略列表失败: " + e.message;
        refs.status.classList.remove("hidden");
    }
}

// ---- 图表 ----
function initCharts() {
    chart = LightweightCharts.createChart(refs.chart, { height: 520, layout: { background: { color: "#fff" } } });
    candleSeries = chart.addCandlestickSeries({
        upColor: "#ef5350", downColor: "#26a69a",
        borderUpColor: "#ef5350", borderDownColor: "#26a69a",
        wickUpColor: "#ef5350", wickDownColor: "#26a69a",
    });
    equityChart = LightweightCharts.createChart(refs.equityChart, {
        height: 160, layout: { background: { color: "#fff" } },
    });
    equitySeries = equityChart.addLineSeries({ color: "#1976d2", lineWidth: 2 });
}

function toChartTime(s) { return Math.floor(new Date(s.replace(" ", "T") + "Z").getTime() / 1000); }

// ---- 运行回测 ----
async function run() {
    refs.status.classList.remove("hidden");
    refs.status.textContent = "运行中...";
    refs.run.disabled = true;

    let params = {};
    try { params = JSON.parse(refs.params.value || "{}"); }
    catch (e) { refs.status.textContent = "参数 JSON 解析失败: " + e.message; refs.run.disabled = false; return; }

    const body = {
        symbol: refs.symbol.value,
        period: refs.period.value,
        start_date: refs.start.value || null,
        end_date: refs.end.value || null,
        initial_capital: Number(refs.capital.value),
        slippage_ticks: Number(refs.slippage.value),
        intraday_only: refs.intraday.checked,
        strategy: refs.strategy.value,
        strategy_params: params,
    };
    if (refs.tickSize.value) body.tick_size = Number(refs.tickSize.value);
    if (refs.tickValue.value) body.tick_value = Number(refs.tickValue.value);
    if (refs.fee.value) body.fee_per_lot = Number(refs.fee.value);
    if (refs.margin.value) body.margin_rate = Number(refs.margin.value);

    try {
        const r = await fetch(`${API_BASE}/backtest/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        lastResult = data;
        render(data);
        refs.status.classList.add("hidden");
    } catch (e) {
        refs.status.textContent = "失败: " + e.message;
    } finally {
        refs.run.disabled = false;
    }
}

// ---- 渲染 ----
function render(data) {
    const candles = data.bars.map(b => ({
        time: toChartTime(b.time),
        open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    candleSeries.setData(candles);

    const markers = [];
    for (const f of data.fills) {
        const t = toChartTime(f.time);
        if (f.action === "open_long") {
            markers.push({ time: t, position: "belowBar", color: "#ef5350", shape: "arrowUp",
                text: `B ${f.qty} @${f.price.toFixed(2)}` });
        } else if (f.action === "open_short") {
            markers.push({ time: t, position: "aboveBar", color: "#26a69a", shape: "arrowDown",
                text: `S ${f.qty} @${f.price.toFixed(2)}` });
        } else if (f.action === "close") {
            const isEod = (f.reason || "").includes("eod");
            const isLiq = (f.reason || "").includes("liquidate");
            markers.push({ time: t, position: "inBar",
                color: isLiq ? "#b71c1c" : (isEod ? "#888" : "#fb8c00"),
                shape: "circle",
                text: `C @${f.price.toFixed(2)}${isLiq ? " 爆仓" : ""}${isEod ? " 日末" : ""}` });
        }
    }
    markers.sort((a, b) => a.time - b.time);
    candleSeries.setMarkers(markers);

    const eq = data.equity_curve.map(p => ({ time: toChartTime(p.time), value: p.equity }));
    equitySeries.setData(eq);

    const m = data.metrics;
    refs.metricsBox.innerHTML = renderMetrics(m, data);

    const rows = data.trades.map(t => `
        <tr>
            <td>${t.open_time}</td>
            <td>${t.close_time}</td>
            <td class="${t.side}">${t.side === "long" ? "多" : "空"}</td>
            <td>${t.qty}</td>
            <td>${t.open_price.toFixed(2)}</td>
            <td>${t.close_price.toFixed(2)}</td>
            <td>${t.holding_bars}</td>
            <td>${t.pnl.toFixed(2)}</td>
            <td>${t.fee.toFixed(2)}</td>
            <td class="${t.net_pnl >= 0 ? "pnl-win" : "pnl-loss"}">${t.net_pnl.toFixed(2)}</td>
            <td>${escape(t.open_reason)}</td>
            <td>${escape(t.close_reason)}</td>
        </tr>
    `);
    refs.tradeBody.innerHTML = rows.length ? rows.join("") : '<tr><td colspan="12" class="muted">暂无交易</td></tr>';
}

function renderMetrics(m, data) {
    const pct = v => (v == null ? "-" : (v * 100).toFixed(2) + "%");
    const num = v => (v == null ? "-" : Number(v).toFixed(2));
    const warn = data.liquidated ? `<div class="alert-warn">⚠ 回测中途爆仓，触发时间：${escape(data.liquidated_at)}</div>` : "";
    return `
        ${warn}
        <div class="metrics-grid">
            <div><label>总收益</label><b>${pct(m.total_return)}</b></div>
            <div><label>最大回撤</label><b>${num(m.max_drawdown)} (${pct(m.max_drawdown_pct)})</b></div>
            <div><label>Sharpe</label><b>${m.sharpe == null ? "-" : num(m.sharpe)}</b></div>
            <div><label>Calmar</label><b>${m.calmar == null ? "-" : num(m.calmar)}</b></div>
            <div><label>交易笔数</label><b>${m.total_trades}</b></div>
            <div><label>胜率</label><b>${pct(m.win_rate)}</b></div>
            <div><label>盈亏比</label><b>${num(m.profit_factor)}</b></div>
            <div><label>平均盈 / 亏</label><b>${num(m.avg_win)} / ${num(m.avg_loss)}</b></div>
            <div><label>最大连胜 / 连败</label><b>${m.max_win_streak} / ${m.max_loss_streak}</b></div>
            <div><label>平均持仓K数</label><b>${num(m.avg_holding_bars)}</b></div>
            <div><label>最终权益</label><b>${num(m.final_equity)}</b></div>
            <div><label>最终持仓</label><b>${m.final_position}</b></div>
        </div>
    `;
}

function escape(s) {
    return String(s || "").replace(/[&<>"']/g, c => (
        {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c]
    ));
}

function exportCsv() {
    if (!lastResult) return;
    const rows = [["open_time", "close_time", "side", "qty", "open_price", "close_price",
        "holding_bars", "pnl", "fee", "net_pnl", "open_reason", "close_reason"]];
    for (const t of lastResult.trades) {
        rows.push([t.open_time, t.close_time, t.side, t.qty, t.open_price, t.close_price,
            t.holding_bars, t.pnl, t.fee, t.net_pnl, t.open_reason, t.close_reason]);
    }
    const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `backtest-${Date.now()}.csv`;
    a.click();
}

initCharts();
initLists();
refs.run.addEventListener("click", run);
refs.exportBtn.addEventListener("click", exportCsv);
