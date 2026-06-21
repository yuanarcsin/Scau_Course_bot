"""
抢课核心 —— 乐观提交（仅 2 步：AddToCart → SubmitCart）。

特点:
  - 跳过 CheckCart，信任 AddToCart 返回码
  - 仅 SubmitCart 报"不在购物车"时补查一次 cart（异常降级）
  - 激进超时（1.2s）+ 无限重试至成功或终态错误
  - 每门课程独立 Session（通过 ConcurrentSniper 管理）
"""

import asyncio, logging, time, traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

from course_bot.config import Config
from course_bot.client import EP
from course_bot.concurrent import SessionFactory, ConcurrentSniper, TaskResult, load_courses_from_cache
from course_bot.prebind import load_cache
from course_bot.errors import ErrorCode

log = logging.getLogger("course_bot")

# ================================================================
# 响应分类
# ================================================================

SUCCESS_FLAGS = {"1", "true", "True"}
TERMINAL_MSG_KEYWORDS = [
    "已选", "已经选", "已修", "重复", "冲突", "时间冲突",
    "不符合", "限制", "培养方案", "不可选", "不能选",
    "不允许", "先修", "学分", "性别", "年级", "专业",
    "容量", "已满", "满课", "余量", "人数已满",
]
RETRY_MSG_KEYWORDS = [
    "未开始", "不在选课时间", "选课时间未到", "暂未开始",
    "系统繁忙", "稍后", "重试", "网络", "超时",
    "temporarily", "unavailable",
]
NOT_IN_CART_KEYWORDS = ["不在购物车", "购物车", "未找到", "未加入"]


def classify_result(flag: str, msg: str) -> str:
    """分类响应: 'success' | 'retry' | 'terminal' | 'not_in_cart'"""
    if flag in SUCCESS_FLAGS:
        return "success"
    if any(kw in msg for kw in NOT_IN_CART_KEYWORDS):
        return "not_in_cart"
    if any(kw in msg for kw in TERMINAL_MSG_KEYWORDS):
        return "terminal"
    if any(kw in msg for kw in RETRY_MSG_KEYWORDS):
        return "retry"
    if flag == "0":
        return "terminal"
    return "retry"


# ================================================================
# 乐观提交函数（供 ConcurrentSniper 调用）
# ================================================================

