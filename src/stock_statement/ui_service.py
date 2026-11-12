"""UI 应用服务，共用账本、报告投影与策略内核。"""
from collections import defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from .aggregation import merge_report
from .amounts import rounded
from .dates import china_today
from .ledger import analyze
from .loading import merge_entries, read_bytes
from .models import Entry, Report
from .presentation import (
    OVERVIEW_COLUMNS,
    SECURITY_COLUMNS,
    hidden_reason,
    security_cells,
)
from .reporting import build_report_view
from .strategies import (
    FeeSettings,
    calculate_strategies,
    event_id,
    iso_day,
    profitable_price,
)


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value) and not isinstance(value, type):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def security_market(code: str) -> str | None:
    if len(code) != 6 or not code.isdecimal() or code.startswith(("204", "1318")):
        return None
    if code.startswith(("6", "5")):
        return "SH"
    if code.startswith(("0", "3", "15", "16", "18")):
        return "SZ"
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return None


@dataclass
class Analysis:
    accounts: dict[str, list[Entry]]
    report: dict
    securities: list[dict]


def create_analysis(files: list[tuple[str, str, bytes]]) -> Analysis:
    groups = defaultdict(list)
    for name, account, raw in files:
        groups[account].append(read_bytes(raw, name))
    accounts = {}
    merged = Report()
    entries = []
    duplicates = 0
    for account, batches in groups.items():
        batch, count = merge_entries(batches)
        accounts[account] = batch
        merge_report(merged, analyze(batch))
        entries.extend(batch)
        duplicates += count
    view = build_report_view(entries, merged, len(files), duplicates)
    records = [security_cells(row) for row in view.securities]
    columns = [column for column in SECURITY_COLUMNS if hidden_reason(column, records, view.show_transfers) is None]

    def row_cells(row) -> dict[str, str | None]:
        cells = security_cells(row)
        result = {}
        for column in columns:
            value = cells[column.key]
            result[column.key] = (None if value is None else f"{rounded(value):.2f}"
                                  if isinstance(value, Decimal) and not column.number else str(value))
        return result

    securities = [{"id": f"{security_market(row.code)}:{row.code}", "code": row.code,
                   "name": row.name, "market": security_market(row.code)}
                  for row in view.securities if security_market(row.code)]
    parents = {
        "CASH_INVESTMENT": "INVESTMENT", "TRANSFER_NET": "INVESTMENT",
        "INTEREST": "PROFIT", "REPO_PROFIT": "PROFIT",
        "SECURITY_TURNOVER": "TURNOVER", "REPO_TURNOVER": "TURNOVER",
        "STAMP_TAX": "FEES", "COMMISSION": "FEES", "REPO_FEES": "FEES",
        "HOLDING_COST": "COST", "REPO_PRINCIPAL": "COST",
    }
    report = {"metadata": json_value(view.metadata), "incomplete": view.incomplete,
              "columns": [{"key": c.key, "title": c.title, "numeric": c.align == "right", "change": c.change} for c in columns],
              "rows": [row_cells(row) for row in view.securities], "totals": row_cells(view.totals),
              "overview": [{"key": row.key.name, "title": OVERVIEW_COLUMNS[row.key].title.strip().removeprefix("其中："),
                            "parent": parents.get(row.key.name),
                            "amount": f"{rounded(row.amount):.2f}", "note": row.note,
                            "change": OVERVIEW_COLUMNS[row.key].change,
                            "tone": OVERVIEW_COLUMNS[row.key].style} for row in view.overview],
              "unknown": [{"source": e.source, "line": e.line, "business": e.business, "code": e.stock_code} for e in view.unknown]}
    return Analysis(accounts, report, securities)


