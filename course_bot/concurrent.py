"""
并发管理器 —— 为每门课程创建独立 Session，错开启动并行提交。

参考: referance_tool/SCAU-course-tool 的 clone_for_worker() 模式
  - 每门课程独立 httpx.AsyncClient（独立 Cookie 域）
  - 错开 stagger 秒避免同时冲击服务器
  - asyncio.gather 并行执行
"""

import asyncio, logging, json, random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine
from pathlib import Path

import httpx

from course_bot.config import Config

log = logging.getLogger("course_bot")


@dataclass
class TaskResult:
    jxbbh: str
    success: bool
    message: str = ""
    started: str = ""
    ended: str = ""


class SessionFactory:
    """为每个课程创建独立的 httpx.AsyncClient（独立 Cookie jar）"""

    def __init__(self, config: Config, base_headers: dict = None):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.base_headers = base_headers or {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    def create(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self.base_headers.copy(),
            follow_redirects=True,
            timeout=httpx.Timeout(self.config.snipe_timeout),
        )


class ConcurrentSniper:
    """管理多门课程并行提交"""

    def __init__(self, config: Config, page_params: dict, session_factory: SessionFactory):
        self.config = config
        self.page_params = page_params
        self.factory = session_factory
        self._results: list[TaskResult] = []
        self._stop = asyncio.Event()

    def stop(self):
        self._stop.set()

    @property
    def results(self) -> list[TaskResult]:
        return self._results

    async def run(
        self,
        courses: list[dict],
        submit_fn: Callable[[httpx.AsyncClient, dict], Coroutine[Any, Any, TaskResult]],
    ) -> list[TaskResult]:
        """
        并行提交多门课程。
        courses: [{do_jxb_id, jxb_id, kch_id, kcmc, jxbbh, kklxdm, ...}]
        submit_fn: async (client, course) -> TaskResult
        """
        if not courses:
            log.warning("无课程待提交")
            return []

        log.info(f"启动并发提交: {len(courses)} 门课程, "
                 f"错开间隔 {self.config.snipe_stagger}s")

        tasks = []
        for i, course in enumerate(courses):
            client = self.factory.create()
            task = asyncio.create_task(
                self._run_one(client, course, submit_fn, i)
            )
            tasks.append(task)
            # 错开启动
            if i < len(courses) - 1:
                await asyncio.sleep(self.config.snipe_stagger)

        await asyncio.gather(*tasks, return_exceptions=True)
        return self._results

    async def _run_one(
        self,
        client: httpx.AsyncClient,
        course: dict,
        submit_fn: Callable,
        index: int,
    ):
        jxbbh = course.get("jxbbh", "?")
        started = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            result = await submit_fn(client, course)
            result.started = started
            result.ended = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._results.append(result)
        except asyncio.CancelledError:
            self._results.append(TaskResult(jxbbh=jxbbh, success=False,
                                            message="任务取消", started=started))
        except Exception as e:
            self._results.append(TaskResult(jxbbh=jxbbh, success=False,
                                            message=str(e), started=started))
        finally:
            try:
                await client.aclose()
            except Exception:
                pass


# ================================================================
# 便捷函数
# ================================================================

def load_courses_from_cache(cache: dict, target_jxbbhs: list[str]) -> list[dict]:
    """从预绑定缓存中提取目标课程列表（保持顺序）"""
    courses = []
    for jxbbh in target_jxbbhs:
        if jxbbh in cache and not jxbbh.startswith("_"):
            c = cache[jxbbh].copy()
            c["jxbbh"] = jxbbh
            courses.append(c)
        else:
            log.warning(f"缓存中未找到: {jxbbh}")
    return courses
