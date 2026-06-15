"""
通信层 —— CDP 连接管理、浏览器启动、iframe 操作、API 调用。
"""

import asyncio, json, os, subprocess, sys, time, urllib.request
from config import Config


class ApiError(Exception):
    """API 调用异常"""
    pass


# ================================================================
# 浏览器启动 & CDP 检查
# ================================================================

def check_cdp(port: int = 9222) -> bool:
    """检查 CDP 端口是否可用"""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3)
        return True
    except Exception:
        return False


def launch_browser(config: Config):
    """启动 Edge 并打开教务页面"""
    print("[启动] 正在启动 Edge（调试模式）...")

    # 先关掉所有 Edge
    if sys.platform == "win32":
        subprocess.run("taskkill /F /IM msedge.exe 2>nul", shell=True)
    time.sleep(2)

    # Edge 常见路径
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge = None
    for p in edge_paths:
        if os.path.exists(p):
            edge = p
            break
    if not edge:
        raise RuntimeError("未找到 Edge 浏览器")

    login_url = (
        f"{config.base_url}/jwglxt/xtgl/index_initMenu.html?jsdm=xs"
    )
    subprocess.Popen(
        [edge, f"--remote-debugging-port={config.cdp_port}", login_url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # 等待浏览器启动
    for _ in range(15):
        time.sleep(1)
        if check_cdp(config.cdp_port):
            print("[启动] Edge 已就绪，请登录教务系统并进入'自主选课'页面")
            return
    raise RuntimeError("Edge 启动超时")


def ensure_cdp(config: Config):
    """确保 CDP 可用，否则自动启动浏览器"""
    if check_cdp(config.cdp_port):
        print("[CDP] 连接正常")
        return
    print("[CDP] 端口不可达，自动启动浏览器...")
    launch_browser(config)
    print("[CDP] 请在浏览器中登录并导航到'自主选课'页面后按 Enter 继续...")
    input()


# ================================================================
# CDP 客户端
# ================================================================

class Client:
    """教务系统通信客户端（基于 CDP）"""

    def __init__(self, config: Config):
        self.config = config
        self.ws = None
        self._msg_id = 0
        self._iframe_doc: str | None = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _find_target(self) -> dict:
        tabs = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{self.config.cdp_port}/json/list"
        ).read())
        for t in tabs:
            url = t.get("url", "")
            if self.config.jw_host in url and self.config.page_keyword in url:
                return t
        raise ApiError(
            "未找到教务页面，请确认已登录并进入教务系统\n"
            + "\n".join(f"  {t['title'][:60]}" for t in tabs)
        )

    async def connect(self):
        """连接浏览器"""
        target = self._find_target()
        print(f"[连接] {target['title']}")
        import websockets
        self.ws = await websockets.connect(target["webSocketDebuggerUrl"])
        self._msg_id = 0

        rid1 = await self._send_cmd("Runtime.enable")
        rid2 = await self._send_cmd("Network.enable")
        done = set()
        while len(done) < 2:
            msg = await self._recv_raw()
            if msg.get("id") in (rid1, rid2):
                done.add(msg["id"])

        ok = await self._eval(
            "document.querySelector('iframe[src*=\"zzxkyzb\"]') !== null"
        )
        if ok:
            self._iframe_doc = (
                "document.querySelector('iframe[src*=\"zzxkyzb\"]').contentDocument"
            )
            print("[连接] 选课 iframe 就绪")
        else:
            raise ApiError("未找到选课 iframe，请确认已进入'自主选课'页面")

    async def reconnect(self):
        """断线重连"""
        print("[重连] ...")
        try:
            await self.close()
        except Exception:
            pass
        await asyncio.sleep(2)
        await self.connect()

    async def close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    # ---- CDP 底层 ----

    async def _send_cmd(self, method: str, params: dict | None = None) -> int:
        mid = self._next_id()
        payload = {"id": mid, "method": method}
        if params:
            payload["params"] = params
        await self.ws.send(json.dumps(payload))
        return mid

    async def _recv_raw(self, timeout: float | None = None):
        t = timeout or self.config.request_timeout
        return json.loads(await asyncio.wait_for(self.ws.recv(), timeout=t))

    async def _eval(self, expression: str, await_promise: bool = False):
        mid = await self._send_cmd("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        while True:
            msg = await self._recv_raw()
            if msg.get("id") == mid:
                result = msg.get("result", {})
                if "exceptionDetails" in result:
                    d = result["exceptionDetails"]
                    raise ApiError(f"JS: {d.get('text', d.get('description', '?'))}")
                return result.get("result", {}).get("value")

    async def _iframe_eval(self, expression: str, await_promise: bool = False):
        return await self._eval(
            f"(function(){{var doc={self._iframe_doc};{expression}}})()",
            await_promise=await_promise,
        )

    # ---- API 调用 ----

    async def sync_post(self, path: str, data: dict):
        """在 iframe 中同步 POST（复用浏览器登录态）"""
        data_js = json.dumps(data, ensure_ascii=False)
        raw = await self._iframe_eval(f"""
            var xhr = new doc.defaultView.XMLHttpRequest();
            xhr.open('POST', '{path}', false);
            xhr.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
            var body = new doc.defaultView.URLSearchParams({data_js}).toString();
            try {{
                xhr.send(body);
                return xhr.responseText;
            }} catch(e) {{
                return JSON.stringify({{error: e.message}});
            }}
        """)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw
        return None

    async def get_page_params(self) -> dict:
        raw = await self._iframe_eval("""
            return JSON.stringify({
                xkkz_id:  (doc.querySelector('#xkkz_id')||{}).value || '',
                xkxnm:    (doc.querySelector('#xkxnm')||{}).value || '',
                xkxqm:    (doc.querySelector('#xkxqm')||{}).value || '',
                xklc:     (doc.querySelector('#xklc')||{}).value || '',
                rwlx:     (doc.querySelector('#rwlx')||{}).value || '',
                rlkz:     (doc.querySelector('#rlkz')||{}).value || '0',
                cdrlkz:   (doc.querySelector('#cdrlkz')||{}).value || '0',
                rlzlkz:   (doc.querySelector('#rlzlkz')||{}).value || '1',
                kklxdm:   (doc.querySelector('#kklxdm')||{}).value || '06',
                njdm_id:  (doc.querySelector('#njdm_id')||{}).value || '2025',
                zyh_id:   (doc.querySelector('#zyh_id')||{}).value || '',
            });
        """)
        return json.loads(raw) if raw else {}

    async def switch_tab(self, keyword: str):
        result = await self._iframe_eval(f"""
            var tabs = doc.querySelectorAll('.nav-tabs li a');
            for (var i = 0; i < tabs.length; i++)
                if (tabs[i].innerText.indexOf('{keyword}') >= 0) {{ tabs[i].click(); return 'OK'; }}
            return 'NOT FOUND';
        """)
        if result == "NOT FOUND":
            raise ApiError(f"未找到 tab: {keyword}")
        await asyncio.sleep(self.config.tab_switch_delay)

    async def find_course(self, jxbbh: str) -> dict | None:
        result_json = await self._iframe_eval(f"""
            var links = doc.querySelectorAll('a[onclick*="showJcInfo"]');
            var found = null;
            links.forEach(function(a) {{
                if (a.innerText.trim() === '{jxbbh}') {{
                    var tr = a.closest('tr');
                    if (!tr) return;
                    var btn = tr.querySelector('button[id^="btn-xk-"]');
                    if (!btn) return;
                    var m = btn.getAttribute('onclick')
                        .match(/insertGwcZzxk\\('([^']+)','([^']+)','([^']+)','([^']+)'\\)/);
                    if (m) found = {{jxb_id:m[1], encrypted_jxb_ids:m[2], kch_id:m[3], jxbzls:m[4]}};
                }}
            }});
            return JSON.stringify(found);
        """)
        return json.loads(result_json) if result_json else None

    async def open_cart(self):
        await self._iframe_eval("doc.querySelector('#btn_gwc').click();")
        await asyncio.sleep(self.config.cart_modal_delay)

    async def find_submit_button(self) -> dict | None:
        result = await self._eval("""
            JSON.stringify(
                Array.from(document.querySelectorAll(
                    '.bootbox-body button, .modal-body button, .bootbox .btn, .modal .btn'
                )).filter(function(b) { return b.offsetHeight > 0; })
                .map(function(b) { return {text:(b.innerText||'').trim(), id:b.id}; })
            )
        """)
        buttons = json.loads(result) if result else []
        for b in buttons:
            if b.get("text", "") in ("提交", "确认", "一键提交", "确认提交"):
                return b
        return buttons[-1] if buttons else None

    async def click_button_by_text(self, text: str):
        await self._eval(f"""
            var btns = document.querySelectorAll('.bootbox-body button, .modal-body button');
            for (var i = 0; i < btns.length; i++)
                if (btns[i].innerText.trim() === '{text}') {{ btns[i].click(); return; }}
        """)

    async def click_button_by_id(self, bid: str):
        await self._eval(f"document.querySelector('#{bid}').click();")

    async def check_visible_alerts(self) -> list:
        result = await self._eval("""
            JSON.stringify(
                Array.from(document.querySelectorAll('.bootbox-alert, .bootbox-body'))
                .filter(function(a) { return a.offsetHeight > 0; })
                .map(function(v) { return (v.innerText || '').trim().substring(0, 200); })
            )
        """)
        try:
            alerts = json.loads(result) if result else []
            return [a for a in alerts if a]
        except Exception:
            return []

    async def get_server_time(self) -> str | None:
        try:
            return await self._iframe_eval(r"""
                var body = doc.body ? doc.body.innerText : '';
                var m = body.match(/选课时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/);
                return m ? m[1] : null;
            """)
        except Exception:
            return None