def stock_detail(analysis: Analysis, security: str, account: str | None, price: Decimal | None,
                 candidate: Decimal | None, lots: int, fees: FeeSettings) -> dict:
    market, code = security.split(":")
    selected = {key: [e for e in entries if e.stock_code == code]
                for key, entries in analysis.accounts.items() if account is None or key == account}
    selected = {key: entries for key, entries in selected.items() if entries}
    results = []
    events = []
    scopes = []
    for label, batch in selected.items():
        platforms = sorted({entry.platform.name for entry in batch})
        for platform in platforms:
            scope = label if len(platforms) == 1 else f"{label} / {platform}"
            scopes.append((scope, [entry for entry in batch if entry.platform.name == platform]))
    for key, entries in scopes:
        holding = analyze(entries).securities[code].holding
        holding_average = holding.cost / holding.quantity if holding.cost_known and holding.quantity > 0 else None
        holding_cost = holding.cost if holding.cost_known else None
        market_value = holding.quantity * price if price is not None else None
        holding_profit = market_value - holding.cost if market_value is not None and holding.cost_known else None
        profit_rate = holding_profit / holding.cost * 100 if holding_profit is not None and holding.cost > 0 else None
        # 外部账户标签也进入事件 ID，避免两个真实账户的来源 ID 碰撞。
        for e in entries:
            events.append({"id": f"{key}:{event_id(e)}", "account": key, "platform": e.platform.name,
                           "date": iso_day(e.date), "time": e.time or None, "business": e.business,
                           "quantity": str(e.quantity) if e.quantity is not None else None,
                           "price": str(e.trade_price) if e.trade_price is not None else None,
                           "amount": str(e.amount), "fees": json_value(e.fees), "source": e.source, "line": e.line})
        for result in calculate_strategies(entries, price):
            data = json_value(result)
            data["account"] = key
            data["holding_average_cost"] = json_value(holding_average)
            data["holding_cost"] = json_value(holding_cost)
            data["holding_market_value"] = json_value(market_value)
            data["holding_profit"] = json_value(holding_profit)
            data["holding_profit_rate"] = json_value(profit_rate)
            allowance = result.sellable_quantity
            # 同价条件的累计量按优先顺序分配一个共享持仓上限。
            used = Decimal(0)
            current_used = Decimal(0)
            ordered = sorted(result.expectations, key=lambda lot: (-lot.reference_price, lot.start_time))
            plans = []
            for lot in ordered:
                item = json_value(lot)
                item["source_event_id"] = f"{key}:{lot.source_event_id}"
                item["id"] = f"{key}:{lot.id}"
                item["account"] = key
                for change in item["changes"]:
                    change["event_id"] = f"{key}:{change['event_id']}"
                tick = Decimal("0.001") if code.startswith(("5", "1")) else Decimal("0.01")
                # A 策略参考量可以大于实际持仓；保留图线，但未来卖出计划不能凭空产生仓位。
                plan_quantity = (min(lot.remaining_quantity, max(Decimal(0), holding.quantity))
                                 if lot.side == "sell" else lot.remaining_quantity)
                item["plan_quantity"] = str(plan_quantity)
                quantity = min(Decimal(lots * 100), plan_quantity)
                target = (profitable_price(lot.reference_price, quantity, lot.side, tick, fees)
                          if quantity > 0 and not result.incomplete else None)
                item["first_profitable_price"] = str(target) if target is not None else None
                item["operation_quantity"] = str(quantity)
                current_executable = None
                if price is not None and not result.incomplete:
                    qualifies_now = target is not None and (price >= target if lot.side == "sell" else price <= target)
                    if lot.side == "buy":
                        current_executable = quantity if qualifies_now else Decimal(0)
                    elif plan_quantity == 0:
                        current_executable = Decimal(0)
                    elif allowance is not None:
                        available_quantity = min(quantity, max(Decimal(0), allowance - current_used))
                        available_target = (profitable_price(lot.reference_price, available_quantity, lot.side, tick, fees)
                                            if available_quantity > 0 else None)
                        qualifies_now = (available_target is not None and price >= available_target
                                         and lot.start_time < china_today().isoformat())
                        current_executable = available_quantity if qualifies_now else Decimal(0)
                        current_used += current_executable
                item["current_executable_quantity"] = json_value(current_executable)
                executable = None
                if allowance is not None and candidate is not None and lot.side == "sell" and not result.incomplete:
                    qualifies = (candidate is not None and candidate > lot.reference_price
                                 and lot.start_time < china_today().isoformat())
                    executable = min(lot.remaining_quantity, max(Decimal(0), allowance - used)) if qualifies else Decimal(0)
                    used += executable
                item["executable_quantity"] = str(executable) if executable is not None else None
                item["cumulative_executable_quantity"] = str(used) if allowance is not None and candidate is not None else None
                plans.append(item)
            data["expectations"] = plans
            for pair in data["pairs"]:
                pair["source_event_id"] = f"{key}:{pair['source_event_id']}"
                pair["target_event_id"] = f"{key}:{pair['target_event_id']}"
            results.append(data)
    markers = []
    grouped = defaultdict(list)
    for event in events:
        if event["business"] in {"证券买入", "证券卖出"} and event["price"] is not None:
            grouped[event["date"], "B" if event["business"] == "证券买入" else "S"].append(event)
    for (day, direction), batch in grouped.items():
        quantity = sum((abs(Decimal(e["quantity"])) for e in batch), Decimal(0))
        prices = [Decimal(e["price"]) for e in batch]
        average = sum((abs(Decimal(e["quantity"])) * Decimal(e["price"]) for e in batch), Decimal(0)) / quantity
        markers.append({"id": f"{day}:{direction}", "date": day, "direction": direction,
                        "quantity": str(quantity), "average": str(average), "low": str(min(prices)),
                        "high": str(max(prices)), "count": len(batch), "event_ids": [e["id"] for e in batch],
                        "position": "below" if direction == "B" else "above", "same_price": False})
    for marker in markers:
        other = next((m for m in markers if m["date"] == marker["date"] and m["direction"] != marker["direction"]), None)
        if other:
            marker["same_price"] = Decimal(marker["average"]) == Decimal(other["average"])
            if not marker["same_price"]:
                marker["position"] = "below" if Decimal(marker["average"]) < Decimal(other["average"]) else "above"
    return {"security": security, "code": code, "market": market, "accounts": list(selected),
            "strategies": results, "events": events, "markers": sorted(markers, key=lambda m: m["date"])}
