"""覆盖字段绑定、平台差异和文件编码。"""

from decimal import Decimal as D
from unittest.mock import patch

import pytest

from stock_statement import platforms
from stock_statement.attributes import BUSINESS, DATE, FEE_COLUMNS, PRICE
from stock_statement.loading import read_entries
from stock_statement.parsing import HeaderIndex, parse_entries
from stock_statement.platforms import Platform
from tests.factory import security_identity, write_statement


def row(**updates):
    """生成一条具有稳定匿名证券编号的买入流水。"""
    code, name = security_identity(1)
    values = {"成交日期": "2026-09-01", "业务名称": "证券买入", "证券代码": code,
              "证券名称": name, "币种": "人民币", "发生金额": "-1005",
              "成交数量": "100", "成交金额": "1000", "成交价格": "10", "流水号": "1"}
    return values | updates


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_file_encodings_preserve_names_and_leading_zeroes(tmp_path, encoding):
    values = row()
    path = write_statement(tmp_path / "流水.txt", list(values), [values], encoding)
    record, = read_entries(path)
    assert (record.stock_code, record.stock_name) == ("000001", "证券零一")
    assert record.date == "20260901"
    assert record.amount == D(-1005)
    assert record.quantity == D(100)


def test_reordered_alternative_columns_bind_once_per_file(tmp_path, monkeypatch):
    platform = Platform(
        "示例平台", frozenset({"记账日", "类别"}), (),
        columns={DATE: ("交易日", "记账日"), BUSINESS: ("类别",), PRICE: ("单价",)},
        businesses={"购入": "证券买入"},
    )
    monkeypatch.setattr(platforms, "PLATFORMS", (platform,))
    values = row()
    values["记账日"] = values.pop("成交日期")
    values["类别"] = "购入"
    del values["业务名称"]
    values["单价"] = values.pop("成交价格")
    del values["流水号"]
    path = write_statement(tmp_path / "别名.txt", list(reversed(values)), [values, values])
    with patch.object(HeaderIndex, "from_columns", wraps=HeaderIndex.from_columns) as bind:
        records = parse_entries(path.read_text(encoding="utf-8"))
    assert bind.call_count == 1
    assert len(records) == 2
    assert all(record.business == "证券买入" for record in records)
    assert all(record.trade_price == D(10) for record in records)
    assert records[0].serial == records[0].time == ""
    assert records[0].fees == dict.fromkeys(FEE_COLUMNS, D(0))


@pytest.mark.parametrize("missing", ["成交数量", "成交金额", "成交价格"])
def test_trade_fields_can_be_derived_from_other_two(tmp_path, missing):
    values = row()
    del values[missing]
    path = write_statement(tmp_path / "缺省.txt", list(values), [values])
    record, = read_entries(path)
    assert (record.quantity, record.trade_amount, record.trade_price) == (D(100), D(1000), D(10))


def test_cash_statement_can_omit_security_and_trade_columns(tmp_path):
    values = {"成交日期": "20260901", "业务名称": "银行转存", "币种": "人民币", "发生金额": "100"}
    path = write_statement(tmp_path / "现金.txt", list(values), [values])
    record, = read_entries(path)
    assert record.stock_code == record.stock_name == ""
    assert record.quantity == 0
    assert record.trade_amount is record.trade_price is None


@pytest.mark.parametrize("declared_amount, expected", [(None, None), ("0", D(0))])
def test_zero_transfer_price_does_not_invent_missing_value(tmp_path, declared_amount, expected):
    values = row(业务名称="转托转入", 发生金额="0", 成交价格="0")
    if declared_amount is None:
        del values["成交金额"]
    else:
        values["成交金额"] = declared_amount
    path = write_statement(tmp_path / "托管.txt", list(values), [values])
    record, = read_entries(path)
    assert record.quantity == D(100)
    assert record.trade_price == D(0)
    assert record.trade_amount == expected


def test_eastmoney_repayment_uses_settlement_date(tmp_path):
    values = {"交收日期": "2026-09-03", "发生日期": "2026-09-02", "发生时间": "15:00:00",
              "交易类别": "融券购回", "证券代码": "000001", "证券名称": "证券零一",
              "币种": "人民币", "发生金额": "1001", "成交数量": "10", "成交均价": "1.5"}
    path = write_statement(tmp_path / "交收.txt", list(values), [values])
    record, = read_entries(path)
    assert record.business == "拆出质押购回"
    assert record.date == "20260903"
    assert record.time == ""
    assert record.quantity == -10
    assert record.trade_amount is None


@pytest.mark.parametrize("index, expected", [(0, ("000000", "证券零零")),
                                             (12, ("000012", "证券一二")),
                                             (99, ("000099", "证券九九"))])
def test_security_identity(index, expected):
    assert security_identity(index) == expected
