"""定位本地前端，或下载与 Python 包同版本的 Release 资源。"""
import hashlib
import shutil
import tarfile
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from .config import config_directory

RELEASE_URL = "https://gh-proxy.org/https://github.com/enihsyou/stock-statement/releases/download"


def download_ui(directory: Path, package_version: str) -> Path:
    asset = f"stock-statement-ui-{package_version}.tar.gz"
    url = f"{RELEASE_URL}/v{package_version}/{asset}"
    directory.parent.mkdir(parents=True, exist_ok=True)
    # 同目录暂存；下载、校验和解压全部完成后才让版本目录可见。
    with TemporaryDirectory(dir=directory.parent) as temporary_name:
        temporary = Path(temporary_name)
        archive = temporary / asset
        with urlopen(url, timeout=30) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with urlopen(f"{url}.sha256", timeout=30) as response:
            expected = response.read().decode("ascii").strip()
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if expected != f"{digest}  {asset}":
            raise ValueError("前端资源 SHA-256 校验失败")

        unpacked = temporary / "ui"
        with tarfile.open(archive, "r:gz") as bundle:
            # Release 只含静态文件；拒绝链接和设备，路径限制交给 data filter。
            if any(not (member.isfile() or member.isdir()) for member in bundle.getmembers()):
                raise ValueError("前端资源包含非普通文件或目录")
            bundle.extractall(unpacked, filter="data")
        if not (unpacked / "index.html").is_file():
            raise ValueError("前端资源缺少 index.html")
        if directory.exists():
            shutil.rmtree(directory)
        unpacked.rename(directory)
    return directory


def find_ui_directory(directory: Path | None = None) -> Path | None:
    if directory is not None:
        directory = directory.expanduser().resolve()
        if not (directory / "index.html").is_file():
            raise ValueError(f"前端资源目录缺少 index.html：{directory}")
        return directory

    frontend = Path(__file__).resolve().parents[2] / "frontend"
    if (frontend / "package.json").is_file():
        return frontend / "dist" if (frontend / "dist" / "index.html").is_file() else None

    package_version = version("stock-statement")
    directory = config_directory() / "cache" / "ui" / package_version
    if (directory / "index.html").is_file():
        return directory
    try:
        return download_ui(directory, package_version)
    except (OSError, URLError, tarfile.TarError, ValueError) as exc:
        raise ValueError(
            f"无法获取 v{package_version} 前端资源：{exc}。"
            "请检查网络及同版本 GitHub Release，或用 --ui-dir 指定已解压的前端目录。"
        ) from exc
