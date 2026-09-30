"""协调单券商核算，并汇总各券商结果。"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal

from .aggregation import merge_report
from .attributes import ZERO
from .errors import AccountingError
from .models import Entry, Report, SecuritySummary, entry_sort_key
from .repo import RepoLedger
from .securities import HoldingLedger

type RegistrationKey = tuple[str, str, Decimal]


@dataclass
class SecurityState:
    """协调一只证券的独立子账本及共有流水统计。"""

    name: str = ""
    holding: HoldingLedger = field(default_factory=HoldingLedger)
    repo: RepoLedger = field(default_factory=RepoLedger)
    cash_change: Decimal = ZERO
    fees: dict[str, Decimal] = field(default_factory=dict)

    def apply(self, entry: Entry) -> bool:
        self.name = entry.stock_name or self.name
        self.cash_change += entry.amount
        for name, amount in entry.fees.items():
            self.fees[name] = self.fees.get(name, ZERO) + amount
        if entry.business in {"质押回购拆出", "拆出质押购回"}:
            self.repo.apply(entry)
            return True
        return self.holding.apply(entry)

    def summarize(self) -> SecuritySummary:
        """输出摘要，不将可变合同状态交给报告。"""
        return SecuritySummary(
            name=self.name, holding=self.holding.summarize(), repo=self.repo.summarize(),
            cash_change=self.cash_change, fees=self.fees.copy(),
        )


def registration_pairs(entries: list[Entry]) -> dict[str, Counter[RegistrationKey]]:
    """找出同日同证券同数量且无现金与费用的指定交易配对。"""
    counts: dict[str, Counter[RegistrationKey]] = {
        "指定入账": Counter(), "撤指转出": Counter(),
    }
    for entry in entries:
        if entry.business not in counts or entry.quantity is None or entry.amount or any(entry.fees.values()):
            continue
        counts[entry.business][entry.date, entry.stock_code, entry.quantity] += 1
    paired = counts["指定入账"] & counts["撤指转出"]
    return {name: paired.copy() for name in counts}


def consume_registration(entry: Entry, pairs: dict[str, Counter[RegistrationKey]]) -> bool:
    """消费数量已披露且不影响资产的指定交易记录。"""
    if entry.quantity is None or entry.amount or any(entry.fees.values()):
        return False
    if entry.business in {"指定交易", "撤销指定"} and entry.quantity == ZERO:
        return True
    key = (entry.date, entry.stock_code, entry.quantity)
    remaining = pairs.get(entry.business)
    if remaining is not None and remaining[key] > 0:
        remaining[key] -= 1
        return True
    return False


def apply_cash(report: Report, entry: Entry) -> None:
    """累计账户级现金业务，其他流水保留待确认。"""
    if entry.business == "银行转存" and entry.amount >= ZERO:
        report.inflow += entry.amount
    elif entry.business == "银行转取" and entry.amount <= ZERO:
        report.outflow -= entry.amount
    elif entry.business == "利息归本":
        report.interest += entry.amount
    elif entry.business == "转存管转出" and entry.amount == ZERO:
        return
    else:
        report.unknown.append(entry)


def apply_entry(
    report: Report, states: dict[str, SecurityState], entry: Entry,
    pairs: dict[str, Counter[RegistrationKey]],
) -> None:
    """统计流水并分派至对应业务的核算入口。"""
    report.businesses[entry.business] += 1
    if entry.business == "交收资金修正":
        report.adjustment += entry.amount
        return
    if entry.business in {"证券买入", "证券卖出"}:
        report.security_turnover += abs(entry.amount)
    if entry.business == "质押回购拆出":
        report.repo_turnover += abs(entry.amount)
    for name, amount in entry.fees.items():
        report.fees[name] = report.fees.get(name, ZERO) + amount
    if consume_registration(entry, pairs):
        return
    if not entry.stock_code:
        apply_cash(report, entry)
        return
    state = states.get(entry.stock_code)
    if state is None:
        state = states[entry.stock_code] = SecurityState()
    if not state.apply(entry):
        report.unknown.append(entry)


def analyze_platform(entries: list[Entry]) -> Report:
    """核算已排序的单券商流水，并生成独立摘要。"""
    report = Report()
    states: dict[str, SecurityState] = {}
    pairs = registration_pairs(entries)
    for entry in entries:
        try:
            apply_entry(report, states, entry, pairs)
        except ArithmeticError as exc:
            raise AccountingError.for_entry(entry, f"核算数值异常：{exc}") from exc
    report.securities = {code: state.summarize() for code, state in states.items()}
    return report


def analyze(entries: list[Entry]) -> Report:
    """各券商独立核算后生成汇总报告。"""
    ordered = sorted(entries, key=entry_sort_key)
    groups: dict[str, list[Entry]] = defaultdict(list)
    latest_names: dict[str, str] = {}
    for entry in ordered:
        groups[entry.platform.name].append(entry)
        if entry.stock_code and entry.stock_name:
            latest_names[entry.stock_code] = entry.stock_name
    result = Report()
    for batch in groups.values():
        merge_report(result, analyze_platform(batch))
    for code, summary in result.securities.items():
        result.securities[code] = replace(summary, name=latest_names.get(code, summary.name))
    result.unknown.sort(key=entry_sort_key)
    return result
