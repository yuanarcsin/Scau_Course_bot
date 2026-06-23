# 华农选课助手

华南农业大学正方教务系统自动选课。纯 HTTP 请求，无需浏览器，选课窗口开启瞬间完成课程提交。

2026.06.23 更新。欢迎任何形式的贡献！

> **免责声明**：仅供学习参考，使用后果自负。

## 快速开始

```bash
git clone https://github.com/yuanarcsin/Scau_Course_bot.git
cd Scau_Course_bot
pip install httpx beautifulsoup4
```

编辑 `course_bot/config.py`，填入你的学号和密码：

```python
student_id: str = "<你的学号>"
password: str = "<你的密码>"
```

然后在浏览器中将目标课程添加到"我的选课意向"，修改配置中的 `target_courses` 和 `window_open`。

```bash
python run.py
```

## 用法

```bash
python run.py               # 完整流程：登录 → 等待窗口 → 批量提交 → 校验 → 持续重试
python run.py --now          # 跳过窗口等待，立即提交
python run.py --window "12:30:00"  # 覆盖窗口时间
```

## 配置说明

编辑 `course_bot/config.py`，所有可配置项：

```python
# ── 必填 ──
student_id: str = "<你的学号>"          # 教务系统学号
password: str = "<你的密码>"            # 教务系统密码

# ── 目标课程 ──
target_courses: list = [
    {"jxbbh": "<教学班编号>", "kklxdm": "01"},   # jxbbh 从选课页面获取
]
# kklxdm: 01=专业选修课  06=板块课(体育/英语)  10=通识选修课

# ── 选课窗口时间 ──
window_open: str = "2026-06-23 13:30:00"   # 脚本以 config 时间为准

# ── 提交速度 ──
snipe_timeout: float = 0.2      # 提交超时 (秒)
burst_count: int = 2            # 爆发并发数
lead_time: float = 0.0          # 提前启动 (秒)

# ── 登录选项 ──
preserve_browser_session: bool = True   # True = 不踢浏览器登录
```

> **注意**：`config.py` 已被 `.gitignore` 排除，不会被提交到 GitHub。请勿将包含真实学号密码的配置公开。

## 流程

| 阶段 | 做了什么 |
| --- | --- |
| 登录 | RSA 加密登录，默认保留浏览器 session |
| 匹配 | 查询选课意向 → 按 jxbbh 匹配 → 获取 xkgwcb_id |
| 等待 | 实时倒计时 + GET 保活 session |
| 提交 | 窗口到，Ajax 批量 SubmitCart（一次 POST 提交全部课程） |
| 校验 | 查询购物车/已选列表，确认课程入选 |
| 重试 | 前 10 轮极速无延迟，之后 0.05s 间隔持续重试 |

## 项目结构

```
course_bot/
├── config.py           # 配置（账号 + 课程 + 窗口）
├── sniper.py           # 抢课引擎（登录/匹配/保活/批量提交/校验/重试）
├── client.py           # HTTP 端点常量
├── main.py             # CLI 入口
├── errors.py           # 错误码
├── logger.py           # 日志模块
└── PyRsa/              # RSA 加密（JSBN 风格 PKCS#1 v1.5）
run.py                  # 启动脚本
```

## 参考项目

- [SCAU-course-tool](https://github.com/N0B0d7-rzddn/SCAU-course-tool) — 购物车并发抢课 Python 脚本，多线程 + 速率控制
- [SCAU-Course-Assistant](https://github.com/Weather174/SCAU-Course-Assistant) — 浏览器扩展，XHR 拦截 + 一键抢课面板
- [new-school-sdk](https://github.com/FarmerChillax/new-school-sdk) — RSA 加密与登录流程
- [PKUAutoElective](https://github.com/zhongxinghong/PKUAutoElective) — HTTP 客户端设计参考

## 许可

AGPL-3.0
