"""
python -m course_bot.mock_server 启动本机模拟教务系统。
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="微教务系统 Mock Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    print(f"Mock Server: http://{args.host}:{args.port}")
    print("场景: 默认（所有 API 正常）")
    uvicorn.run(
        "course_bot.mock_server.server:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
