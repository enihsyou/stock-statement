"""证券流水命令行入口。"""

import argparse
from pathlib import Path

from rich.console import Console

from .errors import StatementError
from .ledger import analyze
from .loading import expand_paths, read_files
from .presentation import render_report
from .reporting import build_report_view


def earnings(args: argparse.Namespace) -> None:
    """加载流水并展示历史收益报告。"""
    console = Console()
    try:
        files = expand_paths(args.files)
        entries, duplicates = read_files(files)
        report = analyze(entries)
        view = build_report_view(entries, report, len(files), duplicates)
    except StatementError as exc:
        Console(stderr=True).print(f"无法生成报告：{exc}", style="red", markup=False, soft_wrap=True)
        raise SystemExit(2) from exc
    render_report(console, view)
    if view.incomplete:
        raise SystemExit(1)


def main() -> None:
    """解析命令行参数并执行对应操作。"""
    parser = argparse.ArgumentParser(description="证券流水历史收益分析")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("earnings", help="展示净投入、已实现收益与交易手续费")
    command.add_argument(
        "files", nargs="+", type=Path,
        help="一个或多个流水文件或目录；目录读取当前层所有 .txt 文件（支持 UTF-8 和 GB18030）",
    )
    command.set_defaults(func=earnings)
    args = parser.parse_args()
    args.func(args)
