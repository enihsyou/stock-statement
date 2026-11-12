"""公共日线缓存覆盖范围、刷新与离线恢复。"""
import json
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from stock_statement.market import Candle, MarketCache


def candle(day, close='10'):
    return Candle(day, '9', '11', '8', close, '1000')


def test_covering_cache_and_incremental_ranges(tmp_path):
    calls = []
    def fetch(*args):
        calls.append(args)
        return [candle('2026-01-02'), candle('2026-01-05', '12')]
    cache = MarketCache(tmp_path, fetcher=fetch, timeout=.1)
    try:
        initial = cache.get('SH', '600000', '20260101', '20260103')
        assert [row.time for row in initial.candles] == ['2026-01-02']
        assert not initial.stale
        cache.get('SH', '600000', '20260102', '20260103')
        assert len(calls) == 1
        result = cache.get('SH', '600000', '20260101', '20260105')
        assert calls[-1] == ('SH', '600000', '20260103', '20260105')
        assert [row.close for row in result.candles] == ['10', '12']
        cache.get('SH', '600000', '20251231', '20260105')
        assert calls[-1] == ('SH', '600000', '20251231', '20260101')
    finally:
        cache.pool.shutdown()


def test_refresh_today_and_preserve_cache_on_failure(tmp_path):
    calls = []
    today = datetime.now().astimezone().date().strftime('%Y%m%d')
    day = datetime.now().astimezone().date().isoformat()
    def fetch(*args):
        calls.append(args)
        return [candle(day)]
    cache = MarketCache(tmp_path, fetcher=fetch, timeout=.1)
    try:
        result = cache.get('SH', '600000', today, today)
        path = next(tmp_path.iterdir())
        saved = json.loads(path.read_text())
        saved['updated_at'] -= 901
        path.write_text(json.dumps(saved))
        cache.get('SH', '600000', today, today)
        assert len(calls) == 2
        def fail(*args):
            raise RuntimeError('private response body')
        cache.fetcher = fail
        end = (datetime.now().astimezone().date() + timedelta(days=1)).strftime('%Y%m%d')
        failed = cache.get('SH', '600000', today, end)
        assert failed.candles == result.candles
        assert failed.stale
        assert failed.error is not None
        assert 'private' not in failed.error
        assert json.loads(path.read_text())['end'] == today
    finally:
        cache.pool.shutdown()


def test_corrupt_cache_is_replaced(tmp_path):
    cache = MarketCache(tmp_path, fetcher=lambda *args: [candle('2026-01-02')], timeout=.1)
    path = tmp_path / 'akshare-SH-600000-daily-unadjusted-v1.json'
    path.write_text('{broken')
    try:
        result = cache.get('SH', '600000', '20260101', '20260105')
        assert not result.stale
        assert len(result.candles) == 1
        assert json.loads(path.read_text())['schema'] == 1
    finally:
        cache.pool.shutdown()


def test_timeout_without_cache_returns_empty_safe_result(tmp_path):
    release = Event()
    def fetch(*args):
        release.wait(1)
        return [candle('2026-01-02')]
    cache = MarketCache(tmp_path, fetcher=fetch, timeout=.001)
    try:
        result = cache.get('SH', '600000', '20260101', '20260105')
        assert result.stale
        assert result.error
        assert result.candles == []
        assert list(tmp_path.iterdir()) == []
    finally:
        release.set()
        cache.pool.shutdown()



def test_intraday_cache_refreshes_next_day_even_when_requested_end_stays_old(tmp_path, monkeypatch):
    from datetime import date

    from stock_statement import market
    now = [datetime(2026, 1, 5, 4, tzinfo=UTC).timestamp()]
    today = [date(2026, 1, 5)]
    monkeypatch.setattr(market.time, 'time', lambda: now[0])
    monkeypatch.setattr(market, 'china_today', lambda: today[0])
    calls = []
    def fetch(*args):
        calls.append(args)
        return [candle('2026-01-05', '10' if len(calls) == 1 else '12')]
    cache = MarketCache(tmp_path, fetcher=fetch, timeout=.1)
    try:
        initial = cache.get('SH', '600000', '20260101', '20260105')
        assert initial.provisional
        today[0] = date(2026, 1, 6)
        now[0] += 86400
        finalized = cache.get('SH', '600000', '20260101', '20260105')
        assert len(calls) == 2
        assert finalized.candles[0].close == '12'
        assert not finalized.provisional
        cache.get('SH', '600000', '20260101', '20260105')
        assert len(calls) == 2
    finally:
        cache.pool.shutdown()


