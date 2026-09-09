"""与来源平台无关的金融属性及其文本转换规则。"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

ZERO = Decimal(0)


def decimal(value: str) -> Decimal:
    """解析带千位分隔符的有限金额或数量。"""
    number = Decimal(value.replace(",", ""))
    if not number.is_finite():
        raise ValueError("金额或数量不是有限数值")
    return number


def trading_date(value: str) -> str:
    """将券商日期转换为有效的八位核算日期。"""
    value = value.replace("-", "")
    if not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError(f"日期应为 YYYYMMDD：{value}")
    date.fromisoformat(value)
    return value


@dataclass(frozen=True)
class FinancialAttribute[T]:
    """定义一个稳定业务概念的名称、转换器与缺省值。"""

    name: str
    parse: Callable[[str], T] | None = None
    default: T | None = None
    required: bool = False
    style: str = "cyan"

    def read(self, value: str) -> T | None:
        """转换单元格，保留未披露值与数值零的区别。"""
        if value and value != "--":
            return self.parse(value) if self.parse else cast("T", value)
        if self.required:
            raise ValueError(f"{self.name}不能为空")
        return self.default


DATE = FinancialAttribute("成交日期", trading_date, required=True)
SETTLEMENT_DATE = FinancialAttribute("交收日期", trading_date)
TIME = FinancialAttribute("发生时间", default="")
BUSINESS = FinancialAttribute("业务名称", required=True)
CODE = FinancialAttribute("证券代码", default="")
NAME = FinancialAttribute("证券名称", default="")
CURRENCY = FinancialAttribute("币种", required=True)
SERIAL = FinancialAttribute("流水号", default="")
QUANTITY = FinancialAttribute("成交数量", decimal, style="blue")
TRADE_AMOUNT = FinancialAttribute("成交金额", decimal, style="blue")
PRICE = FinancialAttribute("成交价格", decimal, style="blue")
CASH_AMOUNT = FinancialAttribute("发生金额", decimal, required=True, style="magenta")
FEES = tuple(FinancialAttribute(name, decimal, ZERO, style="yellow")
             for name in ("印花税", "佣金", "经手费", "证管费", "结算费", "过户费", "其他费用"))
FEE_COLUMNS = tuple(attribute.name for attribute in FEES)
ATTRIBUTES = (DATE, SETTLEMENT_DATE, TIME, BUSINESS, CODE, NAME, CURRENCY,
              SERIAL, QUANTITY, TRADE_AMOUNT, PRICE, CASH_AMOUNT, *FEES)
