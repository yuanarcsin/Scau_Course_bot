"""
FastAPI 本地微服务 —— 为前端页面提供 API 和 SSE 实时日志。
绑定 127.0.0.1，仅本地访问。
"""

import asyncio, json, logging, queue, sys, threading, time, traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from course_bot.config import Config
from course_bot.client import Client
from course_bot.errors import BotError, LoginError

log = logging.getLogger("course_bot")

# ================================================================
# 全局状态
# ================================================================

class AppState:
    def __init__(self):
        self.config = Config()
        self.client: Client | None = None
        self.logged_in = False
        self.all_courses: list[dict] = []
        self.target_jxbbhs: list[str] = []
        self.cache: dict = {}
        self.prebind_done = False

        # 运行状态
        self._seizing = False
        self._hunting = False
        self._stop_event = asyncio.Event()

        # SSE 事件队列
        self._event_queues: list[asyncio.Queue] = []

    @property
    def is_seizing(self):
        return self._seizing

    @property
    def is_hunting(self):
        return self._hunting

    def emit(self, event_type: str, data: Any = None):
        """推送事件到所有 SSE 客户端"""
        payload = json.dumps({"type": event_type, "data": data, "ts": datetime.now().strftime("%H:%M:%S.%f")[:-3]},
                             ensure_ascii=False)
        for q in self._event_queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def add_event_queue(self, q: asyncio.Queue):
        self._event_queues.append(q)

    def remove_event_queue(self, q: asyncio.Queue):
        try:
            self._event_queues.remove(q)
        except ValueError:
            pass


state = AppState()


