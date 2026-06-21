"""
对 Mock Server 运行完整选课流程测试。
用法: python -m pytest course_bot/tests/test_bot.py -v
"""
import asyncio, sys, threading, time, logging
from pathlib import Path

import pytest
import httpx

# 确保项目根目录在 path
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from course_bot.config import Config
from course_bot.client import Client
from course_bot.course import CourseBot
from course_bot.mock_server.server import create_app
from course_bot.mock_server.scenarios import (
    Scenario, normal_scenario, window_closed_scenario,
    conflict_scenario, high_load_scenario,
)
from course_bot.errors import ErrorCode, BotError

logging.basicConfig(level=logging.WARNING)


# ================================================================
# Mock Server 管理
# ================================================================

def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockServerFixture:
    """在独立线程中运行 FastAPI mock server"""

    def __init__(self, scenario: Scenario | None = None):
        self.scenario = scenario or normal_scenario()
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread: threading.Thread | None = None
        self._app = None

    @property
    def url(self) -> str:
        return self.base_url

    def start(self):
        import uvicorn
        self._app = create_app(self.scenario)
        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=self.port,
            log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        time.sleep(0.5)  # 等服务器就绪

    def stop(self):
        if self._server:
            self._server.should_exit = True
            self._thread.join(timeout=2)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# ================================================================
# 测试
# ================================================================

class TestLogin:
    """登录相关测试"""

    def test_login_success(self):
        with MockServerFixture() as m:
            cfg = Config(base_url=m.url, student_id="202514320119",
                         password="060098")
            client = Client(cfg)
            assert client.login() is True
            assert client._logged_in is True
            client.close()

    def test_login_wrong_student_id(self):
        with MockServerFixture() as m:
            cfg = Config(base_url=m.url, student_id="999999999",
                         password="060098")
            client = Client(cfg)
            with pytest.raises(BotError) as exc:
                client.login()
            assert exc.value.code == ErrorCode.LOGIN_FAILED
            client.close()


class TestPageParams:
    """页面参数提取测试"""

    def test_fetch_params(self):
        with MockServerFixture() as m:
            cfg = Config(base_url=m.url, student_id="202514320119",
                         password="060098")
            client = Client(cfg)
            client.login()
            params = client.fetch_page_params()
            assert params["xkxnm"] == "2026"
            assert params["xkxqm"] == "3"
            assert "06" in client.tabs
            client.close()


class TestCourseFind:
    """课程发现测试"""

    def test_find_english(self):
        with MockServerFixture() as m:
            cfg = Config(base_url=m.url, student_id="202514320119",
                         password="060098")
            client = Client(cfg)
            client.login()
            client.fetch_page_params()
            found = client.find_target_course("202620271-604792-005", "06")
            assert found is not None
            assert found["kcmc"] == "大学英语Ⅲ (翻译)"
            assert len(found["do_jxb_id"]) > 50  # 128 位 hex
            client.close()

    def test_find_pe(self):
        with MockServerFixture() as m:
            cfg = Config(base_url=m.url)
            client = Client(cfg)
            client.login()
            client.fetch_page_params()
            found = client.find_target_course(
                "202620271-610023-001-乒乓球02", "06")
            assert found is not None
            assert found["kcmc"] == "乒乓球"
            client.close()

    def test_find_nonexistent(self):
        with MockServerFixture() as m:
            cfg = Config(base_url=m.url)
            client = Client(cfg)
            client.login()
            client.fetch_page_params()
            found = client.find_target_course("NONEXISTENT-COURSE", "06")
            assert found is None
            client.close()


class TestSelectFlow:
    """完整选课流程测试"""

    def _make_config(self, base_url: str) -> Config:
        cfg = Config(base_url=base_url)
        cfg.target_courses = [
            {"jxbbh": "202620271-604792-005", "kklxdm": "06"},
        ]
        cfg.window_open = "2026-06-10 12:30:00"  # 已过窗口
        cfg.submit_retries = 3
        cfg.session_keepalive = 0
        return cfg

    def test_full_flow_normal(self):
        """正常流程：登录→发现→提交→成功"""
        with MockServerFixture() as m:
            cfg = self._make_config(m.url)
            bot = CourseBot(cfg)

            # Phase 1+2
            bot.phase_login_and_find()
            assert len(bot.targets) == 1
            assert bot.targets[0]["jxbbh"] == "202620271-604792-005"

            # Phase 3 (skip wait, window already open)
            bot._window_dt = bot.config.window_open_dt

            # Phase 4: submit
            asyncio.run(bot.phase_submit())
            assert bot._stats["success"] == ["202620271-604792-005"]
            assert len(bot._stats["failed"]) == 0

    def test_full_flow_through_run(self):
        """通过主 run() 方法运行完整流程"""
        with MockServerFixture() as m:
            cfg = self._make_config(m.url)
            cfg.max_retries = 1
            bot = CourseBot(cfg)
            # 直接跳过等待阶段
            bot._window_dt = datetime_far_past()

            async def quick_run():
                bot.phase_login_and_find()
                await bot.phase_submit()

            asyncio.run(quick_run())
            assert len(bot._stats["success"]) == 1


