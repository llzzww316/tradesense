// 全局变量
let chart = null;
let candleSeries = null;
let emaLine = null;
let displayBars = [];      // 显示用K线（5分钟）
let stepBars = [];         // 步进用K线（1分钟）
let currentStepIndex = 0;  // 当前步进索引
let isPlaying = false;
let playInterval = null;

const API_BASE = "http://localhost:8765";

let TICK_VALUE = {};

const el = {};
(function cacheDom() {
    const ids = [
        "chart", "status", "ohlcDisplay", "startDate", "endDate", "symbolSelect",
        "displayPeriod", "stepPeriod", "emaPeriod", "startPosition", "loadBtn",
        "modeTag", "prevBtn", "playBtn", "nextBtn", "tradeBtn", "barInfo",
        "progressBar", "progressFill", "speedSelect", "simEquity", "simAvailable",
        "simPosition", "simUnrealized", "simRealized", "simFees", "tradeLogBody",
        "simInitialCapital", "simFeePerLot", "simSettingsModal", "tradeModal",
        "tradeSymbolText", "tradePriceText", "tradeQty", "tradePositionText",
        "openLongBtn", "openShortBtn", "closePositionBtn", "tradeCancelBtn",
        "simSettingsBtn", "simSettingsCancel", "simSettingsSave"
    ];
    for (const id of ids) {
        el[id] = document.getElementById(id);
    }
})();

// 时间格式化函数（统一用于图表）
function formatChartTime(time) {
    const d = new Date(time * 1000);
    const yyyy = d.getUTCFullYear();
    const MM = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    return `${yyyy}-${MM}-${dd} ${hh}:${mm}`;
}

// 初始化图表
function initChart() {
    const container = el.chart;
    const width = container.clientWidth || 800;
    const height = 600;
    
    chart = LightweightCharts.createChart(container, {
        width: width,
        height: height,
        layout: {
            background: { type: "solid", color: "#ffffff" },
            textColor: "#333",
        },
        localization: {
            timeFormatter: formatChartTime,
        },
        grid: {
            vertLines: { color: "#eee" },
            horzLines: { color: "#eee" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                labelBackgroundColor: "#e94560",
            },
            horzLine: {
                labelBackgroundColor: "#e94560",
            },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            tickMarkFormatter: formatChartTime,
        },
        rightPriceScale: {
            borderColor: "#333",
        },
        crosshairTimeFormatter: formatChartTime,
    });
    
    candleSeries = chart.addCandlestickSeries({
        upColor: "#ef5350",
        downColor: "#26a69a",
        borderUpColor: "#ef5350",
        borderDownColor: "#26a69a",
        wickUpColor: "#ef5350",
        wickDownColor: "#26a69a",
    });
    
    emaLine = chart.addLineSeries({
        color: "#ff9800",
        lineWidth: 2,
    });
    
    window.addEventListener("resize", () => {
        const w = container.clientWidth || 800;
        chart.resize(w, 600);
    });
    
    // 订阅十字线移动事件，显示OHLC
    chart.subscribeCrosshairMove((param) => {
        const ohlcEl = el.ohlcDisplay;

        // 如果鼠标不在图表上，清除显示
        if (!param.point || !param.time) {
            ohlcEl.style.display = "none";
            return;
        }

        // 使用 param.seriesData.get() 获取当前K线数据
        let data = null;
        try {
            data = param.seriesData.get(candleSeries);
        } catch (e) {
            ohlcEl.style.display = "none";
            return;
        }

        if (!data) {
            ohlcEl.style.display = "none";
            return;
        }

        const timeStr = formatChartTime(data.time);
        ohlcEl.innerHTML = `${timeStr} | <span style="color:#f0ad4e">O:${data.open}</span> <span style="color:#ef5350">H:${data.high}</span> <span style="color:#26a69a">L:${data.low}</span> <span style="color:#4fc3f7">C:${data.close}</span>`;
        ohlcEl.style.display = "block";
    });
}

// 初始化日期选择器（默认最近30天）
function initDatePickers() {
    const today = new Date();
    const thirtyDaysAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);
    
    el.endDate.value = today.toISOString().split('T')[0];
    el.startDate.value = thirtyDaysAgo.toISOString().split('T')[0];
}

