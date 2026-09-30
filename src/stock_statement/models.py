"""流水、证券持仓与账户报告的统一数据模型。"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from .attributes import QUANTITY, ZERO
from .errors import AccountingError
from .platforms import Platform, RepoIdentifier


@dataclass
class Entry:
    """记录统一后的交易属性及原文件定位信息。"""

    date: str
    """核算日期（YYYYMMDD）；逆回购购回按平台规则采用成交日或交收日。"""
    original_date: str
    """原始成交日期或发生日期（YYYYMMDD），不随核算日期调整，用于跨文件去重。"""
    business: str
    """统一后的业务名称，如证券买入、证券卖出、拆出质押购回，决定核算方式。"""
    stock_code: str
    """证券代码，保留前导零；无关联证券的资金业务为空字符串。"""
    stock_name: str
    """流水披露的证券名称，用于展示；未披露时为空字符串。"""
    quantity: Decimal | None
    """成交或变动数量；未披露时为 None，卖出、购回及转托转出统一为负数。"""
    amount: Decimal
    """人民币发生金额，正数表示收款、负数表示付款；交易净收付款已包含手续费。"""
    serial: str
    """券商流水号原文，保留前导零及字母，用于排序和去重；缺失时为空字符串。"""
    line: int
    """记录在来源文件中的行号，从 1 开始，用于定位错误及待确认流水。"""
    fees: dict[str, Decimal]
    """按费用名称记录的人民币金额，如佣金、印花税；未单独披露的费用记为零。"""
    platform: Platform
    """来源券商及其字段、业务和逆回购单位规则，用于平台内核算与去重隔离。"""
    trade_amount: Decimal | None
    """成交金额，优先采用披露值，普通交易可由数量和单价补全；无法确定时为 None。"""
    trade_price: Decimal | None
    """成交单价，普通交易可由金额和数量补全；逆回购为年化利率，缺失时为 None。"""
    transaction_id: str = ""
    """交易编号原文：招商证券的合同编号、东方财富证券的成交编号；缺失时为空字符串。"""
    time: str = ""
    """发生时间原文，用于同日排序；未披露或购回改按交收日核算时为空字符串。"""
    source: str = ""
    """来源流水文件路径，与行号共同定位原始记录；未指定来源时为空字符串。"""

    @property
    def location(self) -> str:
        """返回可追溯至原文件行的描述。"""
        return f"{self.source} 第 {self.line} 行"

    @property
    def repo_identifier(self) -> str:
        """按平台规则提取拆出与购回共用的逆回购编号。"""
        if self.platform.repo_identifier is RepoIdentifier.TRANSACTION_ID:
            return self.transaction_id
        return self.serial

    def require_quantity(self) -> Decimal:
        """为需要数量的业务提供已披露或已推算的数量。"""
        if self.quantity is None:
            names = self.platform.column_names(QUANTITY)
            raise AccountingError.for_entry(self, "缺少成交数量，且无法由成交金额和价格推算",
                                            " / ".join(names) if names else QUANTITY.name)
        return self.quantity


def entry_sort_key(entry: Entry) -> tuple:
    """按核算日期、时间和原始编号稳定排列流水。"""
    serial = (0, int(entry.serial)) if entry.serial.isdecimal() else (1, entry.serial)
    return entry.date, entry.time, serial, entry.line


@dataclass(frozen=True)
class HoldingSummary:
    """保存证券买卖、分红和托管核算的不可变结果。"""

    quantity: Decimal
    cost: Decimal
    realized: Decimal
    distributions: Decimal
    transfer_net: Decimal
    transfer_count: int
    transfer_known: bool
    cost_known: bool
    trades: int


@dataclass(frozen=True)
class RepoSummary:
    """保存逆回购余额及收益指标，不携带未结清合同。"""

    principal: Decimal
    quantity: Decimal
    profit: Decimal
    fees: Decimal
    completed_profit: Decimal
    capital_days: Decimal
    completed_count: int
    rate_known: bool
    trades: int


@dataclass(frozen=True)
class SecuritySummary:
    """组合单只证券的核算摘要，供跨券商汇总和报告使用。"""

    name: str
    holding: HoldingSummary
    repo: RepoSummary
    cash_change: Decimal
    fees: dict[str, Decimal]

    @property
    def profit(self) -> Decimal:
        """返回包含分红及红利税的已实现净收益。"""
        return self.holding.realized + self.holding.distributions + self.repo.profit

    @property
    def trades(self) -> int:
        """返回证券买卖与逆回购拆出的流水笔数。"""
        return self.holding.trades + self.repo.trades


@dataclass
class Report:
    """汇集证券核算、账户资金及尚未识别的业务。"""

    securities: dict[str, SecuritySummary] = field(default_factory=dict)
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    interest: Decimal = ZERO
    adjustment: Decimal = ZERO
    security_turnover: Decimal = ZERO
    repo_turnover: Decimal = ZERO
    fees: dict[str, Decimal] = field(default_factory=dict)
    unknown: list[Entry] = field(default_factory=list)
    businesses: Counter[str] = field(default_factory=Counter)

    @property
    def turnover(self) -> Decimal:
        """返回证券买卖与逆回购拆出的交易总额。"""
        return self.security_turnover + self.repo_turnover

    @property
    def incomplete(self) -> bool:
        """判断收益或投入是否存在未知部分，不包含年化报价缺失。"""
        return bool(self.unknown) or any(
            not security.holding.cost_known or not security.holding.transfer_known
            for security in self.securities.values()
        )

