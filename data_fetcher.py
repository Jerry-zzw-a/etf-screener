"""
数据获取模块
- 获取A股全市场ETF列表（AKShare一次性获取）
- 下载每只ETF的历史日线数据（新浪财经K线API，不会被封）
- 本地缓存，避免重复下载
"""

import os
import re
import time
import json
import pickle
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

try:
    import akshare as ak
except ImportError:
    print("请先安装akshare: pip install akshare")
    raise

# 数据缓存目录
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 回测参数
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"


def get_etf_list():
    """
    获取A股全市场ETF列表，过滤不适合普通交易者的ETF

    过滤条件：
    1. 排除名称含"杠杆"/"反向"的ETF
    2. 排除货币ETF
    3. 排除债券ETF（动量策略在低波品种上效果差）

    返回: DataFrame，包含代码、名称、类型等
    """
    print("正在获取ETF列表...")

    cache_file = os.path.join(DATA_DIR, "etf_list.pkl")

    # 从缓存读取（24小时有效）
    if os.path.exists(cache_file):
        mtime = os.path.getmtime(cache_file)
        if time.time() - mtime < 86400:
            print("  从缓存加载ETF列表...")
            with open(cache_file, "rb") as f:
                return pickle.load(f)

    # 使用AKShare获取（仅一次调用，不会触发限流）
    df = ak.fund_etf_spot_em()

    if df.empty:
        raise ValueError("获取ETF列表为空")

    print(f"  共获取到 {len(df)} 只ETF")

    df = df.rename(columns={
        "代码": "code", "名称": "name",
        "最新价": "price", "成交量": "volume", "成交额": "amount",
    })

    # 过滤
    for kw in ["杠杆", "反向", "联接"]:
        df = df[~df["name"].str.contains(kw, na=False)]
    for kw in ["货币", "保证金", "添益", "银华日利", "华宝添益",
               "日利", "财富宝"]:
        df = df[~df["name"].str.contains(kw, na=False)]
    for kw in ["债", "债券", "国债", "转债", "可转债", "城投",
               "利率债", "信用债", "地债"]:
        df = df[~df["name"].str.contains(kw, na=False)]
    # 排除代码以511开头的（多是债券ETF）
    df = df[~df["code"].astype(str).str.startswith("511", na=False)]

    df = df.reset_index(drop=True)

    with open(cache_file, "wb") as f:
        pickle.dump(df, f)

    print(f"  最终可用ETF: {len(df)} 只")
    return df


def _code_to_sina_symbol(code):
    """将ETF代码转换为新浪格式"""
    code = str(code)
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def get_etf_daily_sina(code, name=""):
    """
    从新浪财经获取单只ETF的历史日线数据

    新浪K线API:
    - 免费，无需注册
    - 不会被封IP
    - 每次返回约2000根K线
    - 数据不含复权（ETF分红少，影响可忽略）

    参数:
        code: ETF代码（如 "510050"）
        name: ETF名称（用于日志）

    返回: DataFrame，包含日期、开高低收、成交量等
    """
    cache_file = os.path.join(DATA_DIR, f"{code}.pkl")

    # 从缓存加载
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                df = pickle.load(f)
            if not df.empty and len(df) > 0:
                return df
        except:
            pass

    symbol = _code_to_sina_symbol(code)
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/"
           f"data/CN_MarketDataService.getKLineData"
           f"?symbol={symbol}&scale=240&datalen=1500")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://finance.sina.com.cn/",
    })

    try:
        resp = session.get(url, timeout=15)
        text = resp.text

        # 移除前缀 /*<script>...</script>*/
        text = re.sub(r'/\*.*?\*/', '', text).strip()

        # 移除 JSONP 包装 data(...);
        if text.startswith("data("):
            text = text[5:]
        if text.endswith(");"):
            text = text[:-2]
        elif text.endswith(")"):
            text = text[:-1]
        elif text.endswith(";"):
            text = text[:-1]

        # 处理 null 返回
        if text == "null" or text == "":
            return pd.DataFrame()

        data = json.loads(text)

        if not data:
            return pd.DataFrame()

        # 过滤日期范围
        rows = []
        for item in data:
            day = item.get("day", "")
            if START_DATE <= day <= END_DATE:
                rows.append({
                    "date": day,
                    "open": float(item.get("open", 0)),
                    "high": float(item.get("high", 0)),
                    "low": float(item.get("low", 0)),
                    "close": float(item.get("close", 0)),
                    "volume": float(item.get("volume", 0)),
                })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # 估算成交额 = 成交量 × 均价
        df["amount"] = df["volume"] * (df["open"] + df["close"] + df["high"] + df["low"]) / 4

        # 缓存
        with open(cache_file, "wb") as f:
            pickle.dump(df, f)

        return df

    except Exception as e:
        return pd.DataFrame()


