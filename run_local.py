"""本机启动脚本：加载 .env 后运行程序。

这个项目本身只读环境变量（os.environ），不会自动读取 .env 文件。
README 建议用 godotenv（一个 Go 工具）先行加载。本脚本用 python-dotenv
代替 godotenv，方便在 Windows 上直接运行。

用法：
    .venv\\Scripts\\python.exe run_local.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    # 读取项目根目录下的 .env 注入到进程环境变量
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path, override=True)

    from bilibili_live_notification.__main__ import main as run_app

    asyncio.run(run_app())


if __name__ == "__main__":
    main()
