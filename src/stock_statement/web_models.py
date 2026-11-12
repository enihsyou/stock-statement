"""HTTP 响应契约，前端从 OpenAPI 生成 TypeScript 类型。"""
from pydantic import BaseModel


class ChangeData(BaseModel):
    time: str
    quantity: str
    event_id: str


class ExpectationData(BaseModel):
    id: str
    strategy: str
    source_event_id: str
    account: str
    side: str
    start_time: str
    reference_price: str
    comparison: str
    original_quantity: str
    remaining_quantity: str
    plan_quantity: str
    current_executable_quantity: str | None
    matched_quantity: str
    status: str
    end_time: str | None
    changes: list[ChangeData]
    executable_quantity: str | None
    limitation: str
    first_profitable_price: str | None
    operation_quantity: str
    cumulative_executable_quantity: str | None


class PairData(BaseModel):
    source_event_id: str
    target_event_id: str
    quantity: str
    profit: str
    kind: str


class StrategyData(BaseModel):
    strategy: str
    name: str
    account: str
    expectations: list[ExpectationData]
    pairs: list[PairData]
    realized: str
    quantity: str
    sellable_quantity: str | None
    average_cost: str | None
    holding_average_cost: str | None
    holding_cost: str | None
    holding_market_value: str | None
    holding_profit: str | None
    holding_profit_rate: str | None
    economic_profit: str | None
    unfinished_difference: str | None
    cashflow_profit: str | None
    cashflow_break_even: str | None
    ledger_adjustment: str
    incomplete: bool
    warnings: list[str]


class EventData(BaseModel):
    id: str
    account: str
    platform: str
    date: str
    time: str | None
    business: str
    quantity: str | None
    price: str | None
    amount: str
    fees: dict[str, str]
    source: str
    line: int


class MarkerData(BaseModel):
    id: str
    date: str
    direction: str
    quantity: str
    average: str
    low: str
    high: str
    count: int
    event_ids: list[str]
    position: str
    same_price: bool


class DetailResponse(BaseModel):
    security: str
    code: str
    market: str
    accounts: list[str]
    strategies: list[StrategyData]
    events: list[EventData]
    markers: list[MarkerData]


class CandleData(BaseModel):
    time: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    source: str


class MarketResponse(BaseModel):
    candles: list[CandleData]
    source: str
    adjustment: str
    updated_at: float
    stale: bool
    error: str | None
    cache_path: str
    provisional: bool


class QuoteResponse(BaseModel):
    market: str
    code: str
    price: str | None
    updated_at: float | None
    source: str
    error: str | None
    previous_close: str | None
    change: str | None
    change_percent: str | None
