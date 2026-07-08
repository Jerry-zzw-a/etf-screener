"""
A股ETF动量轮动筛选系统 - 入口脚本

用法:
    python main.py              # 启动Web服务（需先有数据）
    python main.py --download   # 下载数据 + 启动Web服务
    python main.py --backtest   # 仅运行回测（命令行模式）
"""

import os
import sys
import argparse


def main():
    parser = argparse.ArgumentParser(description="A股ETF动量轮动筛选系统")
    parser.add_argument("--download", action="store_true", help="强制重新下载所有数据")
    parser.add_argument("--backtest", action="store_true", help="仅命令行回测，不启动Web服务")
    parser.add_argument("--port", type=int, default=5000, help="Web服务端口 (默认5000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Web服务地址 (默认0.0.0.0)")
    parser.add_argument("--tunnel", action="store_true", help="启动公网隧道（手机可远程访问）")
    args = parser.parse_args()

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    cache_file = os.path.join(data_dir, "all_data.pkl")

    print("=" * 60)
    print("  📈 A股ETF动量轮动筛选系统")
    print("=" * 60)
    print()
    print("策略说明:")
    print("  - 筛选A股中普通交易者可购买的ETF")
    print("  - 排除杠杆/反向/货币/债券ETF")
    print("  - 每日计算动量得分(5/10/20日收益率加权)")
    print("  - 始终持有得分最高的1只ETF")
    print("  - 每日14:40换仓（回测用收盘价模拟）")
    print("  - 回测期间: 2020-01 ~ 2025-12")
    print()

    # === 第一步：下载数据 ===
    need_download = args.download or not os.path.exists(cache_file)

    if need_download:
        print(">>> 第一步：下载ETF数据\n")
        try:
            from data_fetcher import get_etf_list, download_all_data

            # 获取ETF列表
            etf_list = get_etf_list()
            print(f"  共获取 {len(etf_list)} 只候选ETF\n")

            # 下载历史数据
            data_dict = download_all_data(etf_list)
            print(f"\n  成功下载 {len(data_dict)} 只ETF的历史数据")

        except Exception as e:
            print(f"\n❌ 数据下载失败: {e}")
            print("\n可能的原因:")
            print("  1. 网络连接问题，请检查网络")
            print("  2. AKShare服务暂时不可用，请稍后重试")
            print("  3. pip install akshare --upgrade 更新AKShare版本")
            sys.exit(1)
    else:
        print(">>> 数据已缓存，跳过下载（使用 --download 强制刷新）\n")

    # === 第二步：命令行回测模式 ===
    if args.backtest:
        print(">>> 第二步：运行回测\n")
        try:
            from strategy import run_backtest, get_latest_signals
            from data_fetcher import load_all_data, get_etf_list

            data_dict = load_all_data()
            etf_info = get_etf_list()

            if data_dict is None:
                print("❌ 未找到数据，请先运行: python main.py --download")
                sys.exit(1)

            # 运行回测
            result = run_backtest(data_dict, etf_info=etf_info)

            # 显示最新信号
            print("\n>>> 最新ETF动量排名 TOP 10")
            signals = get_latest_signals(data_dict, etf_info=etf_info, top_n=10)
            if not signals.empty:
                cols = [c for c in ["code", "name", "score", "ret_5d", "ret_10d", "ret_20d", "price"]
                        if c in signals.columns]
                print(signals[cols].to_string(index=False))

        except Exception as e:
            print(f"❌ 回测失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        print("\n完成！")
        return

    # === 第三步：启动Web服务 ===
    print(">>> 启动Web服务\n")
    try:
        from web_server import app

        print(f"  本机访问: http://localhost:{args.port}")
        print(f"  按 Ctrl+C 停止服务")
        print()

        # 如果需要公网隧道
        tunnel_proc = None
        if args.tunnel:
            import subprocess
            print(">>> 启动公网隧道...")
            print("    连接中，公网地址将在下方显示...\n")
            tunnel_proc = subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "ServerAliveInterval=30",
                 "-R", f"80:localhost:{args.port}",
                 "nokey@localhost.run"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )

        app.run(host=args.host, port=args.port, debug=False)

    except Exception as e:
        print(f"❌ Web服务启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
