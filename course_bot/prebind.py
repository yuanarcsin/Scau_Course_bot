"""
预绑定器 —— 开抢前搜索课程并缓存 do_jxb_id 到本地文件。

职责:
  1. 登录 + 获取页面参数
  2. 遍历目标课程，通过 PartDisplay 搜索动态提取 do_jxb_id
  3. 写入 cache.json，供抢课核心直接读取

特点:
  - 动态字段提取：不依赖 FieldMapping 硬编码，从响应中反向匹配
  - 宽松超时（5s）+ 有限重试（3 次）
"""

import json, logging, time, traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from course_bot.client import Client
from course_bot.config import Config
from course_bot.errors import BotError

log = logging.getLogger("course_bot")


class Prebinder:
    """预绑定器：搜索并缓存目标课程的 do_jxb_id"""

    def __init__(self, config: Config):
        self.config = config
        self.client = Client(config)

    def run(self) -> dict[str, dict]:
        """执行预绑定，返回 {jxbbh: course_info}"""
        log.info("=" * 50)
        log.info("预绑定：搜索并缓存目标课程 do_jxb_id")
        log.info("=" * 50)

        # 登录 + 提取页面参数
        self.client.login()
        self.client.fetch_page_params()

        cache: dict[str, dict] = {}
        seen = set()

        for course_cfg in self.config.target_courses:
            jxbbh = course_cfg["jxbbh"]
            if jxbbh in seen:
                continue
            seen.add(jxbbh)

            kklxdm = course_cfg.get("kklxdm", "06")
            log.info(f"预绑定: {jxbbh} (kklxdm={kklxdm})")

            for retry in range(self.config.prebind_retries):
                try:
                    found = self._extract_course(jxbbh, kklxdm)
                    if found:
                        cache[jxbbh] = found
                        log.info(f"  [OK] {jxbbh} → {found.get('kcmc','?')} "
                                 f"(do_jxb_id={found['do_jxb_id'][:30]}...)")
                        break
                    else:
                        log.warning(f"  [未找到] {jxbbh} (重试 {retry+1})")
                except BotError as e:
                    log.error(f"  [E{e.code.value[0]}] {e}")
                except Exception as e:
                    log.error(f"  [异常] {e}")

                if retry < self.config.prebind_retries - 1:
                    time.sleep(0.5)
            else:
                log.warning(f"  [跳过] {jxbbh} 预绑定失败，将不在抢课阶段使用")

        # 写入缓存
        cache["_meta"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "student_id": self.config.student_id,
            "count": len([k for k in cache if not k.startswith("_")]),
        }

        cache_path = self._cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        log.info(f"缓存已写入: {cache_path} ({cache['_meta']['count']} 门)")

        self.client.close()
        return cache

    def _cache_path(self) -> Path:
        p = Path(self.config.cache_file)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        return p

    def _extract_course(self, jxbbh: str, kklxdm: str) -> dict | None:
        """执行 PartDisplay 搜索并动态提取字段"""
        # 直接用已有的 Client.find_target_course（已有动态提取能力）
        found = self.client.find_target_course(jxbbh, kklxdm)
        if found is None:
            return None

        # 补充缓存所需字段
        return {
            "do_jxb_id": found["do_jxb_id"],
            "jxb_id": found.get("jxb_id", ""),
            "kch_id": found.get("kch_id", ""),
            "kch": found.get("kch", ""),
            "kcmc": found.get("kcmc", ""),
            "jxbmc": found.get("jxbmc", jxbbh),
            "kklxdm": found.get("kklxdm", kklxdm),
            "jxbzls": found.get("jxbzls", "1"),
            "yxzrs": found.get("yxzrs", "?"),
            "jxbrs": found.get("jxbrs", "?"),
        }


def load_cache(config: Config) -> dict:
    """读取预绑定缓存，验证有效性"""
    p = Path(config.cache_file)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    if not p.exists():
        raise FileNotFoundError(f"缓存文件不存在: {p}，请先执行 prebind")

    with open(p, "r", encoding="utf-8") as f:
        cache = json.load(f)

    meta = cache.get("_meta", {})
    if meta.get("student_id") != config.student_id:
        log.warning("缓存学号与当前配置不匹配，建议重新预绑定")

    ts = meta.get("timestamp", "")
    log.info(f"加载缓存: {p} ({meta.get('count', 0)} 门, 生成于 {ts})")
    return cache