// 初始化品种列表（从后端获取）
async function initSymbolList() {
    try {
        const res = await fetch(`${API_BASE}/symbols`);
        const data = await res.json();
        const select = el.symbolSelect;
        select.innerHTML = "";
        
        if (data.symbols) {
            for (const [name, info] of Object.entries(data.symbols)) {
                const code = info.code || info;
                const tickValue = info.tick_value || 10;
                const option = document.createElement("option");
                option.value = name;
                option.textContent = `${name} (${code})`;
                select.appendChild(option);
                // 更新每跳价值
                TICK_VALUE[name] = tickValue;
            }
        }
    } catch (e) {
        console.warn("加载品种列表失败:", e);
        // 使用默认列表
        const select = el.symbolSelect;
        select.innerHTML = `
            <option value="螺纹钢">螺纹钢 (SHFE.rb2610)</option>
            <option value="热卷">热卷 (SHFE.hc2610)</option>
            <option value="PVC">PVC (DCE.v2609)</option>
            <option value="纯碱">纯碱 (ZCE.SA2509)</option>
        `;
        TICK_VALUE = {"螺纹钢": 10, "热卷": 10, "PVC": 5, "纯碱": 20};
    }
}

// 加载回放数据（显示周期 + 步进周期）
async function loadData() {
    const symbol = el.symbolSelect.value.trim();
    if (!symbol) {
        alert("请输入品种名称或代码");
        return;
    }
    const displayPeriod = el.displayPeriod.value;
    const stepPeriod = el.stepPeriod.value;
    const emaPeriod = el.emaPeriod.value;
    const startDate = el.startDate.value;
    const endDate = el.endDate.value;
    
    el.status.textContent = "加载回放数据...";
    el.status.classList.remove("hidden");
    
    try {
        let url = `${API_BASE}/replay_data?symbol=${encodeURIComponent(symbol)}` +
            `&display_period=${displayPeriod}&step_period=${stepPeriod}` +
            `&count=2000&ma_period=${emaPeriod}`;
        
        if (startDate) url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        
        const res = await fetch(url);
        const data = await res.json();
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        displayBars = data.display;
        stepBars = data.step;
        
        // 更新模式标签
        el.modeTag.textContent =
            `${displayPeriod} / ${stepPeriod}`;
        
        // 初始显示：根据下拉选择设定起始位置
        const startPosSelect = el.startPosition;
        const startPosValue = startPosSelect.value;
        if (startPosValue === "all") {
            currentStepIndex = stepBars.length - 1; // 最末尾，即全部已加载
        } else {
            const N = parseInt(startPosValue);
            currentStepIndex = Math.min(N, stepBars.length - 1);
        }
        updateChart();
        updateUI();
        
        el.status.classList.add("hidden");
        
        // 启用按钮
        el.prevBtn.disabled = false;
        el.playBtn.disabled = false;
        el.nextBtn.disabled = false;
        
    } catch (e) {
        el.status.textContent = "加载失败: " + e.message;
        console.error(e);
    }
}

