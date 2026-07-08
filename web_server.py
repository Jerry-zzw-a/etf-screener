"""
Flask Web服务
- 提供回测结果API
- 提供最新ETF筛选信号API
- 渲染前端页面
"""

import os
import json
import pickle
import pandas as pd
import numpy as np
import functools
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response, redirect, url_for, session

from strategy import (
    run_backtest,
    calculate_momentum_scores,
    get_latest_signals,
    get_benchmark_returns,
)
from data_fetcher import load_all_data, get_etf_list
from scheduler_manager import get_scheduler
from realtime import RealtimeFetcher, check_is_trading_day, is_trading_time

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "etf-stock-screener-2024")

# ==========================================
# Session 登录（兼容隧道穿透）
# ==========================================
AUTH_PASSWORD = os.environ.get("ETF_PASSWORD", "etf123456")

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF筛选系统 - 登录</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f172a;color:#f1f5f9;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:40px 32px;width:100%;max-width:360px}
h1{font-size:1.3rem;text-align:center;margin-bottom:24px;background:linear-gradient(135deg,#60a5fa,#a78bfa);
-webkit-background-clip:text;-webkit-text-fill-color:transparent}
input{width:100%;padding:12px;margin:8px 0;background:#0f172a;border:1px solid #334155;border-radius:8px;
color:#f1f5f9;font-size:1rem}
button{width:100%;padding:12px;margin-top:16px;background:linear-gradient(135deg,#3b82f6,#6366f1);
color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer}
button:hover{box-shadow:0 4px 12px rgba(59,130,246,0.4)}
.error{color:#ef4444;font-size:0.85rem;text-align:center;margin-top:8px}
</style>
</head>
<body>
<div class="login-box">
<h1>📈 ETF筛选系统</h1>
<form method="get" action="/login">
<input type="password" name="key" placeholder="请输入密码" required autofocus>
<button type="submit">登 录</button>
%s
</form>
</div>
</body>
</html>
"""


def login_required(f):
    """装饰器：通过URL参数或Session验证"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # 方式1: URL中带key参数
        if request.args.get("key") == AUTH_PASSWORD:
            session["logged_in"] = True
            return f(*args, **kwargs)
        # 方式2: 已登录session
        if session.get("logged_in"):
            return f(*args, **kwargs)
        # 未登录 → 跳到登录页
        return redirect(url_for("login_page"))
    return decorated


# 全局变量：缓存数据和回测结果
DATA_DICT = None
ETF_INFO = None
BACKTEST_RESULT = None

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 存储筛选事件的监听器列表（SSE模式简化版）
_screening_subscribers = []


def on_screening_complete(result):
    """筛选完成回调：通知所有等待的客户端"""
    _screening_subscribers.append(result)
    # 只保留最新的5条
    if len(_screening_subscribers) > 5:
        _screening_subscribers.pop(0)


def get_data():
    """懒加载数据，并过滤掉不适合的ETF"""
    global DATA_DICT, ETF_INFO
    if DATA_DICT is None:
        DATA_DICT = load_all_data()
        if DATA_DICT is None:
            raise RuntimeError("请先运行 main.py --download 下载数据")
    if ETF_INFO is None:
        try:
            ETF_INFO = get_etf_list()
        except:
            ETF_INFO = pd.DataFrame()
        # 用ETF_INFO过滤data_dict
        if ETF_INFO is not None and not ETF_INFO.empty:
            valid_codes = set(ETF_INFO["code"].astype(str))
            DATA_DICT = {c: df for c, df in DATA_DICT.items()
                        if str(c) in valid_codes}
    return DATA_DICT, ETF_INFO


class NpEncoder(json.JSONEncoder):
    """处理numpy类型的JSON序列化"""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        if isinstance(obj, pd.Period):
            return str(obj)
        if pd.isna(obj):
            return None
        return super().default(obj)


def make_json_safe(obj):
    """递归转换对象为JSON安全格式"""
    if isinstance(obj, dict):
        return {str(k): make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_safe(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.Period):
        return str(obj)
    elif isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    elif pd.isna(obj):
        return None
    return obj


@app.route("/login", methods=["GET"])
def login_page():
    """登录页面"""
    # 检查URL中的key参数
    key = request.args.get("key", "")
    if key == AUTH_PASSWORD:
        session["logged_in"] = True
        return redirect(url_for("index"))
    error_msg = ""
    if key and key != AUTH_PASSWORD:
        error_msg = '<p class="error">密码错误，请重试</p>'
    return LOGIN_HTML % error_msg


@app.route("/logout")
def logout():
    """登出"""
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    """首页"""
    return render_template("index.html")


@login_required
@app.route("/api/backtest")
@login_required
def api_backtest():
    """
    运行回测并返回结果

    查询参数:
        start_date: 开始日期 (默认2020-07-01)
        end_date: 结束日期 (默认2025-12-31)
        capital: 初始资金 (默认100000)
        cost: 交易成本率 (默认0.0003)
        w5: 5日权重 (默认0.3)
        w10: 10日权重 (默认0.3)
        w20: 20日权重 (默认0.4)
    """
    global BACKTEST_RESULT

    try:
        data_dict, etf_info = get_data()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    # 读取参数
    start_date = request.args.get("start_date", "2020-07-01")
    end_date = request.args.get("end_date", "2025-12-31")
    capital = float(request.args.get("capital", 100000))
    cost = float(request.args.get("cost", 0.0003))
    w5 = float(request.args.get("w5", 0.3))
    w10 = float(request.args.get("w10", 0.3))
    w20 = float(request.args.get("w20", 0.4))

    # 归一化权重
    total_w = w5 + w10 + w20
    weights = {5: w5 / total_w, 10: w10 / total_w, 20: w20 / total_w}

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": capital,
        "cost_rate": cost,
        "weights": weights,
        "hold_count": 1,
    }

    # 运行回测
    result = run_backtest(data_dict, etf_info=etf_info, params=params)
    BACKTEST_RESULT = result

    # 构建API返回数据
    daily = result["daily_records"]
    metrics = result["metrics"]

    # 净值曲线数据
    equity_curve = {
        "dates": [r["date"].strftime("%Y-%m-%d") if isinstance(r["date"], pd.Timestamp)
                  else str(r["date"]) for r in daily],
        "values": [round(r["total_value"], 2) for r in daily],
        "returns": [round(r["return"] * 100, 2) for r in daily],
    }

    # 月度收益数据
    monthly_data = metrics.get("monthly_returns", [])
    monthly_heatmap = []
    for m in monthly_data:
        year_month = str(m.get("year_month", ""))
        monthly_heatmap.append({
            "month": year_month,
            "return": round(m.get("monthly_return", 0) * 100, 2),
        })

    # 年度收益汇总
    yearly_returns = {}
    for m in monthly_data:
        ym = str(m.get("year_month", ""))
        if ym:
            year = ym[:4]
            ret = m.get("monthly_return", 0)
            if year not in yearly_returns:
                yearly_returns[year] = 0
            yearly_returns[year] = (1 + yearly_returns[year]) * (1 + ret) - 1

    # 交易记录摘要
    trades = result.get("trade_records", [])
    trade_summary = []
    for t in trades[-50:]:  # 最近50条
        d = t.get("date", "")
        trade_summary.append({
            "date": d.strftime("%Y-%m-%d") if isinstance(d, pd.Timestamp) else str(d),
            "type": t.get("type", ""),
            "code": t.get("code", ""),
            "price": round(t.get("price", 0), 3),
            "shares": t.get("shares", 0),
            "amount": round(t.get("amount", 0), 2),
        })

    response_data = {
        "params": make_json_safe(params),
        "equity_curve": equity_curve,
        "benchmark": make_json_safe(result.get("benchmark_returns")),
        "metrics": make_json_safe(metrics),
        "monthly_heatmap": monthly_heatmap,
        "yearly_returns": make_json_safe(yearly_returns),
        "trade_summary": trade_summary,
        "final_value": round(result["final_value"], 2),
        "initial_capital": result["initial_capital"],
    }

    return jsonify(response_data)


@login_required
@app.route("/api/signals")
@login_required
def api_signals():
    """获取最新的ETF筛选信号"""
    try:
        data_dict, etf_info = get_data()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    w5 = float(request.args.get("w5", 0.3))
    w10 = float(request.args.get("w10", 0.3))
    w20 = float(request.args.get("w20", 0.4))
    total_w = w5 + w10 + w20
    weights = {5: w5 / total_w, 10: w10 / total_w, 20: w20 / total_w}

    top_n = int(request.args.get("top_n", 20))
    signals = get_latest_signals(data_dict, etf_info=etf_info, weights=weights, top_n=top_n)

    if signals.empty:
        return jsonify({"signals": [], "message": "暂无数据"})

    result = []
    for _, row in signals.iterrows():
        result.append({
            "code": str(row.get("code", "")),
            "name": str(row.get("name", "")),
            "score": str(row.get("score", "")),
            "ret_5d": str(row.get("ret_5d", "")),
            "ret_10d": str(row.get("ret_10d", "")),
            "ret_20d": str(row.get("ret_20d", "")),
            "price": round(float(row.get("price", 0)), 3),
        })

    return jsonify({"signals": result, "count": len(result)})


@login_required
@app.route("/api/etf_list")
@login_required
def api_etf_list():
    """获取可用ETF列表"""
    try:
        data_dict, etf_info = get_data()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    etfs = []
    for code in sorted(data_dict.keys()):
        name = ""
        if etf_info is not None and not etf_info.empty:
            match = etf_info[etf_info["code"].astype(str) == code]
            if not match.empty:
                name = str(match["name"].values[0])

        df = data_dict[code]
        latest_close = float(df["close"].iloc[-1]) if not df.empty else 0
        days_count = len(df)

        etfs.append({
            "code": code,
            "name": name,
            "latest_price": latest_close,
            "data_days": days_count,
        })

    return jsonify({"etfs": etfs, "count": len(etfs)})


# ==========================================
# 定时任务控制 API
# ==========================================

@login_required
@app.route("/api/scheduler/start")
@login_required
def api_scheduler_start():
    """启动每日14:40定时筛选"""
    try:
        # 确保调度器有数据
        data_dict, etf_info = get_data()
        sched = get_scheduler()
        sched.set_data(data_dict, etf_info)

        # 读取权重参数
        w5 = float(request.args.get("w5", 0.3))
        w10 = float(request.args.get("w10", 0.3))
        w20 = float(request.args.get("w20", 0.4))
        sched.set_weights(w5, w10, w20)

        # 设置回调
        sched.set_callback(on_screening_complete)

        result = sched.start()
        return jsonify(result)

    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@login_required
@app.route("/api/scheduler/stop")
def api_scheduler_stop():
    """停止定时筛选"""
    sched = get_scheduler()
    result = sched.stop()
    return jsonify(result)


@login_required
@app.route("/api/scheduler/status")
def api_scheduler_status():
    """获取定时任务状态"""
    sched = get_scheduler()
    status = sched.get_status_json()

    # 附加当前是否在交易时间的信息
    status["is_trading_day"] = check_is_trading_day()
    status["is_trading_time"] = is_trading_time()

    # 附加订阅者中最新的筛选结果
    if _screening_subscribers:
        status["latest_event"] = _screening_subscribers[-1]

    return jsonify(status)


@login_required
@app.route("/api/scheduler/run_now")
def api_scheduler_run_now():
    """手动立即执行一次筛选"""
    try:
        data_dict, etf_info = get_data()
        sched = get_scheduler()
        sched.set_data(data_dict, etf_info)

        w5 = float(request.args.get("w5", 0.3))
        w10 = float(request.args.get("w10", 0.3))
        w20 = float(request.args.get("w20", 0.4))
        sched.set_weights(w5, w10, w20)

        result = sched.run_now()

        # 安全序列化
        def safe(o):
            if isinstance(o, dict):
                return {str(k): safe(v) for k, v in o.items()}
            elif isinstance(o, list):
                return [safe(v) for v in o]
            elif hasattr(o, "item"):
                return o.item()
            return o

        return jsonify({"status": "ok", "result": safe(result)})
    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==========================================
# 实时行情 API
# ==========================================

@login_required
@app.route("/api/realtime/quotes")
def api_realtime_quotes():
    """获取ETF实时行情"""
    codes = request.args.get("codes", "")
    if not codes:
        return jsonify({"error": "请提供ETF代码列表，用逗号分隔"}), 400

    fetcher = RealtimeFetcher()
    quotes = fetcher.get_etf_quotes(codes.split(","))

    return jsonify({
        "quotes": quotes,
        "count": len(quotes),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_trading_time": is_trading_time(),
    })


@login_required
@app.route("/api/realtime/screening")
def api_realtime_screening():
    """
    实时筛选：结合历史动量 + 实时价格

    返回当天最值得买入的ETF
    """
    try:
        data_dict, etf_info = get_data()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    w5 = float(request.args.get("w5", 0.3))
    w10 = float(request.args.get("w10", 0.3))
    w20 = float(request.args.get("w20", 0.4))
    total_w = w5 + w10 + w20
    weights = {5: w5 / total_w, 10: w10 / total_w, 20: w20 / total_w}

    top_n = int(request.args.get("top_n", 10))

    fetcher = RealtimeFetcher()
    top_etfs = fetcher.get_current_top_etfs(data_dict, etf_info=etf_info,
                                            weights=weights, top_n=top_n)

    if top_etfs.empty:
        return jsonify({"error": "暂无数据", "signals": []})

    # 判断数据新鲜度
    has_realtime = "realtime_price" in top_etfs.columns
    data_source = top_etfs.iloc[0].get("data_source", "cached") if has_realtime else "cached"

    result = []
    for _, row in top_etfs.iterrows():
        item = {
            "code": str(row.get("code", "")),
            "name": str(row.get("name", "")),
            "score": str(row.get("score_display", row.get("score", ""))),
            "ret_5d": str(row.get("ret_5d", "")),
            "ret_10d": str(row.get("ret_10d", "")),
            "ret_20d": str(row.get("ret_20d", "")),
        }
        if has_realtime:
            item["price"] = float(row.get("realtime_price", row.get("price", 0)))
            item["pct_change"] = float(row.get("realtime_pct", 0)) if row.get("realtime_pct") is not None else None
            item["data_source"] = str(row.get("data_source", ""))
            item["fetch_time"] = str(row.get("fetch_time", ""))
        else:
            item["price"] = float(row.get("price", 0))
            item["data_source"] = "cached"
        result.append(item)

    return jsonify({
        "signals": result,
        "count": len(result),
        "data_source": data_source,
        "is_trading_time": is_trading_time(),
        "is_trading_day": check_is_trading_day(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  A股ETF动量轮动筛选系统")
    print("  访问地址: http://localhost:5000")
    print("=" * 60 + "\n")

    # 启动前检查数据
    if not os.path.exists(os.path.join(DATA_DIR, "all_data.pkl")):
        print("⚠ 警告: 未找到缓存数据，请先运行:")
        print("  python main.py --download")
        print()

    app.run(host="0.0.0.0", port=5000, debug=True)
