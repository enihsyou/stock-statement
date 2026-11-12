"""用户配置目录与环境变量。"""

import os
from pathlib import Path


def config_directory() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / ".config")) / "stock-statement"


def load_environment() -> None:
    from dotenv import load_dotenv

    load_dotenv(config_directory() / ".env", override=False)
