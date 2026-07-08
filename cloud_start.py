"""
云部署启动脚本
- 首次运行自动下载数据
- 数据缓存在 Render 持久化磁盘 /opt/render/project/data/
"""

import os
import sys
import subprocess

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_FILE = os.path.join(DATA_DIR, "all_data.pkl")

print("=" * 50)
print("  A股ETF动量轮动筛选系统 - 云部署")
print("=" * 50)

# 检查是否需要下载数据
if not os.path.exists(CACHE_FILE):
    print("\n[首次运行] 正在下载ETF数据（约20分钟）...")
    print("后续启动将跳过此步骤。\n")

    # 动态导入下载模块
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data_fetcher import get_etf_list, download_all_data

    etf_list = get_etf_list()
    data = download_all_data(etf_list)
    print(f"\n数据下载完成: {len(data)} 只ETF\n")
else:
    print("\n数据已缓存，跳过下载。\n")

# 启动 web 服务
from web_server import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"启动Web服务，端口: {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
