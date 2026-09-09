"""覆盖目录输入展开与跨文件合并。"""

from argparse import Namespace
from unittest.mock import Mock

import pytest

from stock_statement import cli
from stock_statement.loading import expand_paths
from tests.factory import write_statement


def test_expand_paths_preserves_arguments_and_sorts_direct_text_files(tmp_path):
    directory = tmp_path / "流水"
    directory.mkdir()
    for name in ("b.txt", "A.TXT", "说明.csv"):
        (directory / name).write_text("", encoding="utf-8")
    nested = directory / "子目录.txt"
    nested.mkdir()
    (nested / "隐藏.txt").write_text("", encoding="utf-8")
    explicit = tmp_path / "单份.txt"
    explicit.write_text("", encoding="utf-8")

    assert expand_paths([explicit, directory]) == [
        explicit, directory / "A.TXT", directory / "b.txt",
    ]


def test_directory_without_text_files_raises(tmp_path):
    (tmp_path / "说明.md").write_text("无流水", encoding="utf-8")
    with pytest.raises(ValueError, match="目录内没有文本文件"):
        expand_paths([tmp_path])


def test_directory_report_counts_files_and_preserves_duplicate_occurrences(tmp_path, monkeypatch):
    values = {
        "成交日期": "20260901", "业务名称": "银行转存",
        "币种": "人民币", "发生金额": "100",
    }
    write_statement(tmp_path / "一.txt", list(values), [values, values])
    write_statement(tmp_path / "二.TXT", list(values), [values])
    render = Mock(return_value=False)
    monkeypatch.setattr(cli, "render_report", render)

    cli.earnings(Namespace(files=[tmp_path]))

    _, entries, _, file_count, duplicates = render.call_args.args
    assert len(entries) == 2
    assert file_count == 2
    assert duplicates == 1
