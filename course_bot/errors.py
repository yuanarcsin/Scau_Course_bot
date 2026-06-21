"""
错误码体系 —— 每个 API 失败场景对应唯一错误码，方便日志检索与调试。
"""

from enum import Enum


class ErrorCode(Enum):
    # ---- 登录 (1xxx) ----
    CSRF_NOT_FOUND   = (1001, "登录页未找到 CSRF 令牌")
    RSA_KEY_FAILED   = (1002, "获取 RSA 公钥失败")
    LOGIN_FAILED     = (1003, "登录失败，账号或密码错误")

    # ---- 会话 (11xx) ----
    SESSION_EXPIRED  = (1101, "会话已过期，需重新登录")

    # ---- 页面参数 (12xx) ----
    PAGE_LOAD_FAILED = (1201, "选课页面加载失败")
    TAB_NOT_FOUND    = (1202, "未找到课程类型标签页")
    COURSE_NOT_FOUND = (1203, "未在可选列表中找到目标课程")
    DO_JXB_ID_MISSING = (1204, "课程缺少 do_jxb_id，无法提交")

    # ---- 选课操作 (13xx) ----
    WINDOW_NOT_OPEN  = (1301, "选课窗口未开启")
    TIME_CONFLICT    = (1302, "上课时间冲突")
    ADD_CART_FAILED  = (1303, "加入购物车失败")
    CART_SUBMIT_FAILED = (1304, "购物车提交失败")
    ALREADY_SELECTED = (1305, "该课程已选，无需重复提交")
    ALREADY_IN_CART  = (1306, "课程已在购物车中")
    DROP_FAILED      = (1307, "退课失败")
    DROP_SUCCESS_BUT_ENROLL_FAILED = (1308, "退课成功但选课失败，需紧急恢复")

    # ---- 网络 (14xx) ----
    REQUEST_TIMEOUT  = (1401, "请求超时")
    SERVER_ERROR     = (1402, "服务器返回错误")
    INVALID_RESPONSE = (1403, "服务器返回非预期格式")

    # ---- 登录前置检查 (15xx) ----
    IDENTITY_CHECK   = (1501, "触发身份信息确认，当前不支持")
    LOGIN_LOCKED     = (1502, "登录失败次数过多，账号暂时锁定")
    USER_NOT_EXIST   = (1503, "登录前检查：用户不存在")

    @property
    def brief(self) -> str:
        return self.value[1]

    def __str__(self):
        return f"E{self.value[0]} {self.value[1]}"


class BotError(Exception):
    """选课机器人统一异常"""

    def __init__(self, code: ErrorCode, detail: str = ""):
        self.code = code
        self.detail = detail

    def __str__(self):
        base = f"[E{self.code.value[0]}] {self.code.value[1]}"
        return f"{base}: {self.detail}" if self.detail else base


class LoginError(BotError):
    """登录阶段异常（保留兼容旧代码）"""
    pass


class ApiError(BotError):
    """API 调用异常（保留兼容旧代码）"""
    pass
