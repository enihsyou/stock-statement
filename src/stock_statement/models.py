"""流水、证券持仓与账户报告的统一数据模型。"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .attributes import SERIAL, TRANSACTION_ID, ZERO
from .platforms import Platform


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
    quantity: Decimal
    """成交或变动数量；卖出、购回及转托转出统一为负数，逆回购单位由平台规则解释。"""
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
        return {TRANSACTION_ID: self.transaction_id, SERIAL: self.serial}[self.platform.repo_identifier]


def entry_sort_key(entry: Entry) -> tuple:
    """按核算日期、时间和原始编号稳定排列流水。"""
    serial = (0, int(entry.serial)) if entry.serial.isdecimal() else (1, entry.serial)
    return entry.date, entry.time, serial, entry.line


@dataclass
class RepoPosition:
    """保存未购回合同的本金、数量、拆出费用与本金加权报价。"""

    identifier: str
    principal: Decimal
    quantity: Decimal
    fees: Decimal
    quoted_interest: Decimal
    price_known: bool


@dataclass
class Security:
    """累计单只证券的持仓成本、收益和费用。"""

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
    repo_profit: Decimal = ZERO
    repo_fees: Decimal = ZERO
    repo_completed_profit: Decimal = ZERO
    repo_capital_days: Decimal = ZERO
    repo_completed_count: int = 0
    repo_rate_known: bool = True
    repo_positions: list[RepoPosition] = field(default_factory=list)
    cost_known: bool = True
    cash_change: Decimal = ZERO
    trades: int = 0
    fees: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    @property
    def profit(self) -> Decimal:
        """返回包含分红及红利税的已实现净收益。"""
        return self.realized + self.distributions


@dataclass
class Report:
    """汇集证券核算、账户资金及尚未识别的业务。"""

    securities: dict[str, Security] = field(default_factory=dict)
    inflow: Decimal = ZERO
    outflow: Decimal = ZERO
    interest: Decimal = ZERO
    adjustment: Decimal = ZERO
    security_turnover: Decimal = ZERO
    repo_turnover: Decimal = ZERO
    fees: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    unknown: list[Entry] = field(default_factory=list)
    businesses: Counter = field(default_factory=Counter)

    @property
    def turnover(self) -> Decimal:
        """返回证券买卖与逆回购拆出的交易总额。"""
        return self.security_turnover + self.repo_turnover

