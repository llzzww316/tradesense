/**
 * 前端通用工具：API 基址解析 + 时间字符串 → 图表用 Unix 秒。
 * app.js / backtest.js 共用；以普通 <script> 形式先于页面脚本加载，
 * 函数挂到全局作用域。
 */

/**
 * API 根地址（无尾部斜杠）。REST 挂载在服务端 `/api` 下。
 * 优先级：
 * 1) html 根节点 data-api-base — 形如 http://主机:8765/api
 * 2) localStorage tradesense_api_base
 * 3) 页面为 http(s) 且端口为 8765 — 同源整合模式，默认 /api（`uv run python server.py` 场景）
 * 4) localhost / 127.0.0.1 且非 8765（如 npm http-server）— 指向本机 http(s)://:8765/api
 * 5) 否则 "" — 同源相对路径；仅当外层反向代理也把 API 暴露在 /api 时适用
 */
function resolveApiBase() {
    const root = document.documentElement;
    if (root.hasAttribute("data-api-base")) {
        return root.getAttribute("data-api-base").trim().replace(/\/$/, "");
    }
    try {
        const ls = localStorage.getItem("tradesense_api_base");
        if (ls != null && ls.trim() !== "") {
            return ls.trim().replace(/\/$/, "");
        }
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

/**
 * 后端 bob 时间字符串（"YYYY-MM-DD HH:MM:SS"，北京时间 UTC+8）
 * → Lightweight Charts 用的 Unix 秒时间戳。
 * 约定：前端按 UTC 显示（见 formatChartTime），所以这里把字符串当作 UTC 秒传入，
 * 配合 UTC 标签即可得到正确的北京时间显示。
 */
function toChartTime(timeStr) {
    const parts = timeStr.match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
    if (parts) {
        const [, yyyy, MM, dd, hh, mm, ss] = parts.map(Number);
        return Math.floor(Date.UTC(yyyy, MM - 1, dd, hh, mm, ss) / 1000);
    }
    return Math.floor(new Date(timeStr).getTime() / 1000);
}
