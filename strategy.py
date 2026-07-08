"""
动量策略 + 回测引擎（优化版）

优化策略：
- 预先为每只ETF计算5/10/20日收益率列，避免每天重复计算
- 回测时直接查表，而非每次遍历计算
- 1228只ETF × 1379天 回测可在几秒内完成
"""

import os
import pickle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def precompute_returns(data_dict, weights=None):
    """
    为所有ETF预计算收益率列（pandas向量化，极快）

    使用 pandas pct_change 代替 Python for 循环
    """
    if weights is None:
        periods = [5, 10, 20]
        w = [0.3, 0.3, 0.4]
    else:
        periods = list(weights.keys())
        w = list(weights.values())

    for code, df in data_dict.items():
        if df.empty or len(df) < max(periods) + 2:
            continue

        # 清除旧得分（允许参数变更后重新计算）
        for col in ["ret_5", "ret_10", "ret_20", "score"]:
            if col in df.columns:
                del df[col]

        # 向量化计算各周期收益率
        for p in periods:
            df[f"ret_{p}"] = df["close"].pct_change(p)

        # 综合得分（用0填充NaN）
        df["score"] = 0.0
        for p, weight in zip(periods, w):
            df["score"] += df[f"ret_{p}"].fillna(0) * weight

    return data_dict


def calculate_momentum_scores(data_dict, date, weights=None):
    """
    在指定日期快速查表获取ETF动量得分
    （需要先调用 precompute_returns）
    """
    scores = []
    for code, df in data_dict.items():
        if df.empty or "score" not in df.columns:
            continue

        df_date = df[df["date"] <= date]
        if df_date.empty:
            continue

        last = df_date.iloc[-1]
        last_date = last["date"]

        if (date - last_date).days > 5:
            continue

        price = last["close"]
        if price <= 0:
            continue

        scores.append({
            "code": code,
            "price": price,
            "score": last.get("score", 0),
            "ret_5d": last.get("ret_5", 0),
            "ret_10d": last.get("ret_10", 0),
            "ret_20d": last.get("ret_20", 0),
            "volume": last.get("volume", 0),
            "amount": last.get("amount", 0),
        })

    if not scores:
        return pd.DataFrame()

    result = pd.DataFrame(scores)
    result = result.sort_values("score", ascending=False).reset_index(drop=True)
    return result