def download_all_data(etf_list, start_idx=0, end_idx=None):
    """
    批量下载所有ETF的历史数据

    使用新浪财经API（不会被封），每只间隔约0.5秒

    参数:
        etf_list: ETF列表DataFrame
        start_idx: 起始索引
        end_idx: 结束索引

    返回: dict，{code: DataFrame}
    """
    # 按成交额降序排列
    if "amount" in etf_list.columns:
        etf_list = etf_list.sort_values("amount", ascending=False, na_position="last")

    if end_idx is None:
        end_idx = len(etf_list)

    total = end_idx - start_idx
    data_dict = {}
    success_count = 0
    cache_hits = 0

    print(f"\n开始下载ETF历史数据 (共{total}只)")
    print(f"数据源: 新浪财经K线API | 时间: {START_DATE} ~ {END_DATE}")
    print(f"预计: ~{total * 0.6 / 60:.0f} 分钟")
    print("=" * 60)

    start_time = time.time()

    for i in range(start_idx, end_idx):
        row = etf_list.iloc[i]
        code = str(row["code"])
        name = str(row.get("name", ""))

        # 检查缓存
        cache_file = os.path.join(DATA_DIR, f"{code}.pkl")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "rb") as f:
                    df = pickle.load(f)
                if not df.empty and len(df) >= 60:
                    data_dict[code] = df
                    success_count += 1
                    cache_hits += 1
                    continue
            except:
                pass

        idx = i - start_idx + 1
        elapsed = time.time() - start_time
        rate = idx / max(elapsed, 0.1)
        eta = (total - idx) / max(rate, 0.01)

        print(f"\r[{idx}/{total}] {code} {name[:14]:14s} "
              f"| {elapsed/60:.0f}分/{eta/60:.0f}分 "
              f"| 成功:{success_count}", end="", flush=True)

        df = get_etf_daily_sina(code, name)

        if not df.empty and len(df) >= 60:
            avg_amount = df["amount"].mean()
            if avg_amount > 500_000:  # 日均成交额 > 50万
                data_dict[code] = df
                success_count += 1

        # 新浪API对频率要求不高，0.3-0.6秒间隔即可
        time.sleep(0.3 + (time.time() % 0.3))

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"下载完成!")
    print(f"  成功: {success_count}/{total} 只 (含缓存命中 {cache_hits} 只)")
    print(f"  耗时: {elapsed/60:.0f} 分钟")

    cache_file = os.path.join(DATA_DIR, "all_data.pkl")
    with open(cache_file, "wb") as f:
        pickle.dump(data_dict, f)
    print(f"  已缓存至: {cache_file}")

    return data_dict


def load_all_data():
    """从缓存加载所有ETF数据"""
    cache_file = os.path.join(DATA_DIR, "all_data.pkl")
    if os.path.exists(cache_file):
        print("从缓存加载ETF数据...")
        with open(cache_file, "rb") as f:
            data_dict = pickle.load(f)
        print(f"加载完成: {len(data_dict)} 只ETF")
        return data_dict
    return None


def get_benchmark_data():
    """
    获取基准数据（沪深300指数日线）
    使用新浪API，与ETF数据源一致
    """
    cache_file = os.path.join(DATA_DIR, "benchmark_hs300.pkl")

    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)

    print("下载沪深300基准数据...")

    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/"
           "data/CN_MarketDataService.getKLineData"
           "?symbol=sh000300&scale=240&datalen=1500")

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        })
        text = resp.text
        text = re.sub(r'/\*.*?\*/', '', text).strip()
        if text.startswith("data("):
            text = text[5:]
        if text.endswith(");"):
            text = text[:-2]
        elif text.endswith(")"):
            text = text[:-1]
        elif text.endswith(";"):
            text = text[:-1]

        data = json.loads(text)

        rows = []
        for item in data:
            day = item.get("day", "")
            if START_DATE <= day <= END_DATE:
                rows.append({
                    "date": day,
                    "open": float(item.get("open", 0)),
                    "high": float(item.get("high", 0)),
                    "low": float(item.get("low", 0)),
                    "close": float(item.get("close", 0)),
                    "volume": float(item.get("volume", 0)),
                })

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        with open(cache_file, "wb") as f:
            pickle.dump(df, f)

        print(f"  沪深300: {len(df)} 个交易日")
        return df

    except Exception as e:
        print(f"获取沪深300数据失败: {e}")
        return None


if __name__ == "__main__":
    # 测试
    print("测试新浪K线API...")
    df = get_etf_daily_sina("510050", "上证50ETF")
    if not df.empty:
        print(f"510050: {len(df)} 行, {df['date'].min().date()} ~ {df['date'].max().date()}")
        print(f"  最新价: {df['close'].iloc[-1]:.3f}")
    else:
        print("  获取失败")
