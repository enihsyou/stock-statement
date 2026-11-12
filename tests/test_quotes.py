"""公开行情源并行查询与全市场快照复用。"""
from decimal import Decimal

import pytest

from stock_statement.quotes import PriceSnapshot, QuoteCache, fetch_prices


def test_parallel_queries_reuse_market_snapshots_across_securities():
    calls = []

    def fetch(source, market, code):
        calls.append(source)
        if source == "东方财富":
            raise ValueError("暂不可用")
        if source == "新浪":
            return {"600000": PriceSnapshot("10.25", "10"), "600001": PriceSnapshot("12", "12.5")}
        return {code: PriceSnapshot("9", "10")} if code == "600002" else {}

    cache = QuoteCache(fetch)
    try:
        first = cache.get("SH", "600000")
        assert (first.price, first.previous_close, first.change, first.change_percent) == ("10.25", "10", "0.25", "2.500")
        assert cache.get("SH", "600000") == first
        second = cache.get("SH", "600001")
        assert (second.price, second.previous_close, second.change) == ("12", "12.5", "-0.5")
        assert Decimal(second.change_percent or "0") == -4
        assert calls.count("东方财富") == calls.count("新浪") == 1
        assert cache.get("SH", "600002").source == "雪球"
        assert calls.count("东方财富") == calls.count("新浪") == 1
        assert calls.count("雪球") == 3
    finally:
        cache.pool.shutdown()


def test_fund_uses_the_source_that_supports_funds():
    calls = []

    def fetch(source, market, code):
        calls.append((source, market, code))
        return {code: PriceSnapshot("1.234", "1.234")}

    cache = QuoteCache(fetch)
    try:
        quote = cache.get("SZ", "159941")
        assert quote.price == "1.234"
        assert Decimal(quote.change or "NaN") == 0
        assert Decimal(quote.change_percent or "NaN") == 0
        assert calls == [("雪球", "SZ", "159941")]
    finally:
        cache.pool.shutdown()


@pytest.mark.parametrize("source,method", [
    ("东方财富", "stock_zh_a_spot_em"),
    ("新浪", "stock_zh_a_spot"),
    ("雪球", "stock_individual_spot_xq"),
])
def test_sources_read_previous_close(monkeypatch, source, method):
    ak = pytest.importorskip("akshare")
    import pandas as pd

    frame = pd.DataFrame({"item": ["现价", "昨收"], "value": [9.5, 10]}) if source == "雪球" else pd.DataFrame({
        "代码": ["sh600000"], "最新价": [9.5], "昨收": [10],
    })
    monkeypatch.setenv("xq_a_token", "example-token")
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return frame

    monkeypatch.setattr(ak, method, fetch)
    assert fetch_prices(source, "SH", "600000") == {"600000": PriceSnapshot("9.5", "10.0" if source == "雪球" else "10")}
    if source == "雪球":
        assert calls == [{"symbol": "SH600000", "token": "example-token"}]


def test_quote_api_preserves_change_fields_and_missing_previous_close(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from stock_statement import web
    from stock_statement.market import MarketCache

    cache = QuoteCache(lambda source, *_: {
        "600000": PriceSnapshot("9.50", "10"),
        "600001": PriceSnapshot("12", None),
    } if source == "新浪" else {})
    monkeypatch.setattr(web, "QuoteCache", lambda: cache)
    app = web.create_app(market_cache=MarketCache(tmp_path, fetcher=lambda *_: []))
    with TestClient(app) as client:
        response = client.get("/api/quotes/SH/600000")
        assert response.status_code == 200
        quote = response.json()
        assert (quote["price"], quote["previous_close"], quote["change"]) == ("9.50", "10", "-0.50")
        assert Decimal(quote["change_percent"]) == -5
        assert client.get("/api/quotes/SH/600000").json() == quote
        unknown = client.get("/api/quotes/SH/600001").json()
        assert unknown["price"] == "12"
        assert unknown["previous_close"] is unknown["change"] is unknown["change_percent"] is None


def test_quote_returns_fast_source_while_other_sources_are_running():
    from threading import Barrier, Event

    started = Barrier(3)
    release = Event()

    def fetch(source, market, code):
        started.wait(timeout=2)
        if source != "雪球":
            release.wait(2)
        return {code: PriceSnapshot("9", "10")}

    cache = QuoteCache(fetch)
    try:
        assert cache.get("SH", "600000").source == "雪球"
        assert not release.is_set()
        assert sum(not future.done() for future in cache.pending.values()) == 2
    finally:
        release.set()
        cache.pool.shutdown()