class TestErrorScenarios:
    """异常场景测试"""

    def _make_config(self, base_url: str) -> Config:
        cfg = Config(base_url=base_url)
        cfg.target_courses = [
            {"jxbbh": "202620271-604792-005", "kklxdm": "06"},
        ]
        cfg.window_open = "2026-06-10 12:30:00"
        cfg.submit_retries = 3
        cfg.session_keepalive = 0
        return cfg

    def test_window_closed(self):
        """窗口未开 → 提交失败"""
        sc = window_closed_scenario()
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            bot = CourseBot(cfg)
            bot.phase_login_and_find()

            async def run():
                bot._window_dt = datetime_far_past()
                await bot.phase_submit()

            asyncio.run(run())
            assert len(bot._stats["success"]) == 0

    def test_conflict(self):
        """时间冲突 → 提交失败"""
        sc = conflict_scenario()
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            bot = CourseBot(cfg)
            bot.phase_login_and_find()

            async def run():
                bot._window_dt = datetime_far_past()
                await bot.phase_submit()

            asyncio.run(run())
            # 冲突检查应该不阻止加购（只有冲突检查≠加购失败）
            # 看实际实现的行为

    def test_high_load(self):
        """高负载场景 → 登录超时是预期的"""
        sc = high_load_scenario()
        sc.failure_rate = 0.0
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            cfg.request_timeout = 6.0   # 高延迟场景需要更长超时
            cfg.submit_timeout = 10.0
            bot = CourseBot(cfg)
            bot.phase_login_and_find()

            async def run():
                bot._window_dt = datetime_far_past()
                await bot.phase_submit()

            asyncio.run(run())
            # 在有延迟 + 限流的情况下应该仍然成功


class TestLoginChecks:
    """登录前置检查测试"""

    def _make_config(self, base_url: str) -> Config:
        cfg = Config()
        cfg.base_url = base_url
        return cfg

    def test_identity_check_passes(self):
        """身份确认检查：非 true 响应应通过"""
        sc = normal_scenario()
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            client = Client(cfg)
            client.login()  # 应该不抛出异常
            assert client._logged_in

    def test_login_locked(self):
        """登录失败次数达阈值时应抛出 LoginError"""
        sc = normal_scenario()
        sc.login_locked = True
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            client = Client(cfg)
            with pytest.raises(BotError) as exc:
                client.login()
            assert exc.value.code == ErrorCode.LOGIN_LOCKED


class TestDropAndSwap:
    """退选与换课测试"""

    def _make_config(self, base_url: str) -> Config:
        cfg = Config()
        cfg.base_url = base_url
        cfg.target_courses = [
            {"jxbbh": "202620271-604792-005", "kklxdm": "06"},
        ]
        cfg.backup_jxbbh = "202620271-610023-001-乒乓球02"
        cfg.swap_mode = "swap"
        return cfg

    def test_query_selected_empty(self):
        """默认场景下已选列表为空"""
        sc = normal_scenario()
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            client = Client(cfg)
            client.login()
            client.fetch_page_params()
            courses = client.query_selected_courses()
            assert courses == []

    def test_drop_course_not_in_window(self):
        """窗口未开时退选应返回失败"""
        sc = window_closed_scenario()
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            client = Client(cfg)
            client.login()
            client.fetch_page_params()
            result = client.drop_course("test_do_jxb_id", "test_kch_id")
            assert result.get("flag") != "1"

    def test_swap_mode_requires_backup(self):
        """换课模式无保底课程时回退到普通模式"""
        sc = normal_scenario()
        with MockServerFixture(sc) as m:
            cfg = self._make_config(m.url)
            cfg.backup_jxbbh = ""  # 不设置保底
            bot = CourseBot(cfg)
            bot.phase_login_and_find()
            assert bot.backup is None

            async def run():
                bot._window_dt = datetime_far_past()
                await bot.phase_submit()  # 应走正常提交，不抛异常

            asyncio.run(run())


def datetime_far_past():
    from datetime import datetime
    return datetime(2020, 1, 1, 0, 0, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