def create_app() -> FastAPI:
    app = FastAPI(title="华农选课助手", version="6.0")

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_path = static_dir / "index.html"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        return HTMLResponse("<h1>前端页面未构建，请先构建 static/index.html</h1>", status_code=404)

    # ---- API ----

    @app.post("/api/login")
    async def api_login(req: Request):
        body = await req.json()
        sid = body.get("student_id", "").strip()
        pwd = body.get("password", "").strip()
        if not sid or not pwd:
            return JSONResponse({"ok": False, "error": "学号和密码不能为空"}, status_code=400)

        state.config.student_id = sid
        state.config.password = pwd
        state.client = Client(state.config)

        try:
            state.client.login()
            state.logged_in = True
            state.emit("login", {"ok": True, "student_id": sid})
            return {"ok": True, "message": "登录成功"}
        except LoginError as e:
            state.logged_in = False
            return JSONResponse({"ok": False, "error": str(e)}, status_code=401)
        except Exception as e:
            state.logged_in = False
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/courses/scan")
    async def api_scan():
        if not state.logged_in or not state.client:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)

        try:
            state.client.fetch_page_params()
            tabs = state.client.tabs

            # 用 PartDisplay 扫描所有 tab
            pp = state.client.page_params
            all_courses = []
            seen = set()
            for kklxdm, tab_info in tabs.items():
                try:
                    data = _build_partdisplay_data(pp, tab_info, kklxdm)
                    resp = state.client._post(
                        state.client._url("/jwglxt/xsxk/zzxkyzb_cxZzxkYzbPartDisplay.html"),
                        data=data)
                    result = resp.json()
                    for c in result.get("tmpList", []):
                        key = c.get("jxbmc", "")
                        if key and key not in seen:
                            seen.add(key)
                            all_courses.append({
                                "jxbmc": key,
                                "kcmc": c.get("kcmc", ""),
                                "kklxdm": c.get("kklxdm", kklxdm),
                                "xf": c.get("jxbxf", c.get("xf", "?")),
                                "yxzrs": c.get("yxzrs", "?"),
                                "jxbrs": c.get("jxbrs", "?"),
                                "jsxx": c.get("jsxx", ""),
                            })
                except Exception as e:
                    state.emit("log", f"Tab {kklxdm} 扫描失败: {e}")

            state.all_courses = all_courses
            state.emit("scan", {"count": len(all_courses)})
            return {"ok": True, "courses": all_courses, "count": len(all_courses)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/prebind")
    async def api_prebind(req: Request):
        if not state.logged_in or not state.client:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)

        body = await req.json()
        jxbbhs = body.get("jxbbhs", state.target_jxbbhs)
        if not jxbbhs:
            return JSONResponse({"ok": False, "error": "请先添加目标课程"}, status_code=400)

        state.emit("log", f"开始预绑定 {len(jxbbhs)} 门课程...")
        cache = {}
        for jxbbh in jxbbhs:
            try:
                found = state.client.find_target_course(jxbbh, "06")
                if found:
                    cache[jxbbh] = {
                        "do_jxb_id": found["do_jxb_id"],
                        "jxb_id": found.get("jxb_id", ""),
                        "kch_id": found.get("kch_id", ""),
                        "kcmc": found.get("kcmc", ""),
                        "jxbmc": found.get("jxbmc", jxbbh),
                        "kklxdm": found.get("kklxdm", "06"),
                        "yxzrs": found.get("yxzrs", "?"),
                        "jxbrs": found.get("jxbrs", "?"),
                    }
                    state.emit("prebind", {"jxbbh": jxbbh, "status": "ok", "kcmc": found.get("kcmc", "")})
                else:
                    state.emit("prebind", {"jxbbh": jxbbh, "status": "not_found"})
            except Exception as e:
                state.emit("prebind", {"jxbbh": jxbbh, "status": "error", "error": str(e)})

        cache["_meta"] = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          "student_id": state.config.student_id,
                          "count": len([k for k in cache if not k.startswith("_")])}
        cache["_page_params"] = state.client.page_params

        state.cache = cache
        state.prebind_done = True

        # 写入文件
        cache_path = Path(state.config.cache_file)
        if not cache_path.is_absolute():
            cache_path = Path(__file__).resolve().parent.parent / cache_path
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        state.emit("log", f"预绑定完成: {cache['_meta']['count']} 门 → {cache_path}")
        return {"ok": True, "cache": cache, "count": cache["_meta"]["count"]}

    @app.post("/api/seize/start")
    async def api_seize_start():
        if not state.prebind_done:
            return JSONResponse({"ok": False, "error": "请先执行预绑定"}, status_code=400)

        state._seizing = True
        state._stop_event.clear()
        state.emit("log", "抢课核心启动（乐观提交模式）")

        # 在后台启动抢课
        asyncio.create_task(_run_seize())
        return {"ok": True, "message": "抢课已启动"}

    @app.post("/api/picker/start")
    async def api_picker_start():
        """启动捡漏器（测试功能）"""
        if not state.prebind_done:
            return JSONResponse({"ok": False, "error": "请先执行预绑定"}, status_code=400)

        if state._hunting:
            return {"ok": True, "message": "捡漏器已在运行"}

        state._hunting = True
        state.emit("log", "捡漏器启动（测试功能）")
        asyncio.create_task(_run_hunter())
        return {"ok": True, "message": "捡漏器已启动（测试功能）"}

    @app.post("/api/stop")
    async def api_stop():
        state._stop_event.set()
        state._seizing = False
        state._hunting = False
        state.emit("stop", {"message": "所有任务已停止"})
        return {"ok": True, "message": "已停止"}

    @app.get("/api/status")
    async def api_status():
        return {
            "logged_in": state.logged_in,
            "prebind_done": state.prebind_done,
            "seizing": state._seizing,
            "hunting": state._hunting,
            "target_count": len(state.target_jxbbhs),
            "cache_count": len([k for k in state.cache if not k.startswith("_")]),
            "courses_count": len(state.all_courses),
        }

    @app.get("/api/events")
    async def api_events():
        """SSE 实时事件流"""
        q = asyncio.Queue(maxsize=256)
        state.add_event_queue(q)

        async def event_stream():
            try:
                yield "data: {\"type\":\"connected\"}\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield "data: {\"type\":\"ping\"}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                state.remove_event_queue(q)

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    return app


async def _run_seize():
    """后台执行抢课核心"""
    from course_bot.sniper import Sniper, optimistic_submit
    from course_bot.concurrent import SessionFactory, ConcurrentSniper, load_courses_from_cache

    try:
        courses = load_courses_from_cache(state.cache, state.target_jxbbhs)
        if not courses:
            state.emit("log", "[Seizer] 无有效课程")
            return

        pp = state.cache.get("_page_params", {})
        for c in courses:
            c.update({
                "rwlx": pp.get("rwlx", "1"),
                "rlkz": pp.get("rlkz", "0"),
                "rlzlkz": pp.get("rlzlkz", "1"),
                "xkkz_id": pp.get("xkkz_id", pp.get("firstXkkzId", "")),
                "njdm_id": pp.get("njdm_id", ""),
                "zyh_id": pp.get("zyh_id", ""),
                "xklc": pp.get("xklc", "1"),
                "xkxnm": pp.get("xkxnm", ""),
                "xkxqm": pp.get("xkxqm", ""),
                "_config": state.config,
            })

        state.emit("log", f"[Seizer] 开始并发提交 {len(courses)} 门课程")

        factory = SessionFactory(state.config)
        sniper = ConcurrentSniper(state.config, pp, factory)

        # 包装 submit 函数以推送 SSE 事件
        async def tracked_submit(client, course):
            result = await optimistic_submit(client, course)
            if result.success:
                state.emit("seize", {"jxbbh": result.jxbbh, "status": "success", "message": result.message})
            else:
                state.emit("seize", {"jxbbh": result.jxbbh, "status": "fail", "message": result.message})
            return result

        results = await sniper.run(courses, tracked_submit)

        success = sum(1 for r in results if r.success)
        state.emit("log", f"[Seizer] 结束: 成功 {success}/{len(results)}")
        state._seizing = False
    except Exception as e:
        state.emit("log", f"[Seizer] 异常: {e}")
        state._seizing = False


async def _run_hunter():
    """后台执行捡漏器（测试功能）"""
    from course_bot.hunter import Hunter

    hunter = Hunter(state.config)
    try:
        await hunter.run(state.cache)
    except Exception as e:
        state.emit("log", f"[Picker] 异常: {e}")
    finally:
        state._hunting = False


def _build_partdisplay_data(pp: dict, tab: dict, kklxdm: str) -> dict:
    return {
        "rwlx": pp.get("rwlx", "1"),
        "xklc": pp.get("xklc", "1"),
        "xkly": pp.get("xkly", "0"),
        "bklx_id": pp.get("bklx_id", ""),
        "sfkkjyxdxnxq": pp.get("sfkkjyxdxnxq", "0"),
        "kzkcgs": pp.get("kzkcgs", "0"),
        "xqh_id": pp.get("xqh_id", "3"),
        "jg_id": pp.get("jg_id_1", pp.get("jg_id", "14")),
        "njdm_id_1": pp.get("njdm_id", ""),
        "zyh_id_1": pp.get("zyh_id", ""),
        "gnjkxdnj": pp.get("gnjkxdnj", "0"),
        "zyh_id": tab.get("zyh_id", pp.get("zyh_id", "")),
        "zyfx_id": pp.get("zyfx_id", "wfx"),
        "njdm_id": tab.get("njdm_id", pp.get("njdm_id", "")),
        "bh_id": pp.get("bh_id", ""),
        "bjgkczxbbjwcx": pp.get("bjgkczxbbjwcx", "0"),
        "xbm": pp.get("xbm", "1"),
        "xslbdm": pp.get("xslbdm", "1"),
        "mzm": pp.get("mzm", "01"),
        "xz": pp.get("xz", "4"),
        "ccdm": pp.get("ccdm", "1"),
        "xsbj": pp.get("xsbj", "0"),
        "sfkknj": pp.get("sfkknj", "0"),
        "sfkkzy": pp.get("sfkkzy", "0"),
        "kzybkxy": pp.get("kzybkxy", "0"),
        "sfznkx": pp.get("sfznkx", "0"),
        "zdkxms": pp.get("zdkxms", "0"),
        "sfkxq": pp.get("sfkxq", "1"),
        "sfkcfx": pp.get("sfkcfx", "0"),
        "kkbk": pp.get("kkbk", "0"),
        "kkbkdj": pp.get("kkbkdj", "0"),
        "bklbkcj": pp.get("bklbkcj", "0"),
        "sfkgbcx": pp.get("sfkgbcx", "0"),
        "sfrxtgkcxd": pp.get("sfrxtgkcxd", "0"),
        "tykczgxdcs": pp.get("tykczgxdcs", "0"),
        "xkxnm": pp.get("xkxnm", ""),
        "xkxqm": pp.get("xkxqm", ""),
        "kklxdm": kklxdm,
        "bbhzxjxb": pp.get("bbhzxjxb", "0"),
        "xkkz_id": tab.get("xkkz_id", ""),
        "xkkz_xh": tab.get("xkkz_xh", ""),
    }


async def run_server(app: FastAPI, host: str = "127.0.0.1", port: int = 8742):
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
