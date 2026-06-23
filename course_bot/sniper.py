"""
抢课引擎 —— 登录 → 保活等待 → 批量提交 → 校验 → 持续重试。

自包含完整流程，无需预绑定/缓存文件。提前启动，静默等待窗口，
窗口开启瞬间批量提交，从服务器已选列表校验结果，失败课程持续轮询。
"""

import asyncio, logging, random, sys, time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from course_bot.config import Config
from course_bot.client import EP

log = logging.getLogger("course_bot")

# ── 响应分类 ──
# 五类结果，全覆盖：
#   success — 课程已选中（新选上 / 之前已选 / 已修过）
#   full    — 终态：容量已满，无余量
#   stop    — 终态：业务规则拒绝（冲突/限制/培养方案不符等）
#   window  — 窗口未开，需等待
#   retry   — 临时故障（网络/服务器繁忙/未知），应重试
#   auth    — 会话过期，需重新登录

SUCCESS_KW = ["成功"]          # flag=1 优先，"成功"做兜底
ALREADY_KW = ["已选", "已经选", "已修", "重复"]   # 已选过 = 也是成功
FULL_KW = ["满", "容量", "名额", "余量不足", "没有足够的余量"]
STOP_KW = ["冲突", "限制", "培养方案", "先修",
           "性别", "年级", "专业", "不符合", "不允许", "不能选", "不可选"]
WINDOW_KW = ["未开始", "不在选课时间", "选课时间未到", "暂未开始"]
RETRY_KW = ["系统繁忙", "稍后", "重试", "频率", "频繁"]


def classify(flag: str, msg: str, http_status: int = 200) -> str:
    # 1. 显式成功信号
    if str(flag) in ("1", "true", "True"):
        return "success"

    # 2. 关键词匹配（优先级：success > already > full > stop > window > retry）
    if any(k in msg for k in SUCCESS_KW):
        return "success"
    if any(k in msg for k in ALREADY_KW):
        return "success"          # 已选/已修/重复 → 课程已在，等同成功
    if any(k in msg for k in FULL_KW):
        return "full"
    if any(k in msg for k in STOP_KW):
        return "stop"
    if any(k in msg for k in WINDOW_KW):
        return "window"
    if any(k in msg for k in RETRY_KW):
        return "retry"

    # 3. 会话过期（HTTP 状态码 或 响应内容）
    if http_status in (401, 403):
        return "auth"
    if "登录" in msg and ("过期" in msg or "失效" in msg or "超时" in msg):
        return "auth"

    # 4. 默认：flag=0 有消息 → 可能是业务拒绝（保守：retry 让用户看到消息）
    if str(flag) == "0" and msg:
        return "retry"

    # 5. 完全未知 → retry
    return "retry"


def _better(a: str, b: str) -> bool:
    """a 是否比 b 更优？success > retry > window > full/stop/auth"""
    order = {"success": 5, "retry": 3, "window": 2, "full": 1, "stop": 1, "auth": 1}
    return order.get(a, 0) > order.get(b, 0)


