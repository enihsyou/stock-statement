"""流水、证券持仓与账户报告的统一数据模型。"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .attributes import ZERO
from .platforms import Platform


@dataclass
class Entry:
    """记录统一后的交易属性及原文件定位信息。"""

    date: str
    business: str
    stock_code: str
    stock_name: str
    quantity: Decimal
    amount: Decimal
    serial: str
    line: int
    fees: dict[str, Decimal]
    platform: Platform
    trade_amount: Decimal | None
    trade_price: Decimal | None
    time: str = ""
    source: str = ""

    @property
    def location(self) -> str:
        """返回可追溯至原文件行的描述。"""
        return f"{self.source} 第 {self.line} 行"


def entry_sort_key(entry: Entry) -> tuple:
    """按核算日期、时间和原始编号稳定排列流水。"""
    serial = (0, int(entry.serial)) if entry.serial.isdecimal() else (1, entry.serial)
    return entry.date, entry.time, serial, entry.line


@dataclass
class Security:
    """累计单只证券的持仓成本、收益和费用。"""

    name: str = ""
    quantity: Decimal = ZERO
    cost: Decimal = ZERO
    realized: Decimal = ZERO
    distributions: Decimal = ZERO
    transfer_net: Decimal = ZERO
    transfer_count: int = 0
    transfer_known: bool = True
    repo_principal: Decimal = ZERO
    repo_quantity: Decimal = ZERO
    cost_known: bool = True
    cash_change: Decimal = ZERO
    trades: int = 0
    fees: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    @property
    def profit(self) -> Decimal:
        """返回包含分红及红利税的已实现净收益。"""
        return self.realized + self.distributions


@dataclass
class Report:
    """汇集证券核算、账户资金及尚未识别的业务。"""

    securities: dict[str, Security] = field(default_factory=dict)
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    interest: Decimal = ZERO
    adjustment: Decimal = ZERO
    security_turnover: Decimal = ZERO
    fees: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    unknown: list[Entry] = field(default_factory=list)
    businesses: Counter = field(default_factory=Counter)