@pytest.mark.parametrize('failures', [0, 1, 2, 3])
@pytest.mark.parametrize('code', ['600000', '510300'])
@pytest.mark.parametrize('debug', [False, True])
def test_parallel_providers_and_volume_units(monkeypatch, caplog, failures, code, debug):
    import logging

    import akshare as ak
    import pandas as pd

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from stock_statement import market
    from stock_statement.market import fetch_akshare

    calls = []
    original_timeout = ak.get_timeout()
    started = Barrier(3)
    sources = ['东方财富', '新浪', '腾讯']
    caplog.set_level(logging.DEBUG if debug else logging.WARNING, logger='stock_statement.market')

    def provider(index):
        def fetch(**kwargs):
            calls.append((sources[index], kwargs))
            started.wait(timeout=2)
            assert ak.get_timeout() == 5
            if index < failures:
                raise TimeoutError('provider timeout')
            row = {'date': '2026-01-02', 'open': 9, 'high': 11, 'low': 8, 'close': 10,
                   'volume': 1000 if index == 0 else 100000}
            frame = pd.DataFrame([row])
            if index == 0:
                frame = frame.rename(columns=dict(zip(
                    ['date', 'open', 'high', 'low', 'close', 'volume'],
                    ['日期', '开盘', '最高', '最低', '收盘', '成交量'])))
            return frame
        return fetch

    monkeypatch.setattr(ak, 'stock_zh_a_hist', provider(0))
    monkeypatch.setattr(ak, 'fund_etf_hist_em', provider(0))
    monkeypatch.setattr(ak, 'stock_zh_a_daily', provider(1))
    monkeypatch.setattr(ak, 'fund_etf_hist_sina', provider(1))
    monkeypatch.setattr(ak, 'stock_zh_a_hist_tx', provider(2))
    with ThreadPoolExecutor(max_workers=3) as pool:
        monkeypatch.setattr(market, '_source_pool', pool)
        if failures == 3:
            with pytest.raises(RuntimeError, match='均未能获取'):
                fetch_akshare('SH', code, '20260101', '20260105')
        else:
            rows = fetch_akshare('SH', code, '20260101', '20260105')
            assert rows[0].source in sources[failures:]
            assert rows == [Candle('2026-01-02', '9', '11', '8', '10', '1000', rows[0].source)]
    assert sorted(source for source, _ in calls) == sorted(sources)
    assert all(kwargs['adjust'] == '' for source, kwargs in calls if code == '600000' or source != '新浪')
    assert all(kwargs['symbol'] == (code if source == '东方财富' else f'sh{code}')
               for source, kwargs in calls)
    assert ak.get_timeout() == original_timeout
    assert len(caplog.records) == failures
    assert all('TimeoutError: provider timeout' in record.getMessage() for record in caplog.records)
    assert all(bool(record.exc_info) == debug for record in caplog.records)


def test_mixed_sources_survive_cache_and_filtered_response(tmp_path):
    from dataclasses import replace

    def fetch(market, code, start, end):
        if end == '20250103':
            return [replace(candle('2025-01-02'), source='新浪')]
        return [replace(candle('2025-01-06'), source='腾讯')]

    cache = MarketCache(tmp_path, fetcher=fetch)
    try:
        cache.get('SH', '600000', '20250101', '20250103')
        mixed = cache.get('SH', '600000', '20250101', '20250106')
        assert mixed.source == '新浪、腾讯'
        saved = json.loads(next(tmp_path.iterdir()).read_text())
        assert saved['source'] == mixed.source
        assert [row['source'] for row in saved['candles']] == ['新浪', '腾讯']
        cache.fetcher = lambda *args: pytest.fail('covered range must use cache')
        result = cache.get('SH', '600000', '20250106', '20250106')
        assert result.source == '腾讯'
        assert result.candles[0].source == '腾讯'
    finally:
        cache.pool.shutdown()




def test_candles_return_fast_source_while_other_sources_are_running(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from stock_statement import market

    started = Barrier(3)
    release = Event()

    def fetch(source, *args):
        started.wait(timeout=2)
        if source != "腾讯":
            release.wait(2)
        return [Candle('2026-01-02', '9', '11', '8', '10', '1000', source)]

    monkeypatch.setattr(market, 'fetch_source', fetch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        monkeypatch.setattr(market, '_source_pool', pool)
        try:
            rows = market.fetch_akshare('SH', '600000', '20260101', '20260105')
            assert rows[0].source == '腾讯'
            assert not release.is_set()
        finally:
            release.set()
