"""逆回购合同的匹配、余额核销与计息统计。"""

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .attributes import SERIAL, TRANSACTION_ID, ZERO
from .errors import AccountingError
from .models import Entry, RepoSummary
from .platforms import RepoIdentifier


@dataclass
class RepoPosition:
    """保存一笔未结清合同及剩余拆出费用和本金加权报价。"""

    identifier: str
    principal: Decimal
    quantity: Decimal
    fees: Decimal
    quoted_interest: Decimal
    price_known: bool


def interest_days(interest: Decimal, quoted_interest: Decimal) -> int | None:
    """仅接受分位舍入后唯一吻合的正整数计息天数。"""
    if interest < ZERO or quoted_interest <= ZERO:
        return None
    lower = (interest - Decimal("0.005")) * Decimal(365) / quoted_interest
    upper = (interest + Decimal("0.005")) * Decimal(365) / quoted_interest
    first = max(1, int(lower.to_integral_value(rounding=ROUND_CEILING)))
    last = int(upper.to_integral_value(rounding=ROUND_CEILING)) - 1
    if first != last:
        return None
    calculated = (quoted_interest * first / Decimal(365)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )
    return first if calculated == interest else None


@dataclass
class RepoLedger:
    """在一个券商、证券作用域内管理未结清合同。"""

    identified: dict[str, RepoPosition] = field(default_factory=dict)
    unidentified: list[RepoPosition] = field(default_factory=list)
    profit: Decimal = ZERO
    fees: Decimal = ZERO
    completed_profit: Decimal = ZERO
    capital_days: Decimal = ZERO
    completed_count: int = 0
    rate_known: bool = True
    trades: int = 0

    def apply(self, entry: Entry) -> None:
        """核算拆出或购回，余额始终由未结清合同决定。"""
        quantity = abs(entry.require_quantity())
        try:
            unit = entry.platform.repo_quantity_unit(entry.stock_code)
        except ValueError as exc:
            raise AccountingError.for_entry(entry, str(exc), "证券代码") from exc
        principal = quantity * unit
        fees = sum(entry.fees.values(), ZERO)
        if entry.business == "质押回购拆出":
            self.open(entry, principal, quantity, fees)
        else:
            self.settle(entry, principal, quantity, fees)

    def open(self, entry: Entry, principal: Decimal, quantity: Decimal, fees: Decimal) -> None:
        """合并同编号拆出，缺少编号时独立保存。"""
        if entry.amount >= ZERO or principal <= ZERO or -entry.amount - fees != principal:
            raise AccountingError.for_entry(entry, "逆回购拆出金额异常", "发生金额")
        price = entry.trade_price
        price_known = price is not None and price > ZERO
        quoted_interest = principal * price / Decimal(100) if price is not None and price > ZERO else ZERO
        identifier = entry.repo_identifier
        position = self.identified.get(identifier) if identifier else None
        if position is None:
            position = RepoPosition(identifier, principal, quantity, fees, quoted_interest, price_known)
            if identifier:
                self.identified[identifier] = position
            else:
                self.unidentified.append(position)
        else:
            position.principal += principal
            position.quantity += quantity
            position.fees += fees
            position.quoted_interest += quoted_interest
            position.price_known &= price_known
        self.profit -= fees
        self.fees += fees
        self.trades += 1

    def match(self, entry: Entry, principal: Decimal, quantity: Decimal) -> RepoPosition:
        """按编号匹配，仅允许无编号合同的本金数量唯一匹配。"""
        identifier = entry.repo_identifier
        if identifier:
            position = self.identified.get(identifier)
            matches = [position] if position is not None else []
        else:
            matches = [p for p in self.unidentified if p.principal == principal and p.quantity == quantity]
        if len(matches) != 1:
            attribute = (TRANSACTION_ID if entry.platform.repo_identifier is RepoIdentifier.TRANSACTION_ID
                         else SERIAL)
            names = entry.platform.column_names(attribute)
            raise AccountingError.for_entry(
                entry, f"逆回购购回无法唯一匹配拆出合同（编号 {identifier or '未披露'}）；"
                "请补充对应拆出流水。", names[0] if names else attribute.name,
            )
        position = matches[0]
        if principal <= ZERO or principal > position.principal or quantity > position.quantity:
            raise AccountingError.for_entry(entry, "逆回购购回本金或数量超出对应合同的未购回余额", "成交数量")
        return position

    def settle(self, entry: Entry, principal: Decimal, quantity: Decimal, fees: Decimal) -> None:
        """按购回比例分摊拆出费用并核销合同。"""
        if entry.amount <= ZERO:
            raise AccountingError.for_entry(entry, "逆回购购回金额方向异常", "发生金额")
        position = self.match(entry, principal, quantity)
        fraction = principal / position.principal
        opening_fees = position.fees * fraction
        quoted_interest = position.quoted_interest * fraction
        profit = entry.amount - principal
        days = interest_days(profit + fees, quoted_interest) if position.price_known else None
        self.rate_known &= days is not None
        if days is not None:
            self.capital_days += principal * days
        self.completed_count += 1
        self.completed_profit += profit - opening_fees
        self.profit += profit
        self.fees += fees
        position.principal -= principal
        position.quantity -= quantity
        position.fees -= opening_fees
        position.quoted_interest -= quoted_interest
        if not position.principal:
            if position.identifier:
                del self.identified[position.identifier]
            else:
                self.unidentified.remove(position)

    def summarize(self) -> RepoSummary:
        """生成不携带合同状态的逆回购摘要。"""
        positions = (*self.identified.values(), *self.unidentified)
        return RepoSummary(
            principal=sum((p.principal for p in positions), ZERO),
            quantity=sum((p.quantity for p in positions), ZERO),
            profit=self.profit, fees=self.fees, completed_profit=self.completed_profit,
            capital_days=self.capital_days, completed_count=self.completed_count,
            rate_known=self.rate_known, trades=self.trades,
        )
