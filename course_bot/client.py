"""
通信层 —— httpx 同步/异步 HTTP 客户端，基于真实页面抓包修正。
"""

import re, time, logging
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from course_bot.config import Config, FieldMapping
from course_bot.errors import ErrorCode, BotError, LoginError, ApiError
from .PyRsa import RsaKey, Base64

log = logging.getLogger("course_bot")


# ================================================================
# 端点常量
# ================================================================

class EP:
    """教务系统 API 端点（基于 2026-06-18 CDP 抓包验证）"""
    # 登录
    LOGIN_PAGE     = "/jwglxt/xtgl/login_slogin.html"
    PUBLIC_KEY     = "/jwglxt/xtgl/login_getPublicKey.html"
    INDEX_PAGE     = "/jwglxt/xtgl/index_initMenu.html"

    # 登录前置检查
    LOGIN_CHECK_IDENTITY = "/jwglxt/xtgl/yhgl_cxXxqrCheck.html"       # 身份确认
    LOGIN_CHECK_FAILURE  = "/jwglxt/xtgl/login_cxDlxgxx.html"          # 失败次数
    LOGIN_CLEAR_FAILURE  = "/jwglxt/xtgl/login_cxUpdateDlsbcs.html"    # 清零失败次数
    LOGIN_LOGOUT_PREV    = "/jwglxt/xtgl/login_logoutAccount.html"     # 清理已有登录

    # 选课页面
    XK_INDEX       = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html"
    XK_DISPLAY     = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbDisplay.html"
    XK_PART_DISPLAY = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbPartDisplay.html"

    # 课程数据（页面加载时调用，含 do_jxb_id）
    XK_COURSES      = "/jwglxt/xsxk/zzxkyzb_cxZkcZzxkYzb.html"
    XK_CHOOSED      = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbChoosed.html"
    XK_CHOOSED_DISP = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbChoosedDisplay.html"

    # 选课操作（按 JS 源码还原的正确调用链）
    XK_CHECK_CART   = "/jwglxt/xsxk/zzxkyzb_cxCheckZyZzxkYzbInCart.html"
    XK_CHECK_CT     = "/jwglxt/xsxk/zzxkyzb_cxCtKcZyZzxkYzb.html"      # 时间冲突检查
    XK_ADD_CART     = "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbToCart.html" # 加入购物车(单教学班)
    XK_QUERY_CART   = "/jwglxt/xsxk/zzxkyzb_cxWdgwcZzxkYzb.html"
    XK_SUBMIT_CART  = "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbFromCart.html"  # 购物车提交
    XK_DROP_COURSE  = "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html"      # 退课（doType=del）


# ================================================================
# HTTP 客户端
# ================================================================

