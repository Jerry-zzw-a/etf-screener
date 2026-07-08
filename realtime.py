"""
实时行情模块 - 快速数据源

数据源优先级（延迟从低到高）：
1. 新浪财经API (~3秒延迟) - 主力
2. 腾讯财经API (~5秒延迟) - 备用
3. AKShare东方财富 (~15分钟延迟) - 兜底

用法：
    fetcher = RealtimeFetcher()
    quotes = fetcher.get_etf_quotes(["510050", "159919"])
    # 返回: {code: {price, pct_change, volume, ...}}
"""

import re
import time
import json
import requests
import pandas as pd
from datetime import datetime, timedelta


class RealtimeFetcher:
    """ETF实时行情获取器"""

    # 新浪行情API
    SINA_API = "http://hq.sinajs.cn/list={codes}"

    # 腾讯行情API
    TENCENT_API = "http://qt.gtimg.cn/q={codes}"

    def __init__(self, timeout=5):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        })

    def _format_codes_sina(self, codes):
        """格式化ETF代码为新浪格式"""
        result = []
        for code in str(codes).split(",") if isinstance(codes, str) else codes:
            code = str(code).strip()
            if code.startswith(("sh", "sz", "bj")):
                result.append(code)
            elif code.startswith(("5", "6", "9")):  # 上交所
                result.append(f"sh{code}")
            elif code.startswith(("0", "1", "2", "3")):  # 深交所
                result.append(f"sz{code}")
            elif code.startswith(("8", "4")):  # 北交所
                result.append(f"bj{code}")
            else:
                # 默认尝试上交所
                result.append(f"sh{code}")
        return ",".join(result)

    def _format_codes_tencent(self, codes):
        """格式化ETF代码为腾讯格式"""
        result = []
        for code in str(codes).split(",") if isinstance(codes, str) else codes:
            code = str(code).strip()
            if code.startswith(("sh", "sz", "bj")):
                result.append(code)
            elif code.startswith(("5", "6", "9")):
                result.append(f"sh{code}")
            elif code.startswith(("0", "1", "2", "3")):
                result.append(f"sz{code}")
            elif code.startswith(("8", "4")):
                result.append(f"bj{code}")
            else:
                result.append(f"sh{code}")
        return ",".join(result)

    def fetch_sina(self, codes):
        """
        从新浪财经获取实时行情

        新浪返回格式:
        var hq_str_sh510050="名称,今开,昨收,当前价,最高,最低,买一,卖一,成交量,成交额,..."
        """
        sina_codes = self._format_codes_sina(codes)
        url = self.SINA_API.format(codes=sina_codes)

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.encoding = "gbk"
            text = resp.text

            if not text or "FAILED" in text:
                return {}

            results = {}
            # 解析每行 var hq_str_xxx="..."
            pattern = r'var hq_str_(\w+)="([^"]*)"'
            matches = re.findall(pattern, text)

            for code_id, data_str in matches:
                fields = data_str.split(",")
                if len(fields) < 10:
                    continue

                # 提取纯代码（去掉sh/sz前缀）
                pure_code = code_id.replace("sh", "").replace("sz", "").replace("bj", "")

                try:
                    name = fields[0]
                    open_price = float(fields[1]) if fields[1] else 0
                    prev_close = float(fields[2]) if fields[2] else 0
                    current_price = float(fields[3]) if fields[3] else 0
                    high = float(fields[4]) if fields[4] else 0
                    low = float(fields[5]) if fields[5] else 0
                    volume = float(fields[8]) if len(fields) > 8 and fields[8] else 0
                    amount = float(fields[9]) if len(fields) > 9 and fields[9] else 0

                    if current_price > 0:
                        pct_change = (current_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                    else:
                        pct_change = 0

                    results[pure_code] = {
                        "code": pure_code,
                        "name": name,
                        "price": current_price,
                        "open": open_price,
                        "high": high,
                        "low": low,
                        "prev_close": prev_close,
                        "pct_change": round(pct_change, 2),
                        "volume": int(volume),
                        "amount": amount,
                        "source": "sina",
                        "fetch_time": datetime.now().strftime("%H:%M:%S"),
                    }
                except (ValueError, IndexError):
                    continue

            return results

        except requests.RequestException as e:
            print(f"  [新浪API] 请求失败: {e}")
            return {}

    def fetch_tencent(self, codes):
        """
        从腾讯财经获取实时行情（备用）

        腾讯返回格式:
        v_sh510050="1~ETF名称~2.640~2.630~2.625~123456~..."
        """
        tencent_codes = self._format_codes_tencent(codes)
        url = self.TENCENT_API.format(codes=tencent_codes)

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.encoding = "gbk"
            text = resp.text

            if not text:
                return {}

            results = {}
            lines = text.strip().split("\n")

            for line in lines:
                line = line.strip().rstrip(";")
                if not line or "=" not in line:
                    continue

                # v_sh510050="..."
                match = re.match(r'v_(\w+)="(.*)"', line)
                if not match:
                    continue

                code_id = match.group(1)
                data_str = match.group(2)
                fields = data_str.split("~")

                if len(fields) < 10:
                    continue

                pure_code = code_id.replace("sh", "").replace("sz", "").replace("bj", "")

                try:
                    name = fields[1]
                    current_price = float(fields[3]) if fields[3] else 0
                    prev_close = float(fields[4]) if fields[4] else 0
                    open_price = float(fields[5]) if fields[5] else 0
                    volume = float(fields[6]) if fields[6] else 0

                    if current_price > 0 and prev_close > 0:
                        pct_change = (current_price - prev_close) / prev_close * 100
                    else:
                        pct_change = 0

                    results[pure_code] = {
                        "code": pure_code,
                        "name": name,
                        "price": current_price,
                        "open": open_price,
                        "prev_close": prev_close,
                        "pct_change": round(pct_change, 2),
                        "volume": int(volume),
                        "amount": float(fields[7]) if len(fields) > 7 and fields[7] else 0,
                        "source": "tencent",
                        "fetch_time": datetime.now().strftime("%H:%M:%S"),
                    }
                except (ValueError, IndexError):
                    continue

            return results

        except requests.RequestException as e:
            print(f"  [腾讯API] 请求失败: {e}")
            return {}

    def get_etf_quotes(self, codes):
        """
        获取ETF实时行情（自动选择最快的可用数据源）

        参数:
            codes: ETF代码列表，["510050", "159919"]

        返回:
            {code: {price, name, pct_change, volume, ...}, ...}
        """
        if isinstance(codes, str):
            codes = codes.split(",")

        codes = [str(c).strip() for c in codes if str(c).strip()]

        if not codes:
            return {}

        # 分批获取（新浪一次最多约200个）
        batch_size = 180
        all_results = {}

        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]

            # 优先用新浪
            results = self.fetch_sina(batch)

            # 失败的用腾讯补
            if len(results) < len(batch):
                missed = [c for c in batch if c not in results]
                if missed:
                    tencent_results = self.fetch_tencent(missed)
                    results.update(tencent_results)

            all_results.update(results)

            if i + batch_size < len(codes):
                time.sleep(0.1)  # 批次间短暂间隔

        return all_results

    def get_current_top_etfs(self, data_dict, etf_info=None, weights=None, top_n=5):
        """
        结合历史数据和实时行情，计算当前动量排名

        使用历史数据计算动量得分，用实时价格做最终的买入参考价

        返回:
            DataFrame 排名前N的ETF
        """
        if weights is None:
            weights = {5: 0.3, 10: 0.3, 20: 0.4}

        # 找最新交易日
        latest_date = None
        for code, df in data_dict.items():
            if not df.empty:
                max_date = df["date"].max()
                if latest_date is None or max_date > latest_date:
                    latest_date = max_date

        if latest_date is None:
            return pd.DataFrame({"error": ["无历史数据"]})

        # 用历史数据计算动量得分（基于最新收盘价）
        from strategy import calculate_momentum_scores
        scores = calculate_momentum_scores(data_dict, latest_date, weights)

        if scores.empty:
            return scores

        top_codes = scores.head(min(50, len(scores)))["code"].tolist()

        # 获取实时价格
        realtime_quotes = self.get_etf_quotes(top_codes)

        # 更新价格为实时价格
        for i, row in scores.iterrows():
            code = str(row["code"])
            if code in realtime_quotes:
                rt = realtime_quotes[code]
                scores.at[i, "realtime_price"] = rt["price"]
                scores.at[i, "realtime_pct"] = rt["pct_change"]
                scores.at[i, "fetch_time"] = rt["fetch_time"]
                scores.at[i, "data_source"] = rt["source"]
            else:
                scores.at[i, "realtime_price"] = row["price"]
                scores.at[i, "realtime_pct"] = None
                scores.at[i, "fetch_time"] = None
                scores.at[i, "data_source"] = "cached"

        # 添加ETF名称
        if etf_info is not None:
            name_map = {}
            for _, row in etf_info.iterrows():
                code = str(row.get("code", ""))
                name = str(row.get("name", ""))
                name_map[code] = name
            scores["name"] = scores["code"].map(name_map).fillna("")

        # 格式化百分比
        for col in ["ret_5d", "ret_10d", "ret_20d"]:
            if col in scores.columns:
                scores[col] = scores[col].apply(lambda x: f"{x*100:+.2f}%")

        scores["score_display"] = scores["score"].apply(lambda x: f"{x*100:.2f}%")

        return scores.head(top_n)


