"""生成覆盖两个平台核心核算流程的少量确定性流水。"""

import json
from pathlib import Path

from tests.factory import security_identity, write_statement

DESTINATION = Path(__file__).parent / "fixtures"
COLUMNS = ["成交日期", "交收日期", "业务名称", "证券代码", "证券名称",
           "成交数量", "成交金额", "发生金额", "币种", "流水号", "佣金", "印花税"]


def row(serial, business, amount, *, security=None, quantity="0",
        trade_amount="", commission="0", stamp="0"):
    """构造日期有序且证券编号与名称对应的匿名流水。"""
    code, name = security_identity(security) if security is not None else ("", "")
    return {"成交日期": "20260901", "交收日期": "20260901", "业务名称": business,
            "证券代码": code, "证券名称": name, "成交数量": quantity,
            "成交金额": trade_amount, "发生金额": amount, "币种": "人民币",
            "流水号": str(serial), "佣金": commission, "印花税": stamp}


def statement_rows():
    """提供买卖、托管、逆回购及现金业务的最小完整历史。"""
    return {
        "招商证券": [
            row(1, "银行转存", "10000"),
            row(2, "证券买入", "-1005", security=0, quantity="100", commission="5"),
            row(3, "证券买入", "-2005", security=0, quantity="100", commission="5"),
            row(4, "证券卖出", "994", security=0, quantity="-50", commission="5", stamp="1"),
            row(5, "股息入账", "100", security=0),
            row(6, "股息红利税补缴", "-20", security=0),
            row(7, "转托转入", "0", security=1, quantity="100", trade_amount="1000"),
            row(8, "转托转出", "0", security=1, quantity="-40", trade_amount="600"),
            row(9, "质押回购拆出", "-1001", security=2, quantity="1", commission="1"),
            row(10, "拆出质押购回", "1003", security=2, quantity="-1"),
            row(11, "交收资金修正", "1003"),
            row(12, "利息归本", "1.25"),
            row(13, "银行转取", "-2000"),
        ],
        "东方财富证券": [
            row(1, "银行转证券", "5000"),
            row(2, "证券买入", "-2005", security=0, quantity="100", commission="5"),
            row(3, "证券卖出", "2193", security=0, quantity="-100", commission="5", stamp="2"),
            row(4, "融券回购", "-1001", security=2, quantity="10", commission="1"),
            row(5, "融券购回", "1004", security=2, quantity="-10"),
            row(6, "融券回购", "-1001", security=2, quantity="10", commission="1"),
            row(7, "利息归本", "2"),
            row(8, "证券转银行", "-500"),
        ],
    }


def expected_overview(inflow, outflow, transfer, investment, profit, interest,
                      fees, stamp, cost, principal, adjustment, net_cash):
    """将手工推导的金额对应到报告概览字段。"""
    return dict(zip(
        ("银行流入", "银行流出", "托管净转入", "账户净投入", "已实现净收益",
         "其中：资金利息", "交易手续费合计", "其中：印花税", "剩余成本",
         "其中：逆回购本金", "逆回购交收记录", "现金净投入"),
        (inflow, outflow, transfer, investment, profit, interest, fees, stamp,
         cost, principal, adjustment, net_cash), strict=True,
    ))


def main():
    """仅向 tests/fixtures 写入 UTF-8 流水和手工金额基线。"""
    for platform, rows in statement_rows().items():
        aliases = {"成交日期": "发生日期", "业务名称": "交易类别"} if platform == "东方财富证券" else {}
        columns = [aliases.get(name, name) for name in COLUMNS]
        mapped = [{aliases.get(name, name): value for name, value in record.items()}
                  for record in rows]
        write_statement(DESTINATION / f"{platform}.txt", columns, mapped)

    # 金额推导见 tests/README.md，不调用解析或核算代码计算预期。
    expected = {
        "招商证券": {
            "count": 13, "repo_units": {"000002": 1000},
            "overview": expected_overview("10000", "2000", "400", "8400", "524.75",
                                          "1.25", "17", "1", "2857.50", "0", "1003", "8000"),
        },
        "东方财富证券": {
            "count": 8, "repo_units": {"000002": 100},
            "overview": expected_overview("5000", "500", "0", "4500", "192",
                                          "2", "14", "2", "1000", "1000", "0", "4500"),
        },
        "combined": {
            "overview": expected_overview("15000", "2500", "400", "12900", "716.75",
                                          "3.25", "31", "3", "3857.50", "1000", "1003", "12500"),
        },
    }
    (DESTINATION / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
