# 华农教务系统自动选课

华南农业大学正方教务系统 V9.0 自动选课脚本。通过 CDP（Chrome DevTools Protocol）控制 Edge 浏览器，在选课窗口开启瞬间自动提交购物车。

## 原理

1. 脚本通过 CDP 连接已登录的教务系统页面，复用浏览器登录态
2. 支持自动加购物车（`--cart`）或手动加好后仅自动提交
3. 实时倒计时等待选课窗口开启，到点立即模拟点击提交按钮
4. 断线自动重连，提交失败自动重试

## 环境要求

- Python 3.10+
- Microsoft Edge 浏览器
- Windows 系统

## 安装

```bash
pip install -r requirements.txt
```

## 配置

修改 [course_bot/config.py](course_bot/config.py) 中的配置项：

```python
# 账号（选填，手动登录则无需填写）
student_id: str = ""
password: str = ""

# 目标课程：tab_keyword 为侧边栏 tab 名，jxbbh 为教学班编号
target_courses: list = field(default_factory=lambda: [
    {"tab_keyword": "体育",   "jxbbh": "202620271-610023-001-乒乓球02"},
    {"tab_keyword": "大学英语", "jxbbh": "202620271-604792-005"},
])

# 选课时间窗口（建议比官方时间早 5 秒以补偿延迟）
window_open: str = "2026-06-18 12:29:55"
window_close: str = "2026-06-22 23:59:59"
```

> **如何找到 jxbbh（教学班编号）？** 在教务系统"自主选课"页面，搜索目标课程，课程名称旁边显示的那串编号即为 jxbbh。

## 使用

### 方式一：手动加购物车，脚本自动提交（推荐）

1. 手动打开 Edge，登录教务系统，进入"自主选课"页面，将课程加入购物车
2. 运行脚本：

```bash
python course_bot/main.py
```

### 方式二：脚本自动加购物车并提交

```bash
python course_bot/main.py --cart
```

### 自定义选课时间

```bash
python course_bot/main.py --cart --window "2026-06-18 12:29:55"
```

## 运行流程

1. 脚本自动检测或启动 Edge（调试模式），打开教务系统页面
2. 如果 Edge 未登录，手动登录后按 Enter 继续
3. 脚本连接教务页面，开始监控选课窗口
4. 实时显示倒计时，到点自动提交
5. 提交后显示结果汇总

## 注意事项

- 脚本不存储、不上传任何账号密码，所有操作在本地浏览器完成
- CDP 端口默认为 9222，确保不被其他程序占用
- 选课窗口时间建议设置比官方时间早 3-5 秒，补偿网络延迟
- 提交阶段按 `Ctrl+C` 可安全退出
- 仅支持 Edge 浏览器（Windows 自带）
