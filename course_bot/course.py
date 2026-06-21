"""
选课流程编排 —— 渐进式探测 + 窗口期轻量化提交。

流程:
  阶段一: 登录 + 提取页面参数 (同步)
  阶段二: 匹配目标课程，获取 do_jxb_id (同步)
  阶段三: 等待选课窗口 (无 API 调用，仅倒计时)
  阶段四: 窗口期提交 (异步，仅加购 + 提交)
"""

import asyncio, logging, sys, time, traceback
from datetime import datetime, timedelta
from pathlib import Path

from course_bot.config import Config
from course_bot.client import Client, AsyncSubmitClient, EP
from course_bot.errors import ErrorCode, BotError, LoginError, ApiError

log = logging.getLogger("course_bot")


# ================================================================
# 倒计时
# ================================================================

def _format_countdown(seconds: float) -> str:
    if seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _status_line(target_dt: datetime) -> str:
    remaining = (target_dt - datetime.now()).total_seconds()
    if remaining <= 0:
        return "\r  窗口已到！" + " " * 40
    return (f"\r  距选课窗口 {_format_countdown(remaining)}  |  "
            f"目标 {target_dt.strftime('%m-%d %H:%M:%S')}")


# ================================================================
# 选课机器人
# ================================================================

class CourseBot:

    def __init__(self, config: Config):
        self.config = config
        self.client = Client(config)
        self.targets: list[dict] = []  # 匹配到的课程 [{do_jxb_id, jxb_id, kch_id, ...}]
        self.backup: dict | None = None  # 保底课程（已选中，换课时退选它）
        self._window_dt: datetime | None = None
        self._stats: dict = {"success": [], "failed": [], "started": None, "ended": None,
                             "swap_attempts": 0, "swap_dropped": False, "swap_restored": False}

    # ================================================================
    # 阶段一 + 二: 登录 + 课程发现 (同步，窗口前)
    # ================================================================

    def phase_login_and_find(self):
        """登录 → 提取参数 → 匹配目标课程"""
        log.info("=" * 50)
        log.info("阶段一：登录并获取页面参数")
        log.info("=" * 50)

        # 1. 登录
        self.client.login()

        # 2. 获取页面参数
        params = self.client.fetch_page_params()
        tabs = self.client.tabs
        log.info(f"课程类型 tabs: {list(tabs.keys())}")

        # 3. 解析选课时间窗口（用服务器校准时间）
        server_open = params.get("xkkssj", "")
        if server_open:
            try:
                # 服务器返回的时间已是服务器本地时间，加偏移对齐到本地
                srv_dt = datetime.strptime(server_open, "%Y-%m-%d %H:%M:%S")
                offset = self.client._server_time_offset
                self._window_dt = srv_dt - timedelta(seconds=offset)
                if abs(offset) > 1:
                    log.info(f"选课窗口(服务器): {server_open}"
                             f" → 本地 {self._window_dt.strftime('%H:%M:%S')}")
            except ValueError:
                self._window_dt = self.config.window_open_dt
        else:
            self._window_dt = self.config.window_open_dt
            log.info(f"选课窗口(配置): {self._window_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        srv = self.client.server_now()
        log.info(f"服务器当前时间(校准): {srv.strftime('%Y-%m-%d %H:%M:%S')}"
                 f"{f' (偏移 {self.client._server_time_offset:+.1f}s)' if abs(self.client._server_time_offset) > 0.5 else ''}")

        # 4. 遍历所有目标课程，从 PartDisplay 获取 do_jxb_id
        log.info("=" * 50)
        log.info("阶段二：匹配目标课程")
        log.info("=" * 50)

        seen_jxbbh = set()
        for course_cfg in self.config.target_courses:
            jxbbh = course_cfg["jxbbh"]
            if jxbbh in seen_jxbbh:
                continue

            kklxdm = course_cfg.get("kklxdm", "06")
            log.info(f"查找: {jxbbh} (kklxdm={kklxdm})")

            try:
                found = self.client.find_target_course(jxbbh, kklxdm)
                if found:
                    found["jxbbh"] = jxbbh
                    found["cfg_kklxdm"] = kklxdm
                    self.targets.append(found)
                    seen_jxbbh.add(jxbbh)
                    yxzrs = found.get('yxzrs', '?')
                    jxbrs = found.get('jxbrs', '?')
                    log.info(f"  [OK] {jxbbh} → {found['kcmc']} (已选{yxzrs}/容量{jxbrs})")
                else:
                    log.warning(f"  [未找到] {jxbbh} — 可能不在可选列表中")
            except BotError as e:
                log.error(f"  [E{e.code.value[0]}] {e}")
            except Exception as e:
                log.error(f"  [异常] {e}")

        log.info(f"匹配结果: {len(self.targets)}/{len(self.config.target_courses)} 门课程就绪")
        for t in self.targets:
            log.info(f"  {t['jxbbh']} → {t['kcmc']} "
                     f"(do_jxb_id={t['do_jxb_id'][:30]}...)")

        # 5. 换课模式：查找保底课程（已选课程列表中的匹配项）
        if self.config.swap_mode == "swap" and self.config.backup_jxbbh:
            self._find_backup_course()
        elif self.config.swap_mode == "swap":
            log.warning("swap 模式已启用但未设置 backup_jxbbh，将跳过换课")

    def _find_backup_course(self):
        """从已选课程列表中查找保底课程，提取退选所需的 do_jxb_id 和 kch_id"""
        log.info("-" * 40)
        log.info(f"换课模式: 查找保底课程 {self.config.backup_jxbbh}")
        selected = self.client.query_selected_courses()
        if not selected:
            log.warning("已选课程列表为空，无法换课")
            return

        jxbbh = self.config.backup_jxbbh
        for c in selected:
            if c.get("jxbmc") == jxbbh:
                do_jxb_id = c.get("do_jxb_id") or c.get("jxb_id", "")
                kch_id = c.get("kch_id", "")
                if not do_jxb_id:
                    log.warning(f"保底课程 {jxbbh} 缺少选课 ID，无法退选")
                    return
                self.backup = {
                    "jxbbh": jxbbh,
                    "do_jxb_id": do_jxb_id,
                    "jxb_id": c.get("jxb_id", do_jxb_id),
                    "kch_id": kch_id,
                    "kcmc": c.get("kcmc", ""),
                    "kklxdm": c.get("kklxdm", self.config.target_courses[0].get("kklxdm", "06")
                                     if self.config.target_courses else "06"),
                }
                log.info(f"保底课程就绪: {jxbbh} → {self.backup['kcmc']} "
                         f"(do_jxb_id={do_jxb_id[:30]}...)")
                return
        log.warning(f"保底课程 {jxbbh} 未在已选列表中找到")

    # ================================================================
    # 阶段三: 等待窗口 (零 API 调用)
    # ================================================================

    async def phase_wait(self):
        """实时倒计时，窗口开启前做 NAT 保活"""
        log.info("=" * 50)
        log.info("阶段三：等待选课窗口")
        log.info("=" * 50)

        if not self._window_dt:
            self._window_dt = self.config.window_open_dt

        now = datetime.now()
        remaining = (self._window_dt - now).total_seconds()

        if remaining <= 0:
            log.info("窗口已到，跳过等待")
            return

        log.info(f"还需等待: {_format_countdown(remaining)}")
        last_keepalive = time.time()
        _nat_done = False

        try:
            while True:
                now = datetime.now()
                remaining = (self._window_dt - now).total_seconds()
                if remaining <= 0:
                    break

                sys.stdout.write(_status_line(self._window_dt))
                sys.stdout.flush()

                # NAT 保活：窗口临近时发一个 HEAD 请求刷新映射（零响应体）
                nat_sec = self.config.nat_refresh_seconds
                if nat_sec > 0 and not _nat_done and remaining <= nat_sec:
                    log.debug(f"NAT 保活 HEAD (距窗口 {remaining:.1f}s)...")
                    try:
                        resp = self.client.client.head(
                            self.client._url(EP.INDEX_PAGE),
                            timeout=self.config.request_timeout)
                        log.debug(f"NAT 保活完成 (HTTP {resp.status_code})")
                    except Exception:
                        log.debug("NAT 保活请求失败，继续等待")
                    _nat_done = True

                # 定期保活（不频繁）
                if (self.config.session_keepalive > 0 and
                        time.time() - last_keepalive > self.config.session_keepalive):
                    log.debug("session 保活...")
                    if not self.client.refresh_session():
                        log.warning("session 可能已过期，尝试重新登录")
                        try:
                            self.client.login()
                            self.client.fetch_page_params()
                        except Exception as e:
                            log.error(f"重新登录失败: {e}")
                    last_keepalive = time.time()

                await asyncio.sleep(0.5)
            print()
        except KeyboardInterrupt:
            log.info("用户中断等待")
            raise

        log.info("窗口已到！")

    # ================================================================
    # 阶段四: 提交 (仅加购 + 提交，零多余请求)
    # ================================================================

    async def phase_submit(self):
        """窗口期提交 —— 最少 API 调用：加购物车 → 提交购物车"""
        log.info("=" * 50)
        log.info("阶段四：提交选课")
        log.info("=" * 50)

        if not self.targets:
            log.error("无待提交课程")
            return

        self._stats["started"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # 构建异步客户端（复用 Cookie）
        cookies = {c.name: c.value for c in self.client.client.cookies.jar}
        async_client = AsyncSubmitClient(
            self.config, cookies, self.client.page_params)
        await async_client.open()

        try:
            for course in self.targets:
                await self._submit_one(async_client, course)
                await asyncio.sleep(0.1)  # 课程间微间隔
        finally:
            await async_client.close()

        self._stats["ended"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._print_result()

    async def _submit_one(self, client: AsyncSubmitClient, course: dict):
        """提交单门课程：并行加购 + 提交"""
        jxbbh = course.get("jxbbh", "")
        jxb_id = course["jxb_id"]
        do_jxb_id = course["do_jxb_id"]
        kcmc = course.get("kcmc", "")

        log.info(f"[{jxbbh}] 开始提交 {kcmc}")

        max_retries = self.config.submit_retries
        retry_delay = self.config.submit_retry_delay

        for attempt in range(1, max_retries + 1):
            try:
                # Step 1: 检查购物车（轻量，能快速失败）
                in_cart = await client.check_in_cart(jxb_id)
                if in_cart:
                    log.info(f"  [{jxbbh}] 已在购物车中 (E{ErrorCode.ALREADY_IN_CART.value[0]})")
                    # 已在购物车，直接跳到提交
                else:
                    # Step 2: 加入购物车
                    cart_result = await client.add_to_cart(course)
                    flag = str(cart_result.get("flag", ""))
                    msg = cart_result.get("msg", "")

                    if flag == "1":
                        log.info(f"  [{jxbbh}] 加购成功 (attempt {attempt})")
                    elif "时间" in msg or "不可选课" in msg:
                        log.debug(f"  [{jxbbh}] 窗口未到，等待... ({attempt})")
                        await asyncio.sleep(retry_delay)
                        continue
                    elif "已选" in msg or "已修" in msg:
                        log.warning(f"  [{jxbbh}] {msg} (E{ErrorCode.ALREADY_SELECTED.value[0]})")
                        self._stats["success"].append(jxbbh)
                        return
                    else:
                        log.warning(f"  [{jxbbh}] 加购失败: flag={flag} msg={msg} "
                                    f"(E{ErrorCode.ADD_CART_FAILED.value[0]})")
                        await asyncio.sleep(retry_delay * 2)
                        continue

                # Step 3: 提交购物车（用 do_jxb_id 作为 cart id）
                submit_result = await client.submit_cart([do_jxb_id])
                if isinstance(submit_result, list) and submit_result:
                    item = submit_result[0]
                    s_flag = str(item.get("flag", ""))
                    s_msg = item.get("msg", "")
                    if s_flag == "1":
                        log.info(f"  [{jxbbh}] 选课成功！(attempt {attempt})")
                        self._stats["success"].append(jxbbh)
                        return
                    else:
                        log.warning(f"  [{jxbbh}] 提交失败: {s_msg} "
                                    f"(E{ErrorCode.CART_SUBMIT_FAILED.value[0]})")
                        if attempt < max_retries:
                            await asyncio.sleep(retry_delay)
                            continue
                else:
                    log.warning(f"  [{jxbbh}] 提交返回异常: {submit_result}")

            except Exception as e:
                log.error(f"  [{jxbbh}] 异常 (attempt {attempt}): {e}")

            if attempt < max_retries:
                await asyncio.sleep(retry_delay)

        log.error(f"  [{jxbbh}] 超过最大重试 {max_retries} 次，放弃")
        self._stats["failed"].append(jxbbh)

    # ================================================================
    # 阶段四-SWAP: 智能换课提交（退保底 → 选目标 → 失败回退保底）
    # ================================================================

    async def phase_submit_swap(self):
        """换课模式提交 —— 等目标有空位 → 退保底 → 抢目标 → 失败则补回保底"""
        log.info("=" * 50)
        log.info("阶段四（换课模式）：智能换课")
        log.info("=" * 50)

        if not self.backup:
            log.error("保底课程信息缺失，回退到普通提交模式")
            await self.phase_submit()
            return

        self._stats["started"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        target = self.targets[0]  # 换课模式一次只处理一个目标
        backup = self.backup
        jxbbh = target["jxbbh"]
        kklxdm = target.get("cfg_kklxdm", "06")

        log.info(f"目标: {target['kcmc']} ({jxbbh})")
        log.info(f"保底: {backup['kcmc']} ({backup['jxbbh']})")

        cookies = {c.name: c.value for c in self.client.client.cookies.jar}
        client = AsyncSubmitClient(self.config, cookies, self.client.page_params)
        await client.open()

        max_poll = self.config.submit_retries
        poll_delay = self.config.submit_retry_delay

        try:
            for attempt in range(1, max_poll + 1):
                # Step 1: 检查目标课程是否有空位
                log.debug(f"  [swap-{attempt}] 检查目标课程容量...")
                target_info = await client.check_target_availability(jxbbh, kklxdm)

                if target_info is None:
                    # 满员或不可选，继续轮询
                    if attempt % 10 == 1:
                        log.info(f"  [swap-{attempt}] 目标满员/不可选，继续等待...")
                    await asyncio.sleep(poll_delay)
                    continue

                # Step 2: 目标有空位！执行换课
                log.info(f"  [swap-{attempt}] 目标有空位！"
                         f"({target_info['yxzrs']}/{target_info['jxbrs']}) 开始换课")
                self._stats["swap_attempts"] += 1

                # 2a: 退选保底课程
                drop_ok = await self._drop_with_retry(client, backup)
                if not drop_ok:
                    log.error(f"  [swap-{attempt}] 退选保底课程失败，跳过本次换课")
                    await asyncio.sleep(poll_delay)
                    continue

                self._stats["swap_dropped"] = True
                log.info(f"  [swap-{attempt}] 保底课程已退选")

                # 2b: 加购 + 提交目标课程
                enroll_ok = await self._enroll_with_retry(client, target_info)
                if enroll_ok:
                    log.info(f"  [swap-{attempt}] 换课成功！目标已选中")
                    self._stats["success"].append(jxbbh)
                    return
                else:
                    # 2c: 目标抢课失败 → 紧急补回保底
                    log.warning(f"  [swap-{attempt}] 目标选课失败，紧急补回保底课程！")
                    restored = await self._enroll_with_retry(client, backup, is_restore=True)
                    if restored:
                        log.info(f"  [swap-{attempt}] 保底课程已补回")
                        self._stats["swap_restored"] = True
                    else:
                        log.error(f"  [swap-{attempt}] !!保底课程补回也失败!! 请手动检查！")
                        self._stats["failed"].append(backup["jxbbh"])
                    self._stats["failed"].append(jxbbh)
                    return

            log.warning(f"轮询 {max_poll} 次后目标仍无空位，退出")
            self._stats["failed"].append(jxbbh)
        finally:
            await client.close()
            self._stats["ended"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._print_result()

    async def _drop_with_retry(self, client: AsyncSubmitClient, course: dict,
                               max_retry: int = 3) -> bool:
        """退选课程，带重试"""
        for retry in range(1, max_retry + 1):
            try:
                result = await client.drop_course(course["do_jxb_id"], course["kch_id"])
                flag = str(result.get("flag", ""))
                if flag == "1":
                    return True
                log.warning(f"  退选失败 (retry {retry}): {result.get('msg', result)}")
            except Exception as e:
                log.warning(f"  退选异常 (retry {retry}): {e}")
            if retry < max_retry:
                await asyncio.sleep(0.15)
        return False

    async def _enroll_with_retry(self, client: AsyncSubmitClient, course: dict,
                                 is_restore: bool = False,
                                 max_retry: int = 10) -> bool:
        """选课：加购→提交，紧急恢复时无限重试"""
        tag = "补回" if is_restore else "选课"
        retry = 0
        while True:
            retry += 1
            try:
                # 加购
                cart_result = await client.add_to_cart(course)
                flag = str(cart_result.get("flag", ""))
                msg = cart_result.get("msg", "")

                if flag == "1":
                    pass  # 加购成功
                elif "已选" in msg or "已修" in msg:
                    log.info(f"  {tag} [{course.get('jxbbh','')}] 课程已在选课列表中")
                    return True
                else:
                    if retry % 3 == 1:
                        log.warning(f"  {tag}加购失败 (retry {retry}): {msg}")
                    if is_restore or retry < max_retry:
                        await asyncio.sleep(0.15)
                        continue
                    return False

                # 提交
                submit_result = await client.submit_cart([course["do_jxb_id"]])
                if isinstance(submit_result, list) and submit_result:
                    item = submit_result[0]
                    s_flag = str(item.get("flag", ""))
                    s_msg = item.get("msg", "")
                    if s_flag == "1":
                        log.info(f"  {tag}提交成功 (retry {retry})")
                        return True
                    if "已满" in s_msg or "容量" in s_msg:
                        log.warning(f"  {tag}课程已满: {s_msg}")
                        return False
                    if retry % 3 == 1:
                        log.warning(f"  {tag}提交失败 (retry {retry}): {s_msg}")
                else:
                    log.warning(f"  {tag}提交返回异常: {submit_result}")

            except Exception as e:
                log.error(f"  {tag}异常 (retry {retry}): {e}")

            if is_restore:
                await asyncio.sleep(0.15)
                continue  # 紧急恢复模式：无限重试

            if retry >= max_retry:
                return False
            await asyncio.sleep(0.15)

    # ================================================================
    # 主流程
    # ================================================================

    async def run(self, do_find: bool = True):
        """完整流程"""
        try:
            # 阶段一 + 二: 登录 + 课程发现（同步）
            if do_find:
                last_err = None
                for retry in range(self.config.max_retries):
                    try:
                        self.phase_login_and_find()
                        if self.targets:
                            break
                        log.warning(f"未匹配到课程 (重试 {retry+1}/{self.config.max_retries})")
                    except (BotError, LoginError) as e:
                        last_err = e
                        log.error(f"阶段一/二出错 (重试 {retry+1}): {e}")
                        if retry < self.config.max_retries - 1:
                            time.sleep(self.config.retry_delay)
                else:
                    if last_err:
                        raise last_err
            else:
                self.client.login()
                self.client.fetch_page_params()
                log.info("跳过课程发现（使用预设 do_jxb_id）")

            if not self.targets:
                log.error("无目标课程，退出")
                return

            # 阶段三: 等待窗口
            await self.phase_wait()

            # 阶段四: 提交（正常模式 or 换课模式）
            if self.config.swap_mode == "swap" and self.backup:
                await self.phase_submit_swap()
            else:
                await self.phase_submit()

        except KeyboardInterrupt:
            log.info("用户中止")
        except Exception:
            log.error(f"运行异常: {traceback.format_exc()}")
        finally:
            self.client.close()

    def _print_result(self):
        s = self._stats
        log.info("=" * 50)
        log.info("结果")
        log.info("=" * 50)
        log.info(f"成功: {len(s['success'])} — {', '.join(s['success']) if s['success'] else '(无)'}")
        log.info(f"失败: {len(s['failed'])} — {', '.join(s['failed']) if s['failed'] else '(无)'}")
        if s.get("swap_attempts"):
            log.info(f"换课尝试: {s['swap_attempts']} 次 | "
                     f"已退选: {'是' if s.get('swap_dropped') else '否'} | "
                     f"已补回: {'是' if s.get('swap_restored') else '否'}")
        log.info(f"耗时: {s['started']} → {s['ended']}")
