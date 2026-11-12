"""逐笔策略归因；不依赖网络、HTTP 或移动加权成本账本。"""
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from hashlib import sha256

from .dates import china_today
from .models import Entry, entry_sort_key

ZERO = Decimal(0)
STRATEGIES = {
    "retained_buy_pairing": "未完成买入保留策略",
    "current_position_lots": "当前持仓批次策略",
}


def event_id(entry: Entry) -> str:
    identity = (entry.platform.name, entry.stock_code, entry.source, entry.line,
                entry.date, entry.time, entry.serial, entry.transaction_id)
    return sha256(repr(identity).encode()).hexdigest()[:24]


def iso_day(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else value


@dataclass
class QuantityChange:
    time: str
    quantity: Decimal
    event_id: str


@dataclass
class Expectation:
    id: str
    strategy: str
    source_event_id: str
    account: str
    side: str
    start_time: str
    reference_price: Decimal
    comparison: str
    original_quantity: Decimal
    remaining_quantity: Decimal
    matched_quantity: Decimal = ZERO
    status: str = "open"
    end_time: str | None = None
    changes: list[QuantityChange] = field(default_factory=list)
    executable_quantity: Decimal | None = None
    limitation: str = "共享实际可卖上限；价差阈值，未计费用"


@dataclass
class Pair:
    source_event_id: str
    target_event_id: str
    quantity: Decimal
    profit: Decimal
    kind: str


@dataclass
class StrategyResult:
    strategy: str
    name: str
    expectations: list[Expectation] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)
    realized: Decimal = ZERO
    quantity: Decimal = ZERO
    sellable_quantity: Decimal | None = None
    average_cost: Decimal | None = None
    economic_profit: Decimal | None = None
    unfinished_difference: Decimal | None = None
    cashflow_profit: Decimal | None = None
    cashflow_break_even: Decimal | None = None
    ledger_adjustment: Decimal = ZERO
    incomplete: bool = False
    warnings: list[str] = field(default_factory=list)


def _consume(lot: Expectation, quantity: Decimal, entry: Entry) -> None:
    lot.remaining_quantity -= quantity
    lot.matched_quantity += quantity
    lot.changes.append(QuantityChange(iso_day(entry.date), lot.remaining_quantity, event_id(entry)))
    if not lot.remaining_quantity:
        lot.status = "completed"
        lot.end_time = iso_day(entry.date)


def _new(result: StrategyResult, entry: Entry, side: str, quantity: Decimal, price: Decimal) -> None:
    if not quantity:
        return
    ident = event_id(entry)
    result.expectations.append(Expectation(
        id=f"{result.strategy}:{ident}:{side}", strategy=result.strategy,
        source_event_id=ident, account=entry.platform.name, side=side,
        start_time=iso_day(entry.date), reference_price=price,
        comparison=">" if side == "sell" else "<", original_quantity=quantity,
        remaining_quantity=quantity,
        changes=[QuantityChange(iso_day(entry.date), quantity, ident)],
        limitation="共享实际可卖上限；价差阈值，未计费用" if side == "sell" else "计划量；资金与加仓额度未知；未计费用",
    ))


