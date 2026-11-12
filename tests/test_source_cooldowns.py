"""来源限流冷却、恢复与现价/日线共享。"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from stock_statement import market, parallel, quotes


@pytest.mark.parametrize("kind", ["akshare", "api", "http", "wrapped"])
def test_rate_limit_cools_source_for_five_minutes(monkeypatch, kind):
    from akshare.exceptions import APIError, RateLimitError
    from requests import HTTPError, Response

    response = Response()
    response.status_code = 429
    http_error = HTTPError(response=response)
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = http_error
    error = {"akshare": RateLimitError("limited"), "api": APIError("limited", 429),
             "http": http_error, "wrapped": wrapped}[kind]
    now = [1000.0]
    monkeypatch.setattr(parallel.time, "monotonic", lambda: now[0])
    cooldowns = parallel.SourceCooldowns()

    cooldowns.record_failure("东方财富", error)
    assert not cooldowns.available("东方财富")
    assert cooldowns.available("新浪")
    now[0] += 299
    assert not cooldowns.available("东方财富")
    now[0] += 1
    assert cooldowns.available("东方财富")


@pytest.mark.parametrize("error", [TimeoutError("timeout"), ValueError("empty"), PermissionError("login")])
def test_other_errors_do_not_cool_source(error):
    cooldowns = parallel.SourceCooldowns()
    cooldowns.record_failure("雪球", error)
    assert cooldowns.available("雪球")


def test_quote_rate_limit_removes_source_from_quotes_and_candles(monkeypatch):
    from akshare.exceptions import RateLimitError

    cooldowns = parallel.SourceCooldowns()
    monkeypatch.setattr(quotes, "source_cooldowns", cooldowns)
    monkeypatch.setattr(market, "source_cooldowns", cooldowns)
    quote_calls = []
    candle_calls = []

    def fetch_quote(source, *args):
        quote_calls.append(source)
        if source == "东方财富":
            raise RateLimitError("limited")
        return {}

    def fetch_candles(source, *args):
        candle_calls.append(source)
        return []

    cache = quotes.QuoteCache(fetch_quote)
    monkeypatch.setattr(market, "fetch_source", fetch_candles)
    try:
        assert cache.get("SH", "600000").price is None
        assert not cooldowns.available("东方财富")
        cache.get("SH", "600001")
        assert quote_calls.count("东方财富") == 1
        with ThreadPoolExecutor(max_workers=3) as pool:
            monkeypatch.setattr(market, "_source_pool", pool)
            with pytest.raises(RuntimeError, match="均未能获取"):
                market.fetch_akshare("SH", "600000", "20260101", "20260105")
        assert sorted(candle_calls) == sorted(["新浪", "腾讯"])

        for source in ("新浪", "腾讯", "雪球"):
            cooldowns.record_failure(source, RateLimitError("limited"))
        assert cache.get("SH", "600002").price is None
        with pytest.raises(RuntimeError, match="均未能获取"):
            market.fetch_akshare("SH", "600000", "20260101", "20260105")
    finally:
        cache.pool.shutdown()
