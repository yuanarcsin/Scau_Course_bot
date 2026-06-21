"""
教务系统自动选课 —— CLI 入口。

用法:
    python -m course_bot.main prebind          预绑定（搜索并缓存 do_jxb_id）
    python -m course_bot.main snipe            抢课核心（读缓存 → 乐观提交）
    python -m course_bot.main hunt             独立捡漏（轮询空位，测试功能）
    python -m course_bot.main serve            启动 FastAPI 前端服务
    python -m course_bot.main all              完整流程（预绑定 → 等待 → 抢课）
"""

import argparse, asyncio, sys, traceback
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from course_bot.config import Config
from course_bot.logger import setup as setup_logger


async def cmd_prebind(args):
    config = Config()
    from course_bot.prebind import Prebinder
    prebinder = Prebinder(config)
    prebinder.run()
    print("预绑定完成。可执行: python -m course_bot.main snipe")


async def cmd_snipe(args):
    config = Config()
    from course_bot.prebind import load_cache
    cache = load_cache(config)
    from course_bot.sniper import Sniper
    sniper = Sniper(config)
    try:
        await sniper.run(cache)
    except KeyboardInterrupt:
        print("\n用户中断")


async def cmd_hunt(args):
    config = Config()
    from course_bot.prebind import load_cache
    cache = load_cache(config)
    from course_bot.hunter import Hunter
    hunter = Hunter(config)
    try:
        await hunter.run(cache)
    except KeyboardInterrupt:
        print("\n用户中断")
        hunter.stop()


async def cmd_all(args):
    """完整流程：预绑定 → 等待窗口 → 抢课"""
    config = Config()
    if args.window:
        config.window_open = args.window
    if args.no_find:
        print("跳过预绑定，直接使用缓存进行抢课...")
    else:
        from course_bot.prebind import Prebinder
        prebinder = Prebinder(config)
        cache = prebinder.run()
        print(f"预绑定完成，共 {cache.get('_meta', {}).get('count', 0)} 门课程")

    # 可选：启用捡漏
    if args.hunt:
        print("将在抢课后启动捡漏器...")

    # 此处可调用旧版 CourseBot 的等待逻辑，或直接进入 snipe
    from course_bot.prebind import load_cache
    cache = load_cache(config)

    from course_bot.sniper import Sniper
    sniper = Sniper(config)
    try:
        await sniper.run(cache)
    except KeyboardInterrupt:
        print("\n用户中断")

    if args.hunt:
        from course_bot.hunter import Hunter
        hunter = Hunter(config)
        try:
            await hunter.run(cache)
        except KeyboardInterrupt:
            print("\n捡漏器中断")
            hunter.stop()


async def main():
    parser = argparse.ArgumentParser(
        description="华农教务系统自动选课 v6.0",
        prog="python -m course_bot.main",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # prebind
    p_pre = sub.add_parser("prebind", help="预绑定 — 搜索并缓存 do_jxb_id")
    p_pre.set_defaults(func=cmd_prebind)

    # snipe
    p_sni = sub.add_parser("snipe", help="抢课核心 — 读缓存 + 乐观提交")
    p_sni.set_defaults(func=cmd_snipe)

    # hunt
    p_hunt = sub.add_parser("hunt", help="独立捡漏 — 轮询空位（测试功能）")
    p_hunt.set_defaults(func=cmd_hunt)

    # all
    p_all = sub.add_parser("all", help="完整流程（预绑定 → 等待 → 抢课）")
    p_all.add_argument("--window", type=str, help="选课窗口时间")
    p_all.add_argument("--no-find", action="store_true", help="跳过预绑定")
    p_all.add_argument("--hunt", action="store_true", help="抢课后启动捡漏器")
    p_all.set_defaults(func=cmd_all)

    # serve
    p_serve = sub.add_parser("serve", help="启动 FastAPI 前端服务")
    p_serve.add_argument("--port", type=int, default=8742, help="端口（默认 8742）")
    p_serve.set_defaults(func=cmd_serve)

    # 日志参数
    parser.add_argument("--log-dir", type=str, default="logs", help="日志目录")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # 日志初始化
    log_dir = _project_root / args.log_dir
    setup_logger(log_dir)

    if args.command == "serve":
        await cmd_serve(args)
    else:
        await args.func(args)


async def cmd_serve(args):
    """启动 FastAPI 前端服务"""
    from course_bot.server import create_app, run_server
    app = create_app()
    print(f"前端服务启动: http://127.0.0.1:{args.port}")
    await run_server(app, port=args.port)


if __name__ == "__main__":
    asyncio.run(main())
