"""使用 Rich 排版报告投影，不承担核算或指标汇总。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .amounts import rounded
from .attributes import FEE_COLUMNS, ZERO
from .models import Entry
from .reporting import OverviewKey, OverviewRow, ReportView, SecurityRow, SecurityTotals

type Cell = str | Text | Decimal | int | None
type DisplayRecord = Mapping[str, Cell]


def money(value: Decimal) -> str:
    """显示带千分位的两位小数金额。"""
    return f"{rounded(value):,.2f}"


def change_style(value: Decimal) -> str:
    """用红涨绿跌表达有符号的变动。"""
    return "red" if value > ZERO else "green" if value < ZERO else ""


@dataclass(frozen=True)
class DisplayColumn:
    """声明稳定数据键、标题、格式、对齐及隐藏策略。"""

    key: str
    title: str
    style: str = ""
    number: bool = False
    signed: bool = False
    change: bool = False
    align: Literal["left", "right"] = "right"
    hide: Literal["never", "all_zero", "no_transfers", "empty"] = "never"

    def format(self, value: Cell) -> Text:
        """格式化单元格，未知值显示为破折号。"""
        if value is None:
            return Text("—", style="dim")
        if isinstance(value, Text):
            return value
        if isinstance(value, str):
            return Text(value, style=self.style)
        amount = Decimal(value)
        if self.number:
            content = f"{amount:f}"
        elif self.signed and rounded(amount):
            content = f"{rounded(amount):+,.2f}"
        else:
            content = money(amount)
        return Text(content, style=change_style(amount) if self.change else self.style)


SECURITY_COLUMNS = (
    DisplayColumn("code", "证券代码", "cyan", align="left"),
    DisplayColumn("name", "最新名称", "cyan", align="left"),
    DisplayColumn("profit", "已实现净收益", "magenta", change=True),
    DisplayColumn("fee_total", "手续费合计", "yellow", hide="all_zero"),
    *(DisplayColumn(name, name, "yellow", hide="all_zero") for name in FEE_COLUMNS),
    DisplayColumn("trades", "交易笔数", "blue", number=True),
    DisplayColumn("distributions", "分红净额", "magenta", change=True, hide="all_zero"),
    DisplayColumn("quantity", "剩余数量", "blue", number=True),
    DisplayColumn("cost", "剩余成本", "blue"),
    DisplayColumn("transfer_net", "托管净转入", "magenta", signed=True, change=True, hide="no_transfers"),
    DisplayColumn("notes", "备注", align="left", hide="empty"),
)

OVERVIEW_COLUMNS = {
    OverviewKey.INVESTMENT: DisplayColumn("amount", "账户净投入", "magenta", change=True),
    OverviewKey.CASH_INVESTMENT: DisplayColumn("amount", "    其中：现金净投入", "magenta", change=True),
    OverviewKey.TRANSFER_NET: DisplayColumn("amount", "    其中：托管净转入", "magenta", signed=True, change=True),
    OverviewKey.PROFIT: DisplayColumn("amount", "已实现净收益", "magenta", change=True),
    OverviewKey.INTEREST: DisplayColumn("amount", "    其中：资金利息", "magenta", change=True),
    OverviewKey.REPO_PROFIT: DisplayColumn("amount", "    其中：逆回购净收益", "magenta", change=True),
    OverviewKey.TURNOVER: DisplayColumn("amount", "交易总额", "blue"),
    OverviewKey.SECURITY_TURNOVER: DisplayColumn("amount", "    其中：证券交易总额", "blue"),
    OverviewKey.REPO_TURNOVER: DisplayColumn("amount", "    其中：逆回购交易总额", "blue"),
    OverviewKey.FEES: DisplayColumn("amount", "交易手续费合计", "yellow"),
    OverviewKey.STAMP_TAX: DisplayColumn("amount", "    其中：印花税", "yellow"),
    OverviewKey.COMMISSION: DisplayColumn("amount", "    其中：佣金", "yellow"),
    OverviewKey.REPO_FEES: DisplayColumn("amount", "    其中：逆回购手续费", "yellow"),
    OverviewKey.COST: DisplayColumn("amount", "剩余成本", "blue"),
    OverviewKey.HOLDING_COST: DisplayColumn("amount", "    其中：证券持仓成本", "blue"),
    OverviewKey.REPO_PRINCIPAL: DisplayColumn("amount", "    其中：逆回购本金", "blue"),
}


def print_table(
    console: Console, columns: tuple[DisplayColumn, ...], rows: Sequence[DisplayRecord],
    title: str = "", total: DisplayRecord | None = None,
) -> None:
    """按完整单元格宽度排版，保留终端和重定向中的全部文本。"""
    records = [*rows, *([total] if total is not None else [])]
    cells = [[column.format(row[column.key]) for column in columns] for row in records]
    widths = [
        max([Text(column.title).cell_len, *(row[index].cell_len for row in cells)])
        for index, column in enumerate(columns)
    ]
    width = max(sum(widths) + 3 * len(columns) + 1, Text(title).cell_len)
    table = Table(title=title or None, width=width, padding=(0, 1))
    for column, cell_width in zip(columns, widths):
        table.add_column(column.title, header_style=column.style or "bold", min_width=cell_width,
                         no_wrap=True, justify=column.align)
    for index, row in enumerate(cells):
        table.add_row(*row, style="bold" if total is not None and index == len(cells) - 1 else None)
    console.print(table, width=width, crop=False, soft_wrap=True)


def security_cells(row: SecurityRow) -> DisplayRecord:
    """将具名报表字段绑定至展示列。"""
    return {
        "code": row.code, "name": row.name, "profit": row.profit, "fee_total": row.fee_total,
        **{name: row.fees.get(name, ZERO) for name in FEE_COLUMNS},
        "trades": row.trades, "distributions": row.distributions,
        "quantity": row.quantity, "cost": row.cost, "transfer_net": row.transfer_net, "notes": row.notes,
    }


def hidden_reason(column: DisplayColumn, rows: Sequence[DisplayRecord], show_transfers: bool) -> str | None:
    """根据列声明判断隐藏原因。"""
    if column.hide == "no_transfers" and not show_transfers:
        return "无有效托管业务"
    if column.hide == "all_zero" and all(row[column.key] == ZERO for row in rows):
        return "所有明细均为 0"
    if column.hide == "empty" and not any(row[column.key] for row in rows):
        return "无备注内容"
    return None


def hidden_columns_note(hidden: list[tuple[str, str]]) -> str:
    """将隐藏列按原因归组为简洁说明。"""
    names_by_reason: dict[str, list[str]] = {}
    for name, reason in hidden:
        names_by_reason.setdefault(reason, []).append(name)
    groups = [f"{'、'.join(names)}（{reason}）" for reason, names in names_by_reason.items()]
    return f"已隐藏列：{'；'.join(groups)}。"


def show_securities(
    console: Console, rows: Sequence[SecurityRow], totals: SecurityTotals, show_transfers: bool,
) -> None:
    """展示证券明细、合计及隐藏列说明。"""
    records = [security_cells(row) for row in rows]
    decisions = [(column, hidden_reason(column, records, show_transfers)) for column in SECURITY_COLUMNS]
    columns = tuple(column for column, reason in decisions if reason is None)
    print_table(console, columns, records, title="按证券汇总及交易手续费明细", total=security_cells(totals))
    hidden = [(column.title, reason) for column, reason in decisions if reason is not None]
    if hidden:
        console.print(hidden_columns_note(hidden), style="dim", markup=False, soft_wrap=True)


def show_overview(console: Console, rows: tuple[OverviewRow, ...]) -> None:
    """为已计算的概览指标应用标题和金融颜色。"""
    records = []
    for row in rows:
        column = OVERVIEW_COLUMNS[row.key]
        records.append({
            "item": Text(column.title, style=column.style),
            "amount": column.format(row.amount), "notes": Text(row.note),
        })
    columns = (DisplayColumn("item", "项目", align="left"), DisplayColumn("amount", "金额（元）"),
               DisplayColumn("notes", "备注", align="left"))
    print_table(console, columns, records)


def show_unknown(console: Console, entries: tuple[Entry, ...]) -> None:
    """展示待确认流水，包括未披露的数量及来源位置。"""
    columns = (
        DisplayColumn("source", "来源", align="left"), DisplayColumn("platform", "平台", align="left"),
        DisplayColumn("date", "日期", align="left"), DisplayColumn("business", "业务", align="left"),
        DisplayColumn("code", "代码", "cyan", align="left"),
        DisplayColumn("quantity", "数量", "blue", number=True),
        DisplayColumn("amount", "金额", "magenta", change=True),
    )
    rows = [{"source": entry.location, "platform": entry.platform.name, "date": entry.date,
             "business": entry.business, "code": entry.stock_code,
             "quantity": entry.quantity, "amount": entry.amount} for entry in entries]
    print_table(console, columns, rows, title="待确认流水：未纳入收益与净投入，报告不完整")


def render_report(console: Console, view: ReportView) -> None:
    """排版完整报告，完整性和退出状态由调用方处理。"""
    metadata = view.metadata
    console.print(
        f"历史收益 · {'、'.join(metadata.platforms)} · {metadata.start_date}—{metadata.end_date} · "
        f"{metadata.entries_count} 条流水 · {metadata.files_count} 个文件", markup=False, soft_wrap=True,
    )
    if metadata.duplicates:
        console.print(f"已去除跨文件重复流水 {metadata.duplicates} 条。", soft_wrap=True)
    show_overview(console, view.overview)
    show_securities(console, view.securities, view.totals, view.show_transfers)
    for note in metadata.notes:
        console.print(note, markup=False, soft_wrap=True)
    if view.unknown:
        show_unknown(console, view.unknown)
    if view.incomplete:
        console.print("报告不完整：存在未知业务、成本或托管成交金额缺失；— 表示无法确定，合计仅包含已知项。",
                      style="yellow", soft_wrap=True)
