import argparse
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .ledger import FEE_COLUMNS, ZERO, analyze, read_entries


def rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) + ZERO


def money(value: Decimal) -> str:
    return f"{rounded(value):,.2f}"


def display_sum(values) -> Decimal:
    return sum((rounded(value) for value in values), ZERO)


def security_values(security) -> list:
    known = security.cost_known
    return [
        security.profit if known else None,
        sum(security.fees.values(), ZERO),
        *(security.fees[name] for name in FEE_COLUMNS),
        security.trades,
        security.distributions,
        security.quantity + security.repo_quantity,
        security.cost + security.repo_principal if known else None,
        security.transfer_cost if known else None,
    ]


def show_overview(console, report, totals, incomplete) -> None:
    fees = sum(report.fees.values(), ZERO)
    stamp = report.fees["印花税"]
    share = f"占交易手续费 {stamp / fees:.2%}" if fees else "无交易手续费"
    table = Table("项目", "金额（元）", "备注")
    for label, amount, note in (
        ("银行流入", report.inflow, ""),
        ("银行流出", report.outflow, ""),
        ("现金净投入", report.inflow - report.outflow, "仅银行转账"),
        (
            "已实现净收益",
            totals[0] + rounded(report.interest),
            "收益不完整" if incomplete else "",
        ),
        ("    其中：资金利息", report.interest, ""),
        ("交易手续费合计", totals[1], "不再次扣减"),
        ("    其中：印花税", totals[2], share),
        ("转托转出成本", totals[-1], "不计收益"),
        ("剩余成本", totals[-2], ""),
        (
            "    其中：逆回购本金",
            display_sum(s.repo_principal for s in report.securities.values()),
            "",
        ),
        (
            "逆回购交收记录",
            report.adjustment,
            "与购回匹配，不重复计入收益",
        ),
    ):
        table.add_row(label, money(amount), note)
    console.print(table)


def format_values(values) -> list[str]:
    result = []
    for index, value in enumerate(values):
        if value is None:
            result.append("—")
        elif index in {9, 11}:
            result.append(f"{value:f}" if isinstance(value, Decimal) else str(value))
        else:
            result.append(money(value))
    return result


def show_securities(console, rows, totals) -> None:
    table = Table(title="按证券汇总及交易手续费明细", min_width=300)
    titles = (
        "证券代码",
        "最新名称",
        "已实现净收益",
        "手续费合计",
        *FEE_COLUMNS,
        "交易笔数",
        "分红净额",
        "剩余数量",
        "剩余成本",
        "转出成本",
        "备注",
    )
    for title in titles:
        table.add_column(
            title,
            justify="left" if title in {"证券代码", "最新名称", "备注"} else "right",
            no_wrap=True,
        )
    for code, name, values, note in rows:
        table.add_row(code, name, *format_values(values), note)
    table.add_row(
        "合计",
        "",
        *format_values(totals),
        "",
        style="bold",
    )
    console.print(table, crop=False, overflow="ignore", soft_wrap=True)


def report_rows(report):
    ordered = sorted(
        report.securities.items(),
        key=lambda item: (item[1].cost_known, item[1].profit),
        reverse=True,
    )
    rows = [
        (
            code,
            s.name,
            security_values(s),
            "转入成本缺失" if not s.cost_known else "",
        )
        for code, s in ordered
    ]
    unassigned = {
        name: report.fees[name]
        - sum((s.fees[name] for s in report.securities.values()), ZERO)
        for name in FEE_COLUMNS
    }
    if any(unassigned.values()):
        rows.append(
            (
                "—",
                "未归属证券",
                [
                    None,
                    sum(unassigned.values(), ZERO),
                    *unassigned.values(),
                    0,
                    ZERO,
                    ZERO,
                    None,
                    None,
                ],
                "",
            )
        )
    totals = [
        display_sum(row[2][index] for row in rows if row[2][index] is not None)
        if index not in {9, 11}
        else sum((row[2][index] for row in rows), ZERO)
        for index in range(14)
    ]
    return rows, totals


def show_unknown(console, entries) -> None:
    table = Table(title="待确认流水：未纳入收益与净投入，报告不完整")
    for title in ("文件行号", "日期", "业务", "代码", "数量", "金额"):
        table.add_column(title)
    for entry in entries:
        table.add_row(
            str(entry.line),
            entry.date,
            entry.business,
            entry.stock_code,
            str(entry.quantity),
            money(entry.amount),
        )
    console.print(table)


def earnings(args: argparse.Namespace) -> None:
    console = Console()
    try:
        entries = read_entries(args.file)
        report = analyze(entries)
    except (OSError, ValueError, UnicodeError) as exc:
        console.print(f"无法生成报告：{exc}", style="red", markup=False)
        raise SystemExit(2) from exc
    rows, totals = report_rows(report)
    incomplete = bool(report.unknown) or any(
        not s.cost_known for s in report.securities.values()
    )
    console.print(
        f"历史收益 · {entries[0].platform} · {entries[0].date}—{entries[-1].date} · {len(entries)} 条流水",
        markup=False,
    )
    show_overview(console, report, totals, incomplete)
    show_securities(console, rows, totals)
    console.print(
        "收益采用移动加权成本，包含分红、补缴红利税及逆回购收益，不计算浮盈浮亏。费用已包含于净收付款。"
    )
    console.print(
        "金额逐证券四舍五入至分后合计，概览与明细使用相同口径。剩余数量含逆回购原始数量，各证券单位不一定相同。"
    )
    console.print(
        "现金净投入只含银行转账；转出成本不是转出市值。交易笔数统计买入、卖出和逆回购拆出记录。"
    )
    if entries[0].platform == "东方财富证券":
        console.print(
            "东方财富未单列经手费、证管费、结算费，显示 0 表示无独立披露金额；其他费用保留原值。同日同证券同数量的零金额撤指转出与指定入账配对抵消。"
        )
    if report.unknown:
        show_unknown(console, report.unknown)
    if incomplete:
        console.print(
            "报告不完整：未知业务或转入原始成本缺失；— 表示无法确定，合计不包含这些证券的收益与成本。",
            style="yellow",
        )
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="证券流水历史收益分析")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("earnings", help="展示净投入、已实现收益与交易手续费")
    command.add_argument(
        "file",
        type=Path,
        help="招商资金流水或东方财富交割单（优先 GB18030，失败后 UTF-8）",
    )
    command.set_defaults(func=earnings)
    args = parser.parse_args()
    args.func(args)
