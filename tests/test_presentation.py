"""保证文件输出完整并保留终端金融颜色语义。"""

import re
from io import StringIO

from rich.console import Console

from stock_statement.ledger import analyze
from stock_statement.presentation import render_report
from tests.test_ledger import entry


def report_entries():
    """生成一盈一亏的证券报告记录。"""
    return [
        entry("证券买入", "100", "-1000", security=0, serial="1"),
        entry("证券卖出", "-100", "1200", security=0, serial="2"),
        entry("证券买入", "100", "-1000", security=1, serial="3"),
        entry("证券卖出", "-100", "900", security=1, serial="4"),
    ]


def test_redirected_report_keeps_all_security_columns():
    records = report_entries()
    output = StringIO()
    console = Console(file=output, width=40, force_terminal=False)
    render_report(console, records, analyze(records), files_count=1, duplicates=0)
    text = output.getvalue()
    for value in ("000000", "证券零零", "000001", "证券零一", "200.00", "-100.00",
                  "证券代码", "最新名称", "印花税", "其他费用", "交易笔数", "剩余数量", "剩余成本"):
        assert value in text
    assert "…" not in text
    assert "\x1b[" not in text


def test_terminal_profit_uses_red_and_loss_uses_green():
    records = report_entries()
    output = StringIO()
    console = Console(file=output, width=300, force_terminal=True, color_system="standard")
    render_report(console, records, analyze(records), files_count=1, duplicates=0)
    text = output.getvalue()
    assert re.search(r"\x1b\[[\d;]*31m\s*200\.00", text)
    assert re.search(r"\x1b\[[\d;]*32m\s*-100\.00", text)
