"""用独立手算金额验证两种成交配对口径。"""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from stock_statement.models import Entry
from stock_statement.platforms import PLATFORMS
from stock_statement.strategies import (
    FeeSettings,
    calculate_strategies,
    profitable_price,
)


def trades(*orders):
    return [
        Entry(
            date=f"202609{index:02}", original_date=f"202609{index:02}",
            business="证券买入" if quantity > 0 else "证券卖出",
            stock_code="000000", stock_name="证券零零", quantity=D(quantity),
            amount=-D(quantity) * D(price), serial=str(index), line=index,
            fees={}, platform=PLATFORMS[0], trade_amount=abs(D(quantity) * D(price)),
            trade_price=D(price), source="anonymous.txt",
        )
        for index, (quantity, price) in enumerate(orders, 1)
    ]


def results(records, price=None, **kwargs):
    calculated = calculate_strategies(records, current_price=None if price is None else D(price), **kwargs)
    return {result.strategy: result for result in calculated}


def pending(result):
    return [(item.side, item.reference_price, item.remaining_quantity) for item in result.expectations if item.remaining_quantity > 0]


@pytest.mark.parametrize("repurchase", [11, 13])
def test_profitable_sale_stays_realized_after_repurchase(repurchase):
    calculated = results(trades((1000, 10), (-1000, 12), (1000, repurchase)))
    for result in calculated.values():
        assert result.realized == D(2000)
        assert result.quantity == D(1000)
        assert sum(pair.profit for pair in result.pairs) == D(2000)


def test_loss_sale_and_lower_repurchase_have_different_pairing():
    calculated = results(trades((1000, 10), (-1000, 9), (1000, 8)), 8)
    retained = calculated["retained_buy_pairing"]
    position = calculated["current_position_lots"]
    assert retained.realized == D(1000)
    assert pending(retained) == [("sell", D(10), D(1000))]
    assert position.realized == D(-1000)
    assert pending(position) == [("sell", D(8), D(1000))]
    for result in calculated.values():
        assert result.quantity == D(1000)
        assert result.economic_profit == D(-1000)


def test_final_sale_closes_both_strategies_at_same_profit():
    calculated = results(trades((1000, 10), (-1000, 9), (1000, 8), (-1000, 11)), 11)
    for result in calculated.values():
        assert result.realized == D(2000)
        assert result.quantity == 0
        assert result.economic_profit == D(2000)


def test_partial_repurchase_preserves_original_reference_and_unmatched_sale():
    calculated = results(trades((1000, 10), (-400, 9), (200, 8)))
    retained = calculated["retained_buy_pairing"]
    position = calculated["current_position_lots"]
    assert retained.realized == D(200)
    assert pending(retained) == [("sell", D(10), D(1000)), ("buy", D(9), D(200))]
    assert position.realized == D(-400)
    assert pending(position) == [("sell", D(10), D(600)), ("buy", D(9), D(200)), ("sell", D(8), D(200))]
    assert retained.quantity == position.quantity == D(800)


def test_position_lots_consume_highest_profitable_cost_first():
    position = results(trades((300, 10), (200, 12), (500, 14), (-400, 13)))["current_position_lots"]
    assert position.realized == D(800)
    assert pending(position) == [("sell", D(10), D(100)), ("sell", D(14), D(500))]


def test_virtual_repurchase_cannot_consume_more_than_unmatched_sale():
    retained = results(trades((1000, 10), (-400, 9), (1000, 8)))["retained_buy_pairing"]
    assert retained.realized == D(400)
    assert pending(retained) == [("sell", D(10), D(1000)), ("sell", D(8), D(600))]
    assert retained.quantity == D(1600)


@pytest.mark.parametrize("price", [0, 8, 11, 20])
def test_economic_profit_conserves_real_cash_flow(price):
    records = trades((300, 10), (200, 12), (-400, 9), (500, 8), (-100, 11))
    calculated = results(records, price)
    expected = D(-4700) + D(500) * D(price)
    for result in calculated.values():
        assert result.quantity == D(500)
        assert result.economic_profit == expected


def test_input_order_does_not_change_chronological_matching():
    records = trades((1000, 10), (-400, 9), (200, 8))
    assert calculate_strategies(records) == calculate_strategies(list(reversed(records)))


