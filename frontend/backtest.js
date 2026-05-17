// `resolveApiBase` / `toChartTime` 在 shared.js 中定义，先于本文件以 `defer` 加载。
const API_BASE = resolveApiBase();

// ---- DOM refs ----
const $ = id => document.getElementById(id);
const refs = {
    symbol: $("bt-symbol"), contract: $("bt-contract"),
    period: $("bt-period"), start: $("bt-start"), end: $("bt-end"),
    strategy: $("bt-strategy"), params: $("bt-params"),
    capital: $("bt-capital"), slippage: $("bt-slippage"),
    tickSize: $("bt-tick-size"), tickValue: $("bt-tick-value"),
    fee: $("bt-fee"), margin: $("bt-margin"), intraday: $("bt-intraday"),
    run: $("bt-run"), status: $("bt-status"),
    chart: $("bt-chart"), equityChart: $("bt-equity-chart"),
    metricsBox: $("bt-metrics"), tradeBody: $("bt-trade-body"),
    exportBtn: $("bt-export"),
    chartEmpty: $("bt-chart-empty"), equityEmpty: $("bt-equity-empty"),
};

let chart = null, candleSeries = null, equityChart = null, equitySeries = null;
let lastResult = null;
let strategyParams = [];  // {name, params: [{name, type, default}]}

// ---- 初始化品种 + 策略下拉 ----
async function refreshContractList() {
    const select = refs.contract;
    const sym = refs.symbol.value.trim();
    select.innerHTML = "";
    select.disabled = true;

    if (!sym) {
        select.appendChild(new Option("默认主力", ""));
        return;
    }

    select.appendChild(new Option("加载合约…", ""));
    try {
        const res = await fetch(`${API_BASE}/contracts?symbol=${encodeURIComponent(sym)}`);
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.detail || data.error || `HTTP ${res.status}`);
        }
        select.innerHTML = "";
        select.appendChild(new Option("默认主力", ""));
        const contracts = data.contracts || [];
        for (const c of contracts) {
            select.appendChild(new Option(c, c));
        }
        const def = data.default_contract && contracts.includes(data.default_contract)
            ? data.default_contract
            : "";
        if (def) {
            select.value = def;
        }
    } catch (e) {
        console.warn("加载合约列表失败:", e);
        select.innerHTML = "";
        select.appendChild(new Option("合约列表失败", ""));
    }

    select.disabled = false;
}

async function initLists() {
    try {
        const [symResp, stratResp] = await Promise.all([
            fetch(`${API_BASE}/symbols`).then(r => r.ok ? r.json() : Promise.reject(new Error(`GET /symbols HTTP ${r.status}`))),
            fetch(`${API_BASE}/backtest/strategies`).then(r => r.ok ? r.json() : Promise.reject(new Error(`GET /backtest/strategies HTTP ${r.status}`))),
        ]);
        for (const name of Object.keys(symResp.symbols || {})) {
            refs.symbol.appendChild(new Option(name, name));
        }
        strategyParams = stratResp.strategies || [];
        for (const s of strategyParams) {
            refs.strategy.appendChild(new Option(s.name, s.name));
        }
        if (refs.symbol.value) refreshContractList();
        renderParamInputs();
    } catch (e) {
        showStatus("加载品种/策略列表失败: " + e.message, "error");
    }
}

// ---- 参数表单 ----
function renderParamInputs() {
    const name = refs.strategy.value;
    const found = strategyParams.find(s => s.name === name);
    const params = found ? found.params : [];
    let html = "";
    for (const p of params) {
        const val = p.default != null ? p.default : "";
        if (p.type === "boolean") {
            const checked = val ? " checked" : "";
            html += `<label class="bt-param bt-param-label"><input type="checkbox" id="btp-${htmlEscape(p.name)}"${checked}> ${htmlEscape(p.name)}</label>`;
        } else {
            const step = p.type === "number" ? ' step="any"' : '';
            html += `<label class="bt-param-wrap"><span class="bt-param-name">${htmlEscape(p.name)}</span><input class="bt-param" id="btp-${htmlEscape(p.name)}" type="${p.type === "number" ? "number" : "text"}" value="${escapeAttr(String(val))}"${step}></label>`;
        }
    }
    refs.params.innerHTML = html;
}

// ---- 状态提示 ----
function showStatus(msg, type = "loading") {
    refs.status.textContent = msg;
    refs.status.className = `status ${type}`;
    refs.status.classList.remove("hidden");
    if (type === "error") {
        const btn = document.createElement("button");
        btn.textContent = "重试";
        btn.className = "status-retry";
        btn.onclick = () => run();
        refs.status.appendChild(btn);
    }
}

function hideStatus() {
    refs.status.classList.add("hidden");
    refs.status.className = "status hidden";
}

