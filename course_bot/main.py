"""
教务系统自动选课 —— 直接 HTTP 请求，无需浏览器。

用法:
    python -m course_bot.main              自动匹配课程 + 等窗口一键选课
    python -m course_bot.main --window "2026-06-18 12:29:55"  指定窗口时间
"""

import argparse, asyncio, traceback

from course_bot.config import Config
from course_bot.course import CourseBot


async def main():
    parser = argparse.ArgumentParser(description="华农教务系统自动选课")
    parser.add_argument("--window", type=str,
                        help="选课窗口时间，如 '2026-06-18 12:29:55'")
    args = parser.parse_args()

    config = Config()
    if args.window:
        config.window_open = args.window

    bot = CourseBot(config)
    try:
        await bot.run(do_find=True)
    except KeyboardInterrupt:
        print("\n退出")
    except Exception as e:
        print(f"\n[错误] {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
