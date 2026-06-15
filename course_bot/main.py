"""
教务系统自动选课 —— 一键运行

用法:
    python main.py                默认模式：假定购物车已手动填好，直接等窗口提交
    python main.py --cart         先自动加购物车，再等窗口提交
    python main.py --window "..." 指定选课时间（覆盖配置）
"""

import argparse, asyncio

from config import Config
from client import ensure_cdp
from course import CourseBot


async def main():
    parser = argparse.ArgumentParser(description="教务系统自动选课")
    parser.add_argument("--cart", action="store_true",
                        help="先自动加购物车（购物车已手动填好则不传此参数）")
    parser.add_argument("--window", type=str,
                        help="选课窗口时间，如 '2026-06-18 12:29:55'")
    args = parser.parse_args()

    config = Config()
    if args.window:
        config.window_open = args.window

    # 1. 确保 CDP 可用（自动启动浏览器）
    ensure_cdp(config)

    # 2. 连接并运行
    bot = CourseBot(config)
    try:
        await bot.run(do_cart=args.cart)
    except KeyboardInterrupt:
        print("\n退出")
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await bot.client.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