def test_accounts_never_pair_buys_and_sales_with_each_other():
    records = trades((1000, 10), (-1000, 12))
    records.append(replace(trades((1000, 8))[0], platform=PLATFORMS[1]))
    calculated = calculate_strategies(records, current_price=D(9))
    for strategy in ("retained_buy_pairing", "current_position_lots"):
        matching = [result for result in calculated if result.strategy == strategy]
        assert sum(result.realized for result in matching) == D(2000)
        assert sum(result.quantity for result in matching) == D(1000)
        assert [result.economic_profit for result in matching] == [D(3000)]


def test_equal_price_is_not_a_profitable_pair():
    calculated = results(trades((1000, 10), (-1000, 10)))
    retained = calculated["retained_buy_pairing"]
    assert retained.pairs == []
    assert pending(retained) == [("sell", D(10), D(1000)), ("buy", D(10), D(1000))]
    assert calculated["current_position_lots"].realized == 0


def test_repurchase_consumes_lowest_profitable_sale_first():
    retained = results(trades((1000, 20), (-300, 12), (-300, 10), (400, 8)))["retained_buy_pairing"]
    assert retained.realized == D(1000)
    assert pending(retained) == [("sell", D(20), D(1000)), ("buy", D(12), D(200))]


def test_equal_reference_prices_keep_original_event_order():
    calculated = results(trades((300, 10), (300, 10), (-400, 12)))
    for result in calculated.values():
        assert result.realized == D(800)
        assert [pair.quantity for pair in result.pairs] == [D(300), D(100)]
        assert result.pairs[0].source_event_id == result.expectations[0].source_event_id
        assert result.pairs[1].source_event_id == result.expectations[1].source_event_id


def test_net_fees_are_included_once_in_economic_profit():
    records = trades((1000, 10), (-400, 12))
    records = [replace(records[0], amount=D(-10005), fees={"佣金": D(5)}),
               replace(records[1], amount=D(4795), fees={"佣金": D(5)})]
    for result in results(records, 11).values():
        assert result.realized == D(800)
        assert result.economic_profit == D(1390)


def test_stock_sellable_estimate_excludes_same_day_buys():
    records = trades((1000, 10), (200, 11))
    for result in results(records, as_of="20260902").values():
        assert result.quantity == D(1200)
        assert result.sellable_quantity == D(1000)


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("buy_minimum,sell_minimum", [(0, 0), (0, 5), (5, 0), (5, 5)])
def test_first_profitable_quote_includes_both_fees_and_strict_tick(side, buy_minimum, sell_minimum):
    fees = FeeSettings(buy_minimum=D(buy_minimum), sell_minimum=D(sell_minimum))
    reference, quantity, tick = D(10), D(100), D("0.01")
    quoted = profitable_price(reference, quantity, side, tick, fees)
    assert quoted is not None
    assert quoted % tick == 0
    original_side = "buy" if side == "sell" else "sell"
    original_fee = fees.fee(reference, quantity, original_side)
    if side == "sell":
        assert quoted * quantity - fees.fee(quoted, quantity, side) > reference * quantity + original_fee
        previous = quoted - tick
        assert previous * quantity - fees.fee(previous, quantity, side) <= reference * quantity + original_fee
    else:
        assert reference * quantity - original_fee > quoted * quantity + fees.fee(quoted, quantity, side)
        previous = quoted + tick
        assert reference * quantity - original_fee <= previous * quantity + fees.fee(previous, quantity, side)



def test_unaffordable_repurchase_has_no_positive_profitable_quote():
    assert profitable_price(D('.01'), D(100), 'buy', D('.01'), FeeSettings()) is None


@pytest.mark.parametrize(('side', 'expected'), [('sell', '10.01'), ('buy', '9.99')])
def test_zero_fees_still_require_one_strict_tick(side, expected):
    fees = FeeSettings(buy_commission=D(0), sell_commission=D(0), buy_minimum=D(0), sell_minimum=D(0), stamp_tax=D(0), transfer=D(0))
    assert profitable_price(D(10), D(100), side, D('.01'), fees) == D(expected)


def test_unknown_price_preserves_actual_position_but_marks_result_incomplete():
    records = trades((1000, 10), (-400, 12))
    records[0] = replace(records[0], trade_price=None)
    for result in results(records, 11).values():
        assert result.quantity == D(600)
        assert result.incomplete
        assert result.economic_profit is None
