"""生成与券商导出布局一致的匿名资金流水。"""

from pathlib import Path

DIGITS = "零一二三四五六七八九"


def security_identity(index: int) -> tuple[str, str]:
    """返回编号和中文数字一一对应的四字证券名称。"""
    if not 0 <= index < 100:
        raise ValueError("测试证券序号应在 0 到 99 之间")
    return f"{index:06d}", f"证券{DIGITS[index // 10]}{DIGITS[index % 10]}"


def write_statement(path: Path, columns: list[str], rows: list[dict[str, str]],
                    encoding: str = "utf-8") -> Path:
    """根据字段和记录生成保留空列的固定宽度流水文件。"""
    widths = {name: max(len(str(row.get(name, "")).encode("gb18030"))
                        for row in [{name: name}, *rows]) + 4 for name in columns}

    def format_row(row):
        return b"".join(str(row.get(name, "")).encode("gb18030").ljust(widths[name])
                        for name in columns).decode("gb18030")

    content = "\n".join([format_row({name: name for name in columns}),
                         *(format_row(row) for row in rows)]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode(encoding))
    return path