def run_backtest(data_dict, etf_info=None, params=None):
    """
    运行回测（全量ETF参与，秒级完成）

    核心优化：将每只ETF的得分预计算后合并为大表，
    用 groupby 一次找出每天得分最高的ETF，避免逐日遍历。
    """
    if params is None:
        params = {}

    start_date = pd.to_datetime(params.get("start_date", "2020-07-01"))
    end_date = pd.to_datetime(params.get("end_date", "2025-12-31"))
    initial_capital = params.get("initial_capital", 100000)
    cost_rate = params.get("cost_rate", 0.0003)
    weights = params.get("weights", {5: 0.3, 10: 0.3, 20: 0.4})

    print(f"\n{'=' * 60}")
    print(f"开始回测: {start_date.date()} ~ {end_date.date()}")
    print(f"  初始资金: {initial_capital:,.0f} | 成本: {cost_rate*100:.2f}%")
    print(f"  ETF数量: {len(data_dict)} 只（全量）")

    # 换仓阈值：新ETF得分必须比当前持仓高这个比例才换
    switch_threshold = params.get("switch_threshold", 0.3)  # 默认30%
    min_hold_days = params.get("min_hold_days", 3)  # 至少持有3天
    print(f"  换仓阈值: +{switch_threshold*100:.0f}% | 最低持有: {min_hold_days}天")
    print(f"{'=' * 60}")

    t0 = datetime.now()

    # === 步骤1: 预计算 ===
    print("  [1/3] 预计算各ETF收益率...", end=" ", flush=True)
    data_dict = precompute_returns(data_dict, weights)
    print(f"({(datetime.now()-t0).total_seconds():.1f}s)")

    # === 步骤2: 构建合并大表 ===
    print("  [2/3] 构建每日排名表...", end=" ", flush=True)
    max_period = max(weights.keys()) if weights else 20
    frames = []
    for code, df in data_dict.items():
        if "score" not in df.columns:
            continue
        # 只保留ETF有足够历史数据的行（至少max_period天）
        sub = df[["date", "close", "score"]].copy()
        sub["code"] = str(code)
        sub = sub[(sub["date"] >= start_date) & (sub["date"] <= end_date)]
        # 确保至少要有max_period天历史
        if len(sub) >= max_period:
            # 只保留该ETF上市max_period天之后的日期
            sub = sub.iloc[max_period:]
            if len(sub) > 0:
                frames.append(sub)

    combined = pd.concat(frames, ignore_index=True)

    # 排除score为NaN的行（数据不足的ETF）
    combined = combined[combined["score"].notna()]

    # 每天选得分最高的ETF
    idx = combined.groupby("date")["score"].idxmax()
    daily_top = combined.loc[idx].sort_values("date").reset_index(drop=True)
    daily_top = daily_top.rename(columns={"close": "top_price", "code": "top_code"})

    print(f"({(datetime.now()-t0).total_seconds():.1f}s, {len(daily_top)}天)")

    # === 步骤3: 回测模拟 ===
    print("  [3/3] 模拟交易...", end=" ", flush=True)
    cash = initial_capital
    holdings = {}  # {code: (shares, buy_price)}
    daily_records = []
    trade_records = []

    last_switch_date = None  # 上次换仓日期

    for _, row in daily_top.iterrows():
        date = row["date"]
        top_code = row["top_code"]
        top_score = row["score"]

        date_pd = pd.to_datetime(date)

        # 获取top ETF当天的收盘价
        df_top = data_dict.get(top_code)
        if df_top is None:
            continue
        hist = df_top[df_top["date"] == date_pd]
        if hist.empty:
            continue
        top_price = float(hist["close"].iloc[0])

        # 获取当前持仓的得分
        current_score = 0
        for held_code, (_, _) in holdings.items():
            held_df = data_dict.get(held_code)
            if held_df is not None:
                held_hist = held_df[held_df["date"] == date_pd]
                if not held_hist.empty and "score" in held_hist.columns:
                    current_score = float(held_hist["score"].iloc[0])

        # 换仓条件：
        # 1. 新ETF不是当前持仓
        # 2. 新得分比当前高30%以上（避免频繁换仓）
        # 3. 距离上次换仓至少 min_hold_days 天
        should_switch = (
            top_code not in holdings
            and (not holdings or top_score > current_score * (1 + switch_threshold))
            and (last_switch_date is None
                 or (date_pd - last_switch_date).days >= min_hold_days)
        )

        day_trades = []

        if should_switch:
            # 卖出旧仓
            for old_code in list(holdings.keys()):
                shares, buy_price = holdings[old_code]
                df_old = data_dict.get(old_code)
                sold = False
                if df_old is not None:
                    old_hist = df_old[df_old["date"] == date_pd]
                    if not old_hist.empty:
                        sell_price = float(old_hist["close"].iloc[0])
                        cash += shares * sell_price * (1 - cost_rate)
                        day_trades.append({
                            "date": date, "type": "sell", "code": old_code,
                            "price": sell_price, "shares": shares,
                            "amount": shares * sell_price * (1 - cost_rate),
                            "pnl_pct": (sell_price - buy_price) / buy_price * 100,
                        })
                        sold = True
                if sold:
                    del holdings[old_code]
                    last_switch_date = date_pd
                # 如果卖不掉（停牌），保留持仓，不删除

            # 买入第1名
            if cash > 0 and top_price > 0:
                max_shares = int(cash / (top_price * (1 + cost_rate)) / 100) * 100
                if max_shares >= 100:
                    cost = max_shares * top_price * (1 + cost_rate)
                    if cost <= cash:
                        holdings[top_code] = (max_shares, top_price)
                        cash -= cost
                        day_trades.append({
                            "date": date, "type": "buy", "code": top_code,
                            "price": top_price, "shares": max_shares,
                            "amount": cost,
                        })

        trade_records.extend(day_trades)

        # 当日净值
        total_value = cash
        for code, (shares, _) in holdings.items():
            df_h = data_dict.get(code)
            if df_h is not None:
                h = df_h[df_h["date"] == date_pd]
                if not h.empty:
                    total_value += shares * float(h["close"].iloc[0])

        daily_records.append({
            "date": date.date() if isinstance(date, pd.Timestamp) else date,
            "total_value": total_value, "cash": cash,
            "holdings_count": len(holdings),
            "holdings_code": list(holdings.keys())[0] if holdings else "",
            "top_score": float(top_score),
            "return": total_value / initial_capital - 1,
        })

    final_value = daily_records[-1]["total_value"] if daily_records else initial_capital
    elapsed_total = (datetime.now() - t0).total_seconds()
    print(f"({elapsed_total:.1f}s)")

    # 绩效指标
    metrics = calculate_metrics(daily_records, trade_records, initial_capital, start_date, end_date)

    # 基准
    benchmark_returns = get_benchmark_returns(start_date, end_date)
    if benchmark_returns is not None:
        b_returns = benchmark_returns.get("returns", [])
        metrics["benchmark_return"] = b_returns[-1] if b_returns else 0

    result = {
        "params": {**params, "switch_threshold": switch_threshold, "min_hold_days": min_hold_days},
        "initial_capital": initial_capital,
        "final_value": final_value,
        "metrics": metrics,
        "daily_records": daily_records,
        "trade_records": trade_records,
        "benchmark_returns": benchmark_returns,
        "etf_info": etf_info,
    }

    print(f"\n回测结果:")
    print(f"  总收益: {(final_value/initial_capital - 1)*100:+.2f}%")
    print(f"  年化: {metrics.get('annual_return', 0)*100:+.2f}%")
    print(f"  最大回撤: {metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"  夏普: {metrics.get('sharpe_ratio', 0):.2f}")
    print(f"  交易次数: {len(trade_records)}")
    print(f"  总耗时: {elapsed_total:.1f}s")
    print(f"{'=' * 60}")

    return result


def calculate_metrics(daily_records, trade_records, initial_capital, start_date, end_date):
    """计算回测绩效指标"""
    if not daily_records:
        return {}

    df = pd.DataFrame(daily_records)
    df["date"] = pd.to_datetime(df["date"])
    df["daily_return"] = df["total_value"].pct_change()

    total_return = df["total_value"].iloc[-1] / initial_capital - 1

    days = (end_date - start_date).days
    years = days / 365.25
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

    df["cummax"] = df["total_value"].cummax()
    df["drawdown"] = (df["total_value"] - df["cummax"]) / df["cummax"]
    max_drawdown = df["drawdown"].min()

    risk_free_rate = 0.03
    excess = df["daily_return"].dropna() - risk_free_rate / 252
    sharpe = np.sqrt(252) * excess.mean() / excess.std() if excess.std() > 0 else 0

    daily_ret = df["daily_return"].dropna()
    win_rate = (daily_ret > 0).sum() / len(daily_ret) if len(daily_ret) > 0 else 0

    # 月收益
    df["ym"] = df["date"].dt.to_period("M")
    monthly = df.groupby("ym").agg(
        start=("total_value", "first"),
        end=("total_value", "last"),
    )
    monthly["ret"] = monthly["end"] / monthly["start"] - 1
    monthly_win_rate = (monthly["ret"] > 0).sum() / len(monthly) if len(monthly) > 0 else 0

    total_cost = sum(t.get("amount", 0) * 0.0003 for t in trade_records)  # 近似

    # 每笔交易的盈亏
    sell_pnls = [t.get("pnl_pct", 0) for t in trade_records if t["type"] == "sell"]
    avg_trade_ret = np.mean(sell_pnls) if sell_pnls else 0
    trade_win_rate = (np.array(sell_pnls) > 0).sum() / len(sell_pnls) if sell_pnls else 0

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "win_rate": win_rate,
        "monthly_win_rate": monthly_win_rate,
        "total_trades": len(trade_records),
        "buy_count": sum(1 for t in trade_records if t["type"] == "buy"),
        "sell_count": sum(1 for t in trade_records if t["type"] == "sell"),
        "total_cost": total_cost,
        "avg_trade_return": avg_trade_ret,
        "trade_win_rate": trade_win_rate,
        "monthly_returns": [{"year_month": str(idx), "monthly_return": row["ret"]}
                           for idx, row in monthly.iterrows()] if not monthly.empty else [],
    }


