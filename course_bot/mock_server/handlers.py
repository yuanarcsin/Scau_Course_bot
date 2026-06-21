"""
API 端点处理 —— 模拟正方教务系统行为。
"""

import time, random, asyncio, logging
from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .data import (
    MOCK_CSRF, MOCK_RSA_MODULUS, MOCK_RSA_EXPONENT,
    PAGE_PARAMS, TABS, COURSES,
)
from .scenarios import Scenario

log = logging.getLogger("mock_server")


class ServerState:
    """模拟服务器运行时状态"""
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.logged_in: bool = False
        self.login_id: str = ""
        self.cart: list[dict] = []       # 购物车中的课程
        self.selected: list[dict] = []   # [{jxbmc, do_jxb_id, jxb_id, kch_id, kcmc, kklxdm}]
        self._active_requests: int = 0
        self._lock = asyncio.Lock()

    async def _pre_hook(self) -> dict | None:
        """请求前检查：延迟、限流、随机失败"""
        s = self.scenario

        # 网络延迟
        if s.network_delay > 0:
            jitter = random.uniform(0, s.network_delay * 0.5)
            await asyncio.sleep(s.network_delay + jitter)

        # 并发限流
        if s.max_concurrent > 0:
            async with self._lock:
                if self._active_requests >= s.max_concurrent:
                    return {"error": "rate_limited"}
                self._active_requests += 1

        # 随机超时
        if s.timeout_rate > 0 and random.random() < s.timeout_rate:
            await asyncio.sleep(30)  # 模拟超时

        # 随机失败
        if s.failure_rate > 0 and random.random() < s.failure_rate:
            return {"error": "random_failure"}

        return None

    async def _post_hook(self):
        if self.scenario.max_concurrent > 0:
            async with self._lock:
                self._active_requests = max(0, self._active_requests - 1)

    @property
    def _window_check(self) -> bool:
        return self.scenario.window_open


# 全局状态（每次启动重置）
_state: ServerState | None = None


def get_state() -> ServerState:
    global _state
    if _state is None:
        _state = ServerState(Scenario())
    return _state


def init_state(scenario: Scenario):
    global _state
    _state = ServerState(scenario)


# ================================================================
# 端点处理函数
# ================================================================

async def handle_login_page(request: Request) -> HTMLResponse:
    """GET /xtgl/login_slogin.html — 返回含 CSRF 的登录页"""
    st = get_state()
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    <input type="hidden" id="csrftoken" value="{MOCK_CSRF}">
    <form><input id="yhm"><input id="mm"><button id="dl">登录</button></form>
    <div id="tips"></div>
    </body></html>"""
    resp = HTMLResponse(html)
    resp.set_cookie("JSESSIONID", "mock-jsessionid-12345")
    return resp


async def handle_public_key(request: Request) -> JSONResponse:
    """GET /xtgl/login_getPublicKey.html — 返回 RSA 公钥"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)
    await st._post_hook()
    return JSONResponse({
        "modulus": MOCK_RSA_MODULUS,
        "exponent": MOCK_RSA_EXPONENT,
    })


async def handle_login_submit(request: Request) -> HTMLResponse:
    """POST /xtgl/login_slogin.html — 验证登录"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return HTMLResponse("Server Error", status_code=503)

    try:
        body = await request.form()
    except Exception:
        body = {}

    yhm = body.get("yhm", "")
    mm = body.get("mm", "")      # 模拟环境下不验证加密，只检查非空
    csrf = body.get("csrftoken", "")

    await st._post_hook()

    if not yhm or not mm:
        return HTMLResponse('<div id="tips">账号或密码不能为空</div>')

    if yhm != st.scenario.valid_student_id:
        return HTMLResponse('<div id="tips">账号或密码错误</div>')

    # 登录成功
    st.logged_in = True
    st.login_id = yhm
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    <input type="hidden" value="{yhm}">
    <div>欢迎</div>
    </body></html>"""
    resp = HTMLResponse(html)
    resp.set_cookie("SESSION", f"mock-session-{yhm}")
    return resp


async def handle_xk_index(request: Request) -> HTMLResponse:
    """GET /xsxk/zzxkyzb_cxZzxkYzbIndex.html — 选课主页"""
    st = get_state()
    if not st.logged_in:
        return HTMLResponse("Not logged in", status_code=401)

    hook = await st._pre_hook()
    if hook:
        return HTMLResponse("Error", status_code=503)
    await st._post_hook()

    # 构造含所有隐藏 input 的 HTML
    inputs_html = ""
    for key, val in PAGE_PARAMS.items():
        inputs_html += f'<input type="hidden" id="{key}" name="{key}" value="{val}">\n'

    # Tab 标签
    tabs_html = ""
    for kklxdm, tab in TABS.items():
        tabs_html += (
            f'<a id="tab_kklx_{kklxdm}" '
            f'onclick="queryCourse(this,\'{kklxdm}\',\'{tab["xkkz_id"]}\','
            f'\'{tab["njdm_id"]}\',\'{tab["zyh_id"]}\',\'{tab["xkkz_xh"]}\')">'
            f'{kklxdm}</a>\n'
        )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    {inputs_html}
    {tabs_html}
    </body></html>"""
    return HTMLResponse(html)


async def handle_display(request: Request) -> HTMLResponse:
    """POST /xsxk/zzxkyzb_cxZzxkYzbDisplay.html"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return HTMLResponse("Error", status_code=503)
    await st._post_hook()

    html = f"""<html><body>
    <input id="xkkssj" value="{PAGE_PARAMS['xkkssj']}">
    <input id="xkjssj" value="{PAGE_PARAMS['xkjssj']}">
    <input id="hdmc" value="">
    </body></html>"""
    return HTMLResponse(html)


