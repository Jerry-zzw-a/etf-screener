"""
定时任务管理器

功能：
- 每个交易日14:40自动获取实时行情
- 计算动量排名，筛选最优ETF
- 记录每次筛选结果
- 支持启用/禁用开关
"""

import os
import json
import time
import threading
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# 状态文件
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scheduler_status.json")


class ScreeningScheduler:
    """ETF筛选定时任务管理器"""

    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.is_running = False
        self.last_result = None
        self.last_run_time = None
        self.next_run_time = None
        self.screening_history = []
        self._data_dict = None
        self._etf_info = None
        self._weights = {5: 0.3, 10: 0.3, 20: 0.4}
        self._callback = None  # 筛选完成后的回调

        self._load_status()

    def _load_status(self):
        """从文件加载状态"""
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    status = json.load(f)
                self._weights = status.get("weights", self._weights)
                self.screening_history = status.get("history", [])
                if len(self.screening_history) > 100:
                    self.screening_history = self.screening_history[-100:]
            except:
                pass

    def _save_status(self):
        """保存状态到文件"""
        status = {
            "weights": self._weights,
            "history": self.screening_history[-100:],
            "last_run": self.last_run_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_run_time else None,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    def set_data(self, data_dict, etf_info=None):
        """设置数据引用（从主程序注入）"""
        self._data_dict = data_dict
        self._etf_info = etf_info

    def set_weights(self, w5, w10, w20):
        """设置动量权重"""
        total = w5 + w10 + w20
        if total > 0:
            self._weights = {5: w5/total, 10: w10/total, 20: w20/total}
            self._save_status()

    def set_callback(self, callback):
        """设置筛选完成回调函数"""
        self._callback = callback

    def _do_screening(self):
        """执行ETF筛选（在定时任务中调用）"""
        from realtime import RealtimeFetcher, check_is_trading_day

        now = datetime.now()
        print(f"\n{'='*50}")
        print(f"[定时筛选] {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*50}")

        # 检查是否是交易日
        if not check_is_trading_day():
            print("[定时筛选] 今天不是交易日，跳过")
            self.last_run_time = now
            return {"status": "skip", "reason": "非交易日", "time": now.strftime("%Y-%m-%d %H:%M:%S")}

        if self._data_dict is None:
            # 尝试加载
            from data_fetcher import load_all_data, get_etf_list
            self._data_dict = load_all_data()
            self._etf_info = get_etf_list()

        if self._data_dict is None:
            print("[定时筛选] 无数据，跳过")
            return {"status": "error", "reason": "无缓存数据", "time": now.strftime("%Y-%m-%d %H:%M:%S")}

        # 获取实时行情 + 动量排名
        print("[定时筛选] 获取实时行情...")
        fetcher = RealtimeFetcher()

        try:
            top_etfs = fetcher.get_current_top_etfs(
                self._data_dict,
                etf_info=self._etf_info,
                weights=self._weights,
                top_n=10
            )

            # 第1名就是今天应该买入的ETF
            if not top_etfs.empty:
                top1 = top_etfs.iloc[0]
                result = {
                    "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": now.strftime("%Y-%m-%d"),
                    "top1_code": str(top1.get("code", "")),
                    "top1_name": str(top1.get("name", "")),
                    "top1_score": str(top1.get("score_display", "")),
                    "top1_price": float(top1.get("realtime_price", 0)),
                    "top1_pct": float(top1.get("realtime_pct", 0)) if top1.get("realtime_pct") is not None else None,
                    "data_source": str(top1.get("data_source", "")),
                    "fetch_time": str(top1.get("fetch_time", "")),
                    "top5": [],
                }

                # 前5名
                for _, row in top_etfs.head(5).iterrows():
                    result["top5"].append({
                        "code": str(row.get("code", "")),
                        "name": str(row.get("name", "")),
                        "score": str(row.get("score_display", "")),
                        "price": float(row.get("realtime_price", row.get("price", 0))),
                        "pct": float(row.get("realtime_pct", 0)) if row.get("realtime_pct") is not None else None,
                    })

                self.last_result = result
                print(f"[定时筛选] 推荐: {result['top1_code']} {result['top1_name']}")
                print(f"[定时筛选] 得分: {result['top1_score']}  实时价: {result['top1_price']:.3f}")
            else:
                result = {"status": "empty", "reason": "无符合条件的ETF", "time": now.strftime("%Y-%m-%d %H:%M:%S")}

        except Exception as e:
            print(f"[定时筛选] 失败: {e}")
            result = {"status": "error", "reason": str(e), "time": now.strftime("%Y-%m-%d %H:%M:%S")}

        self.last_run_time = now
        self.screening_history.append(result)

        # 限制历史记录数量
        if len(self.screening_history) > 100:
            self.screening_history = self.screening_history[-100:]

        self._save_status()

        # 触发回调（通知Web前端）
        if self._callback:
            try:
                self._callback(result)
            except:
                pass

        print(f"[定时筛选] 完成\n")
        return result

    def start(self):
        """启动定时任务"""
        if self.is_running:
            return {"status": "already_running", "message": "定时任务已在运行中"}

        # 添加任务：每个交易日14:40执行
        self.scheduler.add_job(
            self._do_screening,
            trigger=CronTrigger(hour=14, minute=40, day_of_week="mon-fri"),
            id="etf_screening",
            name="ETF每日筛选",
            replace_existing=True,
        )

        # 也添加一个状态检查任务（每分钟检查一次，用于更新next_run_time）
        self.scheduler.add_job(
            self._update_next_run,
            trigger=CronTrigger(minute="*"),
            id="status_check",
            name="状态更新",
            replace_existing=True,
        )

        if not self.scheduler.running:
            self.scheduler.start()

        self.is_running = True
        self._update_next_run()

        print(f"[定时任务] 已启动，每个交易日14:40自动筛选")
        print(f"[定时任务] 下次运行: {self.next_run_time}")
        return {"status": "started", "message": "定时任务已启动", "next_run": str(self.next_run_time)}

    def stop(self):
        """停止定时任务"""
        if not self.is_running:
            return {"status": "not_running", "message": "定时任务未在运行"}

        try:
            self.scheduler.remove_job("etf_screening")
            self.scheduler.remove_job("status_check")
        except:
            pass

        self.is_running = False
        self.next_run_time = None
        print("[定时任务] 已停止")
        return {"status": "stopped", "message": "定时任务已停止"}

    def _update_next_run(self):
        """更新下次运行时间"""
        try:
            job = self.scheduler.get_job("etf_screening")
            if job and job.next_run_time:
                self.next_run_time = job.next_run_time
        except:
            pass

    def get_status(self):
        """获取定时任务状态"""
        self._update_next_run()

        # 获取今天的筛选历史
        today_results = []
        today_str = datetime.now().strftime("%Y-%m-%d")
        for h in reversed(self.screening_history):
            if h.get("date") == today_str or h.get("time", "").startswith(today_str):
                today_results.append(h)

        return {
            "is_running": self.is_running,
            "next_run": self.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if self.next_run_time else None,
            "last_run": self.last_run_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_run_time else None,
            "last_result": self.last_result,
            "today_results": today_results,
            "history_count": len(self.screening_history),
            "weights": {str(k): v for k, v in self._weights.items()},
        }

    def run_now(self):
        """手动立即执行一次筛选"""
        print("[手动触发] 立即执行ETF筛选...")
        result = self._do_screening()
        return result

    def get_status_json(self):
        """获取状态（JSON安全）"""
        status = self.get_status()

        # 处理 last_result 中的 numpy 类型
        def safe(obj):
            if isinstance(obj, dict):
                return {str(k): safe(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [safe(v) for v in obj]
            elif hasattr(obj, "item"):  # numpy types
                return obj.item()
            elif obj is None:
                return None
            return obj

        return safe(status)


# 全局单例
_scheduler_instance = None


def get_scheduler():
    """获取全局调度器实例"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = ScreeningScheduler()
    return _scheduler_instance


if __name__ == "__main__":
    # 测试
    print("测试定时任务管理器\n")

    sched = get_scheduler()
    print(f"状态: {sched.get_status()}\n")

    # 手动执行一次
    print("手动执行筛选...")
    result = sched.run_now()
    print(f"\n筛选结果:")
    if result and "top1_code" in result:
        print(f"  推荐买入: {result['top1_code']} {result['top1_name']}")
        print(f"  得分: {result['top1_score']}")
        print(f"  实时价格: {result['top1_price']:.3f}")
