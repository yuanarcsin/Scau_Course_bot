"""
华农教务系统自动选课 —— CLI 入口。

用法:
    python run.py              完整流程（登录 → 等待窗口 → 提交 → 校验 → 持续重试）
    python run.py --now         跳过窗口等待，立即提交
    python run.py --hunt        提交成功后持续轮询未选中的课程
"""

import argparse, asyncio, sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from course_bot.config import Config
from course_bot.logger import setup as setup_logger
from course_bot.sniper import Engine


async def main():
    parser = argparse.ArgumentParser(
        description="华农教务系统自动选课",
        prog="python run.py",
    )
    parser.add_argument("--now", action="store_true",
                        help="跳过窗口等待，立即提交")
    parser.add_argument("--hunt", action="store_true",
                        help="提交成功后持续轮询未选中的课程")
    parser.add_argument("--log-dir", type=str, default="logs", help="日志目录")
    parser.add_argument("--window", type=str, help="覆盖选课窗口时间 (YYYY-MM-DD HH:MM:SS)")

    args = parser.parse_args()

    config = Config()
    if args.window:
        config.window_open = args.window

    log_dir = _project_root / args.log_dir
    setup_logger(log_dir)

    engine = Engine(config)

    await engine.run(skip_wait=args.now)

    # 捡漏模式：主流程完成后持续监控
    if args.hunt and engine.courses:
        import logging
        log = logging.getLogger("course_bot")
        pending = [c for c in engine.courses
                   if engine.results.get(c["xkgwcb_id"], {}).get("type") != "success"]
        if pending:
            log.info("=" * 50)
            log.info(f"捡漏模式：监控 {len(pending)} 门课程")
            log.info("=" * 50)
            hunt_interval = getattr(config, 'hunter_interval', 4.0)
            while not engine._stop.is_set():
                await engine._batch_submit()
                await engine._verify()
                pending = [c for c in engine.courses
                           if engine.results.get(c["xkgwcb_id"], {}).get("type") != "success"]
                if not pending:
                    log.info("所有课程已选中，捡漏结束")
                    break
                await asyncio.sleep(hunt_interval)
            engine._print_report()


if __name__ == "__main__":
    asyncio.run(main())
