/**
 * A股ETF动量轮动筛选系统 - 前端交互逻辑
 * 版本 v3 - 修复了所有已知bug
 */

let equityChart = null;
let heatmapChart = null;
let yearlyChart = null;
let schedulerTimer = null;

// 从URL提取key并存到sessionStorage，防止手机浏览器丢失参数
function getAuthKey() {
    const p = new URLSearchParams(window.location.search);
    const key = p.get("key");
    if (key) { sessionStorage.setItem("etf_key", key); return key; }
    return sessionStorage.getItem("etf_key") || "";
}

function apiUrl(path) {
    const key = getAuthKey();
    if (!key) return path;
    const sep = path.includes("?") ? "&" : "?";
    return path + sep + "key=" + key;
}

// ==========================================
// 初始化
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
    // 存储key（防手机浏览器丢失URL参数）
    getAuthKey();
    // 权重滑块联动
    ["w5", "w10", "w20"].forEach(id => {
        const slider = document.getElementById(id);
        const display = document.getElementById(id + "Val");
        if (slider && display) {
            slider.addEventListener("input", () => {
                display.textContent = slider.value + "%";
            });
        }
    });

    // 初始化图表（延迟，等ECharts CDN加载完成）
    setTimeout(initCharts, 500);

    // 检查定时任务状态
    checkSchedulerStatus();
    schedulerTimer = setInterval(checkSchedulerStatus, 60000);
});

function initCharts() {
    try {
        const eq = document.getElementById("equityChart");
        const hm = document.getElementById("heatmapChart");
        const yr = document.getElementById("yearlyChart");
        if (eq && typeof echarts !== "undefined") equityChart = echarts.init(eq);
        if (hm && typeof echarts !== "undefined") heatmapChart = echarts.init(hm);
        if (yr && typeof echarts !== "undefined") yearlyChart = echarts.init(yr);
    } catch(e) {
        console.log("ECharts未就绪，图表将在点击回测时初始化");
    }

    window.addEventListener("resize", () => {
        try { equityChart?.resize(); heatmapChart?.resize(); yearlyChart?.resize(); } catch(e) {}
    });
}

// ==========================================
// 确保ECharts已初始化
// ==========================================
function ensureCharts() {
    if (!equityChart || equityChart.isDisposed()) {
        initCharts();
    }
}

// ==========================================
// 运行回测
// ==========================================
async function runBacktest() {
    ensureCharts();

    const btn = document.querySelector(".btn-run");
    const origText = btn.textContent;
    btn.textContent = "⏳ 计算中...";
    btn.disabled = true;
    showLoading(true);

    try {
        const params = new URLSearchParams({
            start_date: document.getElementById("startDate").value,
            end_date: document.getElementById("endDate").value,
            capital: parseFloat(document.getElementById("capital").value) * 10000,
            w5: parseInt(document.getElementById("w5").value) / 100,
            w10: parseInt(document.getElementById("w10").value) / 100,
            w20: parseInt(document.getElementById("w20").value) / 100,
        });

        const resp = await fetch(apiUrl("/api/backtest?" + params.toString()));
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || "服务器错误 (" + resp.status + ")");
        }
        const data = await resp.json();

        updateMetrics(data.metrics, data.final_value, data.initial_capital);
        updateEquityChart(data.equity_curve, data.benchmark);
        updateHeatmap(data.monthly_heatmap);
        updateYearlyChart(data.yearly_returns);
        updateTradeTable(data.trade_summary);
        fetchSignals();

    } catch (err) {
        alert("回测失败: " + err.message + "\n\n提示: 首次加载数据需要约5秒，请稍后重试。");
    } finally {
        showLoading(false);
        btn.textContent = origText;
        btn.disabled = false;
    }
}

