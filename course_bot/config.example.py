"""
配置模块 —— 学期、账号、目标课程、并发策略。
复制为 config.py 并填入真实信息。
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class FieldMapping:
    """PartDisplay 响应字段映射 —— 服务器改字段名时只改这里即可。"""

    # 动态提取：True 时从响应反向提取字段名
    dynamic_extract: bool = True
    submit_id: str = "do_jxb_id"
    class_id: str = "jxb_id"
    course_id: str = "kch_id"
    course_code: str = "kch"
    course_name: str = "kcmc"
    class_name: str = "jxbmc"
    type_code: str = "kklxdm"
    class_type: str = "jxbzls"
    credit: str = "jxbxf"
    capacity: str = "jxbrs"
    enrolled: str = "yxzrs"
    teacher: str = "jsxx"
    location: str = "jxdd"


@dataclass
class Config:
    """选课配置 —— 复制为 config.py 后填入真实信息"""

    # ====== 账号 ======
    student_id: str = "你的学号"
    password: str = "你的密码"

    # ====== 教务系统 ======
    base_url: str = "https://jwzf.scau.edu.cn"
    gnmkdm: str = "N253512"

    # ====== 字段映射 ======
    fields: FieldMapping = field(default_factory=FieldMapping)

    # ====== 目标课程 ======
    # kklxdm: 01=专业选修课  06=板块课/体育/英语
    target_courses: list = field(default_factory=lambda: [
        {"jxbbh": "202620271-XXXXXX-XXX", "kklxdm": "01"},
        {"jxbbh": "202620271-XXXXXX-XXX", "kklxdm": "01"},
    ])

    # ====== 选课时间窗口 ======
    window_open: str = "2026-06-18 12:29:55"
    window_close: str = "2026-06-22 23:59:59"

    # ====== 超时与重试 ======
    request_timeout: float = 2.0
    max_retries: int = 3
    retry_delay: float = 0.2

    # ====== 提交阶段 ======
    submit_retries: int = 100
    submit_retry_delay: float = 0.15
    submit_timeout: float = 2.0

    # ====== 预绑定 ======
    prebind_advance_minutes: float = 10.0
    prebind_timeout: float = 5.0
    prebind_retries: int = 3
    cache_file: str = "cache.json"

    # ====== 抢课核心（乐观提交） ======
    snipe_timeout: float = 1.2
    snipe_stagger: float = 0.15

    # ====== 捡漏器 ======
    hunter_interval: float = 4.0
    hunter_jitter: float = 1.0
    hunter_duration: int = 600

    # ====== 并发 ======
    max_concurrent: int = 4

    # ====== 等待阶段 ======
    session_keepalive: int = 120
    nat_refresh_seconds: float = 5.0

    # ====== 日志 ======
    log_dir: str = "logs"

    @property
    def window_open_dt(self) -> datetime:
        return datetime.strptime(self.window_open, "%Y-%m-%d %H:%M:%S")

    @property
    def window_close_dt(self) -> datetime:
        return datetime.strptime(self.window_close, "%Y-%m-%d %H:%M:%S")
