# 华农教务系统自动选课 — 使用报告与教程

> 项目目录：`course_bot/` | 测试目录：`course_bot/tests/` | Mock：`course_bot/mock_server/`
> 重构日期：2026-06-18 | 基于 CDP 真实页面抓包修正

---

## 一、项目结构

```
course_bot/
├── __init__.py           # 包声明
├── main.py               # 入口
├── config.py             # 配置 + FieldMapping 字段映射
├── client.py             # HTTP 客户端（同步登录 + 异步提交 + 时间校准）
├── course.py             # 选课编排（登录→发现→等待→提交→NAT保活）
├── errors.py             # 17 个错误码枚举
├── logger.py             # stdout + 文件双输出
├── browser_bot.py        # [废弃] CDP 浏览器方案
├── PyRsa/                # RSA 加密模块
├── mock_server/          # 本地微型教务系统（FastAPI, 10 端点）
└── tests/                # 11 个测试用例

test_results/             # CDP 抓包数据 + API 分析 + 并发测试报告
frontend/                 # 前端方案 + tkinter 原型（gitignore）
logs/                     # 运行时日志
```

---

## 二、快速开始

### 2.1 配置 [config.py](course_bot/config.py)

```python
student_id = "你的学号"
password = "你的密码"

# jxbbh 从选课页面获取（课程名称旁的编号）
# kklxdm 固定 "06"（体育和大学英语板块课都是 06）
target_courses = [
    {"jxbbh": "202620271-610023-001-乒乓球02", "kklxdm": "06"},
    {"jxbbh": "202620271-604792-005",          "kklxdm": "06"},
]

window_open = "2026-06-18 12:29:55"
```

### 2.2 运行

```bash
python course_bot/main.py
python course_bot/main.py --window "2026-06-18 12:29:55"
python course_bot/main.py --no-find              # 跳过课程发现
```

---

## 三、执行流程

```
阶段一（同步）: 登录
  ├─ GET  login_slogin.html                  提取 CSRF
  ├─ GET  login_getPublicKey.html            获取 RSA 公钥
  ├─ POST login_slogin.html                  RSA 加密密码 → 登录
  └─ 时间校准（HTTP Date 头）

阶段二（同步）: 课程发现
  ├─ GET  zzxkyzb_cxZzxkYzbIndex.html        提取 247 隐藏参数
  ├─ POST zzxkyzb_cxZzxkYzbDisplay.html      获取选课时间窗口
  ├─ 时间校准（server_now 字段二次校准）
  └─ POST zzxkyzb_cxZzxkYzbPartDisplay.html  按 kklxdm 查课程 → 获取 do_jxb_id

阶段三（异步）: 等待窗口
  ├─ 实时倒计时
  ├─ NAT 保活（窗口前 5s 发轻量请求）
  └─ Session 保活（每 120s）

阶段四（异步）: 提交 —— 仅 3 个 API，零多余请求
  ├─ POST cxCheckZyZzxkYzbInCart.html        检查购物车
  ├─ POST zzxkyzbjk_xkBcZyZzxkYzbToCart.html 加入购物车（do_jxb_id）
  └─ POST zzxkyzbjk_xkBcZyZzxkYzbFromCart.html 购物车提交
```

---

## 四、配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `request_timeout` | 2.0s | 高峰期超 2s = 堵死，快速失败 |
| `retry_delay` | 0.2s | 重试间隔 |
| `submit_timeout` | 2.0s | 提交超时，快速失败快速重试 |
| `submit_retries` | 100 | 提交最大重试 |
| `submit_retry_delay` | 0.15s | 提交重试间隔 |
| `max_concurrent` | 4 | 并发连接数（基于实际测试） |
| `session_keepalive` | 120s | 等待期间保活间隔 |
| `nat_refresh_seconds` | 5.0s | 窗口前 N 秒发送 NAT 刷新请求 |

### 字段映射（FieldMapping）

服务器改字段名时只改 `config.py` 中 `FieldMapping` 即可，无需改业务代码：

```python
@dataclass
class FieldMapping:
    submit_id: str = "do_jxb_id"   # 加密课程 ID
    class_id: str = "jxb_id"       # 明文教学班 ID
    class_name: str = "jxbmc"      # 教学班编号（匹配用）
    course_name: str = "kcmc"      # 课程名
    type_code: str = "kklxdm"      # 课程类型代码
    # ... 共 12 个字段
```

---

## 五、时间校准

双重校准确保精确到秒：

1. **粗校准** — 登录响应 `Date` HTTP 头 → 计算服务器偏移
2. **精校准** — 页面 `server_now` 隐藏字段（服务器本地时间）

窗口时间 `xkkssj` 基于服务器时间，脚本自动转换为本地时间做倒计时。

---

## 六、错误码

| 码 | 含义 | 码 | 含义 |
|----|------|----|------|
| E1001 | CSRF 未找到 | E1301 | 窗口未开启 |
| E1002 | RSA 公钥失败 | E1302 | 时间冲突 |
| E1003 | 登录失败 | E1303 | 加购失败 |
| E1101 | 会话过期 | E1304 | 提交失败 |
| E1201 | 页面加载失败 | E1305 | 课程已选 |
| E1203 | 未找到目标课程 | E1306 | 已在购物车 |
| E1204 | 缺少 do_jxb_id | E1401 | 请求超时 |
| E1202 | 标签页未找到 | E1403 | 响应格式异常 |

---

## 七、本地测试

```bash
python -m course_bot.mock_server --port 8080    # 启动 Mock 服务器
python -m pytest course_bot/tests/test_bot.py -v # 11 个测试
```

测试覆盖：正常登录/错误学号/提取参数/匹配课程/完整流程/窗口未开/时间冲突/高负载。

可切换场景：`normal_scenario()` / `window_closed_scenario()` / `high_load_scenario()` / `conflict_scenario()`。

---

## 八、并发测试报告

对真实服务器（jwzf.scau.edu.cn）实测结果（[test_results/real_concurrency_report.md](test_results/real_concurrency_report.md)）：

| 并发 | 成功率 | 均延迟 | P99 | 吞吐/s |
|------|--------|--------|-----|--------|
| 1 | 100% | 0.041s | 0.055s | 24 |
| 4 | 100% | 0.045s | 0.059s | 78 |
| 8 | 100% | 0.061s | 0.096s | 93 |

结论：并发 1~8 均 100% 成功，P99 延迟 < 0.1s。保守取 **max_concurrent=4**。

---

## 九、故障排查

**登录失败** — 检查 base_url（内网 `10.42.100.1`，外网 `jwzf.scau.edu.cn`）

**课程匹配失败** — 确认 jxbbh 写对（含空格），kklxdm="06"

**提交阶段失败** — 查看日志错误码：E1301=窗口未开，E1303=加购失败，E1101=session 过期

**服务器超时** — request_timeout 设为 2s 已覆盖正常情况；若频繁超时说明网络不稳