// ==========================================
// 指标卡片
// ==========================================
function updateMetrics(metrics, finalValue, initialCapital) {
    const totalReturn = ((finalValue / initialCapital - 1) * 100).toFixed(2);
    const sign = totalReturn >= 0 ? "+" : "";
    setEl("totalReturn", sign + totalReturn + "%", totalReturn >= 0 ? "" : "danger");
    setEl("annualReturn", metrics.annual_return != null ? (sign + (metrics.annual_return * 100).toFixed(2) + "%") : "--");
    setEl("maxDrawdown", (metrics.max_drawdown != null ? (metrics.max_drawdown * 100).toFixed(2) : "--") + "%");
    setEl("sharpeRatio", metrics.sharpe_ratio != null ? metrics.sharpe_ratio.toFixed(2) : "--");
    setEl("winRate", metrics.win_rate != null ? (metrics.win_rate * 100).toFixed(1) + "%" : "--");
    setEl("totalTrades", metrics.total_trades ?? "--");
}

function setEl(id, text, cls) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = text;
        if (cls !== undefined) el.className = "metric-value " + cls;
    }
}

// ==========================================
// 收益曲线图
// ==========================================
function updateEquityChart(equityCurve, benchmark) {
    if (!equityChart || equityChart.isDisposed()) return;

    const dates = equityCurve.dates || [];
    const values = equityCurve.values || [];
    const strategyReturns = values.map(v => (v / values[0] - 1) * 100);

    const series = [{
        name: "动量策略", type: "line", data: strategyReturns, smooth: true, symbol: "none",
        lineStyle: { width: 2.5, color: "#3b82f6" },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(59, 130, 246, 0.3)" },
            { offset: 1, color: "rgba(59, 130, 246, 0.02)" }]) },
    }];

    if (benchmark && benchmark.returns && benchmark.returns.length > 0) {
        series.push({
            name: "沪深300", type: "line", data: benchmark.returns.map(r => r * 100),
            smooth: true, symbol: "none", lineStyle: { width: 1.5, color: "#94a3b8", type: "dashed" },
        });
    }

    equityChart.setOption({
        tooltip: {
            trigger: "axis", backgroundColor: "#1e293b", borderColor: "#334155",
            textStyle: { color: "#f1f5f9", fontSize: 13 },
            formatter: function (params) {
                let h = `<strong>${params[0].axisValue}</strong><br/>`;
                params.forEach(p => { h += `${p.marker} ${p.seriesName}: <b>${p.value>=0?'+':''}${p.value.toFixed(2)}%</b><br/>`; });
                return h;
            },
        },
        legend: { data: series.map(s => s.name), bottom: 0, textStyle: { color: "#94a3b8" } },
        grid: { left: "3%", right: "4%", top: "5%", bottom: "12%", containLabel: true },
        xAxis: { type: "category", data: dates, axisLabel: { color: "#64748b", fontSize: 11 }, splitLine: { show: false } },
        yAxis: { type: "value", name: "收益率 (%)", nameTextStyle: { color: "#94a3b8" },
                 axisLabel: { color: "#64748b", formatter: v => (v >= 0 ? "+" : "") + v.toFixed(0) + "%" },
                 splitLine: { lineStyle: { color: "#1e293b" } }, scale: true },
        series: series,
    }, true);
}

// ==========================================
// 月度热力图
// ==========================================
function updateHeatmap(monthlyData) {
    if (!heatmapChart || heatmapChart.isDisposed()) return;
    if (!monthlyData || monthlyData.length === 0) {
        heatmapChart.setOption({ title: { text: "暂无数据", left: "center", top: "center", textStyle: { color: "#64748b", fontSize: 14 } } });
        return;
    }

    const months = ["01","02","03","04","05","06","07","08","09","10","11","12"];
    const years = [...new Set(monthlyData.map(m => m.month.substring(0, 4)))].sort();
    const data = [];
    const mmap = {};
    monthlyData.forEach(m => { mmap[m.month] = m.return; });

    years.forEach((year, yi) => {
        months.forEach((month, mi) => {
            data.push([mi, yi, mmap[year + "-" + month] ?? null]);
        });
    });

    heatmapChart.setOption({
        tooltip: {
            backgroundColor: "#1e293b", borderColor: "#334155", textStyle: { color: "#f1f5f9" },
            formatter: p => { const v = p.data[2]; return v == null ? p.name + "<br/>无数据" : `<strong>${p.name}</strong><br/>收益: <b style="color:${v>=0?'#ef4444':'#22c55e'}">${v>=0?'+':''}${v.toFixed(2)}%</b>`; },
        },
        grid: { left: "8%", right: "5%", top: "3%", bottom: "8%" },
        xAxis: { type: "category", data: months.map(m => m + "月"), axisLabel: { color: "#64748b", fontSize: 11 } },
        yAxis: { type: "category", data: years, axisLabel: { color: "#64748b", fontSize: 11 } },
        visualMap: { min: -15, max: 15, calculable: true, orient: "horizontal", left: "center", bottom: 0,
                     inRange: { color: ["#22c55e", "#1e293b", "#ef4444"] }, textStyle: { color: "#94a3b8" }, formatter: v => v + "%" },
        series: [{ type: "heatmap", data: data, label: { show: true, color: "#94a3b8", fontSize: 9,
                    formatter: p => p.data[2] != null ? (p.data[2] >= 0 ? "+" : "") + p.data[2].toFixed(1) + "%" : "" },
                   emphasis: { itemStyle: { shadowBlur: 10 } }, itemStyle: { borderColor: "#0f172a", borderWidth: 2 } }],
    }, true);
}

