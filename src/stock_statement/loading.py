"""读取流水文件并按出现次数合并重叠导出。"""

from collections import Counter
from pathlib import Path

from .models import Entry, entry_sort_key
from .parsing import parse_entries


def expand_paths(paths: list[Path]) -> list[Path]:
    """按参数顺序展开目录内当前层的文本文件，并稳定排序目录内容。"""
    files = []
    for path in paths:
        if not path.is_dir():
            files.append(path)
            continue
        children = sorted(
            (child for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".txt"),
            key=lambda child: (child.name.casefold(), child.name),
        )
        if not children:
            raise ValueError(f"目录内没有文本文件：{path}")
        files.extend(children)
    return files


def read_entries(path: Path) -> list[Entry]:
    """读取 UTF-8 或 GB18030 导出的单份资金流水。"""
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("gb18030")
    return parse_entries(content, str(path))


def entry_identity(entry: Entry) -> tuple:
    """用业务内容识别同平台不同文件中的重复流水。"""
    return (
        entry.platform.name, entry.date, entry.time, entry.serial,
        entry.business, entry.stock_code, entry.quantity, entry.amount,
        entry.trade_amount, entry.trade_price, tuple(sorted(entry.fees.items())),
    )


def read_files(paths: list[Path]) -> tuple[list[Entry], int]:
    """合并多份流水，保留相同记录在任一文件中的最大出现次数。"""
    entries = []
    seen = Counter()
    duplicates = 0
    for path in paths:
        occurrences = Counter()
        for entry in read_entries(path):
            key = entry_identity(entry)
            occurrences[key] += 1
            if occurrences[key] <= seen[key]:
                duplicates += 1
            else:
                entries.append(entry)
        seen |= occurrences
    if not entries:
        raise ValueError("没有输入流水文件")
    return sorted(entries, key=entry_sort_key), duplicates
