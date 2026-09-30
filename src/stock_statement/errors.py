"""携带来源位置的可预期输入与核算错误。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Entry


class StatementError(ValueError):
    """保存文件、行、字段和业务上下文，供命令行统一报告。"""

    def __init__(
        self, message: str, *, source: str = "", line: int | None = None,
        field: str | None = None, business: str = "", code: str = "",
    ) -> None:
        self.source = source
        self.line = line
        self.field = field
        self.business = business
        self.code = code
        context = [source] if source else []
        if line is not None:
            context.append(f"第 {line} 行")
        if field:
            context.append(f"字段「{field}」")
        if business:
            context.append(f"业务「{business}」")
        if code:
            context.append(f"证券 {code}")
        super().__init__(f"{' '.join(context)}：{message}" if context else message)


class ParsingError(StatementError):
    """表示文件布局或字段内容无法解析。"""


class AccountingError(StatementError):
    """表示某条流水无法按业务规则核算。"""

    @classmethod
    def for_entry(cls, entry: Entry, message: str, field: str | None = None) -> AccountingError:
        """将核算失败关联到原始流水。"""
        return cls(message, source=entry.source, line=entry.line, field=field,
                   business=entry.business, code=entry.stock_code)