// 转换时间格式为Lightweight Charts需要的Unix时间戳（秒）
// 时间字符串格式: "2026-03-25 13:35:00" (北京时间 UTC+8)
function toChartTime(timeStr) {
    // 手动解析日期字符串
    const parts = timeStr.match(/(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
    if (parts) {
        const [, yyyy, MM, dd, hh, mm, ss] = parts.map(Number);
        // 直接用北京时间创建UTC时间戳
        // Date.UTC returns milliseconds, divide by 1000 for seconds
        return Math.floor(Date.UTC(yyyy, MM - 1, dd, hh, mm, ss) / 1000);
    }
    const date = new Date(timeStr);
    return Math.floor(date.getTime() / 1000);
}

// 更新图表
function updateChart() {
    if (displayBars.length === 0) return;
    
    if (stepBars.length === 0) {
        // 无步进数据时，直接显示所有历史K线
        const candleData = displayBars.map(bar => ({
            time: toChartTime(bar.time),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
        }));
        candleSeries.setData(candleData);
        emaLine.setData(displayBars.filter(bar => bar.ema).map(bar => ({
            time: toChartTime(bar.time),
            value: bar.ema,
        })));
        chart.timeScale().scrollToRealTime();
        return;
    }
    
    // 有步进数据时的逻辑：
    // 1. 已完成的5分钟K线：直接用原始数据
    // 2. 当前正在形成的5分钟K线：基于1分钟数据实时计算
    
    const currentStep = stepBars[currentStepIndex];
    const currentStepTime = currentStep.time;
    
    // 找到当前1分钟所属的5分钟K线索引
    let currentDisplayIndex = 0;
    for (let i = displayBars.length - 1; i >= 0; i--) {
        if (displayBars[i].time <= currentStepTime) {
            currentDisplayIndex = i;
            break;
        }
    }
    
    // 分离：已完成的K线 + 当前K线
    const completedBars = displayBars.slice(0, currentDisplayIndex);
    const currentBar = displayBars[currentDisplayIndex];
    const currentBarTime = currentBar.time;
    
    // 计算当前5分钟K线从开始到当前1分钟步进的OHLC
    let high = currentBar.open;
    let low = currentBar.open;
    let close = currentStep.close;
    
    for (let i = 0; i <= currentStepIndex; i++) {
        const t = stepBars[i].time;
        if (t >= currentBarTime && t <= currentStepTime) {
            high = Math.max(high, stepBars[i].high);
            low = Math.min(low, stepBars[i].low);
        }
    }
    
    // 构建完整K线数据
    const candleData = completedBars.map(bar => ({
        time: toChartTime(bar.time),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
    }));
    
    // 添加当前正在形成的K线
    candleData.push({
        time: toChartTime(currentBarTime),
        open: currentBar.open,
        high: high,
        low: low,
        close: close,
    });
    
    candleSeries.setData(candleData);
    
    // EMA（只用已完成的K线）
    emaLine.setData(completedBars.filter(bar => bar.ema).map(bar => ({
        time: toChartTime(bar.time),
        value: bar.ema,
    })));
    
    chart.timeScale().scrollToRealTime();
}

// 更新UI状态
function updateUI() {
    el.barInfo.textContent =
        `${currentStepIndex + 1} / ${stepBars.length} | ${getCurrentDisplayIndex() + 1} / ${displayBars.length}`;
    
    const progress = stepBars.length > 0 ? ((currentStepIndex + 1) / stepBars.length) * 100 : 0;
    el.progressFill.style.width = `${progress}%`;
    
    el.prevBtn.disabled = currentStepIndex === 0;
    el.nextBtn.disabled = currentStepIndex >= stepBars.length - 1;
    
    const playBtn = el.playBtn;
    playBtn.textContent = isPlaying ? "⏸ 暂停" : "▶ 播放";
}

function getCurrentDisplayIndex() {
    if (stepBars.length === 0 || displayBars.length === 0) return 0;
    const currentStepTime = stepBars[currentStepIndex].time;
    for (let i = 0; i < displayBars.length; i++) {
        if (displayBars[i].time <= currentStepTime) {
            if (i === displayBars.length - 1) return i;
        } else {
            return i - 1;
        }
    }
    return displayBars.length - 1;
}

// 播放控制
function play() {
    if (isPlaying) {
        stopPlay();
        return;
    }
    
    if (currentStepIndex >= stepBars.length - 1) {
        currentStepIndex = 0;
    }
    
    isPlaying = true;
    const speed = parseInt(el.speedSelect.value);
    
    playInterval = setInterval(() => {
        currentStepIndex++;
        if (currentStepIndex >= stepBars.length - 1) {
            stopPlay();
            return;
        }
        updateChart();
        updateUI();
        updateSimAccountOnStep();
    }, speed);
    
    updateUI();
}

function stopPlay() {
    isPlaying = false;
    if (playInterval) {
        clearInterval(playInterval);
        playInterval = null;
    }
    updateUI();
}

// 步进控制
function prevStep() {
    if (currentStepIndex > 0) {
        stopPlay();
        currentStepIndex--;
        updateChart();
        updateUI();
        updateSimAccountOnStep();
    }
}

function nextStep() {
    if (currentStepIndex < stepBars.length - 1) {
        stopPlay();
        currentStepIndex++;
        updateChart();
        updateUI();
        updateSimAccountOnStep();
    }
}

// ==================== 模拟交易模块 ====================

// 模拟账户状态
let simAccount = {
    initialCapital: 10000,
    feePerLot: 20,
    equity: 10000,
    available: 10000,
    position: null, // { direction: "long"|"short", qty: number, avgPrice: number }
    realized: 0,
    fees: 0,
    tradeLogs: [],
};

// 从 localStorage 加载账户
function loadSimAccount() {
    try {
        const saved = localStorage.getItem("tradesense_sim_account");
        if (saved) {
            const parsed = JSON.parse(saved);
            simAccount = { ...simAccount, ...parsed };
        }
    } catch (e) {
        console.warn("加载模拟账户失败:", e);
    }
    // 始终用当前持仓价格计算浮盈
    recalculateEquity();
    updateSimAccountBar();
    renderTradeLog();
}

// 保存账户到 localStorage
function saveSimAccount() {
    try {
        localStorage.setItem("tradesense_sim_account", JSON.stringify(simAccount));
    } catch (e) {
        console.warn("保存模拟账户失败:", e);
    }
}

// 重置账户（重新开始）
function resetSimAccount(initialCapital, feePerLot) {
    simAccount = {
        initialCapital: initialCapital,
        feePerLot: feePerLot,
        equity: initialCapital,
        available: initialCapital,
        position: null,
        realized: 0,
        fees: 0,
        tradeLogs: [],
    };
    saveSimAccount();
    updateSimAccountBar();
    renderTradeLog();
}

// 获取当前价格（当前步进K线的收盘价）
function getCurrentPrice() {
    if (stepBars.length === 0) return null;
    return stepBars[currentStepIndex].close;
}

// 获取当前品种
function getCurrentSymbol() {
    const symbol = el.symbolSelect.value.trim();
    return symbol || "螺纹钢";
}

// 计算浮盈
function calculateUnrealizedPnl() {
    if (!simAccount.position) return 0;
    const currentPrice = getCurrentPrice();
    if (currentPrice === null) return 0;
    const pos = simAccount.position;
    const tickValue = TICK_VALUE[getCurrentSymbol()] || 10;
    const pnl = (currentPrice - pos.avgPrice) * pos.qty * tickValue;
    return pos.direction === "short" ? -pnl : pnl;
}

// 重新计算权益（权益 = 可用 + 浮盈）
function recalculateEquity() {
    const unrealized = calculateUnrealizedPnl();
    simAccount.equity = simAccount.available + unrealized;
}

// 更新顶部状态栏
function updateSimAccountBar() {
    const unrealized = calculateUnrealizedPnl();
    el.simEquity.textContent = simAccount.equity.toFixed(2);
    el.simAvailable.textContent = simAccount.available.toFixed(2);
    const pos = simAccount.position;
    if (pos) {
        const dirText = pos.direction === "long" ? "多" : "空";
        el.simPosition.textContent = `${dirText}${pos.qty}手 @ ${pos.avgPrice.toFixed(1)}`;
    } else {
        el.simPosition.textContent = "无";
    }
    el.simUnrealized.textContent = unrealized.toFixed(2);
    el.simRealized.textContent = simAccount.realized.toFixed(2);
    el.simFees.textContent = simAccount.fees.toFixed(2);
}

// 记录交易日志
function addTradeLog(action, price, qty, fee, netPnl) {
    const symbol = getCurrentSymbol();
    const time = stepBars.length > 0 ? stepBars[currentStepIndex].time : new Date().toLocaleString();
    simAccount.tradeLogs.unshift({
        time,
        symbol,
        action,
        price,
        qty,
        fee,
        netPnl,
        equity: simAccount.equity,
    });
    // 最多保留100条
    if (simAccount.tradeLogs.length > 100) {
        simAccount.tradeLogs.pop();
    }
}

// 渲染交易日志表格
function renderTradeLog() {
    const tbody = el.tradeLogBody;
    if (simAccount.tradeLogs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="muted">暂无交易记录</td></tr>';
        return;
    }
    tbody.innerHTML = simAccount.tradeLogs.map(log => {
        const pnlColor = log.netPnl >= 0 ? '#ef5350' : '#26a69a';
        return `<tr>
            <td>${log.time}</td>
            <td>${log.symbol}</td>
            <td>${log.action}</td>
            <td>${log.price}</td>
            <td>${log.qty}</td>
            <td>${log.fee.toFixed(2)}</td>
            <td style="color:${pnlColor};">${log.netPnl.toFixed(2)}</td>
            <td>${log.equity.toFixed(2)}</td>
        </tr>`;
    }).join("");
}

// 交易模态框
function openTradeModal() {
    const symbol = getCurrentSymbol();
    const price = getCurrentPrice();
    const pos = simAccount.position;
    el.tradeSymbolText.textContent = symbol;
    el.tradePriceText.textContent = price !== null ? price.toFixed(1) : "-";
    el.tradeQty.value = 1;
    el.tradePositionText.textContent =
        pos ? `${pos.direction === "long" ? "多" : "空"} ${pos.qty}手 @ ${pos.avgPrice.toFixed(1)}` : "无";
    el.tradeModal.classList.add("show");
}

function closeTradeModal() {
    el.tradeModal.classList.remove("show");
}

// 开仓（多 / 空）
function openPosition(direction) {
    const price = getCurrentPrice();
    if (price === null) return;
    const qty = parseInt(el.tradeQty.value) || 1;
    const fee = simAccount.feePerLot * qty;

    if (simAccount.position) {
        alert("当前已有持仓，请先平仓！");
        return;
    }
    if (simAccount.available < fee) {
        alert("可用资金不足支付手续费！");
        return;
    }

    simAccount.position = { direction, qty, avgPrice: price };
    simAccount.available -= fee;
    simAccount.fees += fee;
    recalculateEquity();
    const actionLabel = direction === "long" ? "开多" : "开空";
    addTradeLog(actionLabel, price, qty, fee, -fee);
    saveSimAccount();
    updateSimAccountBar();
    renderTradeLog();
    closeTradeModal();
}

// 平仓
function closePosition() {
    const price = getCurrentPrice();
    if (price === null || !simAccount.position) return;
    const pos = simAccount.position;
    const qty = pos.qty;
    const fee = simAccount.feePerLot * qty;
    const tickValue = TICK_VALUE[getCurrentSymbol()] || 10;
    const pnl = (price - pos.avgPrice) * qty * tickValue;
    const grossPnl = pos.direction === "short" ? -pnl : pnl;

    const realizedPnl = grossPnl - fee;
    simAccount.available += realizedPnl;
    simAccount.realized += realizedPnl;
    simAccount.fees += fee;
    simAccount.position = null;
    recalculateEquity();
    addTradeLog("平仓", price, qty, fee, realizedPnl);
    saveSimAccount();
    updateSimAccountBar();
    renderTradeLog();
    closeTradeModal();
}

// 模拟设置模态框
function openSimSettingsModal() {
    el.simInitialCapital.value = simAccount.initialCapital;
    el.simFeePerLot.value = simAccount.feePerLot;
    el.simSettingsModal.classList.add("show");
}

function closeSimSettingsModal() {
    el.simSettingsModal.classList.remove("show");
}

function saveSimSettings() {
    const initialCapital = parseFloat(el.simInitialCapital.value) || 10000;
    const feePerLot = parseFloat(el.simFeePerLot.value) || 20;
    resetSimAccount(initialCapital, feePerLot);
    closeSimSettingsModal();
}

// 回放步进时更新权益显示
function updateSimAccountOnStep() {
    recalculateEquity();
    updateSimAccountBar();
}

// ==================== 事件绑定 ====================

el.loadBtn.addEventListener("click", () => {
    loadData().then(() => {
        // 数据加载成功后启用交易按钮
        el.tradeBtn.disabled = false;
    });
});
el.prevBtn.addEventListener("click", prevStep);
el.nextBtn.addEventListener("click", nextStep);
el.playBtn.addEventListener("click", play);

// 交易按钮
el.tradeBtn.addEventListener("click", openTradeModal);
el.openLongBtn.addEventListener("click", () => openPosition("long"));
el.openShortBtn.addEventListener("click", () => openPosition("short"));
el.closePositionBtn.addEventListener("click", closePosition);
el.tradeCancelBtn.addEventListener("click", closeTradeModal);

// 模拟设置
el.simSettingsBtn.addEventListener("click", openSimSettingsModal);
el.simSettingsCancel.addEventListener("click", closeSimSettingsModal);
el.simSettingsSave.addEventListener("click", saveSimSettings);

// 进度条点击
el.progressBar.addEventListener("click", (e) => {
    if (stepBars.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = x / rect.width;
    const newIndex = Math.floor(ratio * stepBars.length);
    stopPlay();
    currentStepIndex = Math.max(0, Math.min(newIndex, stepBars.length - 1));
    updateChart();
    updateUI();
    updateSimAccountOnStep();
});

// 键盘快捷键
document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") {
        prevStep();
    } else if (e.key === "ArrowRight") {
        nextStep();
    } else if (e.key === " ") {
        e.preventDefault();
        play();
    }
});

// 初始化
initChart();
initDatePickers();
initSymbolList();
loadSimAccount();