// ==========================================
// 年度收益图
// ==========================================
function updateYearlyChart(yearlyReturns) {
    if (!yearlyChart || yearlyChart.isDisposed()) return;

    const years = Object.keys(yearlyReturns || {}).sort();
    const data = years.map(y => ((yearlyReturns[y] || 0) * 100).toFixed(2));

    yearlyChart.setOption({
        tooltip: {
            trigger: "axis", backgroundColor: "#1e293b", borderColor: "#334155", textStyle: { color: "#f1f5f9" },
            formatter: p => `<strong>${p[0].name}年</strong><br/>策略收益: <b style="color:${p[0].value>=0?'#ef4444':'#22c55e'}">${p[0].value>=0?'+':''}${p[0].value}%</b>`,
        },
        grid: { left: "3%", right: "4%", top: "8%", bottom: "3%", containLabel: true },
        xAxis: { type: "category", data: years, axisLabel: { color: "#64748b" } },
        yAxis: { type: "value", name: "收益率 (%)", nameTextStyle: { color: "#94a3b8" },
                 axisLabel: { color: "#64748b", formatter: v => (v >= 0 ? "+" : "") + v.toFixed(0) + "%" },
                 splitLine: { lineStyle: { color: "#1e293b" } } },
        series: [{ type: "bar", data: data, itemStyle: { color: p => p.value >= 0 ? "#ef4444" : "#22c55e", borderRadius: [4, 4, 0, 0] },
                   label: { show: true, position: "outside", color: "#94a3b8", fontSize: 11,
                            formatter: p => (p.value >= 0 ? "+" : "") + p.value + "%" } }],
    }, true);
}

// ==========================================
// 信号表格
// ==========================================
async function fetchSignals() {
    const w5 = parseInt(document.getElementById("w5").value) / 100;
    const w10 = parseInt(document.getElementById("w10").value) / 100;
    const w20 = parseInt(document.getElementById("w20").value) / 100;

    try {
        const resp = await fetch(apiUrl(`/api/signals?top_n=20&w5=${w5}&w10=${w10}&w20=${w20}`));
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const data = await resp.json();

        if (data.error) {
            document.getElementById("signalBody").innerHTML = `<tr><td colspan="8" class="loading">${data.error}</td></tr>`;
            return;
        }

        const signals = data.signals || [];
        if (signals.length === 0) {
            document.getElementById("signalBody").innerHTML = '<tr><td colspan="8" class="loading">暂无信号</td></tr>';
            return;
        }

        let html = "";
        signals.forEach((s, i) => {
            html += `<tr>
                <td class="${i < 3 ? 'rank' : ''}">${i + 1}</td>
                <td>${s.code}</td><td>${s.name || "--"}</td><td><b>${s.score}</b></td>
                <td class="${parseFloat(s.ret_5d) >= 0 ? 'positive' : 'negative'}">${s.ret_5d}</td>
                <td class="${parseFloat(s.ret_10d) >= 0 ? 'positive' : 'negative'}">${s.ret_10d}</td>
                <td class="${parseFloat(s.ret_20d) >= 0 ? 'positive' : 'negative'}">${s.ret_20d}</td>
                <td>${(s.price || 0).toFixed(3)}</td></tr>`;
        });
        document.getElementById("signalBody").innerHTML = html;

    } catch (err) {
        console.error("信号获取失败:", err);
        document.getElementById("signalBody").innerHTML = '<tr><td colspan="8" class="loading">加载失败，请重试</td></tr>';
    }
}

