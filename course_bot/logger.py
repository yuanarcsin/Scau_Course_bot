"""
日志模块 —— 同时输出到 stdout 和文件，带时间戳和级别。
"""

import logging, sys
from datetime import datetime
from pathlib import Path

_logger: logging.Logger | None = None
_log_file: Path | None = None


def setup(log_dir: Path, tag: str = "") -> logging.Logger:
    """初始化日志：控制台 + 文件双输出。返回 root logger。"""
    global _logger, _log_file

    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{tag}" if tag else ""
    _log_file = log_dir / f"run_{ts}{tag}.log"

    _logger = logging.getLogger("course_bot")
    _logger.setLevel(logging.DEBUG)
    _logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # 控制台
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    _logger.addHandler(ch)

    # 文件
    fh = logging.FileHandler(str(_log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    _logger.info(f"日志文件: {_log_file}")
    return _logger


def get() -> logging.Logger:
    """获取全局 logger，未初始化则返回默认 logger"""
    return _logger or logging.getLogger("course_bot")


def log_file_path() -> Path | None:
    return _log_file
