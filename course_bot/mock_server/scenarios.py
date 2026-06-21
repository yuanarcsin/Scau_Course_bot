"""
可配置测试场景 —— 控制 mock server 行为。
"""

from dataclasses import dataclass, field


@dataclass
class Scenario:
    """模拟场景参数，修改这些值后再启动 server 即可切换测试场景"""

    # 选课窗口
    window_open: bool = True         # 窗口是否开启（False 时所有选课 API 返回"时间未到"）
    server_time_offset: int = 0      # 服务器时间偏移（秒），正数=快，负数=慢

    # 网络模拟
    network_delay: float = 0.0       # 每次请求固定延迟（秒）
    max_concurrent: int = 0          # 最大并发请求数（0=不限）

    # 失败注入
    failure_rate: float = 0.0        # 随机失败概率 (0.0 ~ 1.0)
    timeout_rate: float = 0.0        # 随机超时概率

    # 课程状态
    course_available: bool = True    # 课程是否可选（False 时返回"不可选课"）
    course_capacity: int = 50        # 课程容量
    course_enrolled: int = 0         # 已选人数
    conflict_mode: bool = False      # True 时冲突检查返回冲突
    already_in_cart: bool = False    # 课程是否已在购物车

    # 购物车
    cart_has_items: bool = False     # 购物车是否有待提交课程

    # 登录
    valid_student_id: str = "202514320119"
    valid_password: str = "060098"
    login_locked: bool = False        # True 时模拟登录失败次数达阈值


# 预设场景
def normal_scenario() -> Scenario:
    """正常场景：一切顺利"""
    return Scenario()


def window_closed_scenario() -> Scenario:
    """窗口未开启场景"""
    return Scenario(window_open=False)


def high_load_scenario() -> Scenario:
    """高负载场景：延迟 + 限流"""
    return Scenario(network_delay=2.0, max_concurrent=3, failure_rate=0.3)


def course_unavailable_scenario() -> Scenario:
    """课程不可选场景"""
    return Scenario(course_available=False)


def conflict_scenario() -> Scenario:
    """时间冲突场景"""
    return Scenario(conflict_mode=True)
