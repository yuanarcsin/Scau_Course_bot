"""
选课逻辑 —— 实时倒计时、阶段分离、断点重续。
"""

import asyncio, json, sys, time
from datetime import datetime

from config import Config
from client import Client, ApiError


# ================================================================
# 倒计时显示
# ================================================================

def _format_countdown(seconds: float) -> str:
    """秒数 → 可读倒计时"""
    if seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _countdown_line(target_dt: datetime) -> str:
    """生成一行倒计时状态"""
    remaining = (target_dt - datetime.now()).total_seconds()
    if remaining <= 0:
        return f"\r  时间已到！                                             "
    return f"\r  距选课窗口 {_format_countdown(remaining)}  |  目标 {target_dt.strftime('%m-%d %H:%M:%S')}"


# ================================================================
# 选课机器人
# ================================================================

class CourseBot:
    """选课机器人"""

    def __init__(self, config: Config):
        self.config = config
        self.client = Client(config)
        self.cart_ready: set = set()
        self.cart_submitted: bool = False

    # ============================================================
    # 阶段一：加购物车
    # ============================================================

    async def phase_add_to_cart(self):
        """批量加购物车，已加自动跳过"""
        print("\n" + "=" * 50)
        print("阶段一：加入购物车")
        print("=" * 50)

        params = await self.client.get_page_params()
        print(f"参数: 学年={params.get('xkxnm')} 学期={params.get('xkxqm')} "
              f"轮次={params.get('xklc')}")

        for course_cfg in self.config.target_courses:
            jxbbh = course_cfg["jxbbh"]
            if jxbbh in self.cart_ready:
                print(f"  [{jxbbh}] 已加，跳过")
                continue

            print(f"  [{jxbbh}] ", end="", flush=True)
            try:
                await self._add_one(course_cfg)
                self.cart_ready.add(jxbbh)
                print("OK")
            except ApiError as e:
                print(f"FAIL — {e}")

        print(f"\n结果: {len(self.cart_ready)}/{len(self.config.target_courses)} 门已加")

    async def _add_one(self, course_cfg: dict):
        jxbbh = course_cfg["jxbbh"]
        await self.client.switch_tab(course_cfg["tab_keyword"])

        course = await self.client.find_course(jxbbh)
        if not course:
            raise ApiError("未找到课程")

        result = await self.client.sync_post(
            "/jwglxt/xsxk/zzxkyzb_cxCheckZyZzxkYzbInCart.html?gnmkdm=N253512",
            {"jxb_id": course["jxb_id"]},
        )
        status = str(result) if not isinstance(result, dict) else str(result.get("flag", "0"))
        if status == "2":
            return  # 已在购物车

        params = await self.client.get_page_params()
        result = await self.client.sync_post(
            "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbToCart.html?gnmkdm=N253512",
            {
                "jxb_ids": course["encrypted_jxb_ids"],
                "kch_id": course["kch_id"], "kcmc": "",
                "rwlx": params.get("rwlx", "3"),
                "rlkz": params.get("rlkz", "0"),
                "cdrlkz": params.get("cdrlkz", "0"),
                "rlzlkz": params.get("rlzlkz", "1"),
                "xxkbj": "0", "qz": "0", "cxbj": "0",
                "xkkz_id": params.get("xkkz_id", ""),
                "njdm_id": params.get("njdm_id", "2025"),
                "zyh_id": params.get("zyh_id", ""),
                "kklxdm": params.get("kklxdm", "06"),
                "xklc": params.get("xklc", "1"),
                "xkxnm": params.get("xkxnm", "2026"),
                "xkxqm": params.get("xkxqm", "3"),
            },
        )
        flag = result.get("flag") if isinstance(result, dict) else None
        if flag != "1":
            msg = result.get("msg", str(result)) if isinstance(result, dict) else str(result)
            raise ApiError(f"加入失败: {msg}")

    # ============================================================
    # 阶段二：等待 + 提交
    # ============================================================

    async def phase_wait_and_submit(self):
        """实时倒计时等待，到点立即提交"""
        print("\n" + "=" * 50)
        print("阶段二：等待窗口并提交")
        print("=" * 50)

        if not self.cart_ready:
            print("购物车为空，请先用 --cart 加购物车")
            return

        open_dt = self.config.window_open_dt
        print(f"选课窗口: {open_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 如果窗口已过，直接提交
        now = datetime.now()
        if now >= open_dt:
            print("窗口已到，立即提交！")
        else:
            wait_sec = (open_dt - now).total_seconds()
            print(f"还需等待: {_format_countdown(wait_sec)}")
            print()
            print("实时倒计时（按 Ctrl+C 可安全退出）:")
            print("-" * 45)

            try:
                while True:
                    now = datetime.now()
                    remaining = (open_dt - now).total_seconds()
                    if remaining <= 0:
                        break
                    # 实时刷新同一行
                    sys.stdout.write(_countdown_line(open_dt))
                    sys.stdout.flush()
                    await asyncio.sleep(0.5)
                print()  # 换行
            except KeyboardInterrupt:
                print("\n\n用户中断，退出等待。")
                return

        # 到点提交
        print("\n[!] 窗口已到，开始提交...")
        await self._submit_with_retry()

    async def _submit_with_retry(self):
        """带重试的提交"""
        for attempt in range(self.config.max_retries):
            try:
                print(f"\n  提交尝试 {attempt + 1}/{self.config.max_retries}")
                await self._do_submit()
                self.cart_submitted = True
                return
            except ApiError as e:
                print(f"  异常: {e}")
                if attempt < self.config.max_retries - 1:
                    print(f"  {self.config.retry_delay}s 后重试...")
                    await asyncio.sleep(self.config.retry_delay)
                    try:
                        await self.client.reconnect()
                    except Exception:
                        pass
            except Exception as e:
                print(f"  未知错误: {e}")
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay)
                    try:
                        await self.client.reconnect()
                    except Exception:
                        pass
        print("\n[!] 提交失败，请手动点击'我的选课意向'→'提交'")

    async def _do_submit(self):
        """执行一次提交"""
        await self.client.open_cart()
        btn = await self.client.find_submit_button()
        if not btn:
            raise ApiError("未找到提交按钮")

        print(f"  点击 [{btn['text']}]")
        if btn.get("id"):
            await self.client.click_button_by_id(btn["id"])
        else:
            await self.client.click_button_by_text(btn["text"])

        await asyncio.sleep(2)

        try:
            alerts = await self.client.check_visible_alerts()
            if alerts:
                for a in alerts:
                    print(f"  反馈: {a}")
        except Exception:
            pass

    # ============================================================
    # 主流程
    # ============================================================

    async def run(self, do_cart: bool = False):
        """完整流程：连接 → [加购物车] → 等待 → 提交"""
        await self.client.connect()

        if do_cart:
            for retry in range(self.config.max_retries):
                try:
                    await self.phase_add_to_cart()
                    break
                except ApiError as e:
                    print(f"阶段一出错: {e}")
                    if retry < self.config.max_retries - 1:
                        await self.client.reconnect()
        else:
            # 跳过加购物车，标记全部已加
            self.cart_ready = {c["jxbbh"] for c in self.config.target_courses}
            print("\n[跳过加购物车阶段，假定已手动加好]")

        await self.phase_wait_and_submit()
        await self.client.close()
        self._print_summary()

    def _print_summary(self):
        print(f"\n{'='*50}")
        print("结果")
        print(f"{'='*50}")
        for c in self.config.target_courses:
            status = "已加" if c["jxbbh"] in self.cart_ready else "未加"
            print(f"  [{status}] {c['jxbbh']}")
        print(f"  提交: {'已尝试' if self.cart_submitted else '未提交'}")
