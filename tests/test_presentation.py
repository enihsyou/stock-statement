"""保证文件输出完整并保留终端金融颜色语义。"""

import re
from io import StringIO

from rich.console import Console

from stock_statement.ledger import analyze
from stock_statement.presentation import render_report, report_rows, show_securities
from tests.test_ledger import entry


def report_entries():
    """生成一盈一亏的证券报告记录。"""
    return [
        entry("证券买入", "100", "-1000", security=0, serial="1"),
        entry("证券卖出", "-100", "1200", security=0, serial="2"),
        entry("证券买入", "100", "-1000", security=1, serial="3"),
        entry("证券卖出", "-100", "900", security=1, serial="4"),
    ]


def security_table(records):
    """只渲染证券汇总表，避免概览中的同名项目干扰列断言。"""
    report = analyze(records)
    rows, totals = report_rows(report)
    output = StringIO()
    console = Console(file=output, width=40, force_terminal=False)
    show_transfers = any(stock.transfer_count for stock in report.securities.values())
    show_securities(console, rows, totals, show_transfers)
    return output.getvalue()


def test_redirected_report_keeps_nonzero_security_columns():
    records = report_entries()
    text = security_table(records)
    for value in ("000000", "证券零零", "000001", "证券零一", "200.00", "-100.00",
                  "证券代码", "最新名称", "交易笔数", "剩余数量", "剩余成本"):
        assert value in text
    assert (
        "已隐藏列：手续费合计、印花税、佣金、经手费、证管费、结算费、过户费、"
        "其他费用、分红净额（所有明细均为 0）；托管净转入（无有效托管业务）；"
        "备注（无备注内容）。"
    ) in text
    assert "…" not in text
    assert "\x1b[" not in text


def test_report_keeps_nonzero_fee_and_distribution_columns():
    records = [
        entry("证券买入", "100", "-1001", fees={"佣金": "1"}, serial="1"),
        entry("股息入账", amount="10", serial="2"),
    ]
    text = security_table(records)
    for value in ("手续费合计", "佣金", "分红净额"):
        assert value in text
    assert (
        "已隐藏列：印花税、经手费、证管费、结算费、过户费、其他费用"
        "（所有明细均为 0）；托管净转入（无有效托管业务）；备注（无备注内容）。"
    ) in text


def test_terminal_profit_uses_red_and_loss_uses_green():
    records = report_entries()
    output = StringIO()
    console = Console(file=output, width=300, force_terminal=True, color_system="standard")
    render_report(console, records, analyze(records), files_count=1, duplicates=0)
    text = output.getvalue()
    assert re.search(r"\x1b\[[\d;]*31m\s*200\.00", text)
    assert re.search(r"\x1b\[[\d;]*32m\s*-100\.00", text)
