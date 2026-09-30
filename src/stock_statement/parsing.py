"""将固定宽度的单份流水解析成统一记录。"""

import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

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
    TRANSACTION_ID,
    FinancialAttribute,
)
from .errors import ParsingError
from .models import Entry, entry_sort_key
from .platforms import Platform, identify_platform


@dataclass(frozen=True)
class ColumnBinding:
    """关联实际列名与固定宽度字节切片。"""

    name: str
    span: slice


@dataclass(frozen=True)
class AttributeValues:
    """在异构字段映射边界保留属性的返回类型。"""

    values: dict[FinancialAttribute[Any], Any]
    source: str
    line: int

    def get[T](self, attribute: FinancialAttribute[T]) -> T | None:
        """读取字段，未披露时保留 None。"""
        return cast(T | None, self.values[attribute])

    def require[T](self, attribute: FinancialAttribute[T]) -> T:
        """读取具有明确值或明确缺省值的字段。"""
        value = self.get(attribute)
        if value is None:
            raise ParsingError("不能为空", source=self.source, line=self.line, field=attribute.name)
        return value


@dataclass(frozen=True)
class HeaderIndex:
    """保存一份文件中金融属性对应的字节切片并重复使用。"""

    platform: Platform
    bindings: dict[FinancialAttribute[Any], ColumnBinding]

    @classmethod
    def from_columns(
        cls, platform: Platform, columns: list[tuple[str, int]], source: str = "", number: int = 1,
    ) -> HeaderIndex:
        """一次性查找平台别名并验证必需的金融属性。"""
        positions = {name: ColumnBinding(name, slice(start, columns[i + 1][1] if i + 1 < len(columns) else None))
                     for i, (name, start) in enumerate(columns)}
        if len(positions) != len(columns):
            duplicates = [name for name, count in Counter(name for name, _ in columns).items() if count > 1]
            raise ParsingError("表头包含重复列名", source=source, line=number, field=" / ".join(duplicates))
        bindings = {}
        for attribute in ATTRIBUTES:
            match = next((positions[name] for name in platform.column_names(attribute)
                          if name in positions), None)
            if match is not None:
                bindings[attribute] = match
            elif attribute.required:
                raise ParsingError("缺少必需列", source=source, line=number,
                                   field=" / ".join(platform.column_names(attribute)))
        return cls(platform, bindings)

    def read(self, line: str, source: str, number: int) -> AttributeValues:
        """按已绑定的索引提取并转换本行金融属性。"""
        try:
            raw = line.encode("gb18030")
        except UnicodeError as exc:
            raise ParsingError("文本无法转换为固定列宽编码", source=source, line=number) from exc
        values = {
            attribute: self.read_attribute(attribute, raw, source, number)
            for attribute in ATTRIBUTES
        }
        return AttributeValues(values, source, number)

    def read_attribute[T](
        self, attribute: FinancialAttribute[T], raw: bytes, source: str, number: int,
    ) -> T | None:
        """转换单个字段，失败时指出券商的实际列名。"""
        binding = self.bindings.get(attribute)
        if binding is None:
            return attribute.default
        try:
            text = raw[binding.span].decode("gb18030").strip()
        except UnicodeError as exc:
            raise ParsingError("单元格字节无法按 GB18030 解码", source=source, line=number,
                               field=binding.name) from exc
        try:
            return attribute.read(text)
        except (ValueError, ArithmeticError) as exc:
            raise ParsingError(f"无法解析值 {text!r}：{exc}", source=source, line=number,
                               field=binding.name) from exc


def find_header(lines: list[str], source: str = "") -> tuple[int, HeaderIndex]:
    """定位任意列顺序的表头并建立一次属性索引。"""
    for number, line in enumerate(lines):
        try:
            columns = [(m.group().decode("gb18030"), m.start())
                       for m in re.finditer(rb"\S+", line.encode("gb18030"))]
            platform = identify_platform({name for name, _ in columns})
        except ValueError as exc:
            raise ParsingError(str(exc), source=source, line=number + 1) from exc
        if platform is not None:
            return number, HeaderIndex.from_columns(platform, columns, source, number + 1)
    raise ParsingError("未找到资金流水表头，请使用包含业务名称和费用明细的资金流水文件。", source=source)


def trade_values(values: AttributeValues, business: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """保留成交原值，并在证券交易缺项时用数量、金额和价格补全。"""
    quantity, amount, price = (values.get(attribute) for attribute in (QUANTITY, TRADE_AMOUNT, PRICE))
    if business in {"证券买入", "证券卖出", "转托转出", "转托转入"}:
        if amount is None and quantity is not None and price:
            amount = abs(quantity * price)
        if quantity is None and amount is not None and price:
            quantity = abs(amount / price)
        if price is None and amount is not None and quantity:
            price = abs(amount / quantity)
    if quantity is not None and business in {"证券卖出", "拆出质押购回", "转托转出"}:
        quantity = -abs(quantity)
    return quantity, amount, price


def parse_entry(index: HeaderIndex, line: str, number: int, source: str) -> Entry:
    """将单行金融属性转换为可直接核算的流水记录。"""
    values = index.read(line, source, number)
    platform = index.platform
    business = platform.normalize_business(values.require(BUSINESS))
    try:
        quantity, amount, price = trade_values(values, business)
    except ArithmeticError as exc:
        raise ParsingError(f"成交字段推算失败：{exc}", source=source, line=number,
                           field="成交数量 / 成交金额 / 成交价格", business=business) from exc
    day, time = values.require(DATE), values.require(TIME)
    if business == "拆出质押购回":
        day = values.get(platform.repayment_date)
        if day is None:
            names = platform.column_names(platform.repayment_date)
            raise ParsingError("购回核算日期不能为空", source=source, line=number,
                               field=names[0] if names else platform.repayment_date.name, business=business)
        if platform.repayment_date != DATE:
            time = ""
    if values.require(CURRENCY) != "人民币":
        raise ParsingError("仅支持人民币，不能合计不同币种", source=source, line=number, field=CURRENCY.name)
    return Entry(date=day, original_date=values.require(DATE), business=business,
                 stock_code=values.require(CODE), stock_name=values.require(NAME),
                 quantity=quantity, amount=values.require(CASH_AMOUNT), serial=values.require(SERIAL),
                 transaction_id=values.require(TRANSACTION_ID),
                 line=number, fees={fee.name: values.require(fee) for fee in FEES}, platform=platform,
                 trade_amount=amount, trade_price=price, time=time, source=source)


def parse_entries(content: str, source: str = "") -> list[Entry]:
    """复用表头索引解析一份文件，遇到错误保留来源上下文后向上抛出。"""
    lines = content.splitlines()
    header_number, index = find_header(lines, source)
    entries = []
    for number, line in enumerate(lines[header_number + 1:], header_number + 2):
        if not line.strip() or set(line.strip()) <= {"-", "="}:
            continue
        entries.append(parse_entry(index, line, number, source))
    if not entries:
        raise ParsingError("文件没有流水记录", source=source, line=header_number + 1)
    return sorted(entries, key=entry_sort_key)
