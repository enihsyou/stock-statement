"""按账户核算移动加权成本并合并证券历史收益。"""

from collections import Counter, defaultdict
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .attributes import ZERO
from .models import Entry, Report, RepoPosition, Security, entry_sort_key


def remove_cost(security: Security, quantity: Decimal, entry: Entry) -> Decimal:
    """按移动加权成本移除持仓，数量不足时停止核算。"""
    if quantity <= 0 or quantity > security.quantity:
        raise ValueError(
            f"{entry.location} {entry.stock_code} 数量无法匹配历史持仓"
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
    """核算一条证券业务，并告知调用方是否识别该业务。"""
    business, amount = entry.business, entry.amount
    if business == "证券买入":
        if entry.quantity <= 0 or amount >= 0:
            raise ValueError(f"{entry.location} 买入数量或金额方向异常")
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
    elif business in {"质押回购拆出", "拆出质押购回"}:
        apply_repo(security, entry)
    else:
        return False
    return True


def apply_repo(security: Security, entry: Entry) -> None:
    """按合同核销逆回购本金，并分别累计费用、收益和计息本金。"""
    unit = entry.platform.repo_quantity_unit(entry.stock_code)
    fees = sum(entry.fees.values(), ZERO)
    principal = abs(entry.quantity) * unit
    if entry.business == "质押回购拆出":
        if entry.amount >= 0 or principal <= ZERO or -entry.amount - fees != principal:
            raise ValueError(f"{entry.location} 逆回购拆出金额异常")
        add_repo_position(security, entry, principal, fees)
        security.repo_principal += principal
        security.repo_quantity += abs(entry.quantity)
        security.realized -= fees
        security.repo_profit -= fees
        security.repo_fees += fees
        security.trades += 1
        return
    settle_repo_position(security, entry, principal, fees)


def add_repo_position(security: Security, entry: Entry, principal: Decimal, fees: Decimal) -> None:
    """将同一合同的拆出成交合并，缺少编号的成交独立保留。"""
    price_known = entry.trade_price is not None and entry.trade_price > ZERO
    quoted_interest = principal * (entry.trade_price or ZERO) / Decimal(100) if price_known else ZERO
    identifier = entry.repo_identifier
    position = next((p for p in security.repo_positions
                     if identifier and p.identifier == identifier), None)
    if position is None:
        security.repo_positions.append(RepoPosition(
            identifier, principal, abs(entry.quantity), fees, quoted_interest, price_known,
        ))
        return
    position.principal += principal
    position.quantity += abs(entry.quantity)
    position.fees += fees
    position.quoted_interest += quoted_interest
    position.price_known &= price_known


def match_repo_position(security: Security, entry: Entry, principal: Decimal) -> RepoPosition:
    """优先按合同匹配，仅对双方无编号且本金数量唯一的记录推断配对。"""
    identifier = entry.repo_identifier
    if identifier:
        matches = [p for p in security.repo_positions if p.identifier == identifier]
    else:
        matches = [p for p in security.repo_positions
                   if not p.identifier and p.principal == principal
                   and p.quantity == abs(entry.quantity)]
    if len(matches) != 1:
        raise ValueError(
            f"{entry.location} 逆回购购回无法唯一匹配拆出合同"
            f"（{entry.platform.repo_identifier.name} {identifier or '未披露'}）；请补充对应拆出流水。"
        )
    position = matches[0]
    if principal <= ZERO or principal > position.principal or abs(entry.quantity) > position.quantity:
        raise ValueError(f"{entry.location} 逆回购购回本金或数量超出对应合同的未购回余额")
    return position


def repo_interest_days(interest: Decimal, quoted_interest: Decimal) -> int | None:
    """反推整数计息天数，仅接受分位舍入后唯一吻合的结果。"""
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


def settle_repo_position(security: Security, entry: Entry, principal: Decimal, fees: Decimal) -> None:
    """按购回比例分摊拆出费用，核销合同并累计已完成交易的年化分母。"""
    if entry.amount <= ZERO:
        raise ValueError(f"{entry.location} 逆回购购回金额方向异常")
    position = match_repo_position(security, entry, principal)
    fraction = principal / position.principal
    opening_fees = position.fees * fraction
    quoted_interest = position.quoted_interest * fraction
    profit = entry.amount - principal
    interest = profit + fees
    days = repo_interest_days(interest, quoted_interest) if position.price_known else None
    security.repo_rate_known &= days is not None
    if days is not None:
        security.repo_capital_days += principal * days
    security.repo_completed_count += 1
    security.repo_completed_profit += profit - opening_fees
    security.repo_profit += profit
    security.repo_fees += fees
    security.realized += profit
    security.repo_principal -= principal
    security.repo_quantity -= abs(entry.quantity)
    position.principal -= principal
    position.quantity -= abs(entry.quantity)
    position.fees -= opening_fees
    position.quoted_interest -= quoted_interest
    if not position.principal:
        security.repo_positions.remove(position)


def apply_transfer(security: Security, entry: Entry) -> None:
    """以流水披露的估值记录托管转移及其成本变化。"""
    incoming = entry.business == "转托转入"
    quantity = abs(entry.quantity)
    if quantity == 0:
        raise ValueError(f"{entry.location} 托管转移数量为零")
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


def registration_pairs(entries: list[Entry]) -> dict[str, Counter]:
    """找出同日同证券同数量且无现金与费用的指定交易配对。"""
    registrations = Counter(
        (e.date, e.stock_code, e.quantity)
        for e in entries
        if e.business == "指定入账" and e.amount == 0 and not any(e.fees.values())
    )
    deregistrations = Counter(
        (e.date, e.stock_code, e.quantity)
        for e in entries
        if e.business == "撤指转出" and e.amount == 0 and not any(e.fees.values())
    )
    paired = registrations & deregistrations
    return {name: paired.copy() for name in ("指定入账", "撤指转出")}


def consume_registration(entry: Entry, remaining_pairs: dict[str, Counter]) -> bool:
    """消费一条对资产没有影响的指定交易记录。"""
    if entry.amount or any(entry.fees.values()):
        return False
    if entry.business in {"指定交易", "撤销指定"} and entry.quantity == 0:
        return True
    key = (entry.date, entry.stock_code, entry.quantity)
    if entry.business in remaining_pairs and remaining_pairs[entry.business][key] > 0:
        remaining_pairs[entry.business][key] -= 1
        return True
    return False


def analyze_platform(entries: list[Entry]) -> Report:
    """在单个平台账户内按时间累计持仓与资金业务。"""
    report = Report()
    remaining_pairs = registration_pairs(entries)
    for entry in entries:
        report.businesses[entry.business] += 1
        if entry.business == "交收资金修正":
            report.adjustment += entry.amount
            continue
        if entry.business in {"证券买入", "证券卖出"}:
            report.security_turnover += abs(entry.amount)
        if entry.business == "质押回购拆出":
            report.repo_turnover += abs(entry.amount)
        for name, amount in entry.fees.items():
            report.fees[name] += amount
        if consume_registration(entry, remaining_pairs):
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
        apply_cash(report, entry)
    return report


def merge_security(target: Security, source: Security) -> None:
    """合并各平台独立核算的同一证券结果。"""
    for name in (
        "quantity", "cost", "realized", "distributions", "transfer_net",
        "transfer_count", "repo_principal", "repo_quantity", "cash_change", "trades",
        "repo_profit", "repo_fees", "repo_completed_profit", "repo_capital_days", "repo_completed_count",
    ):
        setattr(target, name, getattr(target, name) + getattr(source, name))
    target.cost_known &= source.cost_known
    target.transfer_known &= source.transfer_known
    target.repo_rate_known &= source.repo_rate_known
    for name, amount in source.fees.items():
        target.fees[name] += amount


def analyze(entries: list[Entry]) -> Report:
    """按平台独立核算后汇总证券与账户资金。"""
    groups = defaultdict(list)
    for entry in sorted(entries, key=entry_sort_key):
        groups[entry.platform.name].append(entry)
    result = Report()
    for batch in groups.values():
        report = analyze_platform(batch)
        for name in ("inflow", "outflow", "interest", "adjustment", "security_turnover", "repo_turnover"):
            setattr(result, name, getattr(result, name) + getattr(report, name))
        result.unknown.extend(report.unknown)
        result.businesses.update(report.businesses)
        for name, amount in report.fees.items():
            result.fees[name] += amount
        for code, security in report.securities.items():
            merge_security(result.securities.setdefault(code, Security()), security)
    for entry in sorted(entries, key=entry_sort_key):
        if entry.stock_code in result.securities and entry.stock_name:
            result.securities[entry.stock_code].name = entry.stock_name
    result.unknown.sort(key=entry_sort_key)
    return result


def apply_cash(report: Report, entry: Entry) -> None:
    """累计银行资金和利息，保留未知业务。"""
    if entry.business == "银行转存" and entry.amount >= 0:
        report.inflow += entry.amount
    elif entry.business == "银行转取" and entry.amount <= 0:
        report.outflow -= entry.amount
    elif entry.business == "利息归本":
        report.interest += entry.amount
    elif entry.business == "转存管转出" and entry.amount == 0:
        return
    else:
        report.unknown.append(entry)