class Client:
    """教务系统 HTTP 客户端（同步登录 + 异步提交）"""

    def __init__(self, config: Config, skip_logout_prev: bool = False):
        self.config = config
        self.base = config.base_url.rstrip("/")
        self._client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

        # 是否跳过清理已有登录（保留浏览器 session）
        self.skip_logout_prev = skip_logout_prev

        # 页面运行时参数（每次登录后刷新）
        self._csrf: str | None = None
        self.page_params: dict = {}
        self.tabs: dict[str, dict] = {}          # kklxdm → tab_info
        self._logged_in: bool = False
        self._server_time_offset: float = 0.0    # 服务器-本地时间差（秒）

    # ============================================================
    # 内部辅助
    # ============================================================

    def _url(self, path: str) -> str:
        return urljoin(self.base, path)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                follow_redirects=True,
                timeout=httpx.Timeout(self.config.request_timeout),
            )
        return self._client

    def _post(self, path: str, data: dict = None, timeout: float = None,
              gnmkdm: bool = True) -> httpx.Response:
        """同步 POST（自动拼接 gnkmdm）"""
        url = self._url(path)
        if gnmkdm and "gnmkdm" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}gnmkdm={self.config.gnmkdm}"
        t = timeout or self.config.request_timeout
        return self.client.post(url, data=data or {}, timeout=t)

    def _get(self, path: str, timeout: float = None,
             gnmkdm: bool = True) -> httpx.Response:
        url = self._url(path)
        if gnmkdm and "gnmkdm" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}gnmkdm={self.config.gnmkdm}"
        t = timeout or self.config.request_timeout
        return self.client.get(url, timeout=t)

    # ============================================================
    # 登录
    # ============================================================

    def login(self) -> bool:
        """登录教务系统，成功后自动获取选课页面参数"""
        log.info(f"登录: {self.base}")

        # Step 1: 获取 CSRF
        resp = self._get(EP.LOGIN_PAGE, gnmkdm=False)
        resp.encoding = "utf-8" if resp.encoding is None else resp.encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.select_one("#csrftoken")
        if not csrf_input:
            raise LoginError(ErrorCode.CSRF_NOT_FOUND)
        self._csrf = csrf_input.get("value", "")
        log.debug(f"CSRF: {self._csrf[:20]}...")

        # Step 1.5: 身份确认检查（参考 SCAU-course-tool）
        self._check_identity()

        # Step 2: 获取 RSA 公钥
        ts = int(time.time() * 1000)
        key_resp = self._get(f"{EP.PUBLIC_KEY}?time={ts}&_={ts}", gnmkdm=False)
        key_data = key_resp.json()
        modulus = key_data.get("modulus", "")
        exponent = key_data.get("exponent", "")
        if not modulus or not exponent:
            raise LoginError(ErrorCode.RSA_KEY_FAILED,
                             f"服务器返回: {key_data}")

        # Step 3: RSA 加密密码
        b64 = Base64()
        rsa = RsaKey()
        rsa.set_public(b64.b64tohex(modulus), b64.b64tohex(exponent))
        encrypted = rsa.rsa_encrypt(self.config.password)
        mm = b64.hex2b64(encrypted)

        # Step 3.5: 登录失败次数检查 + 清理已有会话（参考 SCAU-course-tool）
        hidden = {el.get("id") or el.get("name") or "": el.get("value", "")
                  for el in soup.find_all("input")}
        self._check_login_failure(hidden)
        if not self.skip_logout_prev:
            self._clear_previous_session()

        # Step 4: 提交登录
        login_resp = self._post(
            f"{EP.LOGIN_PAGE}?time={ts}",
            data={
                "csrftoken": self._csrf,
                "yhm": self.config.student_id,
                "mm": mm,
                "language": "zh_CN",
            },
            gnmkdm=False,
        )
        login_resp.encoding = "utf-8" if login_resp.encoding is None else login_resp.encoding

        if not self._check_logged_in(login_resp.text):
            soup = BeautifulSoup(login_resp.text, "html.parser")
            tips = soup.select_one("#tips")
            err_msg = tips.text.strip() if tips else "未知"
            raise LoginError(ErrorCode.LOGIN_FAILED, err_msg)

        self._logged_in = True
        self._calibrate_time(login_resp)
        log.info("登录成功"
                 f"{f' (服务器时间偏移 {self._server_time_offset:+.1f}s)' if abs(self._server_time_offset) > 1 else ''}")
        return True

    def _calibrate_time(self, resp: httpx.Response):
        """从 HTTP Date 响应头校准服务器时间"""
        date_str = resp.headers.get("Date", "")
        if date_str:
            try:
                server_dt = parsedate_to_datetime(date_str)
                local_dt = datetime.now(timezone.utc)
                self._server_time_offset = (server_dt - local_dt).total_seconds()
            except Exception:
                self._server_time_offset = 0.0

    def server_now(self) -> datetime:
        """返回校准后的服务器当前时间"""
        return datetime.now() + timedelta(seconds=self._server_time_offset)

    def _check_logged_in(self, html: str) -> bool:
        # 成功后页面会包含已登录学号
        if f'value="{self.config.student_id}"' in html:
            return True
        # 严格判断：有 #tips 说明登录失败
        if 'id="tips"' in html:
            return False
        # 无 tips 且不在登录页 → 已登录
        return "login_slogin" not in html and "login" not in html.lower()

    # ============================================================
    # 登录前置检查（参考 SCAU-course-tool v2.5）
    # ============================================================

    def _check_identity(self):
        """身份信息确认检查 —— 触发时中断，当前不支持该分支"""
        try:
            resp = self._post(EP.LOGIN_CHECK_IDENTITY,
                            data={"yhm": self.config.student_id}, gnmkdm=False)
            if resp.text.strip() == "true":
                raise LoginError(ErrorCode.IDENTITY_CHECK,
                                 "账号触发身份信息确认，脚本暂不支持，请在浏览器中完成确认后重试")
        except LoginError:
            raise
        except Exception:
            pass  # 接口可能不存在或报错，不阻塞登录

    def _check_login_failure(self, hidden: dict):
        """检查登录失败次数是否达到阈值，达到则等待或清零"""
        try:
            resp = self._post(EP.LOGIN_CHECK_FAILURE,
                            data={"yhm": self.config.student_id}, gnmkdm=False)
            value = resp.text.strip()
        except Exception:
            return

        if value == '"0"' or value == "0":
            raise LoginError(ErrorCode.USER_NOT_EXIST)
        if "_" not in value:
            return

        try:
            count_text, ts_text = value.strip('"').split("_", 1)
            count = int(count_text)
            last_failed = int(ts_text)
            threshold = int(hidden.get("yzcskz", "3") or "3")
            lock_minutes = int(hidden.get("dlsbsdsj", "3") or "3")
        except (ValueError, TypeError):
            return

        if count < threshold:
            return

        # 检查是否仍在锁定期
        locked_until = last_failed + lock_minutes * 60000
        current_ms = int(time.time() * 1000)
        if locked_until > current_ms:
            seconds = max(1, int((locked_until - current_ms) / 1000))
            raise LoginError(ErrorCode.LOGIN_LOCKED,
                             f"登录失败次数达 {count}/{threshold}，请约 {seconds}s 后重试")

        # 锁定已过期，清零失败次数
        self._clear_failure_count()

    def _clear_failure_count(self):
        """清零登录失败次数"""
        try:
            resp = self._post(EP.LOGIN_CLEAR_FAILURE,
                            data={"yhm": self.config.student_id}, gnmkdm=False)
            if resp.text.strip() != '"操作成功"':
                log.warning(f"清零登录失败次数未返回预期: {resp.text[:50]}")
        except Exception as e:
            log.warning(f"清零登录失败次数失败: {e}")

    def _clear_previous_session(self):
        """清理已有账号登录状态（模拟前端流程）"""
        try:
            self._post(EP.LOGIN_LOGOUT_PREV, data={}, gnmkdm=False)
        except Exception:
            pass

    # ============================================================
    # 页面参数提取
    # ============================================================

    def fetch_page_params(self) -> dict:
        """获取选课主页，提取所有隐藏参数和 tab 信息"""
        if not self._logged_in:
            raise BotError(ErrorCode.SESSION_EXPIRED)

        url = (f"{EP.XK_INDEX}?"
               f"gnmkdm={self.config.gnmkdm}&layout=default&"
               f"su={self.config.student_id}")
        resp = self._get(url, gnmkdm=False)
        resp.encoding = "utf-8" if resp.encoding is None else resp.encoding

        soup = BeautifulSoup(resp.text, "html.parser")

        # 提取所有隐藏 input
        params: dict = {}
        for el in soup.find_all("input", attrs={"type": "hidden"}):
            key = el.get("id") or el.get("name") or ""
            if key:
                params[key] = el.get("value", "")

        # 提取可见 input 的值（如 rwlx, xklc 等）
        for el in soup.find_all("input"):
            if el.get("type") == "hidden":
                continue
            key = el.get("id") or el.get("name") or ""
            if key and key not in params:
                val = el.get("value", "")
                if val:
                    params[key] = val

        # 提取 select 选中的值
        for el in soup.find_all("select"):
            key = el.get("id") or el.get("name") or ""
            if key:
                selected = el.select_one("option[selected]")
                if selected:
                    params[key] = selected.get("value", "")

        if not params.get("xkxnm"):
            raise BotError(ErrorCode.PAGE_LOAD_FAILED, "未找到 xkxnm 等核心参数")

        # 提取 tab 信息
        tabs: dict[str, dict] = {}
        for tab_el in soup.select("a[id^='tab_kklx_']"):
            onclick = tab_el.get("onclick", "")
            m = re.search(
                r"queryCourse\(this,'(\w+)','([^']+)','([^']+)','([^']+)','([^']+)'\)",
                onclick)
            if m:
                tabs[m.group(1)] = {
                    "kklxdm":  m.group(1),
                    "xkkz_id": m.group(2),
                    "njdm_id": m.group(3),
                    "zyh_id":  m.group(4),
                    "xkkz_xh": m.group(5),
                }

        if not tabs:
            # fallback: 从页面 first 参数构造
            default_kklxdm = params.get("firstKklxdm", "06")
            tabs[default_kklxdm] = {
                "kklxdm": default_kklxdm,
                "xkkz_id": params.get("firstXkkzId", ""),
                "njdm_id": params.get("firstNjdmId", params.get("njdm_id", "")),
                "zyh_id": params.get("firstZyhId", params.get("zyh_id", "")),
                "xkkz_xh": params.get("firstXkkzXh", ""),
            }

        self.page_params = params
        self.tabs = tabs

        # 获取选课时间窗口
        self._fetch_display_time()

        # 二次时间校准（server_now 字段比 Date 头更精确）
        server_now = params.get("server_now", "") or params.get("currentsj", "")
        if server_now:
            try:
                srv_dt = datetime.strptime(server_now, "%Y-%m-%d %H:%M:%S")
                self._server_time_offset = (srv_dt - datetime.now()).total_seconds()
            except ValueError:
                pass

        log.info(f"页面参数: {len(params)} 个, tabs: {list(tabs.keys())} "
                 f"({', '.join(t.get('id','')[:20] for t in soup.select('a[id^=tab_kklx_]'))})")
        log.info(f"学年={params.get('xkxnm')} 学期={params.get('xkxqm')} "
                 f"轮次={params.get('xklc')} "
                 f"窗口={params.get('xkkssj','?')}~{params.get('xkjssj','?')}"
                 f"{f' | 偏移={self._server_time_offset:+.1f}s' if abs(self._server_time_offset) > 0.5 else ''}")
        return params

    def _fetch_display_time(self):
        """从 Display 页面获取选课时间窗口"""
        first = next(iter(self.tabs.values()), {})
        try:
            data = {
                "xkkz_id": first.get("xkkz_id", ""),
                "kklxdm":  first.get("kklxdm", "06"),
                "njdm_id": first.get("njdm_id", ""),
                "zyh_id":  first.get("zyh_id", ""),
                "xszxzt":  self.page_params.get("xszxzt", "1"),
                "kspage":  "0",
                "jspage":  "0",
            }
            resp = self._post(EP.XK_DISPLAY, data=data)
            resp.encoding = "utf-8" if resp.encoding is None else resp.encoding
            soup = BeautifulSoup(resp.text, "html.parser")
            for fid in ("xkkssj", "xkjssj"):
                el = soup.find(attrs={"id": fid}) or soup.find(attrs={"name": fid})
                if el and el.get("value"):
                    self.page_params[fid] = el.get("value")
        except Exception:
            log.debug("Display 页面获取失败，使用本地时间", exc_info=True)

    def refresh_session(self) -> bool:
        """刷新会话（等待期间保活，HEAD 轻量请求）"""
        try:
            url = self._url(EP.INDEX_PAGE)
            resp = self.client.head(url, timeout=self.config.request_timeout)
            return resp.status_code < 400
        except Exception:
            return False

    # ============================================================
    # 课程发现
    # ============================================================

    def find_target_course(self, jxbbh: str, kklxdm: str) -> dict | None:
        """在 PartDisplay 中匹配课程，使用 FieldMapping 提取字段"""
        tab = self.tabs.get(kklxdm)
        if not tab:
            tab = {
                "kklxdm": kklxdm,
                "xkkz_id": self.page_params.get("firstXkkzId", ""),
                "njdm_id": self.page_params.get("njdm_id", ""),
                "zyh_id":  self.page_params.get("zyh_id", ""),
                "xkkz_xh": self.page_params.get("firstXkkzXh", ""),
            }

        pp = self.page_params
        data = {
            "rwlx":    pp.get("rwlx", "1"),
            "xklc":    pp.get("xklc", "1"),
            "xkly":    pp.get("xkly", "0"),
            "bklx_id": pp.get("bklx_id", ""),
            "sfkkjyxdxnxq": pp.get("sfkkjyxdxnxq", "0"),
            "kzkcgs":  pp.get("kzkcgs", "0"),
            "xqh_id":  pp.get("xqh_id", "3"),
            "jg_id":   pp.get("jg_id_1", pp.get("jg_id", "14")),
            "njdm_id_1": pp.get("njdm_id", ""),
            "zyh_id_1": pp.get("zyh_id", ""),
            "gnjkxdnj": pp.get("gnjkxdnj", "0"),
            "zyh_id":  tab.get("zyh_id", pp.get("zyh_id", "")),
            "zyfx_id": pp.get("zyfx_id", "wfx"),
            "njdm_id": tab.get("njdm_id", pp.get("njdm_id", "")),
            "bh_id":   pp.get("bh_id", ""),
            "bjgkczxbbjwcx": pp.get("bjgkczxbbjwcx", "0"),
            "xbm":     pp.get("xbm", "1"),
            "xslbdm":  pp.get("xslbdm", "1"),
            "mzm":     pp.get("mzm", "01"),
            "xz":      pp.get("xz", "4"),
            "ccdm":    pp.get("ccdm", "1"),
            "xsbj":    pp.get("xsbj", "0"),
            "sfkknj":  pp.get("sfkknj", "0"),
            "sfkkzy":  pp.get("sfkkzy", "0"),
            "kzybkxy": pp.get("kzybkxy", "0"),
            "sfznkx":  pp.get("sfznkx", "0"),
            "zdkxms":  pp.get("zdkxms", "0"),
            "sfkxq":   pp.get("sfkxq", "1"),
            "sfkcfx":  pp.get("sfkcfx", "0"),
            "kkbk":    pp.get("kkbk", "0"),
            "kkbkdj":  pp.get("kkbkdj", "0"),
            "bklbkcj": pp.get("bklbkcj", "0"),
            "sfkgbcx": pp.get("sfkgbcx", "0"),
            "sfrxtgkcxd": pp.get("sfrxtgkcxd", "0"),
            "tykczgxdcs": pp.get("tykczgxdcs", "0"),
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "kklxdm":  kklxdm,
            "bbhzxjxb": pp.get("bbhzxjxb", "0"),
            "xkkz_id": tab.get("xkkz_id", ""),
            "xkkz_xh": tab.get("xkkz_xh", ""),
        }

        resp = self._post(EP.XK_PART_DISPLAY, data=data)
        try:
            result = resp.json()
        except Exception:
            raise ApiError(ErrorCode.INVALID_RESPONSE,
                           f"PartDisplay 返回非 JSON: {resp.text[:200]}")

        fm = self.config.fields
        courses = result.get("tmpList", [])
        for c in courses:
            # 使用字段映射匹配教学班编号
            if c.get(fm.class_name) == jxbbh:
                submit_id = c.get(fm.submit_id, "")
                if not submit_id:
                    raise BotError(ErrorCode.DO_JXB_ID_MISSING, jxbbh)
                return {
                    "do_jxb_id": submit_id,
                    "jxb_id":    c.get(fm.class_id, ""),
                    "kch_id":    c.get(fm.course_id, ""),
                    "kch":       c.get(fm.course_code, ""),
                    "kcmc":      c.get(fm.course_name, ""),
                    "jxbzls":    c.get(fm.class_type, "1"),
                    "jxbmc":     c.get(fm.class_name, ""),
                    "kklxdm":    c.get(fm.type_code, kklxdm),
                    "yxzrs":     c.get(fm.enrolled, "?"),
                    "jxbrs":     c.get(fm.capacity, "?"),
                }

        return None

    # ============================================================
    # 选课操作（同步版，用于单步探测）
    # ============================================================

    def check_in_cart(self, jxb_id: str) -> bool:
        """检查课程是否已在购物车。返回 True=已在。"""
        resp = self._post(EP.XK_CHECK_CART, data={"jxb_id": jxb_id})
        return resp.text.strip() == '"2"'

    def check_conflict(self, do_jxb_id: str, kch_id: str) -> dict:
        """检查时间冲突。返回 {flag, msg}。"""
        data = {
            "jxb_ids": do_jxb_id,
            "xkxnm":   self.page_params.get("xkxnm", ""),
            "xkxqm":   self.page_params.get("xkxqm", ""),
            "kch_id":  kch_id,
        }
        resp = self._post(EP.XK_CHECK_CT, data=data)
        try:
            return resp.json()
        except Exception:
            return {"flag": "-1", "msg": f"冲突检查异常: {resp.text[:100]}"}

    def add_to_cart(self, course: dict) -> dict:
        """将课程加入购物车。返回 {flag, msg}。"""
        pp = self.page_params
        kch_id = course["kch_id"]
        data = {
            "jxb_ids": course["do_jxb_id"],  # ← 关键：用 do_jxb_id！
            "kch_id":  kch_id,
            "kcmc":    course.get("kcmc", ""),
            "rwlx":    pp.get("rwlx", "1"),
            "rlkz":    pp.get("rlkz", "0"),
            "cdrlkz":  pp.get("cdrlkz", "0"),
            "rlzlkz":  pp.get("rlzlkz", "1"),
            "xxkbj":   pp.get("xxkbj", "0"),
            "qz":      pp.get("qz", "0"),
            "cxbj":    pp.get("cxbj", "0"),
            "xkkz_id": pp.get("xkkz_id", course.get("xkkz_id", "")),
            "njdm_id": pp.get("njdm_id", ""),
            "zyh_id":  pp.get("zyh_id", ""),
            "kklxdm":  course.get("kklxdm", "06"),
            "xklc":    pp.get("xklc", "1"),
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
        }
        resp = self._post(EP.XK_ADD_CART, data=data)
        try:
            return resp.json()
        except Exception:
            return {"flag": "-1", "msg": f"加购异常: {resp.text[:100]}"}

    def submit_cart(self, cart_ids: list[str]) -> list[dict]:
        """提交购物车中选中的课程。返回 [{flag, msg, xkgwcb_id}]。"""
        resp = self._post(EP.XK_SUBMIT_CART, data={"ids": ",".join(cart_ids)})
        try:
            return resp.json()
        except Exception:
            return [{"flag": "-1", "msg": f"提交异常: {resp.text[:100]}"}]

    def query_cart_courses(self) -> list[dict]:
        """查询购物车中所有课程（参考 SCAU-course-tool）。
        返回 [{xkgwcb_id, kcmc, jxbmc, kklxmc, ...}]"""
        pp = self.page_params
        data = {
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "showCount": "100",
            "kspage":  "0",
            "jspage":  "100",
            "sidx":    "zjsj",
            "sord":    "asc",
        }
        resp = self._post(EP.XK_QUERY_CART + "?doType=query", data=data, gnmkdm=False)
        try:
            return resp.json().get("items", [])
        except Exception:
            return []

    def get_cart_items(self, kklxdm: str) -> list[dict]:
        """获取购物车中指定 kklxdm 的课程列表"""
        pp = self.page_params
        data = {
            "xkkz_id": pp.get("firstXkkzId", ""),
            "kklxdm":  kklxdm,
            "njdm_id": pp.get("njdm_id", ""),
            "zyh_id":  pp.get("zyh_id", ""),
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "xklc":    pp.get("xklc", "1"),
            "xszxzt":  pp.get("xszxzt", "1"),
            "rwlx":    pp.get("rwlx", "1"),
            "kspage":  "0",
            "jspage":  "15",
        }
        resp = self._post(EP.XK_QUERY_CART + "?doType=query", data=data, gnmkdm=False)
        try:
            return resp.json().get("items", [])
        except Exception:
            return []

    def query_selected_courses(self) -> list[dict]:
        """查询已选课程列表。返回 [{jxbmc, kcmc, do_jxb_id, kch_id, ...}]"""
        pp = self.page_params
        data = {
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "xklc":    pp.get("xklc", "1"),
            "xszxzt":  pp.get("xszxzt", "1"),
            "kspage":  "0",
            "jspage":  "15",
        }
        resp = self._post(EP.XK_CHOOSED + "?doType=query", data=data, gnmkdm=False)
        try:
            return resp.json().get("items", [])
        except Exception:
            return []

    def drop_course(self, jxb_ids: str, kch_id: str) -> dict:
        """退选课程。jxb_ids=do_jxb_id, kch_id=课程号ID。返回 {flag, msg}"""
        pp = self.page_params
        data = {
            "jxb_ids": jxb_ids,
            "kch_id":  kch_id,
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "txbsfrl": "0",
        }
        resp = self._post(EP.XK_DROP_COURSE + "?doType=del", data=data)
        text = resp.text.strip()
        try:
            j = resp.json()
            if isinstance(j, dict):
                return j
            return {"flag": "1" if text == '"1"' or text == "1" else "0", "msg": text}
        except Exception:
            return {"flag": "1" if text == '"1"' or text == "1" else "0", "msg": text}

    def close(self):
        """关闭会话"""
        if self._client:
            self._client.close()
            self._client = None