async def handle_part_display(request: Request) -> JSONResponse:
    """POST /xsxk/zzxkyzb_cxZzxkYzbPartDisplay.html — 课程列表"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)
    await st._post_hook()

    try:
        body = await request.form()
    except Exception:
        body = {}

    kklxdm = body.get("kklxdm", "06")
    filter_text = body.get("filter_list[0]", "")

    # 筛选匹配课程
    results = []
    for c in COURSES:
        if c["kklxdm"] == kklxdm:
            if not filter_text or filter_text in c["kcmc"] or filter_text in c["jxbmc"]:
                results.append(dict(c))

    if not results:
        return JSONResponse({"tmpList": [], "sfxsjc": "1"})

    return JSONResponse({"tmpList": results})


async def handle_check_cart(request: Request) -> PlainTextResponse:
    """POST /xsxk/zzxkyzb_cxCheckZyZzxkYzbInCart.html"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)
    await st._post_hook()

    if st.scenario.already_in_cart:
        return PlainTextResponse('"2"')
    return PlainTextResponse('"1"')


async def handle_check_conflict(request: Request) -> JSONResponse:
    """POST /xsxk/zzxkyzb_cxCtKcZyZzxkYzb.html"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)
    await st._post_hook()

    if st.scenario.conflict_mode:
        return JSONResponse({
            "flag": "0",
            "msg": "与 数据结构 上课时间冲突",
        })
    return JSONResponse({"flag": "1"})


async def handle_add_cart(request: Request) -> JSONResponse:
    """POST /xsxk/zzxkyzbjk_xkBcZyZzxkYzbToCart.html — 加入购物车"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)

    try:
        body = await request.form()
    except Exception:
        body = {}
    await st._post_hook()

    if not st._window_check:
        return JSONResponse({"flag": "0", "msg": "不在选课时间内，不可选课"})

    if not st.scenario.course_available:
        return JSONResponse({"flag": "0", "msg": "不可选课"})

    do_jxb_id = body.get("jxb_ids", "")

    # 存入购物车
    st.cart.append({
        "do_jxb_id": do_jxb_id,
        "xkgwcb_id": f"cart_{len(st.cart)+1:04d}",
    })

    log.info(f"加购: do_jxb_id={do_jxb_id[:30]}...")
    return JSONResponse({"flag": "1"})


async def handle_query_cart(request: Request) -> JSONResponse:
    """POST /xsxk/zzxkyzb_cxWdgwcZzxkYzb.html — 查询购物车"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)
    await st._post_hook()
    return JSONResponse({"items": st.cart})


async def handle_submit_cart(request: Request) -> JSONResponse:
    """POST /xsxk/zzxkyzbjk_xkBcZyZzxkYzbFromCart.html — 购物车提交"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)

    try:
        body = await request.form()
    except Exception:
        body = {}
    await st._post_hook()

    if not st._window_check:
        return JSONResponse([{"flag": "0", "msg": "不在选课时间内", "xkgwcb_id": ""}])

    ids = body.get("ids", "")
    id_list = [x.strip() for x in ids.split(",") if x.strip()]

    results = []
    for xkgwcb_id in id_list:
        # 检查容量
        if st.scenario.course_enrolled >= st.scenario.course_capacity:
            results.append({
                "flag": "0",
                "msg": "课程容量已满",
                "xkgwcb_id": xkgwcb_id,
            })
        else:
            st.scenario.course_enrolled += 1
            results.append({
                "flag": "1",
                "msg": "选课成功",
                "xkgwcb_id": xkgwcb_id,
            })
            log.info(f"提交成功: {xkgwcb_id}")

    return JSONResponse(results)


# ================================================================
# 登录前置检查（参考 SCAU-course-tool）
# ================================================================

async def handle_identity_check(request: Request):
    """POST /xtgl/yhgl_cxXxqrCheck.html — 身份确认检查"""
    st = get_state()
    return JSONResponse(False)


async def handle_login_failure_check(request: Request):
    """POST /xtgl/login_cxDlxgxx.html — 登录失败次数检查"""
    st = get_state()
    if st.scenario.login_locked:
        return PlainTextResponse('"3_9999999999999"')  # 锁定状态
    return PlainTextResponse('"0_0"')


async def handle_clear_login_failure(request: Request):
    """POST /xtgl/login_cxUpdateDlsbcs.html — 清零失败次数"""
    return PlainTextResponse('"操作成功"')


async def handle_logout_prev(request: Request):
    """POST /xtgl/login_logoutAccount.html — 清理已有登录"""
    return PlainTextResponse("")


# ================================================================
# 已选课程 + 退选
# ================================================================

async def handle_query_selected(request: Request):
    """POST /xsxk/zzxkyzb_cxZzxkYzbChoosed.html?doType=query — 查询已选课程"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)
    await st._post_hook()
    return JSONResponse({"items": list(st.selected)})


async def handle_drop_course(request: Request):
    """POST /xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?doType=del — 退选课程"""
    st = get_state()
    hook = await st._pre_hook()
    if hook:
        return JSONResponse(hook, status_code=503)

    try:
        body = await request.form()
    except Exception:
        body = {}
    await st._post_hook()

    if not st._window_check:
        return PlainTextResponse('"0"')

    jxb_ids = body.get("jxb_ids", "")
    before = len(st.selected)
    st.selected = [c for c in st.selected if c.get("do_jxb_id") != jxb_ids
                   and c.get("jxb_id") != jxb_ids]
    if len(st.selected) < before:
        log.info(f"退选成功: {jxb_ids[:30]}...")
        return PlainTextResponse('"1"')
    return PlainTextResponse('"0"')
