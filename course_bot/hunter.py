"""
独立捡漏器 —— 开抢后定时轮询，发现空位时触发提交。

特点:
  - 独立于抢课核心，不干扰主流程
  - 防封控：间隔 3-5s + 随机抖动
  - 读取缓存中的 do_jxb_id（无需重新搜索）
  - 前端标注为「测试功能」
"""

import asyncio, logging, random, time, traceback
from datetime import datetime
from urllib.parse import urljoin

import httpx

from course_bot.config import Config
from course_bot.client import EP
from course_bot.prebind import load_cache
from course_bot.errors import ErrorCode

log = logging.getLogger("course_bot")


class Hunter:
    """独立捡漏器：轮询 PartDisplay，发现空位立即提交"""

    def __init__(self, config: Config):
        self.config = config
        self._stop = asyncio.Event()
        self._stats: dict = {"polled": 0, "found": 0, "succeeded": 0, "failed": 0}

    def stop(self):
        self._stop.set()

    @property
    def stats(self) -> dict:
        return self._stats

    async def run(self, cache: dict = None):
        """主流程：定时轮询 → 发现空位 → 提交"""
        log.info("=" * 50)
        log.info("独立捡漏器（测试功能）")
        log.info("=" * 50)

        if cache is None:
            cache = load_cache(self.config)

        targets = {}
        for course_cfg in self.config.target_courses:
            jxbbh = course_cfg["jxbbh"]
            if jxbbh in cache and not jxbbh.startswith("_"):
                targets[jxbbh] = {
                    **cache[jxbbh],
                    "kklxdm": course_cfg.get("kklxdm", "06"),
                }
        if not targets:
            log.error("无有效缓存课程")
            return

        log.info(f"监控 {len(targets)} 门: {', '.join(targets)}")
        log.info(f"间隔 {self.config.hunter_interval}±{self.config.hunter_jitter}s")
        log.info(f"持续 {self.config.hunter_duration}s")

        # 登录获取 page_params
        from course_bot.client import Client
        client = Client(self.config)
        try:
            client.login()
            client.fetch_page_params()
            page_params = client.page_params
        finally:
            client.close()

        end_time = time.time() + self.config.hunter_duration

        while not self._stop.is_set() and time.time() < end_time:
            interval = self.config.hunter_interval + random.uniform(
                -self.config.hunter_jitter, self.config.hunter_jitter)
            interval = max(1.5, interval)

            await self._poll_once(targets, page_params)

            self._stats["polled"] += 1
            if self._stats["polled"] % 10 == 1:
                log.info(f"  轮询 #{self._stats['polled']}: "
                         f"发现 {self._stats['found']} 次, "
                         f"成功 {self._stats['succeeded']}, "
                         f"失败 {self._stats['failed']}")

            # 等待间隔（可被 stop 打断）
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

        log.info(f"捡漏器退出: 轮询 {self._stats['polled']} 次 | "
                 f"发现 {self._stats['found']} | "
                 f"成功 {self._stats['succeeded']} | "
                 f"失败 {self._stats['failed']}")

    async def _poll_once(self, targets: dict, page_params: dict):
        """执行一次轮询：PartDisplay 搜索 → 检查空位"""
        pp = page_params

        for jxbbh, target in targets.items():
            if self._stop.is_set():
                return

            kklxdm = target.get("kklxdm", "06")
            try:
                found = await self._check_availability(jxbbh, kklxdm, pp)
                if found:
                    self._stats["found"] += 1
                    log.info(f"  [捡漏] {jxbbh} 发现空位 "
                             f"({found.get('yxzrs','?')}/{found.get('jxbrs','?')})")
                    # 更新 do_jxb_id（可能变了）
                    targets[jxbbh]["do_jxb_id"] = found["do_jxb_id"]
                    targets[jxbbh]["kch_id"] = found.get("kch_id",
                                                          targets[jxbbh].get("kch_id", ""))

                    # 提交
                    ok = await self._submit_one(targets[jxbbh], pp)
                    if ok:
                        self._stats["succeeded"] += 1
                        log.info(f"  [捡漏] {jxbbh} 选课成功！")
                        return  # 一门成功即退出本轮
                    else:
                        self._stats["failed"] += 1
            except Exception as e:
                log.debug(f"  [捡漏] {jxbbh} 查询异常: {e}")

    async def _check_availability(self, jxbbh: str, kklxdm: str, pp: dict) -> dict | None:
        """检查目标课程是否有空位"""
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            base = self.config.base_url.rstrip("/")
            gnmkdm = self.config.gnmkdm
            data = {
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
                "zyh_id": pp.get("zyh_id", ""),
                "zyfx_id": pp.get("zyfx_id", "wfx"),
                "njdm_id": pp.get("njdm_id", ""),
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
                "xkkz_id": pp.get("firstXkkzId", ""),
                "xkkz_xh": pp.get("firstXkkzXh", ""),
            }
            url = urljoin(base, EP.XK_PART_DISPLAY)
            url = f"{url}?gnmkdm={gnmkdm}"
            resp = await client.post(url, data=data)
            result = resp.json()
            courses = result.get("tmpList", [])
            for c in courses:
                if c.get("jxbmc") == jxbbh:
                    enrolled = int(c.get("yxzrs", 0))
                    capacity = int(c.get("jxbrs", 0))
                    if enrolled < capacity:
                        return {
                            "do_jxb_id": c.get("do_jxb_id", ""),
                            "jxb_id": c.get("jxb_id", ""),
                            "kch_id": c.get("kch_id", ""),
                            "kcmc": c.get("kcmc", ""),
                            "jxbmc": c.get("jxbmc", ""),
                            "kklxdm": c.get("kklxdm", kklxdm),
                            "yxzrs": enrolled,
                            "jxbrs": capacity,
                        }
                    return None
            return None

    async def _submit_one(self, course: dict, pp: dict) -> bool:
        """提交单门课程：加购 → 提交"""
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            base = self.config.base_url.rstrip("/")
            gnmkdm = self.config.gnmkdm

            # 加购
            add_url = urljoin(base, EP.XK_ADD_CART)
            add_url = f"{add_url}?gnmkdm={gnmkdm}"
            try:
                add_resp = await client.post(add_url, data={
                    "jxb_ids": course["do_jxb_id"],
                    "kch_id": course.get("kch_id", ""),
                    "kcmc": course.get("kcmc", ""),
                    "rwlx": pp.get("rwlx", "1"),
                    "rlkz": pp.get("rlkz", "0"),
                    "rlzlkz": pp.get("rlzlkz", "1"),
                    "xxkbj": "0", "qz": "0", "cxbj": "0",
                    "xkkz_id": pp.get("xkkz_id", pp.get("firstXkkzId", "")),
                    "njdm_id": pp.get("njdm_id", ""),
                    "zyh_id": pp.get("zyh_id", ""),
                    "kklxdm": course.get("kklxdm", "06"),
                    "xklc": pp.get("xklc", "1"),
                    "xkxnm": pp.get("xkxnm", ""),
                    "xkxqm": pp.get("xkxqm", ""),
                })
                add_data = add_resp.json()
            except Exception as e:
                log.debug(f"  捡漏加购异常: {e}")
                return False

            if str(add_data.get("flag", "")) != "1":
                log.debug(f"  捡漏加购失败: {add_data.get('msg', '')}")
                return False

            # 提交
            submit_url = urljoin(base, EP.XK_SUBMIT_CART)
            submit_url = f"{submit_url}?gnmkdm={gnmkdm}"
            try:
                submit_resp = await client.post(submit_url,
                                                data={"ids": course["do_jxb_id"]})
                submit_data = submit_resp.json()
            except Exception as e:
                log.debug(f"  捡漏提交异常: {e}")
                return False

            if isinstance(submit_data, list) and submit_data:
                item = submit_data[0]
            elif isinstance(submit_data, dict):
                item = submit_data
            else:
                return False

            if str(item.get("flag", "")) == "1":
                return True
            log.debug(f"  捡漏提交失败: {item.get('msg', '')}")
            return False
