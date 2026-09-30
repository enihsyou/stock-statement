"""合并不同券商分别核算的摘要。"""

from collections.abc import Mapping
from decimal import Decimal

from .attributes import ZERO
from .models import HoldingSummary, Report, RepoSummary, SecuritySummary


def combine_fees(left: Mapping[str, Decimal], right: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """按费用名称合并金额。"""
    names = dict.fromkeys((*left, *right))
    return {name: left.get(name, ZERO) + right.get(name, ZERO) for name in names}


def combine_holding(left: HoldingSummary, right: HoldingSummary) -> HoldingSummary:
    """汇总独立持仓结果，并保留未知标记。"""
    return HoldingSummary(
        quantity=left.quantity + right.quantity, cost=left.cost + right.cost,
        realized=left.realized + right.realized, distributions=left.distributions + right.distributions,
        transfer_net=left.transfer_net + right.transfer_net,
        transfer_count=left.transfer_count + right.transfer_count,
        transfer_known=left.transfer_known and right.transfer_known,
        cost_known=left.cost_known and right.cost_known, trades=left.trades + right.trades,
    )


def combine_repo(left: RepoSummary, right: RepoSummary) -> RepoSummary:
    """汇总逆回购指标，合同不参与跨券商合并。"""
    return RepoSummary(
        principal=left.principal + right.principal, quantity=left.quantity + right.quantity,
        profit=left.profit + right.profit, fees=left.fees + right.fees,
        completed_profit=left.completed_profit + right.completed_profit,
        capital_days=left.capital_days + right.capital_days,
        completed_count=left.completed_count + right.completed_count,
        rate_known=left.rate_known and right.rate_known, trades=left.trades + right.trades,
    )


def combine_security(left: SecuritySummary, right: SecuritySummary) -> SecuritySummary:
    """合并同代码证券的摘要。"""
    return SecuritySummary(
        name=right.name or left.name,
        holding=combine_holding(left.holding, right.holding), repo=combine_repo(left.repo, right.repo),
        cash_change=left.cash_change + right.cash_change, fees=combine_fees(left.fees, right.fees),
    )


def merge_report(target: Report, source: Report) -> None:
    """将一个券商的结果累计至汇总报告。"""
    target.inflow += source.inflow
    target.outflow += source.outflow
    target.interest += source.interest
    target.adjustment += source.adjustment
    target.security_turnover += source.security_turnover
    target.repo_turnover += source.repo_turnover
    target.fees = combine_fees(target.fees, source.fees)
    target.unknown.extend(source.unknown)
    target.businesses.update(source.businesses)
    for code, summary in source.securities.items():
        existing = target.securities.get(code)
        target.securities[code] = combine_security(existing, summary) if existing is not None else summary
