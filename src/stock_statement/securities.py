"""证券持仓的移动加权成本、分红和托管核算。"""

from dataclasses import dataclass
from decimal import Decimal

from .attributes import ZERO
from .errors import AccountingError
from .models import Entry, HoldingSummary


@dataclass
class HoldingLedger:
    """在一个券商账户内维护单只证券的持仓状态。"""

    quantity: Decimal = ZERO
    cost: Decimal = ZERO
    realized: Decimal = ZERO
    distributions: Decimal = ZERO
    transfer_net: Decimal = ZERO
    transfer_count: int = 0
    transfer_known: bool = True
    cost_known: bool = True
    trades: int = 0

    def apply(self, entry: Entry) -> bool:
        """核算证券业务，其他业务返回 False。"""
        if entry.business == "证券买入":
            quantity = entry.require_quantity()
            if quantity <= ZERO or entry.amount >= ZERO:
                raise AccountingError.for_entry(entry, "买入数量或金额方向异常")
            self.quantity += quantity
            self.cost -= entry.amount
            self.trades += 1
        elif entry.business == "证券卖出":
            self.realized += entry.amount - self.remove_cost(-entry.require_quantity(), entry)
            self.trades += 1
        elif entry.business in {"转托转出", "转托转入"}:
            self.apply_transfer(entry)
        elif entry.business in {"股息入账", "股息红利税补缴"}:
            self.distributions += entry.amount
        else:
            return False
        return True

    def remove_cost(self, quantity: Decimal, entry: Entry) -> Decimal:
        """按移动加权成本移除持仓，数量不足时停止核算。"""
        if quantity <= ZERO or quantity > self.quantity:
            raise AccountingError.for_entry(
                entry, f"数量无法匹配历史持仓（需减少 {quantity}，已有 {self.quantity}）；"
                "请补充买入或转入数量记录。", "成交数量",
            )
        cost = self.cost if quantity == self.quantity else self.cost * quantity / self.quantity
        self.quantity -= quantity
        self.cost -= cost
        return cost

    def apply_transfer(self, entry: Entry) -> None:
        """按流水披露的估值处理托管转入或转出。"""
        incoming = entry.business == "转托转入"
        quantity = abs(entry.require_quantity())
        if quantity == ZERO:
            raise AccountingError.for_entry(entry, "托管转移数量为零", "成交数量")
        self.transfer_count += 1
        removed = ZERO
        if incoming:
            self.quantity += quantity
        else:
            removed = self.remove_cost(quantity, entry)
        if entry.trade_amount is None:
            self.transfer_known = False
            self.cost_known = False
            return
        value = abs(entry.trade_amount)
        self.transfer_net += value if incoming else -value
        if incoming:
            self.cost += value
        else:
            self.realized += value - removed

    def summarize(self) -> HoldingSummary:
        """生成与后续可变核算状态无关的结果。"""
        return HoldingSummary(
            quantity=self.quantity, cost=self.cost, realized=self.realized,
            distributions=self.distributions, transfer_net=self.transfer_net,
            transfer_count=self.transfer_count, transfer_known=self.transfer_known,
            cost_known=self.cost_known, trades=self.trades,
        )
