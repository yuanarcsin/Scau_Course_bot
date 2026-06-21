# 华农选课助手

华南农业大学正方教务系统自动选课工具。

> 纯 HTTP 请求，无需浏览器。仅供学习参考，使用后果自负。

## 是什么

华农选课助手是一个基于 HTTP 请求的自动选课脚本，通过分析教务系统 API，在选课窗口开启瞬间以最低延迟完成课程提交。

### 核心设计：三模块解耦

| 模块 | 职责 | 时机 |
| --- | --- | --- |
| 预绑定器 | 搜索课程，缓存加密 ID 到本地 | 开抢前 5~10 分钟 |
| 抢课核心 | 读缓存，仅 2 步（加购→提交），跳过冗余检查 | 窗口归零瞬间 |
| 捡漏器 | 定时轮询空位，发现即提交 | 开抢后持续运行 |

相比传统方案：跳过"搜索课程"和"检查购物车"两步，同等多课程使用独立 Session 并发提交。

## 怎么用

### 安装

```bash
pip install -r requirements.txt
```

### 配置

```bash
cp course_bot/config.example.py course_bot/config.py
```

编辑 `course_bot/config.py`，填入：

- `student_id` / `password` — 教务系统账号
- `target_courses` — 目标课程的 `jxbbh`（教学班编号）和 `kklxdm`（01=专业选修课，06=板块课）
- `window_open` / `window_close` — 选课时间窗口

### 运行

#### 一键启动（推荐）

```bash
python run.py
```

自动启动后端服务并打开浏览器，在界面中完成登录→扫描→预绑定→抢课全流程。

#### 命令行模式

```bash
python -m course_bot.main prebind   # 预绑定：搜索课程并缓存 ID
python -m course_bot.main snipe     # 抢课核心：读缓存→乐观提交
python -m course_bot.main hunt      # 捡漏器：轮询空位（测试功能）
python -m course_bot.main serve     # 仅启动后端服务
```

#### 完整自动流程

```bash
python -m course_bot.main all --window "2026-06-18 12:29:55"
```

### 测试

```bash
python -m course_bot.mock_server --port 8080   # 启动模拟教务系统
python -m pytest course_bot/tests/ -v          # 运行测试
```

## 项目结构

```text
course_bot/
├── config.example.py   # 配置模板（复制为 config.py）
├── main.py             # CLI 入口
├── server.py           # FastAPI 后端服务
├── static/index.html   # 前端页面
├── prebind.py          # 预绑定器
├── sniper.py           # 抢课核心（乐观提交）
├── hunter.py           # 独立捡漏器
├── concurrent.py       # 多 Session 并发管理
├── client.py           # HTTP 客户端
├── course.py           # 旧版流程编排（保留兼容）
├── errors.py           # 错误码
├── logger.py           # 日志模块
├── PyRsa/              # RSA 加密
├── mock_server/        # 本地模拟教务系统（FastAPI）
└── tests/              # 测试用例
run.py                  # 一键启动脚本
```

## 借鉴

- [new-school-sdk](https://github.com/FarmerChillax/new-school-sdk) — RSA 加密、登录流程
- [PKUAutoElective](https://github.com/zhongxinghong/PKUAutoElective) — HTTP 客户端设计
- SCAU-course-tool — 购物车并发抢课模式

## 许可

AGPL-3.0