// ==========================================
// 交易记录
// ==========================================
function updateTradeTable(trades) {
    if (!trades || trades.length === 0) {
        document.getElementById("tradeBody").innerHTML = '<tr><td colspan="6" class="loading">暂无交易记录</td></tr>';
        return;
    }

    let html = "";
    [...trades].reverse().slice(0, 50).forEach(t => {
        const cls = t.type === "buy" ? "buy" : "sell";
        const label = t.type === "buy" ? "买入" : "卖出";
        html += `<tr><td>${t.date}</td><td class="${cls}">${label}</td><td>${t.code}</td>
            <td>${t.price.toFixed(3)}</td><td>${t.shares}</td>
            <td>${t.amount.toLocaleString("zh-CN", {minimumFractionDigits: 2})}</td></tr>`;
    });
    document.getElementById("tradeBody").innerHTML = html;
}

// ==========================================
// 定时任务控制
// ==========================================
async function checkSchedulerStatus() {
    try {
        const resp = await fetch(apiUrl("/api/scheduler/status"));
        if (!resp.ok) return;
        const data = await resp.json();
        updateSchedulerUI(data);
    } catch (err) {
        // 调度器未响应，忽略
    }
}

function updateSchedulerUI(data) {
    const dot = document.querySelector("#schedulerStatusBadge .status-dot");
    const text = document.querySelector("#schedulerStatusBadge .status-text");
    const btnStart = document.getElementById("btnSchedulerStart");
    const btnStop = document.getElementById("btnSchedulerStop");

    if (data.is_running) {
        if (dot) dot.className = "status-dot on";
        if (text) { text.textContent = "运行中"; text.style.color = "#22c55e"; }
        if (btnStart) btnStart.disabled = true;
        if (btnStop) btnStop.disabled = false;
    } else {
        if (dot) dot.className = "status-dot off";
        if (text) { text.textContent = "未启动"; text.style.color = ""; }
        if (btnStart) btnStart.disabled = false;
        if (btnStop) btnStop.disabled = true;
    }

    const setVal = (id, val, cls) => {
        const el = document.getElementById(id);
        if (el) { el.textContent = val; if (cls) el.className = cls; }
    };

    setVal("nextRunTime", data.next_run || "--");
    setVal("lastRunTime", data.last_run || "--");
    setVal("isTradingDay", data.is_trading_day ? "是" : "否", data.is_trading_day ? "info-value active" : "info-value");
    setVal("isTradingTime", data.is_trading_time ? "交易中" : "休市", data.is_trading_time ? "info-value active" : "info-value");

    // 显示筛选结果
    if (data.latest_event && data.latest_event.top1_code) {
        showScreeningAlert(data.latest_event);
    }
}

async function startScheduler() {
    const w5 = parseInt(document.getElementById("w5").value) / 100;
    const w10 = parseInt(document.getElementById("w10").value) / 100;
    const w20 = parseInt(document.getElementById("w20").value) / 100;

    try {
        const resp = await fetch(apiUrl(`/api/scheduler/start?w5=${w5}&w10=${w10}&w20=${w20}`));
        const data = await resp.json();
        if (data.status === "started" || data.status === "already_running") {
            alert("✅ 定时任务已启动！\n\n每个交易日14:40自动筛选ETF。\n请保持程序在后台运行。");
            checkSchedulerStatus();
        } else {
            alert("启动失败: " + (data.message || "未知错误"));
        }
    } catch (err) {
        alert("启动失败，请检查服务器是否在运行。");
    }
}

