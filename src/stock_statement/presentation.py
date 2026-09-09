"""以完整表格展示证券流水核算结果。"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .attributes import FEE_COLUMNS, FEES, ZERO
from .models import Entry, Report, Security


def rounded(value: Decimal) -> Decimal:
    """将金额按四舍五入保留到分。"""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) + ZERO


def money(value: Decimal) -> str:
    """将金额显示为带千分位的两位小数。"""
    return f"{rounded(value):,.2f}"


def display_sum(values: Iterable[Decimal]) -> Decimal:
    """先逐项舍入再合计，保持明细与概览一致。"""
    return sum((rounded(value) for value in values), ZERO)


def rate_text(profit: Decimal, investment: Decimal) -> str:
    """根据净投入生成收益率说明。"""
    return f"收益率 {profit / investment:.2%}" if investment else "收益率不可计算"


def change_style(value: Decimal) -> str:
    """用红涨绿跌表示有符号的金融变化。"""
    return "red" if value > ZERO else "green" if value < ZERO else ""


@dataclass(frozen=True)
class DisplayColumn:
    """定义报表列的名称、金融领域和数值呈现方式。"""

    name: str
    style: str = ""
    number: bool = False
    signed: bool = False
    change: bool = False

    def format(self, value: str | Text | Decimal | int | None) -> Text:
        """按列语义格式化单元格并应用颜色。"""
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
    DisplayColumn("证券代码", "cyan"),
    DisplayColumn("最新名称", "cyan"),
    DisplayColumn("已实现净收益", "magenta", change=True),
    DisplayColumn("手续费合计", "yellow"),
    *(DisplayColumn(attribute.name, attribute.style) for attribute in FEES),
    DisplayColumn("交易笔数", "blue", number=True),
    DisplayColumn("分红净额", "magenta", change=True),
    DisplayColumn("剩余数量", "blue", number=True),
    DisplayColumn("剩余成本", "blue"),
    DisplayColumn("托管净转入", "magenta", signed=True, change=True),
    DisplayColumn("备注"),
)
NUMERIC_COLUMNS = tuple(
    column for column in SECURITY_COLUMNS
    if column.name not in {"证券代码", "最新名称", "备注"}
)


def print_table(
    console: Console,
    columns: tuple[DisplayColumn, ...],
    rows: list[dict],
    title: str = "",
    total: dict | None = None,
) -> None:
    """按完整单元格宽度输出表格，避免终端与重定向裁剪内容。"""
    records = [*rows, *([total] if total is not None else [])]
    cells = [[column.format(row[column.name]) for column in columns] for row in records]
    widths = [
        max([Text(column.name).cell_len, *(row[index].cell_len for row in cells)])
        for index, column in enumerate(columns)
    ]
    width = max(sum(widths) + 3 * len(columns) + 1, Text(title).cell_len)
    table = Table(title=title or None, width=width, padding=(0, 1))
    text_columns = {"证券代码", "最新名称", "备注", "项目", "来源", "平台", "日期", "业务", "代码"}
    for column, cell_width in zip(columns, widths):
        table.add_column(
            column.name, header_style=column.style or "bold", min_width=cell_width,
            no_wrap=True, justify="left" if column.name in text_columns else "right",
        )
    for index, row in enumerate(cells):
        table.add_row(*row, style="bold" if total is not None and index == len(cells) - 1 else None)
    console.print(table, width=width, crop=False, soft_wrap=True)


def security_values(security: Security) -> dict:
    """提取单只证券的具名展示数值。"""
    return {
        "已实现净收益": security.profit if security.cost_known else None,
        "手续费合计": sum(security.fees.values(), ZERO),
        **{name: security.fees[name] for name in FEE_COLUMNS},
        "交易笔数": security.trades,
        "分红净额": security.distributions,
        "剩余数量": security.quantity + security.repo_quantity,
        "剩余成本": security.cost + security.repo_principal if security.cost_known else None,
        "托管净转入": security.transfer_net if security.transfer_known else None,
    }


def report_rows(report: Report) -> tuple[list[dict], dict]:
    """按收益排列证券，并汇总已知金额及未归属证券的费用。"""
    ordered = sorted(
        report.securities.items(),
        key=lambda item: (item[1].cost_known, item[1].profit), reverse=True,
    )
    rows = []
    for code, security in ordered:
        notes = []
        if not security.cost_known:
            notes.append("收益及成本不完整")
        if not security.transfer_known:
            notes.append("托管成交金额缺失")
        rows.append({
            "证券代码": code, "最新名称": security.name,
            **security_values(security), "备注": "；".join(notes),
        })
    unassigned = {
        name: report.fees[name] - sum((s.fees[name] for s in report.securities.values()), ZERO)
        for name in FEE_COLUMNS
    }
    if any(unassigned.values()):
        unassigns: dict[str, object] = {column.name: ZERO for column in NUMERIC_COLUMNS}
        unassigns.update(unassigned)
        unassigns.update({
            "已实现净收益": None, "手续费合计": sum(unassigned.values(), ZERO),
            "剩余成本": None, "托管净转入": None,
        })
        rows.append({ "证券代码": "—", "最新名称": "未归属证券", **unassigns, "备注": "" })
    totals: dict[str, object] = {"证券代码": "合计", "最新名称": "", "备注": ""}
    for column in NUMERIC_COLUMNS:
        values = (row[column.name] for row in rows if row[column.name] is not None)
        totals[column.name] = sum(values, ZERO) if column.number else display_sum(values)
    return rows, totals


def show_overview(console: Console, report: Report, totals: dict, incomplete: bool) -> None:
    """显示净投入、已实现收益与剩余成本概览。"""
    fees = sum(report.fees.values(), ZERO)
    share = f"占交易手续费 {report.fees['印花税'] / fees:.2%}" if fees else "无交易手续费"
    investment = report.inflow - report.outflow + totals["托管净转入"]
    profit = totals["已实现净收益"] + rounded(report.interest)
    profit_note = ("收益不完整；" if incomplete else "") + rate_text(profit, investment)
    cost_note = "成本不完整，仅合计已知项" if any(not s.cost_known for s in report.securities.values()) else ""
    rows = [
        ("银行流入", report.inflow, "", "magenta", False),
        ("银行流出", report.outflow, "", "magenta", False),
        ("现金净投入", report.inflow - report.outflow, "仅银行转账", "magenta", True),
        ("已实现净收益", profit, profit_note, "magenta", True),
        ("    其中：资金利息", report.interest, "", "magenta", True),
        ("交易手续费合计", totals["手续费合计"], "不再次扣减", "yellow", False),
        ("    其中：印花税", totals["印花税"], share, "yellow", False),
        ("剩余成本", totals["剩余成本"], cost_note, "blue", False),
        ("    其中：逆回购本金", display_sum(s.repo_principal for s in report.securities.values()), "", "blue", False),
        ("逆回购交收记录", report.adjustment, "与购回匹配，不重复计入收益", "magenta", False),
    ]
    if any(s.transfer_count for s in report.securities.values()):
        missing = any(not s.transfer_known for s in report.securities.values())
        rows[3:3] = [
            ("托管净转入", totals["托管净转入"], "成交金额缺失，仅合计已知项" if missing else "转入为正，转出为负", "magenta", True),
            ("账户净投入", investment, "不完整" if missing else "", "magenta", True),
        ]
    columns = (DisplayColumn("项目"), DisplayColumn("金额（元）"), DisplayColumn("备注"))
    formatted = []
    for label, amount, note, style, change in rows:
        amount_column = DisplayColumn("金额", style, signed=label == "托管净转入", change=change)
        formatted.append({
            "项目": Text(label, style=style),
            "金额（元）": amount_column.format(amount), "备注": Text(note),
        })
    print_table(console, columns, formatted)


def show_securities(console: Console, rows: list[dict], totals: dict, show_transfers: bool) -> None:
    """展示证券明细及费用分项。"""
    columns = tuple(
        column for column in SECURITY_COLUMNS
        if (column.name != "托管净转入" or show_transfers)
        and (column.name != "备注" or any(row["备注"] for row in rows))
    )
    print_table(console, columns, rows, title="按证券汇总及交易手续费明细", total=totals)


def show_unknown(console: Console, entries: list[Entry]) -> None:
    """列出尚未纳入核算的流水及其文件位置。"""
    columns = (DisplayColumn("来源"), DisplayColumn("平台"), DisplayColumn("日期"), DisplayColumn("业务"),
               DisplayColumn("代码", "cyan"), DisplayColumn("数量", "blue", number=True), DisplayColumn("金额", "magenta", change=True))
    rows = [{"来源": entry.location, "平台": entry.platform.name, "日期": entry.date, "业务": entry.business,
             "代码": entry.stock_code, "数量": entry.quantity, "金额": entry.amount} for entry in entries]
    print_table(console, columns, rows, title="待确认流水：未纳入收益与净投入，报告不完整")


def render_report(
    console: Console, entries: list[Entry], report: Report, files_count: int, duplicates: int,
) -> bool:
    """展示完整报告，并返回是否存在无法确定的核算结果。"""
    rows, totals = report_rows(report)
    incomplete = bool(report.unknown) or any(
        not s.cost_known or not s.transfer_known for s in report.securities.values()
    )
    platforms = {entry.platform.name: entry.platform for entry in entries}
    console.print(
        f"历史收益 · {'、'.join(platforms)} · {entries[0].date}—{entries[-1].date} · "
        f"{len(entries)} 条流水 · {files_count} 个文件", markup=False, soft_wrap=True,
    )
    if duplicates:
        console.print(f"已去除跨文件重复流水 {duplicates} 条。", soft_wrap=True)
    show_overview(console, report, totals, incomplete)
    show_securities(console, rows, totals, any(s.transfer_count for s in report.securities.values()))
    notes = [
        "收益采用移动加权成本，包含分红、补缴红利税及逆回购收益，不计算浮盈浮亏。费用已包含于净收付款。",
        "金额逐证券四舍五入至分后合计，概览与明细使用相同口径。剩余数量含逆回购原始数量，各证券单位不一定相同。",
        "现金净投入只含银行转账；账户净投入另含托管净转入。交易笔数统计买入、卖出和逆回购拆出记录。",
        "托管转移依流水披露的成交金额或数量与单价估值。",
    ]
    if len(platforms) > 1:
        notes.append("各平台分别核算后按证券汇总；托管转移按各账户记录金额计价，跨平台转移不另作抵消。")
    notes.extend(dict.fromkeys(note for platform in platforms.values() for note in platform.notes))
    for note in notes:
        console.print(note, markup=False, soft_wrap=True)
    if report.unknown:
        show_unknown(console, report.unknown)
    if incomplete:
        console.print("报告不完整：存在未知业务、成本或托管成交金额缺失；— 表示无法确定，合计仅包含已知项。", style="yellow", soft_wrap=True)
    return incomplete
