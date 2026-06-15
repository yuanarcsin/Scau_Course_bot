"""
配置模块 —— 修改这里即可复用于不同学期。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Config:
    """选课配置"""

    # ====== 账号 ======
    student_id: str = ""
    password: str = ""

    # ====== 教务系统 ======
    base_url: str = "https://jwzf.scau.edu.cn"
    cdp_port: int = 9222
    # 选课页面 URL 关键词（用于 CDP 定位标签页）
    jw_host: str = "jwzf.scau.edu.cn"
    page_keyword: str = "index_initMenu"

    # ====== 目标课程 ======
    # 每门课: tab_keyword=侧边栏tab名, jxbbh=教学班编号
    target_courses: list = field(default_factory=lambda: [
        {"tab_keyword": "体育",   "jxbbh": "202620271-610023-001-乒乓球02"},# 这是例子，想选的课自己改
        {"tab_keyword": "大学英语", "jxbbh": "202620271-604792-005"},# 202620271-604792-005就是这个部分，自己改
    ])

    # ====== 选课时间窗口（服务器时间可能有偏差，建议提前几秒） ======
    # 格式: "2026-06-18 12:29:55"  （比官方时间早 5 秒以补偿延迟）
    window_open: str = "2026-06-18 12:29:55"
    window_close: str = "2026-06-22 23:59:59"

    # ====== 重试与超时 ======
    max_retries: int = 5            # 最大重试次数
    retry_delay: float = 2.0        # 重试等待（秒）
    request_timeout: float = 10.0   # 单次请求超时
    tab_switch_delay: float = 3.0   # tab 切换后等待
    cart_modal_delay: float = 4.0   # 购物车弹窗加载等待

    # ====== 检查间隔 ======
    # 选课前多久开始轮询（秒），提前 5 分钟开始检查
    poll_before: int = 300
    poll_interval: float = 1.0      # 轮询间隔

    @property
    def window_open_dt(self) -> datetime:
        return datetime.strptime(self.window_open, "%Y-%m-%d %H:%M:%S")
