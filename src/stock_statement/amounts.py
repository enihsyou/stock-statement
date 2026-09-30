"""报表金额的舍入与可见明细合计规则。"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from .attributes import ZERO


def rounded(value: Decimal) -> Decimal:
    """四舍五入至分，并消除负零。"""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) + ZERO


def display_sum(values: Iterable[Decimal]) -> Decimal:
    """先逐项舍入再合计，保持可见明细与合计一致。"""
    return sum((rounded(value) for value in values), ZERO)
