"""覆盖成本、托管、逆回购和现金的正常核算路径。"""

from dataclasses import replace
from decimal import Decimal as D

from stock_statement.ledger import analyze
from stock_statement.models import Entry
from stock_statement.platforms import PLATFORMS
from tests.factory import security_identity


def entry(business, quantity="0", amount="0", *, security=0, trade_amount=None,
          fees=None, platform=None, serial="1", day="20260901"):
    """构造仅含业务所需内容的匿名流水。"""
    code, name = security_identity(security) if security is not None else ("", "")
    return Entry(
        date=day, business=business, stock_code=code, stock_name=name,
        quantity=D(quantity), amount=D(amount), serial=serial, line=1,
        fees={name: D(value) for name, value in (fees or {}).items()},
        platform=platform or PLATFORMS[0],
        trade_amount=None if trade_amount is None else D(trade_amount),
        trade_price=None,
    )


def test_moving_average_cost_dividends_and_tax():
    report = analyze([
        entry("证券买入", "100", "-1005", serial="1", fees={"佣金": "5"}),
        entry("证券买入", "100", "-2005", serial="2", fees={"佣金": "5"}),
        entry("证券卖出", "-50", "995", serial="3", fees={"佣金": "5"}),
        entry("股息入账", amount="100", serial="4"),
        entry("股息红利税补缴", amount="-20", serial="5"),
    ])
    stock = report.securities["000000"]
    assert stock.quantity == D(150)
    assert stock.cost == D("2257.50")
    assert stock.realized == D("242.50")
    assert stock.distributions == D(80)
    assert stock.profit == D("322.50")
    assert stock.trades == 3
    assert report.fees["佣金"] == D(15)
    assert not report.unknown


def test_full_sale_removes_all_cost():
    report = analyze([
        entry("证券买入", "3", "-100", serial="1"),
        entry("证券卖出", "-1", "40", serial="2"),
        entry("证券卖出", "-2", "80", serial="3"),
    ])
    stock = report.securities["000000"]
    assert stock.quantity == stock.cost == 0
    assert stock.realized == D(20)


def test_transfer_in_and_out_track_value_and_cost():
    report = analyze([
        entry("转托转入", "100", trade_amount="1000", serial="1"),
        entry("转托转出", "-40", trade_amount="600", serial="2"),
    ])
    stock = report.securities["000000"]
    assert stock.quantity == D(60)
    assert stock.cost == D(600)
    assert stock.transfer_net == D(400)
    assert stock.realized == D(200)
    assert stock.transfer_count == 2
    assert stock.cost_known and stock.transfer_known


def test_transfer_without_value_marks_cost_unknown():
    stock = analyze([entry("转托转入", "100")]).securities["000000"]
    assert stock.quantity == 100
    assert not stock.cost_known
    assert not stock.transfer_known


def test_repo_principal_interest_and_cash_settlement():
    platform = replace(PLATFORMS[0], repo_units=(("0000", D(100)),))
    report = analyze([
        entry("质押回购拆出", "10", "-1001", platform=platform,
              fees={"佣金": "1"}, serial="1"),
        entry("拆出质押购回", "-10", "1003", platform=platform, serial="2"),
        entry("交收资金修正", amount="1003", security=None, platform=platform, serial="3"),
    ])
    stock = report.securities["000000"]
    assert stock.repo_principal == stock.repo_quantity == 0
    assert stock.realized == D(2)
    assert report.adjustment == D(1003)
    assert not report.unknown


def test_cash_and_zero_value_administrative_records():
    report = analyze([
        entry("银行转存", amount="1000", security=None),
        entry("银行转取", amount="-200", security=None),
        entry("利息归本", amount="1.25", security=None),
        entry("转存管转出", security=None),
        entry("指定交易", security=None),
        entry("撤销指定", security=None),
        entry("指定入账", "100"),
        entry("撤指转出", "100"),
    ])
    assert report.inflow == 1000
    assert report.outflow == 200
    assert report.interest == D("1.25")
    assert not report.securities
    assert not report.unknown


def test_platform_costs_are_calculated_before_combining():
    report = analyze([
        entry("证券买入", "100", "-1000", platform=PLATFORMS[0], serial="1"),
        entry("证券买入", "100", "-2000", platform=PLATFORMS[1], serial="2"),
        entry("证券卖出", "-100", "1500", platform=PLATFORMS[0], serial="3"),
    ])
    stock = report.securities["000000"]
    assert stock.quantity == 100
    assert stock.cost == 2000
    assert stock.realized == 500


def test_registration_with_fees_does_not_cancel_free_counterpart():
    records = [
        entry("指定入账", "100", serial="1"),
        entry("撤指转出", "100", fees={"佣金": "1"}, serial="2"),
    ]
    report = analyze(records)
    assert report.unknown == records
    assert report.fees["佣金"] == D(1)


def test_unknown_business_remains_available_for_review():
    record = entry("未识别业务", amount="10", security=None)
    assert analyze([record]).unknown == [record]
