"""配置目录中的环境变量加载。"""

from stock_statement.config import config_directory, load_environment


def test_load_environment_from_config_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("xq_a_token", raising=False)
    directory = tmp_path / "stock-statement"
    directory.mkdir()
    (directory / ".env").write_text('xq_a_token="example-token"\n', encoding="utf-8")

    assert config_directory() == directory
    load_environment()

    import os

    assert os.environ["xq_a_token"] == "example-token"


def test_existing_environment_overrides_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("xq_a_token", "environment-token")
    directory = tmp_path / "stock-statement"
    directory.mkdir()
    (directory / ".env").write_text("xq_a_token=file-token\n", encoding="utf-8")

    load_environment()

    import os

    assert os.environ["xq_a_token"] == "environment-token"
