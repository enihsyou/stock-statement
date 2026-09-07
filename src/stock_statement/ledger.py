"""证券流水解析与历史成本核算。"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .platforms import FEE_COLUMNS, PLATFORMS, Platform, decimal, identify_platform

ZERO = Decimal(0)


@dataclass
class Entry:
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


def read_entries(path: Path) -> list[Entry]:
    raw = path.read_bytes()
    try:
        content = raw.decode("gb18030")
    except UnicodeDecodeError:
        content = raw.decode("utf-8-sig")
    lines = content.splitlines()
    header_index = next(
        (
            i
            for i, line in enumerate(lines)
            if line.lstrip().startswith(tuple(p.header_start for p in PLATFORMS))
        ),
        None,
    )
    if header_index is None:
        raise ValueError(
            "未找到资金流水表头，请使用包含业务名称和费用明细的资金流水文件。"
        )
    # GB18030 字节位置保留客户端中文两格、ASCII 一格的固定列宽。
    header = lines[header_index].encode("gb18030")
    columns = [
        (m.group().decode("gb18030"), m.start()) for m in re.finditer(rb"\S+", header)
    ]
    platform = identify_platform({name for name, _ in columns})
    entries = []
    for number, line in enumerate(lines[header_index + 1 :], header_index + 2):
        if not line.strip() or set(line.strip()) <= {"-", "="}:
            continue
        values = split_columns(line, columns)
        try:
            values = platform.normalize(values)
            quantity, trade_amount, trade_price = platform.trade_values(values)
            validate_date(values["成交日期"])
            if values["币种"] != "人民币":
                raise ValueError("仅支持人民币，不能合计不同币种")
            entries.append(
                Entry(
                    values["成交日期"],
                    values["业务名称"],
                    values["证券代码"],
                    values["证券名称"],
                    quantity,
                    decimal(values["发生金额"]),
                    values["流水号"],
                    number,
                    {name: decimal(values[name]) for name in FEE_COLUMNS},
                    platform,
                    trade_amount,
                    trade_price,
                    values.get("发生时间", ""),
                )
            )
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"第 {number} 行无法解析：{exc}") from exc
    if not entries:
        raise ValueError("文件没有流水记录")
    return sorted(entries, key=entry_sort_key)


def validate_date(value: str) -> None:
    if not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError(f"日期应为 YYYYMMDD：{value}")
    date.fromisoformat(value)


def entry_sort_key(entry: Entry) -> tuple:
    serial = (0, int(entry.serial)) if entry.serial.isdecimal() else (1, entry.serial)
    return entry.date, entry.time, serial, entry.line


def split_columns(line: str, columns: list[tuple[str, int]]) -> dict[str, str]:
    data = line.encode("gb18030")
    result = {}
    for index, (name, start) in enumerate(columns):
        end = columns[index + 1][1] if index + 1 < len(columns) else len(data)
        result[name] = data[start:end].decode("gb18030").strip()
    return result


@dataclass
class Security:
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
        return self.realized + self.distributions


@dataclass
class Report:
    securities: dict[str, Security] = field(default_factory=dict)
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    interest: Decimal = ZERO
    adjustment: Decimal = ZERO
    fees: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    unknown: list[Entry] = field(default_factory=list)
    businesses: Counter = field(default_factory=Counter)


def remove_cost(security: Security, quantity: Decimal, entry: Entry) -> Decimal:
    if quantity <= 0 or quantity > security.quantity:
        raise ValueError(
            f"第 {entry.line} 行 {entry.stock_code} 数量无法匹配历史持仓"
            f"（需减少 {quantity}，已有 {security.quantity}）；请补充买入或转入数量记录。"
        )
    cost = (
        security.cost
        if quantity == security.quantity
        else security.cost * quantity / security.quantity
    )
    security.quantity -= quantity
    security.cost -= cost
    return cost


def apply_security(security: Security, entry: Entry) -> bool:
    business, amount = entry.business, entry.amount
    if business == "证券买入":
        if entry.quantity <= 0 or amount >= 0:
            raise ValueError(f"第 {entry.line} 行买入数量或金额方向异常")
        security.quantity += entry.quantity
        security.cost -= amount
        security.trades += 1
    elif business == "证券卖出":
        security.realized += amount - remove_cost(security, -entry.quantity, entry)
        security.trades += 1
    elif business in {"转托转出", "转托转入"}:
        apply_transfer(security, entry)
    elif business in {"股息入账", "股息红利税补缴"}:
        security.distributions += amount
    elif business == "质押回购拆出":
        if not entry.stock_code.startswith(("204", "1318")):
            return False
        fees = sum(entry.fees.values(), ZERO)
        if amount >= 0 or -amount < fees:
            raise ValueError(f"第 {entry.line} 行逆回购拆出金额异常")
        security.repo_principal += -amount - fees
        security.repo_quantity += abs(entry.quantity)
        security.realized -= fees
        security.trades += 1
    elif business == "拆出质押购回":
        if not entry.stock_code.startswith(("204", "1318")):
            return False
        unit = entry.platform.repo_quantity_unit(entry.stock_code)
        principal = abs(entry.quantity) * unit
        if principal > security.repo_principal:
            raise ValueError(
                f"第 {entry.line} 行逆回购购回本金超出历史拆出本金，需要更早的流水。"
            )
        security.repo_principal -= principal
        security.repo_quantity -= abs(entry.quantity)
        security.realized += amount - principal
    else:
        return False
    return True


def apply_transfer(security: Security, entry: Entry) -> None:
    incoming = entry.business == "转托转入"
    quantity = abs(entry.quantity)
    if quantity == 0:
        raise ValueError(f"第 {entry.line} 行托管转移数量为零")
    security.transfer_count += 1
    removed = ZERO
    if incoming:
        security.quantity += quantity
    else:
        removed = remove_cost(security, quantity, entry)
    if entry.trade_amount is None:
        security.transfer_known = False
        security.cost_known = False
        return
    value = abs(entry.trade_amount)
    security.transfer_net += value if incoming else -value
    if incoming:
        security.cost += value
    else:
        security.realized += value - removed


def analyze(entries: list[Entry]) -> Report:
    report = Report()
    repayments = Counter(
        (e.date, e.amount) for e in entries if e.business == "拆出质押购回"
    )
    registrations = Counter(
        (e.date, e.stock_code, e.quantity)
        for e in entries
        if e.business == "指定入账" and e.amount == 0
    )
    deregistrations = Counter(
        (e.date, e.stock_code, e.quantity)
        for e in entries
        if e.business == "撤指转出" and e.amount == 0
    )
    paired = registrations & deregistrations
    remaining_pairs = {name: paired.copy() for name in ("指定入账", "撤指转出")}
    for entry in entries:
        report.businesses[entry.business] += 1
        for name, amount in entry.fees.items():
            report.fees[name] += amount
        if entry.amount == 0 and not any(entry.fees.values()):
            if entry.business in {"指定交易", "撤销指定"} and entry.quantity == 0:
                continue
            key = (entry.date, entry.stock_code, entry.quantity)
            if (
                entry.business in remaining_pairs
                and remaining_pairs[entry.business][key] > 0
            ):
                remaining_pairs[entry.business][key] -= 1
                continue
        if entry.stock_code:
            security = report.securities.setdefault(entry.stock_code, Security())
            security.name = entry.stock_name or security.name
            security.cash_change += entry.amount
            for name, amount in entry.fees.items():
                security.fees[name] += amount
            if not apply_security(security, entry):
                report.unknown.append(entry)
            continue
        apply_cash(report, entry, repayments)
    return report


def apply_cash(report: Report, entry: Entry, repayments: Counter) -> None:
    if entry.business == "银行转存" and entry.amount >= 0:
        report.inflow += entry.amount
    elif entry.business == "银行转取" and entry.amount <= 0:
        report.outflow -= entry.amount
    elif entry.business == "利息归本":
        report.interest += entry.amount
    elif (
        entry.business == "交收资金修正" and repayments[(entry.date, entry.amount)] > 0
    ):
        repayments[(entry.date, entry.amount)] -= 1
        report.adjustment += entry.amount
    elif entry.business == "转存管转出" and entry.amount == 0:
        return
    else:
        report.unknown.append(entry)