# ================================================================
# 异步提交客户端（抢课窗口期使用，零额外开销）
# ================================================================

class AsyncSubmitClient:
    """异步 HTTP 客户端 —— 仅用于提交阶段的高频请求"""

    def __init__(self, config: Config, cookies: dict, page_params: dict):
        self.config = config
        self.base = config.base_url.rstrip("/")
        self.page_params = page_params
        self._client: httpx.AsyncClient | None = None

        self._cookies = cookies
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    async def _post(self, path: str, data: dict, timeout: float = None) -> httpx.Response:
        url = self._url(path)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}gnmkdm={self.config.gnmkdm}"
        t = timeout or self.config.submit_timeout
        return await self._client.post(url, data=data, timeout=t)

    def _url(self, path: str) -> str:
        return urljoin(self.base, path)

    async def open(self):
        self._client = httpx.AsyncClient(
            headers=self._headers,
            cookies=self._cookies,
            follow_redirects=True,
        )

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def check_in_cart(self, jxb_id: str) -> bool:
        resp = await self._post(EP.XK_CHECK_CART, {"jxb_id": jxb_id})
        return resp.text.strip() == '"2"'

    async def add_to_cart(self, course: dict) -> dict:
        pp = self.page_params
        resp = await self._post(EP.XK_ADD_CART, {
            "jxb_ids": course["do_jxb_id"],
            "kch_id":  course["kch_id"],
            "kcmc":    course.get("kcmc", ""),
            "rwlx":    pp.get("rwlx", "1"),
            "rlkz":    pp.get("rlkz", "0"),
            "cdrlkz":  pp.get("cdrlkz", "0"),
            "rlzlkz":  pp.get("rlzlkz", "1"),
            "xxkbj":   pp.get("xxkbj", "0"),
            "qz":      pp.get("qz", "0"),
            "cxbj":    pp.get("cxbj", "0"),
            "xkkz_id": pp.get("xkkz_id", ""),
            "njdm_id": pp.get("njdm_id", ""),
            "zyh_id":  pp.get("zyh_id", ""),
            "kklxdm":  course.get("kklxdm", "06"),
            "xklc":    pp.get("xklc", "1"),
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
        })
        try:
            return resp.json()
        except Exception:
            return {"flag": "-1", "msg": f"加购异常: {resp.text[:100]}"}

    async def submit_cart(self, cart_ids: list[str]) -> list[dict]:
        resp = await self._post(EP.XK_SUBMIT_CART, {"ids": ",".join(cart_ids)})
        try:
            return resp.json()
        except Exception:
            return [{"flag": "-1", "msg": f"提交异常: {resp.text[:100]}"}]

    async def check_conflict(self, do_jxb_id: str, kch_id: str) -> dict:
        resp = await self._post(EP.XK_CHECK_CT, {
            "jxb_ids": do_jxb_id,
            "xkxnm":   self.page_params.get("xkxnm", ""),
            "xkxqm":   self.page_params.get("xkxqm", ""),
            "kch_id":  kch_id,
        })
        try:
            return resp.json()
        except Exception:
            return {"flag": "-1", "msg": f"冲突检查异常: {resp.text[:100]}"}

    async def query_selected_courses(self) -> list[dict]:
        """查询已选课程列表"""
        pp = self.page_params
        data = {
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "xklc":    pp.get("xklc", "1"),
            "xszxzt":  pp.get("xszxzt", "1"),
            "kspage":  "0",
            "jspage":  "15",
        }
        resp = await self._post(EP.XK_CHOOSED + "?doType=query", data=data, gnmkdm=False)
        try:
            return resp.json().get("items", [])
        except Exception:
            return []

    async def drop_course(self, jxb_ids: str, kch_id: str) -> dict:
        """退选课程。jxb_ids=do_jxb_id"""
        pp = self.page_params
        data = {
            "jxb_ids": jxb_ids,
            "kch_id":  kch_id,
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "txbsfrl": "0",
        }
        resp = await self._post(EP.XK_DROP_COURSE + "?doType=del", data=data)
        text = resp.text.strip()
        try:
            j = resp.json()
            if isinstance(j, dict):
                return j
            return {"flag": "1" if text == '"1"' or text == "1" else "0", "msg": text}
        except Exception:
            return {"flag": "1" if text == '"1"' or text == "1" else "0", "msg": text}

    async def check_target_availability(self, jxbbh: str, kklxdm: str) -> dict | None:
        """检查目标课程是否有空位。返回课程信息或 None（满员/不可选）"""
        pp = self.page_params
        data = {
            "rwlx":    pp.get("rwlx", "1"),
            "xklc":    pp.get("xklc", "1"),
            "xkly":    pp.get("xkly", "0"),
            "bklx_id": pp.get("bklx_id", ""),
            "sfkkjyxdxnxq": pp.get("sfkkjyxdxnxq", "0"),
            "kzkcgs":  pp.get("kzkcgs", "0"),
            "xqh_id":  pp.get("xqh_id", "3"),
            "jg_id":   pp.get("jg_id_1", pp.get("jg_id", "14")),
            "njdm_id_1": pp.get("njdm_id", ""),
            "zyh_id_1": pp.get("zyh_id", ""),
            "gnjkxdnj": pp.get("gnjkxdnj", "0"),
            "zyh_id":  pp.get("zyh_id", ""),
            "zyfx_id": pp.get("zyfx_id", "wfx"),
            "njdm_id": pp.get("njdm_id", ""),
            "bh_id":   pp.get("bh_id", ""),
            "bjgkczxbbjwcx": pp.get("bjgkczxbbjwcx", "0"),
            "xbm":     pp.get("xbm", "1"),
            "xslbdm":  pp.get("xslbdm", "1"),
            "mzm":     pp.get("mzm", "01"),
            "xz":      pp.get("xz", "4"),
            "ccdm":    pp.get("ccdm", "1"),
            "xsbj":    pp.get("xsbj", "0"),
            "sfkknj":  pp.get("sfkknj", "0"),
            "sfkkzy":  pp.get("sfkkzy", "0"),
            "kzybkxy": pp.get("kzybkxy", "0"),
            "sfznkx":  pp.get("sfznkx", "0"),
            "zdkxms":  pp.get("zdkxms", "0"),
            "sfkxq":   pp.get("sfkxq", "1"),
            "sfkcfx":  pp.get("sfkcfx", "0"),
            "kkbk":    pp.get("kkbk", "0"),
            "kkbkdj":  pp.get("kkbkdj", "0"),
            "bklbkcj": pp.get("bklbkcj", "0"),
            "sfkgbcx": pp.get("sfkgbcx", "0"),
            "sfrxtgkcxd": pp.get("sfrxtgkcxd", "0"),
            "tykczgxdcs": pp.get("tykczgxdcs", "0"),
            "xkxnm":   pp.get("xkxnm", ""),
            "xkxqm":   pp.get("xkxqm", ""),
            "kklxdm":  kklxdm,
            "bbhzxjxb": pp.get("bbhzxjxb", "0"),
            "xkkz_id": pp.get("firstXkkzId", ""),
            "xkkz_xh": pp.get("firstXkkzXh", ""),
        }
        resp = await self._post(EP.XK_PART_DISPLAY, data=data)
        try:
            result = resp.json()
        except Exception:
            return None

        courses = result.get("tmpList", [])
        for c in courses:
            if c.get("jxbmc") == jxbbh:
                enrolled = int(c.get("yxzrs", 0))
                capacity = int(c.get("jxbrs", 0))
                if enrolled < capacity:
                    return {
                        "do_jxb_id": c.get("do_jxb_id", ""),
                        "jxb_id":    c.get("jxb_id", ""),
                        "kch_id":    c.get("kch_id", ""),
                        "kcmc":      c.get("kcmc", ""),
                        "jxbmc":     c.get("jxbmc", ""),
                        "kklxdm":    c.get("kklxdm", kklxdm),
                        "yxzrs":     enrolled,
                        "jxbrs":     capacity,
                    }
                return None  # 满员
        return None
