/**
 * realtime.js — 实时行情 SSE 客户端
 * 管理 EventSource 连接、品种订阅、数据渲染
 */

const STORAGE_KEY = "realtime_symbols";
let eventSource = null;
let subscribedSymbols = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");

// ── 初始化 ──────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("symbol-input");
    const btnAdd = document.getElementById("btn-add");

    btnAdd.addEventListener("click", () => addSymbol(input.value));
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") addSymbol(input.value);
    });

    // 恢复已订阅列表
    subscribedSymbols.forEach((s) => renderSubscribedBadge(s));
    if (subscribedSymbols.length > 0) connectSSE();
    renderQuoteGrid();
});

// ── 品种管理 ────────────────────────────────────────────────────────

function addSymbol(raw) {
    const name = (raw || "").trim();
    if (!name) return;
    if (subscribedSymbols.includes(name)) {
        input.value = "";
        return;
    }
    subscribedSymbols.push(name);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(subscribedSymbols));
    renderSubscribedBadge(name);
    renderQuoteGrid();
    connectSSE();
    document.getElementById("symbol-input").value = "";
}

function removeSymbol(name) {
    subscribedSymbols = subscribedSymbols.filter((s) => s !== name);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(subscribedSymbols));
    // 移除 badge
    const badge = document.getElementById(`badge-${name}`);
    if (badge) badge.remove();
    // 移除卡片
    const card = document.getElementById(`card-${name}`);
    if (card) card.remove();
    // 更新空状态
    if (subscribedSymbols.length === 0) {
        renderQuoteGrid();
        disconnectSSE();
    } else {
        connectSSE(); // 重新连接更新订阅
    }
}

function renderSubscribedBadge(name) {
    const list = document.getElementById("subscribed-list");
    if (document.getElementById(`badge-${name}`)) return;

    const badge = document.createElement("span");
    badge.id = `badge-${name}`;
    badge.className = "subscribed-badge";
    badge.innerHTML = `${name} <button class="badge-remove" onclick="removeSymbol('${name}')">&times;</button>`;
    list.appendChild(badge);
}

// ── SSE 连接 ────────────────────────────────────────────────────────

function connectSSE() {
    if (eventSource) eventSource.close();
    if (subscribedSymbols.length === 0) return;

    const symbolsParam = encodeURIComponent(subscribedSymbols.join(","));
    eventSource = new EventSource(`/api/sse/quotes?symbols=${symbolsParam}`);

    const statusEl = document.getElementById("conn-status");
    statusEl.textContent = "连接中…";
    statusEl.className = "status-badge connecting";

    eventSource.onopen = () => {
        statusEl.textContent = "已连接";
        statusEl.className = "status-badge connected";
    };

    eventSource.addEventListener("quote", (e) => {
        try {
            const data = JSON.parse(e.data);
            updateQuoteCard(data);
        } catch (err) {
            console.error("解析报价数据失败:", err);
        }
    });

    eventSource.addEventListener("error", (e) => {
        try {
            const data = JSON.parse(e.data || "{}");
            showCardError(data.symbol, data.error || data.message);
        } catch {
            // EventSource 自动重连
        }
    });

    eventSource.onerror = () => {
        statusEl.textContent = "连接断开，重连中…";
        statusEl.className = "status-badge disconnected";
    };
}

function disconnectSSE() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    const statusEl = document.getElementById("conn-status");
    statusEl.textContent = "未连接";
    statusEl.className = "status-badge disconnected";
}

// ── 报价卡片渲染 ────────────────────────────────────────────────────

function renderQuoteGrid() {
    const grid = document.getElementById("quotes-grid");
    grid.innerHTML = "";

    if (subscribedSymbols.length === 0) {
        grid.innerHTML = '<div class="empty-state"><p>添加品种开始接收实时行情</p></div>';
        return;
    }

    subscribedSymbols.forEach((name) => {
        if (document.getElementById(`card-${name}`)) return;

        const card = document.createElement("div");
        card.id = `card-${name}`;
        card.className = "quote-card";
        card.innerHTML = `
            <div class="card-header">
                <span class="card-symbol">${name}</span>
                <span class="card-contract">--</span>
                <button class="card-close" onclick="removeSymbol('${name}')">&times;</button>
            </div>
            <div class="card-price">
                <span class="price-value">--</span>
                <span class="price-change"></span>
            </div>
            <div class="card-details">
                <div class="detail-row">
                    <span class="detail-label">开盘</span>
                    <span class="detail-value open">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">最高</span>
                    <span class="detail-value high">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">最低</span>
                    <span class="detail-value low">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">成交量</span>
                    <span class="detail-value volume">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">买一</span>
                    <span class="detail-value bid1">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">卖一</span>
                    <span class="detail-value ask1">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">时间</span>
                    <span class="detail-value time">--</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">数据源</span>
                    <span class="detail-value source">--</span>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}

function updateQuoteCard(data) {
    const card = document.getElementById(`card-${data.symbol}`);
    if (!card) return;

    // 合约
    const contractEl = card.querySelector(".card-contract");
    contractEl.textContent = data.contract || "--";

    // 价格
    const priceEl = card.querySelector(".price-value");
    const changeEl = card.querySelector(".price-change");
    priceEl.textContent = data.price != null ? data.price.toFixed(2) : "--";

    // 涨跌幅
    if (data.price && data.last_close) {
        const change = data.price - data.last_close;
        const pct = ((change / data.last_close) * 100).toFixed(2);
        changeEl.textContent = `${change >= 0 ? "+" : ""}${pct}%`;
        changeEl.className = `price-change ${change >= 0 ? "up" : "down"}`;
        priceEl.className = `price-value ${change >= 0 ? "up" : "down"}`;
    } else {
        changeEl.textContent = "";
        changeEl.className = "price-change";
    }

    // 详情
    setText(card, ".open", data.open);
    setText(card, ".high", data.high);
    setText(card, ".low", data.low);
    setText(card, ".volume", data.volume);
    setText(card, ".bid1", data.bid1);
    setText(card, ".ask1", data.ask1);
    setText(card, ".time", data.time);
    setText(card, ".source", data.source);

    // 数据源标签
    const sourceEl = card.querySelector(".source");
    if (sourceEl) {
        sourceEl.className = `detail-value source ${data.source === "hq" || data.source === "exhq" ? "source-live" : "source-offline"}`;
    }
}

function showCardError(symbol, msg) {
    const card = document.getElementById(`card-${symbol}`);
    if (!card) return;
    const priceEl = card.querySelector(".price-value");
    priceEl.textContent = "⚠";
    priceEl.title = msg;
}

function setText(parent, selector, value) {
    const el = parent.querySelector(selector);
    if (el && value != null) {
        el.textContent = typeof value === "number" ? value.toFixed(2) : value;
    }
}
