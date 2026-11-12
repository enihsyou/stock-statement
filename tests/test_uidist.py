"""独立前端资源的版本匹配、校验与安全解压。"""
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from stock_statement import uidist


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setattr(uidist, "__file__", str(tmp_path / "installed/stock_statement/uidist.py"))
    monkeypatch.setattr(uidist, "config_directory", lambda: tmp_path / "config")
    monkeypatch.setattr(uidist, "version", lambda _: "0.1.0")
    monkeypatch.setattr(uidist, "RELEASE_URL", (tmp_path / "releases").as_uri())
    directory = tmp_path / "releases/v0.1.0"
    directory.mkdir(parents=True)
    return directory / "stock-statement-ui-0.1.0.tar.gz"


def write_release(archive, files):
    with tarfile.open(archive, "w:gz") as bundle:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(f"{archive}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")


def test_download_and_offline_cache(release):
    write_release(release, {"index.html": b"hello", "assets/app.js": b"app"})
    directory = uidist.find_ui_directory()
    assert directory.name == "0.1.0"
    assert (directory / "index.html").read_bytes() == b"hello"
    assert (directory / "assets/app.js").read_bytes() == b"app"
    release.unlink()
    assert uidist.find_ui_directory() == directory


def test_different_version_does_not_reuse_cache(release, monkeypatch):
    write_release(release, {"index.html": b"hello"})
    uidist.find_ui_directory()
    monkeypatch.setattr(uidist, "version", lambda _: "0.2.0")
    with pytest.raises(ValueError, match="无法获取 v0.2.0"):
        uidist.find_ui_directory()


def test_checksum_mismatch_leaves_no_cached_ui(release):
    write_release(release, {"index.html": b"hello"})
    release.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        uidist.find_ui_directory()
    cache = uidist.config_directory() / "cache/ui"
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize("files", [
    {"../escaped": b"bad", "index.html": b"hello"},
    {"assets/app.js": b"missing index"},
])
def test_invalid_archive_is_not_cached(release, files):
    write_release(release, files)
    with pytest.raises(ValueError, match="无法获取"):
        uidist.find_ui_directory()
    cache = uidist.config_directory() / "cache/ui"
    assert list(cache.iterdir()) == []
    assert not (cache / "escaped").exists()


def test_symlink_is_rejected(release):
    with tarfile.open(release, "w:gz") as bundle:
        member = tarfile.TarInfo("index.html")
        member.type = tarfile.SYMTYPE
        member.linkname = "other.html"
        bundle.addfile(member)
    digest = hashlib.sha256(release.read_bytes()).hexdigest()
    Path(f"{release}.sha256").write_text(f"{digest}  {release.name}\n", encoding="ascii")
    with pytest.raises(ValueError, match="非普通文件"):
        uidist.find_ui_directory()


def test_explicit_directory_takes_priority(release, tmp_path):
    (tmp_path / "index.html").write_text("local", encoding="utf-8")
    assert uidist.find_ui_directory(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError, match="缺少 index.html"):
        uidist.find_ui_directory(tmp_path / "missing")


def test_source_checkout_without_build_stays_offline(release, monkeypatch, tmp_path):
    monkeypatch.setattr(uidist, "__file__", str(tmp_path / "src/stock_statement/uidist.py"))
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    assert uidist.find_ui_directory() is None
    (frontend / "dist").mkdir()
    (frontend / "dist/index.html").write_text("source", encoding="utf-8")
    assert uidist.find_ui_directory() == frontend / "dist"