async function stopScheduler() {
    if (!confirm("确定要关闭每日14:40的自动筛选吗？")) return;
    try {
        const resp = await fetch(apiUrl("/api/scheduler/stop"));
        const data = await resp.json();
        alert("⏹ " + data.message);
        checkSchedulerStatus();
    } catch (err) {
        alert("停止失败: " + err.message);
    }
}

async function runScreeningNow() {
    const btn = document.getElementById("btnRunNow");
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ 筛选中...";

    const w5 = parseInt(document.getElementById("w5").value) / 100;
    const w10 = parseInt(document.getElementById("w10").value) / 100;
    const w20 = parseInt(document.getElementById("w20").value) / 100;

    try {
        const fetchUrl = apiUrl(`/api/realtime/screening?top_n=20&w5=${w5}&w10=${w10}&w20=${w20}`);
        const resp = await fetch(fetchUrl);
        const rawText = await resp.text();

        // 调试：看实际返回了什么
        let data;
        try { data = JSON.parse(rawText); }
        catch { alert("服务器返回异常 HTTP" + resp.status + ": " + rawText.substring(0, 200)); return; }

        if (data.error) {
            alert("筛选失败: " + data.error + "\n(HTTP " + resp.status + ", URL: " + fetchUrl.substring(0,60) + "...)");
            return;
        }

        // 更新信号表格
        const signals = data.signals || [];
        if (signals.length > 0) {
            let html = "";
            signals.forEach((s, i) => {
                html += `<tr>
                    <td class="${i < 3 ? 'rank' : ''}">${i + 1}</td>
                    <td>${s.code}</td><td>${s.name || "--"}</td><td><b>${s.score}</b></td>
                    <td class="${parseFloat(s.ret_5d) >= 0 ? 'positive' : 'negative'}">${s.ret_5d}</td>
                    <td class="${parseFloat(s.ret_10d) >= 0 ? 'positive' : 'negative'}">${s.ret_10d}</td>
                    <td class="${parseFloat(s.ret_20d) >= 0 ? 'positive' : 'negative'}">${s.ret_20d}</td>
                    <td>${(s.price || 0).toFixed(3)}</td></tr>`;
            });
            document.getElementById("signalBody").innerHTML = html;

            // 显示推荐
            const top1 = signals[0];
            showScreeningAlert({
                top1_code: top1.code, top1_name: top1.name, top1_score: top1.score,
                top1_price: top1.price, data_source: data.data_source, fetch_time: data.time,
                top5: signals.slice(0, 5).map(s => ({ code: s.code, name: s.name, score: s.score, price: s.price })),
            });
        }

    } catch (err) {
        alert("筛选失败，请检查服务器是否在运行。");
    } finally {
        btn.disabled = false;
        btn.textContent = origText;
    }
}

function showScreeningAlert(event) {
    const alertDiv = document.getElementById("screeningAlert");
    const body = document.getElementById("screeningAlertBody");
    if (!alertDiv || !body) return;

    let top5html = '<table><tr><th>#</th><th>代码</th><th>名称</th><th>得分</th><th>价格</th></tr>';
    if (event.top5) {
        event.top5.forEach((etf, i) => {
            top5html += `<tr><td><b>#${i + 1}</b></td><td>${etf.code}</td><td>${etf.name || "--"}</td><td>${etf.score}</td><td>${(etf.price || 0).toFixed(3)}</td></tr>`;
        });
    }
    top5html += "</table>";

    body.innerHTML = `
        <div class="alert-top1">
            <div class="top1-label">🏆 推荐买入</div>
            <div class="top1-code">${event.top1_code}</div>
            <div class="top1-name">${event.top1_name || ""}</div>
            <div class="top1-meta">得分: ${event.top1_score || ""} | 价格: ${(event.top1_price || 0).toFixed(3)} | 数据: ${event.data_source || "cached"}</div>
        </div>
        <div class="alert-top5">${top5html}</div>`;
    alertDiv.style.display = "block";
}

// ==========================================
// 加载动画
// ==========================================
function showLoading(show) {
    const el = document.getElementById("loadingOverlay");
    if (el) el.style.display = show ? "flex" : "none";
}