def check_is_trading_day(date=None):
    """
    检查是否是A股交易日

    使用AKShare交易日历（缓存7天）
    """
    if date is None:
        date = datetime.now().date()
    elif isinstance(date, datetime):
        date = date.date()

    # 简单的周末检查先过滤
    if date.weekday() >= 5:  # 周六日
        return False

    # 用AKShare交易日历
    try:
        import os
        import pickle
        import akshare as ak

        cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", "trading_calendar.pkl")

        trading_dates = None
        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 86400 * 7:  # 7天有效
                with open(cache_file, "rb") as f:
                    trading_dates = pickle.load(f)

        if trading_dates is None:
            # 获取交易日历
            cal_df = ak.tool_trade_date_hist_sina()
            trading_dates = set(
                pd.to_datetime(cal_df["trade_date"]).dt.date.tolist()
            )
            with open(cache_file, "wb") as f:
                pickle.dump(trading_dates, f)

        return date in trading_dates

    except Exception:
        # 无法获取交易日历时，退化为简单的周末检查
        return date.weekday() < 5


def is_trading_time():
    """
    检查当前是否在A股交易时间内
    交易时间: 9:30-11:30, 13:00-15:00
    14:40 在下午交易时段内
    """
    now = datetime.now()
    morning_start = now.replace(hour=9, minute=30, second=0)
    morning_end = now.replace(hour=11, minute=30, second=0)
    afternoon_start = now.replace(hour=13, minute=0, second=0)
    afternoon_end = now.replace(hour=15, minute=0, second=0)

    return (morning_start <= now <= morning_end) or (afternoon_start <= now <= afternoon_end)


if __name__ == "__main__":
    # 测试实时行情
    print("=" * 50)
    print("测试实时行情获取")
    print("=" * 50)

    fetcher = RealtimeFetcher()

    # 测试几只主流ETF
    test_codes = ["510050", "510300", "159919", "159915", "510500"]
    print(f"\n获取 {test_codes} 的实时行情...\n")

    quotes = fetcher.get_etf_quotes(test_codes)

    if quotes:
        print(f"{'代码':<8} {'名称':<16} {'最新价':<10} {'涨跌幅':<10} {'数据源':<10} {'时间':<10}")
        print("-" * 65)
        for code in test_codes:
            if code in quotes:
                q = quotes[code]
                sign = "+" if q["pct_change"] >= 0 else ""
                print(f"{q['code']:<8} {q['name']:<16} {q['price']:<10.3f} "
                      f"{sign}{q['pct_change']:<9.2f}% {q['source']:<10} {q['fetch_time']:<10}")
    else:
        print("⚠ 当前非交易时间，显示的是延迟数据")
        print("  交易时间 (9:30-15:00) 内可获取3-5秒延迟的实时行情")

    print(f"\n交易日检查: {check_is_trading_day()}")
    print(f"交易时间内: {is_trading_time()}")