async def optimistic_submit(client: httpx.AsyncClient, course: dict) -> TaskResult:
    """
    乐观提交单门课程：AddToCart → SubmitCart（仅 2 步）
    仅当 SubmitCart 返回"不在购物车"时补查一次 cart。
    """
    jxbbh = course.get("jxbbh", "?")
    kcmc = course.get("kcmc", "")
    base_url = client.base_url if hasattr(client, 'base_url') else ""
    config = course.get("_config", None)

    gnmkdm = getattr(config, 'gnmkdm', 'N253512') if config else 'N253512'

    def _url(path: str) -> str:
        base = base_url or "https://jwzf.scau.edu.cn"
        return urljoin(base, path)

    async def _post(path: str, data: dict, timeout: float = None) -> httpx.Response:
        url = _url(path)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}gnmkdm={gnmkdm}"
        t = timeout or (config.snipe_timeout if config else 1.2)
        return await client.post(url, data=data, timeout=t)

    # 注入 config 到 course 中（供 http 层使用）
    if config and "_config" not in course:
        course["_config"] = config

    while True:
        # Step 1: AddToCart（乐观，不检查是否已在购物车）
        try:
            cart_result = await _post(EP.XK_ADD_CART, {
                "jxb_ids": course["do_jxb_id"],
                "kch_id": course.get("kch_id", ""),
                "kcmc": kcmc,
                "rwlx": course.get("rwlx", "1"),
                "rlkz": course.get("rlkz", "0"),
                "rlzlkz": course.get("rlzlkz", "1"),
                "xxkbj": "0", "qz": "0", "cxbj": "0",
                "xkkz_id": course.get("xkkz_id", ""),
                "njdm_id": course.get("njdm_id", ""),
                "zyh_id": course.get("zyh_id", ""),
                "kklxdm": course.get("kklxdm", "06"),
                "xklc": course.get("xklc", "1"),
                "xkxnm": course.get("xkxnm", ""),
                "xkxqm": course.get("xkxqm", ""),
            })
            cart_data = cart_result.json() if cart_result.status_code < 500 else {"flag": "-1", "msg": f"HTTP {cart_result.status_code}"}
        except Exception as e:
            log.debug(f"  [{jxbbh}] 加购异常: {e}")
            await asyncio.sleep(0.1)
            continue

        cart_flag = str(cart_data.get("flag", ""))
        cart_msg = cart_data.get("msg", "")
        cart_cls = classify_result(cart_flag, cart_msg)

        if cart_cls == "success":
            log.debug(f"  [{jxbbh}] 加购成功")
        elif cart_cls == "terminal":
            if "已选" in cart_msg or "已修" in cart_msg:
                return TaskResult(jxbbh=jxbbh, success=True,
                                  message=f"已选/已修: {cart_msg}")
            return TaskResult(jxbbh=jxbbh, success=False,
                              message=f"加购失败(终态): {cart_msg}")
        elif "时间" in cart_msg or cart_cls == "retry":
            await asyncio.sleep(0.1)
            continue
        else:
            log.warning(f"  [{jxbbh}] 加购异常响应: flag={cart_flag} msg={cart_msg}")
            await asyncio.sleep(0.15)
            continue

        # Step 2: SubmitCart（乐观提交）
        try:
            submit_result = await _post(EP.XK_SUBMIT_CART, {"ids": course["do_jxb_id"]})
            submit_data = submit_result.json() if submit_result.status_code < 500 else [{"flag": "-1", "msg": f"HTTP {submit_result.status_code}"}]
        except Exception as e:
            log.debug(f"  [{jxbbh}] 提交异常: {e}")
            await asyncio.sleep(0.1)
            continue

        if isinstance(submit_data, list) and submit_data:
            item = submit_data[0]
        elif isinstance(submit_data, dict):
            item = submit_data
        else:
            log.warning(f"  [{jxbbh}] 提交返回非预期: {submit_data}")
            await asyncio.sleep(0.1)
            continue

        s_flag = str(item.get("flag", ""))
        s_msg = item.get("msg", "")
        s_cls = classify_result(s_flag, s_msg)

        if s_cls == "success":
            return TaskResult(jxbbh=jxbbh, success=True,
                              message=f"选课成功: {s_msg}")

        if s_cls == "not_in_cart":
            # 异常降级：补查一次购物车
            log.debug(f"  [{jxbbh}] 不在购物车，补查 cart...")
            try:
                check_resp = await _post(EP.XK_CHECK_CART, {"jxb_id": course.get("jxb_id", course["do_jxb_id"])})
                if check_resp.text.strip() != '"2"':
                    # 确实不在，重新加购
                    log.debug(f"  [{jxbbh}] 重新加购")
                    continue
            except Exception:
                pass
            # 在购物车，直接重试提交
            await asyncio.sleep(0.1)
            continue

        if s_cls == "terminal":
            return TaskResult(jxbbh=jxbbh, success=False,
                              message=f"提交失败(终态): {s_msg}")

        # retry
        await asyncio.sleep(0.1)


# ================================================================
# Sniper 主类
# ================================================================

class Sniper:
    """抢课核心：读取缓存 → 并发乐观提交"""

    def __init__(self, config: Config):
        self.config = config
        self._stop = asyncio.Event()

    def stop(self):
        self._stop.set()

    async def run(self, cache: dict = None):
        """主流程：加载缓存 → 并发提交"""
        log.info("=" * 50)
        log.info("抢课核心：乐观提交模式")
        log.info("=" * 50)

        # 加载缓存
        if cache is None:
            cache = load_cache(self.config)
        courses = load_courses_from_cache(cache,
            [c["jxbbh"] for c in self.config.target_courses])

        if not courses:
            log.error("无有效课程，请先执行预绑定")
            return

        for c in courses:
            log.info(f"  {c['jxbbh']} → {c.get('kcmc','?')} "
                     f"(do_jxb_id={c['do_jxb_id'][:30]}...)")

        # 并发提交
        factory = SessionFactory(self.config)
        sniper = ConcurrentSniper(self.config, {}, factory)

        # 注入 page_params 到每个 course
        pp = cache.get("_page_params", {})
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
                "_config": self.config,
            })

        results = await sniper.run(courses, optimistic_submit)
        self._print_results(results)

    def _print_results(self, results: list[TaskResult]):
        log.info("=" * 50)
        log.info("抢课结果")
        log.info("=" * 50)
        success = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        log.info(f"成功: {len(success)} — {', '.join(r.jxbbh for r in success) if success else '(无)'}")
        log.info(f"失败: {len(failed)} — {', '.join(r.jxbbh for r in failed) if failed else '(无)'}")
        for r in results:
            status = "OK" if r.success else "FAIL"
            log.info(f"  [{status}] {r.jxbbh}: {r.message} ({r.started}→{r.ended})")