// ---- 图表 ----
function initCharts() {
    const w = refs.chart.clientWidth || window.innerWidth - 40;
    const h = refs.chart.clientHeight || 400;
    chart = LightweightCharts.createChart(refs.chart, {
        width: w, height: h,
        layout: { background: { color: "#fff" } },
        timeScale: { timeVisible: true, secondsVisible: false },
    });
    candleSeries = chart.addCandlestickSeries({
        upColor: "#ef5350", downColor: "#26a69a",
        borderUpColor: "#ef5350", borderDownColor: "#26a69a",
        wickUpColor: "#ef5350", wickDownColor: "#26a69a",
    });
    const ew = refs.equityChart.clientWidth || w;
    const eh = refs.equityChart.clientHeight || 220;
    equityChart = LightweightCharts.createChart(refs.equityChart, {
        width: ew, height: eh,
        layout: { background: { color: "#fff" } },
        timeScale: { timeVisible: true, secondsVisible: false },
    });
    equitySeries = equityChart.addLineSeries({ color: "#1976d2", lineWidth: 2 });

    // 同步十字线和时间轴
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) equityChart.timeScale().setVisibleLogicalRange(range);
    });
    equityChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (range) chart.timeScale().setVisibleLogicalRange(range);
    });

    chart.subscribeCrosshairMove(param => {
        if (param.time) {
            equityChart.setCrosshairPosition(0, param.time, equitySeries);
        } else {
            equityChart.clearCrosshairPosition();
        }
    });
    equityChart.subscribeCrosshairMove(param => {
        if (param.time) {
            chart.setCrosshairPosition(0, param.time, candleSeries);
        } else {
            chart.clearCrosshairPosition();
        }
    });

    const ro = new ResizeObserver(entries => {
        for (const entry of entries) {
            if (entry.target === refs.chart && chart) {
                chart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height });
            } else if (entry.target === refs.equityChart && equityChart) {
                equityChart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height });
            }
        }
    });
    ro.observe(refs.chart);
    ro.observe(refs.equityChart);
}

// ---- 运行回测 ----
async function run() {
    showStatus("运行中…", "loading");
    refs.run.disabled = true;

    const found = strategyParams.find(s => s.name === refs.strategy.value);
    const paramDefs = found ? found.params : [];
    const params = {};
    for (const p of paramDefs) {
        const el = document.getElementById(`btp-${p.name}`);
        if (!el) continue;
        if (p.type === "boolean") {
            params[p.name] = el.checked;
        } else if (p.type === "number") {
            const v = parseFloat(el.value);
            if (!isNaN(v)) params[p.name] = v;
        } else {
            params[p.name] = el.value;
        }
    }

    const body = {
        symbol: refs.symbol.value,
        contract: refs.contract.value.trim() || null,
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
        hideStatus();
        refs.exportBtn.disabled = false;
    } catch (e) {
        showStatus("失败: " + e.message, "error");
    } finally {
        refs.run.disabled = false;
    }
}

// ---- 渲染 ----
function render(data) {
    // 隐藏空状态占位
    if (refs.chartEmpty) refs.chartEmpty.style.display = "none";
    if (refs.equityEmpty) refs.equityEmpty.style.display = "none";

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

    // 合并同一时间的 markers，避免 Lightweight Charts 报错 (Record is out of order / duplicate time)
    const mergedMarkers = [];
    for (const m of markers) {
        if (mergedMarkers.length > 0 && mergedMarkers[mergedMarkers.length - 1].time === m.time) {
            const last = mergedMarkers[mergedMarkers.length - 1];
            last.text += ` | ${m.text}`;
        } else {
            mergedMarkers.push(m);
        }
    }
    candleSeries.setMarkers(mergedMarkers);

    const eq = data.equity_curve.map(p => ({ time: toChartTime(p.time), value: p.equity }));
    equitySeries.setData(eq);

    const m = data.metrics;
    refs.metricsBox.innerHTML = renderMetrics(m, data);

    const rows = data.trades.map(t => `
        <tr>
            <td>${htmlEscape(t.open_time)}</td>
            <td>${htmlEscape(t.close_time)}</td>
            <td class="${htmlEscape(t.side)}">${t.side === "long" ? "多" : "空"}</td>
            <td>${htmlEscape(String(t.qty))}</td>
            <td>${t.open_price.toFixed(2)}</td>
            <td>${t.close_price.toFixed(2)}</td>
            <td>${htmlEscape(String(t.holding_bars))}</td>
            <td>${t.pnl.toFixed(2)}</td>
            <td>${t.fee.toFixed(2)}</td>
            <td class="${t.net_pnl >= 0 ? "pnl-win" : "pnl-loss"}">${t.net_pnl.toFixed(2)}</td>
            <td>${htmlEscape(t.open_reason)}</td>
            <td>${htmlEscape(t.close_reason)}</td>
        </tr>
    `);
    refs.tradeBody.innerHTML = rows.length ? rows.join("") : '<tr><td colspan="12" class="muted">暂无交易</td></tr>';
}

function renderMetrics(m, data) {
    const pct = v => (v == null ? "-" : (v * 100).toFixed(2) + "%");
    const num = v => (v == null ? "-" : Number(v).toFixed(2));
    const warn = data.liquidated
        ? `<div class="alert-warn"><span class="alert-icon" aria-hidden="true"></span>回测中途爆仓，触发时间：${htmlEscape(data.liquidated_at)}</div>`
        : "";
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

function escapeAttr(s) {
    return String(s || "").replace(/["&<>]/g, c => (
        {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;"}[c]
    ));
}

function htmlEscape(s) {
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

requestAnimationFrame(() => {
    initCharts();
    initLists();
    refs.exportBtn.disabled = true;
    refs.symbol.addEventListener("change", refreshContractList);
    refs.strategy.addEventListener("change", renderParamInputs);
    refs.run.addEventListener("click", run);
    refs.exportBtn.addEventListener("click", exportCsv);
});
