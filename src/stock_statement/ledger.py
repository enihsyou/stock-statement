"""按账户核算移动加权成本并合并证券历史收益。"""

from collections import Counter, defaultdict
from decimal import Decimal

from .attributes import ZERO
from .models import Entry, Report, Security, entry_sort_key


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
    """按平台数量单位核算逆回购本金、费用和购回收益。"""
    unit = entry.platform.repo_quantity_unit(entry.stock_code)
    amount = entry.amount
    if entry.business == "质押回购拆出":
        fees = sum(entry.fees.values(), ZERO)
        if amount >= 0 or -amount < fees:
            raise ValueError(f"{entry.location} 逆回购拆出金额异常")
        security.repo_principal += -amount - fees
        security.repo_quantity += abs(entry.quantity)
        security.realized -= fees
        security.trades += 1
    else:
        principal = abs(entry.quantity) * unit
        if principal > security.repo_principal:
            raise ValueError(
                f"{entry.location} 逆回购购回本金超出历史拆出本金，需要更早的流水。"
            )
        security.repo_principal -= principal
        security.repo_quantity -= abs(entry.quantity)
        security.realized += amount - principal


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
    repayments = Counter(
        (e.date, e.amount) for e in entries if e.business == "拆出质押购回"
    )
    remaining_pairs = registration_pairs(entries)
    for entry in entries:
        report.businesses[entry.business] += 1
        if entry.business in {"证券买入", "证券卖出"}:
            report.security_turnover += abs(entry.amount)
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
        apply_cash(report, entry, repayments)
    return report


def merge_security(target: Security, source: Security) -> None:
    """合并各平台独立核算的同一证券结果。"""
    for name in (
        "quantity", "cost", "realized", "distributions", "transfer_net",
        "transfer_count", "repo_principal", "repo_quantity", "cash_change", "trades",
    ):
        setattr(target, name, getattr(target, name) + getattr(source, name))
    target.cost_known &= source.cost_known
    target.transfer_known &= source.transfer_known
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
        for name in ("inflow", "outflow", "interest", "adjustment", "security_turnover"):
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


def apply_cash(report: Report, entry: Entry, repayments: Counter) -> None:
    """累计银行资金、利息和匹配的交收修正，保留未知业务。"""
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
