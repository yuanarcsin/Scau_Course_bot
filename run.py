"""
选课助手启动器 —— 自动启动后端 + 打开浏览器。

用法:
    python run.py              # 默认端口 8742
    python run.py --port 8080  # 指定端口
"""

import sys, time, webbrowser, threading, asyncio
from pathlib import Path

_project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))


def open_browser(port: int):
    """等待服务就绪后打开浏览器"""
    import urllib.request
    url = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    webbrowser.open(url)
    print(f"浏览器已打开: {url}")


async def start_server(port: int):
    from course_bot.server import create_app
    import uvicorn

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    print(f"后端服务启动: http://127.0.0.1:{port}")
    print("按 Ctrl+C 停止")

    await server.serve()


def main():
    port = 8742
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    # 延迟打开浏览器
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    try:
        asyncio.run(start_server(port))
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
