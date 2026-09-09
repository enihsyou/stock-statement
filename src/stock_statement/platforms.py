"""声明平台字段别名与业务差异，供文件解析和核算使用。"""

from dataclasses import dataclass, field
from decimal import Decimal

from .attributes import BUSINESS, DATE, PRICE, SETTLEMENT_DATE, FinancialAttribute


@dataclass(frozen=True)
class Platform:
    """用字段映射和业务规则描述一种券商导出格式。"""

    name: str
    identifiers: frozenset[str]
    repo_units: tuple[tuple[str, Decimal], ...]
    columns: dict[FinancialAttribute, tuple[str, ...]] = field(default_factory=dict)
    businesses: dict[str, str] = field(default_factory=dict)
    repayment_date: FinancialAttribute = DATE
    notes: tuple[str, ...] = ()

    def column_names(self, attribute: FinancialAttribute) -> tuple[str, ...]:
        """返回当前平台中某个金融属性可匹配的列名。"""
        return self.columns.get(attribute, (attribute.name,))

    def normalize_business(self, business: str) -> str:
        """将平台业务名称映射到统一核算业务。"""
        return self.businesses.get(business, business)

    def repo_quantity_unit(self, code: str) -> Decimal:
        """返回逆回购每个原始数量单位所代表的本金。"""
        for prefix, unit in self.repo_units:
            if code.startswith(prefix):
                return unit
        raise ValueError(f"不支持的逆回购证券代码：{code}")


PLATFORMS = (
    Platform(
        name="招商证券", identifiers=frozenset({"成交日期", "业务名称"}),
        repo_units=(("204", Decimal(1000)), ("1318", Decimal(100))),
    ),
    Platform(
        name="东方财富证券", identifiers=frozenset({"发生日期", "交易类别"}),
        columns={DATE: ("发生日期",), BUSINESS: ("交易类别",), PRICE: ("成交均价",)},
        repo_units=(("204", Decimal(100)), ("1318", Decimal(100))),
        businesses={"银行转证券": "银行转存", "证券转银行": "银行转取",
                    "融券回购": "质押回购拆出", "融券购回": "拆出质押购回",
                    "转托管入": "转托转入", "转托管出": "转托转出"},
        repayment_date=SETTLEMENT_DATE,
        notes=("东方财富未单列经手费、证管费、结算费，显示 0 表示无独立披露金额；其他费用保留原值。",),
    ),
)


def identify_platform(columns: set[str]) -> Platform | None:
    """从表头识别唯一平台，非表头行返回空值。"""
    matches = [platform for platform in PLATFORMS if platform.identifiers <= columns]
    if len(matches) > 1:
        raise ValueError("表头匹配多个平台")
    return matches[0] if matches else None
