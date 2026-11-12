"""覆盖目录输入展开与跨文件合并。"""

from argparse import Namespace
from unittest.mock import Mock

import pytest

from stock_statement import cli
from stock_statement.loading import expand_paths, read_files
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
        "流水号": "1",
    }
    write_statement(tmp_path / "一.txt", list(values), [values, values])
    write_statement(tmp_path / "二.TXT", list(values), [values])
    render = Mock()
    monkeypatch.setattr(cli, "render_report", render)

    cli.earnings(Namespace(files=[tmp_path]))

    _, view = render.call_args.args
    assert view.metadata.entries_count == 2
    assert view.metadata.files_count == 2
    assert view.metadata.duplicates == 1


def test_china_merchants_trade_query_only_fills_unambiguous_time(tmp_path):
    normal = {
        "成交日期": "20261112", "业务名称": "证券买入", "证券代码": "000001",
        "成交数量": "100", "成交价格": "10", "发生金额": "-1001",
        "币种": "人民币", "合同编号": "42", "流水号": "7",
    }
    statement = write_statement(
        tmp_path / "流水.txt", list(normal) + ["股东代码"],
        [normal | {"股东代码": "深圳甲"}, normal | {"股东代码": "深圳乙"}],
    )
    history = {
        "成交日期": "20261112", "成交时间": "09:30:01", "买卖标志": "买入",
        "成交价格": "10", "成交数量": "100", "委托编号": "42",
        "证券代码": "000001", "股东代码": "深圳甲",
    }
    query = write_statement(
        tmp_path / "历史成交查询.txt", list(history),
        [history, history, history | {"股东代码": "深圳乙", "成交时间": "09:30:02"},
         history | {"股东代码": "深圳乙", "成交时间": "09:30:03"}],
    )

    entries, duplicates = read_files([query, statement, query])

    assert len(entries) == 2
    assert duplicates == 0
    assert {entry.shareholder_code: entry.time for entry in entries} == {
        "深圳甲": "09:30:01", "深圳乙": "",
    }


def test_partial_trade_times_keep_numeric_serial_order():
    from dataclasses import replace

    from stock_statement.ledger import analyze
    from stock_statement.models import entry_sort_key
    from tests.test_strategies import trades

    buy, sell = trades((100, 10), (-100, 11))
    buy = replace(buy, date="20261106", original_date="20261106", serial="8240", time="09:39:32")
    sell = replace(sell, date="20261106", original_date="20261106", serial="8245", time="")

    assert sorted([sell, buy], key=entry_sort_key) == [buy, sell]
    assert analyze([sell, buy]).securities["000000"].holding.quantity == 0
