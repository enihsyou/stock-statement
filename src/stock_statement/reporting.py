"""将核算摘要投影为报表指标、可见合计及解释信息。"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from .amounts import display_sum, rounded
from .attributes import FEE_COLUMNS, ZERO
from .errors import StatementError
from .models import Entry, Report, SecuritySummary


@dataclass(frozen=True)
class SecurityRow:
    """使用稳定字段名表达证券明细与合计。"""

    code: str
    name: str
    profit: Decimal | None
    fee_total: Decimal
    fees: dict[str, Decimal]
    trades: int
    distributions: Decimal
    quantity: Decimal
    cost: Decimal | None
    transfer_net: Decimal | None
    notes: str


@dataclass(frozen=True)
class SecurityTotals(SecurityRow):
    """已知项的合计总能得到明确金额。"""

    profit: Decimal
    cost: Decimal
    transfer_net: Decimal


class OverviewKey(Enum):
    """独立于显示标题的概览指标标识。"""

    INVESTMENT = auto()
    CASH_INVESTMENT = auto()
    TRANSFER_NET = auto()
    PROFIT = auto()
    INTEREST = auto()
    REPO_PROFIT = auto()
    TURNOVER = auto()
    SECURITY_TURNOVER = auto()
    REPO_TURNOVER = auto()
    FEES = auto()
    STAMP_TAX = auto()
    COMMISSION = auto()
    REPO_FEES = auto()
    COST = auto()
    HOLDING_COST = auto()
    REPO_PRINCIPAL = auto()


@dataclass(frozen=True)
class OverviewRow:
    """保存一个概览指标及其计算口径说明。"""

    key: OverviewKey
    amount: Decimal
    note: str = ""


@dataclass(frozen=True)
class ReportMetadata:
    """保存输入范围、去重统计及平台说明。"""

    platforms: tuple[str, ...]
    start_date: str
    end_date: str
    entries_count: int
    files_count: int
    duplicates: int
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ReportView:
    """保存与终端库无关的完整报告投影。"""

    metadata: ReportMetadata
    securities: tuple[SecurityRow, ...]
    totals: SecurityTotals
    overview: tuple[OverviewRow, ...]
    unknown: tuple[Entry, ...]
    show_transfers: bool
    incomplete: bool


def security_row(code: str, security: SecuritySummary) -> SecurityRow:
    """投影单只证券，保留既定的未知成本屏蔽规则。"""
    holding, repo = security.holding, security.repo
    notes = []
    if not holding.cost_known:
        notes.append("收益及成本不完整")
    if not holding.transfer_known:
        notes.append("托管成交金额缺失")
    return SecurityRow(
        code=code, name=security.name,
        profit=security.profit if holding.cost_known else None,
        fee_total=sum(security.fees.values(), ZERO), fees=security.fees.copy(),
        trades=security.trades, distributions=holding.distributions,
        quantity=holding.quantity + repo.quantity,
        cost=holding.cost + repo.principal if holding.cost_known else None,
        transfer_net=holding.transfer_net if holding.transfer_known else None,
        notes="；".join(notes),
    )


def sum_known(values: Iterable[Decimal | None]) -> Decimal:
    """合计已知金额，未知项不作为明确零值参与明细。"""
    return display_sum(value for value in values if value is not None)


def total_row(rows: list[SecurityRow]) -> SecurityTotals:
    """按可见明细的舍入口径生成合计。"""
    return SecurityTotals(
        code="合计", name="", profit=sum_known(row.profit for row in rows),
        fee_total=display_sum(row.fee_total for row in rows),
        fees={name: display_sum(row.fees.get(name, ZERO) for row in rows) for name in FEE_COLUMNS},
        trades=sum(row.trades for row in rows),
        distributions=display_sum(row.distributions for row in rows),
        quantity=sum((row.quantity for row in rows), ZERO),
        cost=sum_known(row.cost for row in rows),
        transfer_net=sum_known(row.transfer_net for row in rows), notes="",
    )


def build_security_rows(report: Report) -> tuple[list[SecurityRow], SecurityTotals]:
    """按收益排序明细，并补充未归属证券的费用。"""
    ordered = sorted(report.securities.items(),
                     key=lambda item: (item[1].holding.cost_known, item[1].profit), reverse=True)
    rows = [security_row(code, security) for code, security in ordered]
    unassigned = {
        name: report.fees.get(name, ZERO) - sum(
            (security.fees.get(name, ZERO) for security in report.securities.values()), ZERO,
        ) for name in FEE_COLUMNS
    }
    if any(unassigned.values()):
        rows.append(SecurityRow(
            code="—", name="未归属证券", profit=None,
            fee_total=sum(unassigned.values(), ZERO), fees=unassigned,
            trades=0, distributions=ZERO, quantity=ZERO, cost=None, transfer_net=None, notes="",
        ))
    return rows, total_row(rows)


def rate_text(profit: Decimal, investment: Decimal) -> str:
    """计算收益率说明，零投入不生成比率。"""
    return f"收益率 {profit / investment:.2%}" if investment else "收益率不可计算"


def ratio_text(value: Decimal, total: Decimal, label: str, scale: Decimal, suffix: str) -> str:
    """生成指定比例单位的占比说明。"""
    return f"占{label} {value / total * scale:.2f}{suffix}" if total else f"占{label}不可计算"


def fee_note(amount: Decimal, turnover: Decimal, fees: Decimal) -> str:
    """先说明占交易额的千分比，再说明占手续费的百分比。"""
    return (
        ratio_text(amount, turnover, "交易总额", Decimal(1000), "‰") + "；"
        + ratio_text(amount, fees, "手续费", Decimal(100), "%")
    )


def repo_rate_note(report: Report) -> str:
    """按已完成交易的本金计息天数计算扣费后年化收益率。"""
    repos = [security.repo for security in report.securities.values()]
    if not any(repo.completed_count for repo in repos):
        return "年化收益率不可计算：无已完成交易"
    if not all(repo.rate_known for repo in repos):
        return "年化收益率不可计算：计息天数无法唯一确定"
    capital_days = sum((repo.capital_days for repo in repos), ZERO)
    profit = sum((repo.completed_profit for repo in repos), ZERO)
    return "年化" + rate_text(profit * Decimal(365), capital_days)


def build_overview(report: Report, totals: SecurityTotals) -> tuple[OverviewRow, ...]:
    """集中生成概览金额与说明，不使用显示标题作为计算键。"""
    securities = tuple(report.securities.values())
    fees = sum(report.fees.values(), ZERO)
    repo_fees = display_sum(security.repo.fees for security in securities)
    cash_investment = report.inflow - report.outflow
    investment = cash_investment + totals.transfer_net
    profit = totals.profit + rounded(report.interest)
    missing_transfer = any(not security.holding.transfer_known for security in securities)
    missing_cost = any(not security.holding.cost_known for security in securities)
    rows = [
        OverviewRow(OverviewKey.INVESTMENT, investment, "不完整" if missing_transfer else ""),
        OverviewRow(OverviewKey.CASH_INVESTMENT, cash_investment, "仅银行转账"),
    ]
    if any(security.holding.transfer_count for security in securities):
        rows.append(OverviewRow(
            OverviewKey.TRANSFER_NET, totals.transfer_net,
            "成交金额缺失，仅合计已知项" if missing_transfer else "转入为正，转出为负",
        ))
    rows.extend((
        OverviewRow(OverviewKey.PROFIT, profit,
                    ("收益不完整；" if report.incomplete else "") + rate_text(profit, investment)),
        OverviewRow(OverviewKey.INTEREST, report.interest, rate_text(report.interest, investment)),
        OverviewRow(OverviewKey.REPO_PROFIT, display_sum(s.repo.profit for s in securities), repo_rate_note(report)),
        OverviewRow(OverviewKey.TURNOVER, report.turnover),
        OverviewRow(OverviewKey.SECURITY_TURNOVER, report.security_turnover, "证券买卖金额之和，含手续费"),
        OverviewRow(OverviewKey.REPO_TURNOVER, report.repo_turnover, "拆出金额之和，含手续费"),
        OverviewRow(OverviewKey.FEES, totals.fee_total,
                    ratio_text(fees, report.turnover, "交易总额", Decimal(1000), "‰")),
        OverviewRow(OverviewKey.STAMP_TAX, totals.fees["印花税"],
                    fee_note(report.fees.get("印花税", ZERO), report.turnover, fees)),
        OverviewRow(OverviewKey.COMMISSION, totals.fees["佣金"],
                    fee_note(report.fees.get("佣金", ZERO), report.turnover, fees)),
        OverviewRow(OverviewKey.REPO_FEES, repo_fees, fee_note(repo_fees, report.turnover, fees)),
        OverviewRow(OverviewKey.COST, totals.cost, "成本不完整，仅合计已知项" if missing_cost else ""),
        OverviewRow(OverviewKey.HOLDING_COST,
                    display_sum(s.holding.cost for s in securities if s.holding.cost_known)),
        OverviewRow(OverviewKey.REPO_PRINCIPAL, display_sum(s.repo.principal for s in securities)),
    ))
    return tuple(rows)


def build_metadata(entries: list[Entry], files_count: int, duplicates: int) -> ReportMetadata:
    """提取核算日期范围、文件统计和平台说明。"""
    if not entries:
        raise StatementError("没有可展示的流水")
    platforms = {entry.platform.name: entry.platform for entry in entries}
    notes = [
        "收益采用移动加权成本，不计算浮盈浮亏；费用已包含于净收付款。",
        "账户净投入包含托管净转入；其余计算口径见项目文档。",
    ]
    if len(platforms) > 1:
        notes.append("各平台分别核算后按证券汇总，跨平台托管转移不另作抵消。")
    notes.extend(dict.fromkeys(note for platform in platforms.values() for note in platform.notes))
    return ReportMetadata(
        platforms=tuple(platforms), start_date=min(entry.date for entry in entries),
        end_date=max(entry.date for entry in entries), entries_count=len(entries),
        files_count=files_count, duplicates=duplicates, notes=tuple(notes),
    )


def build_report_view(entries: list[Entry], report: Report, files_count: int, duplicates: int) -> ReportView:
    """生成完整报告投影，供任意展示入口消费。"""
    rows, totals = build_security_rows(report)
    return ReportView(
        metadata=build_metadata(entries, files_count, duplicates), securities=tuple(rows),
        totals=totals, overview=build_overview(report, totals), unknown=tuple(report.unknown),
        show_transfers=any(security.holding.transfer_count for security in report.securities.values()),
        incomplete=report.incomplete,
    )
