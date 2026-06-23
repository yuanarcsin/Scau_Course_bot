"""
华农选课助手 —— 一键启动。

前置：浏览器中先将目标课程加入"我的选课意向"。

用法:
    python run.py              完整流程（登录 → 等待窗口 → 批量提交 → 校验 → 持续重试）
    python run.py --now         跳过窗口等待，立即提交
    python run.py --hunt        提交成功后持续监控未选中的课程
    python run.py --window "2026-06-23 12:30:00"  指定选课窗口时间
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))

if __name__ == "__main__":
    from course_bot.main import main
    import asyncio
    asyncio.run(main())
