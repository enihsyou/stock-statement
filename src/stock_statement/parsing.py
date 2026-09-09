"""将固定宽度的单份流水解析成统一记录。"""

import re
from dataclasses import dataclass

from .attributes import (
    ATTRIBUTES,
    BUSINESS,
    CASH_AMOUNT,
    CODE,
    CURRENCY,
    DATE,
    FEES,
    NAME,
    PRICE,
    QUANTITY,
    SERIAL,
    TIME,
    TRADE_AMOUNT,
    ZERO,
    FinancialAttribute,
)
from .models import Entry, entry_sort_key
from .platforms import Platform, identify_platform


@dataclass(frozen=True)
class HeaderIndex:
    """保存一份文件中金融属性对应的字节切片并重复使用。"""

    platform: Platform
    slices: dict[FinancialAttribute, slice]

    @classmethod
    def from_columns(cls, platform: Platform, columns: list[tuple[str, int]]):
        """一次性查找平台别名并验证必需的金融属性。"""
        positions = {name: slice(start, columns[i + 1][1] if i + 1 < len(columns) else None)
                     for i, (name, start) in enumerate(columns)}
        if len(positions) != len(columns):
            raise ValueError("表头包含重复列名")
        slices = {}
        for attribute in ATTRIBUTES:
            match = next((positions[name] for name in platform.column_names(attribute)
                          if name in positions), None)
            if match is not None:
                slices[attribute] = match
            elif attribute.required:
                raise ValueError(f"缺少列：{attribute.name}")
        return cls(platform, slices)

    def read(self, line: str) -> dict:
        """按已绑定的索引提取并转换本行金融属性。"""
        raw = line.encode("gb18030")
        return {attribute: attribute.read(raw[self.slices[attribute]].decode("gb18030").strip())
                if attribute in self.slices else attribute.default for attribute in ATTRIBUTES}


def find_header(lines: list[str]) -> tuple[int, HeaderIndex]:
    """定位任意列顺序的表头并建立一次属性索引。"""
    for number, line in enumerate(lines):
        columns = [(m.group().decode("gb18030"), m.start())
                   for m in re.finditer(rb"\S+", line.encode("gb18030"))]
        if platform := identify_platform({name for name, _ in columns}):
            return number, HeaderIndex.from_columns(platform, columns)
    raise ValueError("未找到资金流水表头，请使用包含业务名称和费用明细的资金流水文件。")


def trade_values(values: dict, business: str):
    """保留成交原值，并在证券交易缺项时用数量、金额和价格补全。"""
    quantity, amount, price = (values[field] for field in (QUANTITY, TRADE_AMOUNT, PRICE))
    if business not in {"质押回购拆出", "拆出质押购回"}:
        if amount is None and quantity is not None and price:
            amount = abs(quantity * price)
        if quantity is None and amount is not None and price:
            quantity = abs(amount / price)
        if price is None and amount is not None and quantity:
            price = abs(amount / quantity)
    if quantity is None and (not values[CODE] or business in {"股息入账", "股息红利税补缴"}):
        quantity = ZERO
    if quantity is None:
        raise ValueError("缺少成交数量，且无法由成交金额和价格推算")
    if business in {"证券卖出", "拆出质押购回", "转托转出"}:
        quantity = -abs(quantity)
    return quantity, amount, price


def parse_entry(index: HeaderIndex, line: str, number: int, source: str) -> Entry:
    """将单行金融属性转换为可直接核算的流水记录。"""
    values = index.read(line)
    platform = index.platform
    business = platform.normalize_business(values[BUSINESS])
    quantity, amount, price = trade_values(values, business)
    day, time = values[DATE], values[TIME]
    if business == "拆出质押购回":
        day = values[platform.repayment_date]
        if day is None:
            raise ValueError(f"{platform.repayment_date.name}不能为空")
        if platform.repayment_date != DATE:
            time = ""
    if values[CURRENCY] != "人民币":
        raise ValueError("仅支持人民币，不能合计不同币种")
    return Entry(date=day, business=business, stock_code=values[CODE], stock_name=values[NAME],
                 quantity=quantity, amount=values[CASH_AMOUNT], serial=values[SERIAL],
                 line=number, fees={fee.name: values[fee] for fee in FEES}, platform=platform,
                 trade_amount=amount, trade_price=price, time=time, source=source)


def parse_entries(content: str, source: str = "") -> list[Entry]:
    """复用表头索引解析一份文件，遇到错误保留来源上下文后向上抛出。"""
    lines = content.splitlines()
    header_number, index = find_header(lines)
    entries = []
    for number, line in enumerate(lines[header_number + 1:], header_number + 2):
        if not line.strip() or set(line.strip()) <= {"-", "="}:
            continue
        try:
            entries.append(parse_entry(index, line, number, source))
        except (ValueError, ArithmeticError) as exc:
            raise ValueError(f"{source} 第 {number} 行无法解析：{exc}") from exc
    if not entries:
        raise ValueError(f"{source}：文件没有流水记录")
    return sorted(entries, key=entry_sort_key)
