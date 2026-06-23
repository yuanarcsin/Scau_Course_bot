"""
配置模板 —— 复制为 config.py 后填入个人信息。

config.py 已被 .gitignore 排除，不会被提交到 GitHub。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FieldMapping:
    """PartDisplay 响应字段映射 —— 服务器改字段名时只改这里。"""
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
    """选课配置 —— 复制此文件为 config.py，修改 <> 标记的占位符。"""

    # ====== 账号（必填） ======
    student_id: str = "<你的学号>"
    password: str = "<你的密码>"

    # ====== 教务系统（一般无需修改） ======
    base_url: str = "https://jwzf.scau.edu.cn"
    gnmkdm: str = "N253512"

    # ====== 字段映射（一般无需修改） ======
    fields: FieldMapping = field(default_factory=FieldMapping)

    # ====== 目标课程（必填） ======
    # jxbbh: 教学班编号，从选课页面课程名旁获取，格式 学年学期-课程号-课序号
    # kklxdm: 01=专业选修课  06=板块课(体育/英语)  10=通识选修课
    target_courses: list = field(default_factory=lambda: [
        {"jxbbh": "<教学班编号>", "kklxdm": "01"},
        # 多门课继续添加:
        # {"jxbbh": "<教学班编号2>", "kklxdm": "06"},
    ])

    # ====== 选课时间窗口（必填） ======
    window_open: str = "<2026-06-23 12:30:00>"
    window_close: str = "2026-06-23 23:59:59"

    # ====== 超时与重试 ======
    request_timeout: float = 2.0
    max_retries: int = 3
    retry_delay: float = 0.2

    # ====== 提交阶段（抢课窗口期） ======
    submit_retries: int = 200
    submit_retry_delay: float = 0.05

    # ====== 爆发提交 ======
    snipe_timeout: float = 0.2          # 提交请求超时（秒）
    burst_count: int = 2                # 爆发并发数
    lead_time: float = 0.0              # 提前启动秒数，0 = 准时

    # ====== 登录选项 ======
    # True = 保留浏览器登录（脚本和浏览器可同时在线）
    preserve_browser_session: bool = True

    # ====== 等待阶段 ======
    session_keepalive: int = 120        # 保活间隔（秒），0 禁用
    nat_refresh_seconds: float = 5.0    # 窗口前 NAT 刷新

    # ====== 日志 ======
    log_dir: str = "logs"

    @property
    def window_open_dt(self) -> datetime:
        return datetime.strptime(self.window_open, "%Y-%m-%d %H:%M:%S")

    @property
    def window_close_dt(self) -> datetime:
        return datetime.strptime(self.window_close, "%Y-%m-%d %H:%M:%S")
