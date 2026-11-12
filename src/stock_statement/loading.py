"""读取流水文件并按出现次数合并重叠导出。"""

from collections import Counter, defaultdict
from pathlib import Path

from .errors import ParsingError, StatementError
from .models import Entry, entry_sort_key
from .parsing import TradeTime, is_trade_time_file, parse_entries, parse_trade_times


def expand_paths(paths: list[Path]) -> list[Path]:
    """按参数顺序展开目录内当前层的文本文件，并稳定排序目录内容。"""
    files = []
    for path in paths:
        if not path.is_dir():
            files.append(path)
            continue
        try:
            children = sorted(
                (child for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".txt"),
                key=lambda child: (child.name.casefold(), child.name),
            )
        except OSError as exc:
            raise StatementError(f"无法读取目录：{exc}", source=str(path)) from exc
        if not children:
            raise StatementError("目录内没有文本文件", source=str(path))
        files.extend(children)
    return files


def read_entries(path: Path) -> list[Entry] | list[TradeTime]:
    """读取 UTF-8 或 GB18030 导出的流水或招商历史成交查询。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StatementError(f"无法读取文件：{exc}", source=str(path)) from exc
    return read_bytes(raw, str(path))


def read_bytes(raw: bytes, source: str = "") -> list[Entry] | list[TradeTime]:
    """CLI 与内存上传共用解码和解析入口。"""
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            number = raw[:exc.start].count(b"\n") + 1
            raise ParsingError("文件无法按 UTF-8 或 GB18030 解码", source=source, line=number) from exc
    return (parse_trade_times(content, source) if is_trade_time_file(content)
            else parse_entries(content, source))


def entry_identity(entry: Entry) -> tuple:
    """用平台、股东账户、证券和原始编号识别跨文件重复流水。"""
    return (entry.platform.name, entry.shareholder_code, entry.original_date,
            entry.business, entry.stock_code,
            entry.transaction_id or None, entry.serial or None)


def read_files(paths: list[Path]) -> tuple[list[Entry], int]:
    """合并多份流水，保留相同记录在任一文件中的最大出现次数。"""
    return merge_entries(read_entries(path) for path in paths)


def merge_entries(batches) -> tuple[list[Entry], int]:
    """按文件批次去重，并用招商查询补齐唯一匹配的成交时间。"""
    entries = []
    seen = Counter()
    duplicates = 0
    trade_times = defaultdict(set)
    for batch in batches:
        if batch and isinstance(batch[0], TradeTime):
            for record in batch:
                key = (record.date, record.stock_code, record.shareholder_code,
                       record.order_id, record.business, record.quantity, record.price)
                trade_times[key].add(record.time)
            continue
        occurrences = Counter()
        for entry in batch:
            key = entry_identity(entry)
            occurrences[key] += 1
            if occurrences[key] <= seen[key]:
                duplicates += 1
            else:
                entries.append(entry)
        seen |= occurrences
    if not entries:
        raise StatementError("没有输入流水文件")
    for entry in entries:
        if entry.platform.name != "招商证券" or entry.time or not (
            entry.shareholder_code and entry.transaction_id
            and entry.quantity is not None and entry.trade_price is not None
        ):
            continue
        key = (entry.original_date, entry.stock_code, entry.shareholder_code,
               entry.transaction_id, entry.business, abs(entry.quantity), entry.trade_price)
        candidates = trade_times[key]
        if len(candidates) == 1:
            entry.time = next(iter(candidates))
    return sorted(entries, key=entry_sort_key), duplicates
