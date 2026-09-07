"""将券商导出格式转换为统一字段与业务语义。"""

from dataclasses import dataclass, field
from decimal import Decimal

FEE_COLUMNS = ("印花税", "佣金", "经手费", "证管费", "结算费", "过户费", "其他费用")


def decimal(value: str) -> Decimal:
    number = Decimal(value.replace(",", ""))
    if not number.is_finite():
        raise ValueError("金额或数量不是有限数值")
    return number


@dataclass(frozen=True)
class Platform:
    name: str
    header_start: str
    business_column: str
    date_column: str
    price_column: str
    fee_columns: tuple[str, ...]
    repo_units: tuple[tuple[str, Decimal], ...]
    aliases: dict[str, str] = field(default_factory=dict)
    repayment_date_column: str = ""
    time_column: str = ""
    notes: tuple[str, ...] = ()

    def validate_columns(self, columns: set[str]) -> None:
        required = {self.business_column, self.date_column, "证券代码", "证券名称",
                    "发生金额", "流水号", "币种", *self.fee_columns}
        required.update(filter(None, (self.repayment_date_column, self.time_column)))
        if missing := required - columns:
            raise ValueError(f"缺少列：{'、'.join(sorted(missing))}")
        if len(columns & {"成交数量", "成交金额", self.price_column}) < 2:
            raise ValueError("成交数量、成交金额、成交价格至少需要两列")

    def normalize(self, values: dict[str, str]) -> dict[str, str]:
        result = {key: "" if value == "--" else value for key, value in values.items()}
        business = result[self.business_column]
        result["业务名称"] = self.aliases.get(business, business)
        result["成交日期"] = result[self.date_column].replace("-", "")
        result["发生时间"] = result.get(self.time_column, "")
        if self.repayment_date_column and result["业务名称"] == "拆出质押购回":
            result["成交日期"] = result[self.repayment_date_column].replace("-", "")
            result["发生时间"] = ""
        for name in FEE_COLUMNS:
            result.setdefault(name, "0")
        return result

    def repo_quantity_unit(self, code: str) -> Decimal:
        for prefix, unit in self.repo_units:
            if code.startswith(prefix):
                return unit
        raise ValueError(f"不支持的逆回购证券代码：{code}")

    def trade_values(self, values: dict[str, str]) -> tuple[Decimal, Decimal | None, Decimal | None]:
        quantity, amount, price = (
            decimal(values[name]) if values.get(name) else None
            for name in ("成交数量", "成交金额", self.price_column)
        )
        # 逆回购价格是年化利率，不能使用证券的金额换算关系。
        is_repo = values["业务名称"] in {"质押回购拆出", "拆出质押购回"}
        if not is_repo:
            if amount is None and quantity is not None and price is not None:
                amount = abs(quantity * price)
            if quantity is None and amount is not None and price:
                quantity = abs(amount / price)
            if price is None and amount is not None and quantity:
                price = abs(amount / quantity)
        if quantity is None:
            raise ValueError("缺少成交数量，且无法由成交金额和价格推算")
        if values["业务名称"] in {"证券卖出", "拆出质押购回", "转托转出"}:
            quantity = -abs(quantity)
        return quantity, amount, price


PLATFORMS = (
    Platform(
        name="招商证券", header_start="成交日期", business_column="业务名称",
        date_column="成交日期", price_column="成交价格", fee_columns=FEE_COLUMNS,
        repo_units=(("204", Decimal(1000)), ("1318", Decimal(100))),
    ),
    Platform(
        name="东方财富证券", header_start="交收日期", business_column="交易类别",
        date_column="发生日期", price_column="成交均价",
        fee_columns=("佣金", "其他费用", "印花税", "过户费"),
        repo_units=(("204", Decimal(100)), ("1318", Decimal(100))),
        aliases={"银行转证券": "银行转存", "证券转银行": "银行转取",
                 "融券回购": "质押回购拆出", "融券购回": "拆出质押购回",
                 "转托管入": "转托转入", "转托管出": "转托转出"},
        repayment_date_column="交收日期", time_column="发生时间",
        notes=("东方财富未单列经手费、证管费、结算费，显示 0 表示无独立披露金额；其他费用保留原值。",),
    ),
)


def identify_platform(columns: set[str]) -> Platform:
    for platform in PLATFORMS:
        if {platform.header_start, platform.business_column} <= columns:
            platform.validate_columns(columns)
            return platform
    raise ValueError("无法识别证券平台表头")
