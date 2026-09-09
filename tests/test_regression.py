"""使用精简匿名流水与手工金额基线覆盖完整核算流程。"""

from decimal import ROUND_HALF_UP, Decimal

import pytest

from stock_statement.ledger import analyze
from stock_statement.loading import read_entries, read_files
from tests.conftest import FIXTURES


def overview(report):
    """从业务结果汇总报告中的概览指标。"""
    stocks = list(report.securities.values())
    def sum_money(values):
        return sum((value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    for value in values), Decimal(0))

    transfer = sum_money(stock.transfer_net for stock in stocks)
    principal = sum_money(stock.repo_principal for stock in stocks)
    unassigned = {name: amount - sum((stock.fees[name] for stock in stocks), Decimal(0))
                  for name, amount in report.fees.items()}
    fee_rows = [stock.fees for stock in stocks]
    if any(unassigned.values()):
        fee_rows.append(unassigned)
    return {
        "银行流入": report.inflow,
        "银行流出": report.outflow,
        "现金净投入": report.inflow - report.outflow,
        "托管净转入": transfer,
        "账户净投入": report.inflow - report.outflow + transfer,
        "已实现净收益": sum_money(stock.profit for stock in stocks) + report.interest,
        "其中：资金利息": report.interest,
        "交易手续费合计": sum_money(sum(fees.values(), Decimal(0)) for fees in fee_rows),
        "其中：印花税": sum_money(fees.get("印花税", Decimal(0)) for fees in fee_rows),
        "剩余成本": sum_money(stock.cost + stock.repo_principal for stock in stocks),
        "其中：逆回购本金": principal,
        "逆回购交收记录": report.adjustment,
    }


@pytest.mark.parametrize("platform_name", ["招商证券", "东方财富证券", "combined"])
def test_core_statements_match_manual_overview(platform_name, baseline, anonymous_platforms):
    names = [p.name for p in anonymous_platforms] if platform_name == "combined" else [platform_name]
    entries, duplicates = read_files([FIXTURES / f"{name}.txt" for name in names])
    assert len(entries) == sum(baseline[name]["count"] for name in names)
    assert duplicates == 0
    report = analyze(entries)
    assert not report.unknown
    actual = overview(report)
    for field, expected in baseline[platform_name]["overview"].items():
        assert actual[field].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(expected), field


def test_reading_overlapping_files_keeps_maximum_occurrences(tmp_path, anonymous_platforms):
    source = FIXTURES / "招商证券.txt"
    lines = source.read_text(encoding="utf-8").splitlines()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("\n".join([lines[0], lines[1], lines[1]]) + "\n", encoding="utf-8")
    second.write_text("\n".join([lines[0], lines[1], lines[1], lines[1]]) + "\n", encoding="utf-8")
    entries, duplicates = read_files([first, second])
    assert len(entries) == 3
    assert duplicates == 2
    assert len(read_entries(first)) == 2