class Engine:
    """抢课引擎：单次启动，全流程自动化"""

    def __init__(self, config: Config):
        self.config = config
        self.base = config.base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._csrf: str = ""
        self.page_params: dict = {}
        self.courses: list[dict] = []          # 目标课程 [{jxbbh, xkgwcb_id, kcmc, kklxdm}]
        self.results: dict[str, dict] = {}     # xkgwcb_id → {type, msg}
        self._stop = asyncio.Event()
        self._window_dt: datetime | None = None
        self._server_offset: float = 0.0

    # ═════════════════════════════════════════════════════════════
    # 登录
    # ═════════════════════════════════════════════════════════════

    async def _login(self):
        log.info(f"登录: {self.base}")
        c = self.config

        # 1. 获取 CSRF
        resp = await self._get(EP.LOGIN_PAGE, gnmkdm=False)
        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_el = soup.select_one("#csrftoken")
        if not csrf_el:
            raise RuntimeError("[E1001] 未找到 CSRF 令牌")
        self._csrf = csrf_el.get("value", "")

        # 2. RSA 公钥
        ts = int(time.time() * 1000)
        key_resp = await self._get(f"{EP.PUBLIC_KEY}?time={ts}&_={ts}", gnmkdm=False)
        key_data = key_resp.json()
        modulus = key_data.get("modulus", "")
        exponent = key_data.get("exponent", "")
        if not modulus or not exponent:
            raise RuntimeError("[E1002] RSA 公钥获取失败")

        # 3. RSA 加密
        from .PyRsa import RsaKey, Base64
        b64 = Base64()
        rsa = RsaKey()
        rsa.set_public(b64.b64tohex(modulus), b64.b64tohex(exponent))
        mm = b64.hex2b64(rsa.rsa_encrypt(c.password))

        # 4. 前置检查（保留浏览器登录）
        preserve = getattr(c, 'preserve_browser_session', True)
        if not preserve:
            try:
                await self._post(EP.LOGIN_LOGOUT_PREV, data={}, gnmkdm=False)
            except Exception:
                pass

        # 5. 登录提交
        login_resp = await self._post(
            f"{EP.LOGIN_PAGE}?time={ts}",
            data={"csrftoken": self._csrf, "yhm": c.student_id,
                  "mm": mm, "language": "zh_CN"},
            gnmkdm=False,
        )

        if f'value="{c.student_id}"' not in login_resp.text and 'id="tips"' in login_resp.text:
            tip = BeautifulSoup(login_resp.text, "html.parser").select_one("#tips")
            raise RuntimeError(f"[E1003] 登录失败: {tip.text.strip() if tip else '未知'}")

        self._calibrate_time(login_resp)
        log.info("登录成功")
        return login_resp

    def _calibrate_time(self, resp):
        from email.utils import parsedate_to_datetime
        date_str = resp.headers.get("Date", "")
        if date_str:
            try:
                server_dt = parsedate_to_datetime(date_str)
                self._server_offset = (server_dt - datetime.now(timezone.utc)).total_seconds()
            except Exception:
                pass

    def _server_now(self) -> datetime:
        from datetime import timezone
        return datetime.now() + timedelta(seconds=self._server_offset)

    # ═════════════════════════════════════════════════════════════
    # 页面参数 + 课程匹配
    # ═════════════════════════════════════════════════════════════

    async def _fetch_params(self):
        c = self.config
        url = (f"{EP.XK_INDEX}?gnmkdm={c.gnmkdm}&layout=default&su={c.student_id}")
        resp = await self._get(url, gnmkdm=False)
        soup = BeautifulSoup(resp.text, "html.parser")

        params = {}
        for el in soup.find_all("input"):
            key = el.get("id") or el.get("name") or ""
            if key:
                val = el.get("value", "")
                if val or key not in params:
                    params[key] = val

        # 提取所有 tab，查询 Display 获取窗口时间
        tabs = {}
        for tab_el in soup.select("a[id^='tab_kklx_']"):
            import re
            onclick = tab_el.get("onclick", "")
            m = re.search(
                r"queryCourse\(this,'(\w+)','([^']+)','([^']+)','([^']+)','([^']+)'\)",
                onclick)
            if m:
                tabs[m.group(1)] = {"kklxdm": m.group(1), "xkkz_id": m.group(2),
                                    "njdm_id": m.group(3), "zyh_id": m.group(4),
                                    "xkkz_xh": m.group(5)}
        if not tabs:
            default_kklxdm = params.get("firstKklxdm", "01")
            tabs[default_kklxdm] = {"kklxdm": default_kklxdm,
                                    "xkkz_id": params.get("firstXkkzId", ""),
                                    "njdm_id": params.get("firstNjdmId", params.get("njdm_id", "")),
                                    "zyh_id": params.get("firstZyhId", params.get("zyh_id", "")),
                                    "xkkz_xh": params.get("firstXkkzXh", "")}

        # 查询第一个 tab 的 Display 获取窗口时间
        first = next(iter(tabs.values()), {})
        try:
            data = {"xkkz_id": first.get("xkkz_id", ""),
                    "kklxdm": first.get("kklxdm", "01"),
                    "njdm_id": first.get("njdm_id", ""),
                    "zyh_id": first.get("zyh_id", ""),
                    "xszxzt": params.get("xszxzt", "1"),
                    "kspage": "0", "jspage": "0"}
            resp = await self._post(EP.XK_DISPLAY, data=data)
            soup2 = BeautifulSoup(resp.text, "html.parser")
            for fid in ("xkkssj", "xkjssj"):
                el = soup2.find(attrs={"id": fid}) or soup2.find(attrs={"name": fid})
                if el and el.get("value"):
                    params[fid] = el.get("value")
        except Exception:
            pass

        self.page_params = params
        # 服务器窗口时间（仅作参考显示）
        server_open = params.get("xkkssj", "")
        if server_open:
            log.info(f"服务器窗口={server_open}")
        # 使用 config 窗口时间（可通过 --window 覆盖）
        try:
            self._window_dt = c.window_open_dt
        except Exception:
            self._window_dt = datetime.now()
        log.info(f"使用窗口={self._window_dt.strftime('%m-%d %H:%M:%S')}"
                 f" 学年={params.get('xkxnm')} 学期={params.get('xkxqm')}")

    async def _match_courses(self):
        """查询选课意向/购物车，匹配目标课程"""
        c = self.config

        # 查询选课意向
        items = []
        try:
            data = {"xkxnm": self.page_params.get("xkxnm", ""),
                    "xkxqm": self.page_params.get("xkxqm", ""),
                    "xklc": self.page_params.get("xklc", "1"),
                    "xszxzt": self.page_params.get("xszxzt", "1"),
                    "kspage": "0", "jspage": "50"}
            resp = await self._post(EP.XK_CHOOSED + "?doType=query", data=data, gnmkdm=False)
            items = resp.json().get("items", [])
        except Exception:
            pass

        # 购物车备选
        if not items:
            try:
                data = {"xkxnm": self.page_params.get("xkxnm", ""),
                        "xkxqm": self.page_params.get("xkxqm", ""),
                        "showCount": "100", "kspage": "0", "jspage": "100",
                        "sidx": "zjsj", "sord": "asc"}
                resp = await self._post(EP.XK_QUERY_CART + "?doType=query", data=data, gnmkdm=False)
                items = resp.json().get("items", [])
            except Exception:
                pass

        log.info(f"可用意向共 {len(items)} 门")

        for course_cfg in c.target_courses:
            jxbbh = course_cfg["jxbbh"]
            kklxdm = course_cfg.get("kklxdm", "01")
            matched = None
            for item in items:
                if item.get("jxbmc") == jxbbh:
                    matched = item
                    break
            if matched:
                info = {
                    "jxbbh": jxbbh,
                    "xkgwcb_id": matched.get("xkgwcb_id", ""),
                    "kcmc": matched.get("kcmc", ""),
                    "kklxdm": kklxdm,
                }
                self.courses.append(info)
                log.info(f"  [OK] {jxbbh} → {info['kcmc']}")
            else:
                log.warning(f"  [未找到] {jxbbh} — 请先在浏览器添加到选课意向")

    # ═════════════════════════════════════════════════════════════
    # 等待窗口 + 保活
    # ═════════════════════════════════════════════════════════════

    async def _wait_window_until(self, target_dt: datetime):
        """等待直到指定时间，期间保活 session 和 NAT"""
        remaining = (target_dt - datetime.now()).total_seconds()
        if remaining <= 0:
            return

        log.info(f"等待到 {target_dt.strftime('%H:%M:%S')}（{self._fmt_countdown(remaining)}）")
        last_keepalive = time.monotonic()
        keepalive_interval = getattr(self.config, 'session_keepalive', 120)
        nat_sec = getattr(self.config, 'nat_refresh_seconds', 5.0)

        while not self._stop.is_set():
            remaining = (target_dt - datetime.now()).total_seconds()
            if remaining <= 0:
                break

            sys.stdout.write(f"\r  距目标时间 {self._fmt_countdown(remaining)}  ")
            sys.stdout.flush()

            if 0 < remaining <= nat_sec:
                try:
                    await self._client.head(
                        urljoin(self.base, EP.INDEX_PAGE), timeout=3.0)
                except Exception:
                    pass
                nat_sec = -1

            elapsed = time.monotonic() - last_keepalive
            if keepalive_interval > 0 and elapsed > keepalive_interval:
                try:
                    resp = await self._client.get(
                        urljoin(self.base, EP.INDEX_PAGE), timeout=5.0)
                    if resp.status_code >= 400 and remaining > 60:
                        log.warning(f"保活异常 (HTTP {resp.status_code})，尝试重登...")
                        try:
                            await self._login()
                            await self._fetch_params()
                        except Exception as e:
                            log.error(f"重登失败: {e}")
                except Exception:
                    pass
                last_keepalive = time.monotonic()

            await asyncio.sleep(0.2)

        sys.stdout.write("\r  时间到！" + " " * 30 + "\n")
        sys.stdout.flush()

    @staticmethod
    def _fmt_countdown(seconds):
        h, m, s = int(seconds // 3600), int((seconds % 3600) // 60), int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ═════════════════════════════════════════════════════════════
    # 提交
    # ═════════════════════════════════════════════════════════════

    def _ajax_headers(self) -> dict:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base,
            "Referer": f"{self.base}{EP.XK_INDEX}?gnmkdm={self.config.gnmkdm}&layout=default",
        }

    async def _burst_submit(self, courses: list = None):
        """爆发提交：并发 N 个批量请求，合并每门课最佳结果。"""
        if courses is None:
            courses = self.courses
        ids = [c["xkgwcb_id"] for c in courses]
        url = f"{urljoin(self.base, EP.XK_SUBMIT_CART)}?gnmkdm={self.config.gnmkdm}"
        timeout = self.config.snipe_timeout
        headers = self._ajax_headers()

        async def _one_shot():
            try:
                resp = await self._client.post(url, data={"ids": ",".join(ids)},
                                               headers=headers, timeout=timeout)
                sc = resp.status_code
                if sc in (401, 403) or "login_slogin" in (resp.text or "")[:200]:
                    return ("auth", sc, [{"flag": "0", "msg": "会话已过期"}])
                return ("ok", sc, resp.json())
            except Exception as e:
                return ("err", 0, str(e)[:80])

        burst_count = getattr(self.config, 'burst_count', 3)
        tasks = [_one_shot() for _ in range(burst_count)]
        all_shots = await asyncio.gather(*tasks)

        # 合并：每门课取最佳结果
        for course in courses:
            rid = course["xkgwcb_id"]
            best_type, best_msg = "retry", "无响应"
            for status, sc, data in all_shots:
                if status == "err":
                    if best_type == "retry":
                        best_msg = data
                    continue
                items = data if isinstance(data, list) else [data]
                idx = ids.index(rid) if rid in ids else -1
                if 0 <= idx < len(items):
                    item = items[idx]
                elif isinstance(data, dict):
                    item = data
                else:
                    item = {"flag": "0", "msg": "响应缺项"}

                flag = str(item.get("flag", ""))
                msg = item.get("msg", "")
                cls = classify(flag, msg, sc if status == "ok" else 401)
                if _better(cls, best_type):
                    best_type, best_msg = cls, msg

            self.results[rid] = {"type": best_type, "msg": best_msg}
            label = {"success": "[OK]", "full": "[FULL]", "stop": "[STOP]",
                     "auth": "[AUTH]", "window": "[WINDOW]"}.get(best_type, "")
            lvl = {"success": "info", "full": "warning", "stop": "warning",
                   "auth": "error"}.get(best_type, "debug")
            detail = best_msg if best_msg else f"(flag/type={best_type})"
            getattr(log, lvl)(f"  {label} {course['jxbbh']} {course['kcmc']}: {detail}")

        # auth → 重新登录
        if any(self.results.get(c["xkgwcb_id"], {}).get("type") == "auth"
               for c in courses):
            await self._relogin()

    async def _batch_submit(self, courses: list = None):
        """单次批量提交（重试阶段使用，比爆发模式轻量）"""
        if courses is None:
            courses = self.courses
        retry = [c for c in courses
                 if self.results.get(c["xkgwcb_id"], {}).get("type") not in ("success", "full", "stop")]
        if not retry:
            return

        ids = [c["xkgwcb_id"] for c in retry]
        url = f"{urljoin(self.base, EP.XK_SUBMIT_CART)}?gnmkdm={self.config.gnmkdm}"
        timeout = self.config.snipe_timeout

        sc = 0
        try:
            resp = await self._client.post(url, data={"ids": ",".join(ids)},
                                           headers=self._ajax_headers(), timeout=timeout)
            sc = resp.status_code
            if sc in (401, 403) or "login_slogin" in (resp.text or "")[:200]:
                sc = 401
                data = [{"flag": "0", "msg": "会话已过期"}]
            else:
                data = resp.json()
        except httpx.TimeoutException:
            for c in retry:
                self.results[c["xkgwcb_id"]] = {"type": "retry", "msg": "请求超时"}
            return
        except Exception as e:
            for c in retry:
                self.results[c["xkgwcb_id"]] = {"type": "retry", "msg": str(e)[:80]}
            return

        items = data if isinstance(data, list) else [data]
        for i, c in enumerate(retry):
            rid = c["xkgwcb_id"]
            if i < len(items):
                item = items[i]
            elif isinstance(data, dict):
                item = data
            else:
                self.results[rid] = {"type": "retry", "msg": "响应缺项"}
                continue

            flag = str(item.get("flag", ""))
            msg = item.get("msg", "")
            cls = classify(flag, msg, sc)
            self.results[rid] = {"type": cls, "msg": msg}
            label = {"success": "[OK]", "full": "[FULL]", "stop": "[STOP]",
                     "auth": "[AUTH]"}.get(cls, "")
            lvl = {"success": "info", "full": "warning", "stop": "warning",
                   "auth": "error"}.get(cls, "debug")
            detail = msg if msg else f"(flag={flag}, type={cls})"
            getattr(log, lvl)(f"  {label} {c['jxbbh']} {c['kcmc']}: {detail}")

        if any(self.results.get(c["xkgwcb_id"], {}).get("type") == "auth"
               for c in retry):
            await self._relogin()

    # ═════════════════════════════════════════════════════════════
    # 验证：查询已选课程列表
    # ═════════════════════════════════════════════════════════════

    async def _verify(self):
        """查询已选课程列表，确认选课是否真正落库"""
        pp = self.page_params
        data = {"xkxnm": pp.get("xkxnm", ""), "xkxqm": pp.get("xkxqm", ""),
                "xklc": pp.get("xklc", "1"), "xszxzt": pp.get("xszxzt", "1"),
                "kspage": "0", "jspage": "50"}

        # 购物车查询优先（更可靠），CHOOSED 兜底
        items = []
        for endpoint in (EP.XK_QUERY_CART, EP.XK_CHOOSED):
            try:
                resp = await self._post(endpoint + "?doType=query", data=data, gnmkdm=False)
                text = resp.text[:200]
                if "login" in text.lower() or "csrftoken" in text:
                    log.debug(f"校验查询被重定向到登录页 ({endpoint.split('/')[-1][:20]})")
                    continue
                items = resp.json().get("items", [])
                if items:
                    break
            except Exception as e:
                log.debug(f"校验查询失败 ({endpoint.split('/')[-1][:20]}): {e}")

        if not items:
            log.debug("校验查询无结果（可能需刷新页面参数）")
            return

        selected_jxbmc = {item.get("jxbmc", "") for item in items}
        log.debug(f"校验: 已选/购物车共 {len(selected_jxbmc)} 门")

        for c in self.courses:
            rid = c["xkgwcb_id"]
            r = self.results.get(rid, {})
            if r.get("type") != "success":
                continue
            if c["jxbbh"] in selected_jxbmc:
                r["verified"] = True
                log.info(f"  [确认] {c['jxbbh']} {c['kcmc']} 已入选课列表")
            else:
                r["type"] = "retry"
                r["msg"] = "提交返回成功但未出现在已选列表，重新提交"
                log.warning(f"  [校验失败] {c['jxbbh']}: {r['msg']}")

    # ═════════════════════════════════════════════════════════════
    # 主循环
    # ═════════════════════════════════════════════════════════════

    def _ensure_client(self):
        if self._client is not None:
            return
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self._client = httpx.AsyncClient(
            headers=headers, cookies={},
            follow_redirects=True,
            timeout=httpx.Timeout(self.config.request_timeout),
        )
        self._client.base_url = self.base

    async def run(self, skip_wait: bool = False):
        log.info("=" * 50)
        log.info("华农选课引擎" + (" (跳过窗口等待)" if skip_wait else ""))
        log.info("=" * 50)

        self._ensure_client()

        try:
            # 1. 登录 + 获取参数 + 匹配课程
            await self._login()
            await self._fetch_params()
            await self._match_courses()

            if not self.courses:
                log.error("无匹配课程，退出")
                return

            log.info(f"目标课程: {', '.join(c['jxbbh'] + ' ' + c['kcmc'] for c in self.courses)}")

            # 2. 等待窗口（提前 lead_time 秒）
            lead_time = getattr(self.config, 'lead_time', 1.0)
            if not skip_wait:
                target = self._window_dt - timedelta(seconds=lead_time)
                await self._wait_window_until(target)
            else:
                log.info("跳过窗口等待，立即尝试提交...")

            if self._stop.is_set():
                return

            # 3. 连接预热
            try:
                await self._client.head(
                    urljoin(self.base, EP.INDEX_PAGE), timeout=3.0)
            except Exception:
                pass

            # 4. 爆发提交（3 并发冗余请求，每个请求包含全部课程 ID）
            log.info(f"爆发提交（{len(self.courses)} 门课程, {getattr(self.config, 'burst_count', 3)} 并发冗余）...")
            await self._burst_submit(self.courses)

            # 5. 窗口未开 → 等待到真正窗口时间再爆发（仅当目标时间仍在未来）
            pending = [c for c in self.courses
                       if self.results.get(c["xkgwcb_id"], {}).get("type") in ("retry", "window", None)]
            if (pending and self._window_dt and datetime.now() < self._window_dt and
                all(self.results.get(c["xkgwcb_id"], {}).get("type") == "window"
                    for c in pending)):
                log.info("提前启动太早（窗口未开），等待窗口...")
                await self._wait_window_until(self._window_dt)
                log.info("窗口到，再次爆发提交...")
                await self._burst_submit(self.courses)

            # 6. 校验 + 持续重试
            await asyncio.sleep(0.15)
            await self._verify()
            await self._retry_loop()

            # 7. 最终报告
            await self._verify()
            self._print_report()

        except KeyboardInterrupt:
            log.info("用户中断")
        except Exception as e:
            log.error(f"运行异常: {e}", exc_info=True)
        finally:
            await self._client.aclose()

    async def _relogin(self):
        """重新登录并刷新页面参数"""
        log.info("尝试重新登录...")
        try:
            await self._login()
            await self._fetch_params()
            # 清除 auth 状态
            for c in self.courses:
                if self.results.get(c["xkgwcb_id"], {}).get("type") == "auth":
                    self.results[c["xkgwcb_id"]] = {"type": "retry", "msg": "重新登录后重试"}
            log.info("重新登录成功")
        except Exception as e:
            log.error(f"重新登录失败: {e}")

    async def _retry_loop(self, courses: list = None):
        """持续重试失败的课程，直到全部完成或用户中断"""
        if courses is None:
            courses = self.courses
        round_num = 1
        while not self._stop.is_set():
            pending = [c for c in courses
                       if self.results.get(c["xkgwcb_id"], {}).get("type") in ("retry", "window", "auth", None)]
            if not pending:
                break

            # 会话过期 → 重新登录
            has_auth = any(
                self.results.get(c["xkgwcb_id"], {}).get("type") == "auth"
                for c in pending)
            if has_auth:
                await self._relogin()
                continue

            # 窗口未开且目标时间在未来 → 等待；已过时间 → 正常重试
            all_window = pending and all(
                self.results.get(c["xkgwcb_id"], {}).get("type") == "window"
                for c in pending)
            if all_window and self._window_dt and datetime.now() < self._window_dt:
                log.info("窗口尚未开启，转入等待...")
                await self._wait_window_until(self._window_dt)
                for c in pending:
                    self.results[c["xkgwcb_id"]] = {"type": "retry", "msg": ""}
                await self._burst_submit(courses)
                continue

            if round_num % 20 == 1:
                log.info(f"重试轮次 {round_num}: {len(pending)} 门待提交 "
                         f"({', '.join(c['jxbbh'] for c in pending)})")

            # 前 10 轮极速（无延迟），之后恢复间隔防止频率封控
            if round_num > 10:
                await asyncio.sleep(self.config.submit_retry_delay)
            await self._batch_submit(courses)

            if round_num % 30 == 0:
                await self._verify()

            round_num += 1

    def _print_report(self):
        log.info("=" * 50)
        log.info("最终结果")
        log.info("=" * 50)
        success, full, stop, retry = [], [], [], []
        for c in self.courses:
            r = self.results.get(c["xkgwcb_id"], {})
            t = r.get("type", "?")
            verified = r.get("verified", False)
            v = " ✓" if verified else ""
            if t == "success":
                success.append(c["jxbbh"] + v)
                log.info(f"  [OK] {c['jxbbh']} {c['kcmc']}: {r.get('msg','')}{v}")
            elif t == "full":
                full.append(c["jxbbh"])
                log.info(f"  [FULL] {c['jxbbh']} {c['kcmc']}: {r.get('msg','')}")
            elif t == "stop":
                stop.append(c["jxbbh"])
                log.info(f"  [STOP] {c['jxbbh']} {c['kcmc']}: {r.get('msg','')}")
            else:
                retry.append(c["jxbbh"])
                log.info(f"  [未完成] {c['jxbbh']} {c['kcmc']}: {r.get('msg','')}")
        log.info(f"选课成功: {len(success)}{' — ' + ', '.join(success) if success else ''}")
        if full:
            log.info(f"满员: {len(full)} — {', '.join(full)}")
        if stop:
            log.info(f"业务拒绝: {len(stop)} — {', '.join(stop)}")
        if retry:
            log.info(f"最终未完成: {len(retry)} — {', '.join(retry)}")

    def stop(self):
        self._stop.set()

    # ═════════════════════════════════════════════════════════════
    # HTTP 辅助
    # ═════════════════════════════════════════════════════════════

    async def _get(self, path, timeout=None, gnmkdm=True):
        url = urljoin(self.base, path)
        if gnmkdm and "gnmkdm" not in url:
            url += ("&" if "?" in url else "?") + f"gnmkdm={self.config.gnmkdm}"
        return await self._client.get(url, timeout=timeout or self.config.request_timeout)

    async def _post(self, path, data=None, timeout=None, gnmkdm=True):
        url = urljoin(self.base, path)
        if gnmkdm and "gnmkdm" not in url:
            url += ("&" if "?" in url else "?") + f"gnmkdm={self.config.gnmkdm}"
        return await self._client.post(url, data=data or {},
                                        timeout=timeout or self.config.request_timeout)