def get_benchmark_returns(start_date, end_date):
    """获取沪深300基准收益曲线"""
    cache_file = os.path.join(DATA_DIR, "benchmark_hs300.pkl")
    if not os.path.exists(cache_file):
        try:
            from data_fetcher import get_benchmark_data
            get_benchmark_data()
        except:
            return None
    if not os.path.exists(cache_file):
        return None

    with open(cache_file, "rb") as f:
        bm = pickle.load(f)
    if bm.empty:
        return None

    bm["date"] = pd.to_datetime(bm["date"])
    mask = (bm["date"] >= start_date) & (bm["date"] <= end_date)
    bm = bm[mask].sort_values("date")
    if bm.empty:
        return None

    base = bm["close"].iloc[0]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in bm["date"].tolist()],
        "returns": (bm["close"] / base - 1).tolist(),
    }


def get_latest_signals(data_dict, etf_info=None, weights=None, top_n=10):
    """获取最新的ETF筛选信号"""
    latest_date = None
    for df in data_dict.values():
        if not df.empty:
            d = df["date"].max()
            if latest_date is None or d > latest_date:
                latest_date = d
    if latest_date is None:
        return pd.DataFrame()

    data_dict = precompute_returns(data_dict, weights)
    scores_df = calculate_momentum_scores(data_dict, latest_date, weights)
    if scores_df.empty:
        return scores_df

    if etf_info is not None:
        name_map = {str(r["code"]): str(r["name"]) for _, r in etf_info.iterrows()}
        scores_df["name"] = scores_df["code"].map(name_map).fillna("")

    result = scores_df.head(top_n).copy()
    for col in ["ret_5d", "ret_10d", "ret_20d"]:
        if col in result.columns:
            result[col] = result[col].apply(lambda x: f"{x*100:+.2f}%")
    result["score"] = result["score"].apply(lambda x: f"{x*100:.2f}%")
    return result


if __name__ == "__main__":
    from data_fetcher import load_all_data, get_etf_list

    print("加载数据...")
    data_dict = load_all_data()
    if data_dict is None:
        print("请先下载数据")
        exit(1)

    try:
        etf_list = get_etf_list()
    except:
        etf_list = None

    result = run_backtest(data_dict, etf_info=etf_list)

    print("\n最新信号 TOP 10:")
    signals = get_latest_signals(data_dict, etf_info=etf_list, top_n=10)
    cols = [c for c in ["code", "name", "score", "ret_5d", "ret_10d", "ret_20d"]
            if c in signals.columns]
    if not signals.empty:
        print(signals[cols].to_string(index=False))