def calculate_strategies(entries: list[Entry], current_price: Decimal | None = None,
                         as_of: str | None = None) -> list[StrategyResult]:
    """每个证券、券商独立处理，返回两种互斥比较情景。

    as_of 是估算可卖日期；流水无法证明实时券商余额。基金结算规则未知时不猜可卖量。
    """
    ordered = sorted(entries, key=entry_sort_key)
    event_securities = {event_id(entry): entry.stock_code for entry in ordered}
    day = (as_of or china_today().strftime("%Y%m%d")).replace("-", "")
    results = []
    for strategy, name in STRATEGIES.items():
        result = StrategyResult(strategy, name)
        positions = {}
        bought_today = {}
        cash = ZERO
        adjustment = ZERO
        for entry in ordered:
            key = (entry.platform.name, entry.stock_code)
            positions.setdefault(key, ZERO)
            bought_today.setdefault(key, ZERO)
            if entry.business not in {"证券买入", "证券卖出"}:
                if entry.business in {"股息入账", "股息红利税补缴"}:
                    adjustment += entry.amount
                else:
                    result.incomplete = True
                    if entry.business in {"转托转入", "转托转出"} and entry.quantity is not None:
                        positions[key] += abs(entry.quantity) * (1 if entry.business == "转托转入" else -1)
                continue
            quantity = abs(entry.require_quantity())
            buy = entry.business == "证券买入"
            positions[key] += quantity if buy else -quantity
            if positions[key] < 0:
                result.incomplete = True
            if buy and entry.date >= day:
                bought_today[key] += quantity
            price = entry.trade_price
            if price is None or price <= 0:
                result.incomplete = True
                continue
            cash += (-1 if buy else 1) * quantity * price
            adjustment += entry.amount - (-1 if buy else 1) * quantity * price
            # 仅检索本证券本账户；已完成批次永不重新参与匹配。
            candidates = [lot for lot in result.expectations
                          if lot.account == entry.platform.name and lot.remaining_quantity > 0
                          and event_securities[lot.source_event_id] == entry.stock_code
                          and lot.side == ("buy" if buy else "sell")]
            profitable = [lot for lot in candidates if
                          (lot.reference_price > price if buy else lot.reference_price < price)]
            # Python 稳定排序保留相同价位的原始事件顺序。
            profitable.sort(key=lambda lot: lot.reference_price if buy else -lot.reference_price)
            remaining = quantity
            for lot in profitable:
                matched = min(remaining, lot.remaining_quantity)
                if not matched:
                    break
                profit = matched * abs(price - lot.reference_price)
                kind = "replenishment" if buy and strategy == "current_position_lots" else "realized"
                result.pairs.append(Pair(lot.source_event_id, event_id(entry), matched, profit, kind))
                if kind == "realized":
                    result.realized += profit
                _consume(lot, matched, entry)
                remaining -= matched
            if strategy == "current_position_lots":
                if buy:
                    _new(result, entry, "sell", quantity, price)
                else:
                    unprofitable = sorted((lot for lot in candidates if lot.remaining_quantity),
                                          key=lambda lot: -lot.reference_price)
                    loss_quantity = ZERO
                    for lot in unprofitable:
                        matched = min(remaining, lot.remaining_quantity)
                        if not matched:
                            break
                        profit = matched * (price - lot.reference_price)
                        result.realized += profit
                        result.pairs.append(Pair(lot.source_event_id, event_id(entry), matched, profit, "realized"))
                        _consume(lot, matched, entry)
                        remaining -= matched
                        loss_quantity += matched
                    _new(result, entry, "buy", loss_quantity, price)
            else:
                _new(result, entry, "sell" if buy else "buy", remaining, price)
        result.quantity = sum(positions.values(), ZERO)
        result.ledger_adjustment = adjustment
        fund = any(e.stock_code.startswith(("5", "1")) for e in ordered)
        result.sellable_quantity = None if result.incomplete or fund else sum(
            (max(ZERO, q - bought_today[k]) for k, q in positions.items()), ZERO)
        opened = [lot for lot in result.expectations if lot.remaining_quantity > 0]
        buys = [lot for lot in opened if lot.side == "sell"]
        buy_quantity = sum((lot.remaining_quantity for lot in buys), ZERO)
        result.average_cost = (sum((lot.reference_price * lot.remaining_quantity for lot in buys), ZERO)
                               / buy_quantity if buy_quantity else None)
        if (current_price is not None or result.quantity == 0) and not result.incomplete:
            valuation_price = current_price if current_price is not None else ZERO
            result.unfinished_difference = sum((lot.remaining_quantity *
                (valuation_price - lot.reference_price if lot.side == "sell" else lot.reference_price - valuation_price)
                for lot in opened if strategy == "retained_buy_pairing" or lot.side == "sell"), ZERO)
            result.economic_profit = result.realized + result.unfinished_difference + adjustment
            result.cashflow_profit = cash + result.quantity * valuation_price + adjustment
        if result.quantity > 0 and not result.incomplete:
            result.cashflow_break_even = -(cash + adjustment) / result.quantity
        if result.incomplete:
            result.average_cost = None
            result.warnings.append("缺少期初、价格或存在不支持的非买卖事件；策略不完整，隐藏期待价位。")
            for lot in result.expectations:
                lot.status = "unreliable"
        if any(not e.time for e in ordered):
            result.warnings.append("同日优先按数字流水号排序；其余按披露时间、编号和原始行号排序。缺少时间的记录不推断具体成交时刻。")
        result.warnings.append("可卖数量仅基于交割单估算，普通股票按 T+1；不代表券商实时可用持仓。")
        results.append(result)
    return results


@dataclass(frozen=True)
class FeeSettings:
    buy_commission: Decimal = Decimal("0.0001")
    sell_commission: Decimal = Decimal("0.0001")
    buy_minimum: Decimal = Decimal(5)
    sell_minimum: Decimal = Decimal(5)
    stamp_tax: Decimal = Decimal("0.0005")
    transfer: Decimal = Decimal("0.00001")

    def fee(self, price: Decimal, quantity: Decimal, side: str) -> Decimal:
        amount = price * quantity
        commission = self.buy_commission if side == "buy" else self.sell_commission
        minimum = self.buy_minimum if side == "buy" else self.sell_minimum
        return max(minimum, amount * commission) + amount * (self.transfer + (self.stamp_tax if side == "sell" else ZERO))


def profitable_price(reference: Decimal, quantity: Decimal, side: str,
                     tick: Decimal, fees: FeeSettings) -> Decimal | None:
    """按所选数量计算双边费用，返回严格盈利的首个报价档位。"""
    original_side = "buy" if side == "sell" else "sell"
    base = reference * quantity
    original_fee = fees.fee(reference, quantity, original_side)
    # 最低佣金与比例佣金两个约束必须同时满足，直接解出分段线性边界。
    if side == "sell":
        net_rate = 1 - fees.stamp_tax - fees.transfer
        boundary = max((base + original_fee + fees.sell_minimum) / (quantity * net_rate),
                       (base + original_fee) / (quantity * (net_rate - fees.sell_commission)))
        return ((boundary / tick).to_integral_value(rounding=ROUND_FLOOR) + 1) * tick
    available = base - original_fee
    boundary = min((available - fees.buy_minimum) / (quantity * (1 + fees.transfer)),
                   available / (quantity * (1 + fees.transfer + fees.buy_commission)))
    candidate = ((boundary / tick).to_integral_value(rounding=ROUND_CEILING) - 1) * tick
    return candidate if candidate > 0 else None
