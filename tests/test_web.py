"""本地上传 API 的隔离、限制与报告一致性。"""
import base64
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from stock_statement import web
from stock_statement.market import MarketCache
from stock_statement.ui_service import create_analysis

RAW = (Path(__file__).parent / 'fixtures/招商证券.txt').read_bytes()


def payload(raw=RAW, account='账户甲', name='anonymous.txt'):
    return {'name': name, 'account': account, 'data': base64.b64encode(raw).decode()}


@pytest.fixture
def client(tmp_path, anonymous_platforms):
    app = web.create_app(market_cache=MarketCache(tmp_path, fetcher=lambda *args: [], timeout=.1))
    with TestClient(app, headers={'Origin': 'http://testserver'}) as session:
        yield session
    assert not app.state.sessions.items


def upload(client, files=None):
    response = client.post('/api/sessions', json={'files': files or [payload()]})
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_report_matches_shared_projection_and_deduplicates(client):
    token = upload(client, [payload(), payload(name='overlap.txt')])
    response = client.get(f'/api/sessions/{token}/report')
    expected = create_analysis([('anonymous.txt', '账户甲', RAW), ('overlap.txt', '账户甲', RAW)]).report
    assert response.json() == expected
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'


def test_same_broker_accounts_remain_independent(client):
    token = upload(client, [payload(account='甲'), payload(account='乙')])
    response = client.post(f'/api/sessions/{token}/stocks/SZ:000000', json={})
    assert response.status_code == 200
    detail = response.json()
    assert detail['accounts'] == ['甲', '乙']
    assert len(detail['strategies']) == 4
    assert len({event['id'] for event in detail['events']}) == len(detail['events'])
    for account in ('甲', '乙'):
        filtered = client.post(f'/api/sessions/{token}/stocks/SZ:000000', json={'account': account}).json()
        assert filtered['accounts'] == [account]
        assert all(event['account'] == account for event in filtered['events'])


def test_session_delete_isolated(client):
    first, second = upload(client), upload(client)
    assert client.delete(f'/api/sessions/{first}').status_code == 204
    assert client.get(f'/api/sessions/{first}/report').status_code == 404
    assert client.get(f'/api/sessions/{second}/report').status_code == 200
    assert set(client.app.state.sessions.items) == {second}


@pytest.mark.parametrize('headers', [{'Origin': 'http://evil.example'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}])
def test_cross_origin_access_rejected(client, headers):
    assert client.post('/api/sessions', json={'files': [payload()]}, headers=headers).status_code == 403


def test_body_and_decoded_limits(client, monkeypatch):
    monkeypatch.setattr(web, 'MAX_BODY_BYTES', 10)
    assert client.post('/api/sessions', content=b'x' * 11).status_code == 413
    monkeypatch.setattr(web, 'MAX_BODY_BYTES', 100000)
    monkeypatch.setattr(web, 'MAX_FILE_BYTES', len(RAW) - 1)
    assert client.post('/api/sessions', json={'files': [payload()]}).status_code == 413
    monkeypatch.setattr(web, 'MAX_FILE_BYTES', len(RAW))
    monkeypatch.setattr(web, 'MAX_TOTAL_BYTES', len(RAW))
    assert client.post('/api/sessions', json={'files': [payload(), payload()]}).status_code == 413


def test_capacity_and_safe_errors(client, monkeypatch):
    monkeypatch.setattr(web, 'MAX_SESSIONS', 1)
    upload(client)
    assert client.post('/api/sessions', json={'files': [payload()]}).status_code == 429
    monkeypatch.setattr(web, 'MAX_SESSIONS', 8)
    response = client.post('/api/sessions', json={'files': [dict(payload(), data='private-invalid-base64')]})
    assert response.status_code == 422
    assert 'private-invalid' not in response.text
    assert client.post('/api/sessions', json={'files': []}).status_code == 422
    response = client.post('/api/sessions', json={'files': [payload(b'bad statement')]})
    assert response.status_code == 422
    assert 'anonymous.txt' in response.json()['detail']


def test_upload_and_report_never_write_files(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('上传不能写入文件')
    monkeypatch.setattr(Path, 'write_bytes', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    import tempfile
    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', forbidden)
    token = upload(client)
    assert client.get(f'/api/sessions/{token}/report').status_code == 200



def detail_for_entries(client, records, settings=None):
    from stock_statement.ui_service import Analysis
    analysis = Analysis({'同一标签': records}, {}, [{'id': 'SZ:000000', 'code': '000000', 'name': '匿名证券', 'market': 'SZ'}])
    client.app.state.sessions.items['targeted'] = analysis
    response = client.post('/api/sessions/targeted/stocks/SZ:000000', json=settings or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_platforms_never_share_executable_allowance(client):
    from dataclasses import replace

    from stock_statement.platforms import PLATFORMS
    from tests.test_strategies import trades
    records = trades((1000, 10), (-1000, 9))
    records.append(replace(trades((1000, 8))[0], platform=PLATFORMS[1]))
    detail = detail_for_entries(client, records, {'candidate':'11'})
    retained = [item for item in detail['strategies'] if item['strategy'] == 'retained_buy_pairing']
    assert len(retained) == 2
    closed = next(item for item in retained if item['quantity'] == '0')
    holding = next(item for item in retained if item['quantity'] == '1000')
    assert closed['sellable_quantity'] == '0'
    assert all(item['executable_quantity'] == '0' for item in closed['expectations'] if item['side'] == 'sell')
    assert sum(int(item['executable_quantity']) for item in holding['expectations'] if item['side'] == 'sell') == 1000
    assert closed['account'] != holding['account']


def test_today_expectation_never_borrows_older_sellable_quantity(client):
    from dataclasses import replace
    from datetime import datetime

    from tests.test_strategies import trades
    today = datetime.now().astimezone().date()
    records = trades((1000, 8), (1000, 10))
    records[1] = replace(records[1], date=today.strftime('%Y%m%d'), original_date=today.strftime('%Y%m%d'))
    detail = detail_for_entries(client, records, {'candidate':'11'})
    for strategy in detail['strategies']:
        assert strategy['sellable_quantity'] == '1000'
        today_plan = next(plan for plan in strategy['expectations'] if plan['start_time'] == today.isoformat())
        assert today_plan['executable_quantity'] == '0'
        old_plan = next(plan for plan in strategy['expectations'] if plan['start_time'] < today.isoformat())
        assert old_plan['executable_quantity'] == '1000'


@pytest.mark.parametrize(('buys','sell_price','buy_average','buy_position','sell_position','same'), [
    ([(100, 10), (300, 14)], 12, '13', 'above', 'below', False),
    ([(100, 10), (300, 14)], 13, '13', 'below', 'above', True),
    ([(100, 10), (300, 14)], 15, '13', 'below', 'above', False),
])
def test_markers_backend_vwap_price_range_and_relative_layout(client, buys, sell_price, buy_average, buy_position, sell_position, same):
    from dataclasses import replace

    from tests.test_strategies import trades
    records = trades(*buys, (-100, sell_price))
    records = [replace(item, date='20260901', original_date='20260901') for item in records]
    detail = detail_for_entries(client, records)
    buy = next(marker for marker in detail['markers'] if marker['direction'] == 'B')
    sell = next(marker for marker in detail['markers'] if marker['direction'] == 'S')
    assert (buy['quantity'], buy['average'], buy['low'], buy['high'], buy['count']) == ('400', buy_average, '10', '14', 2)
    assert buy['position'] == buy_position
    assert sell['position'] == sell_position
    assert buy['same_price'] is same and sell['same_price'] is same
    assert len(buy['event_ids']) == 2
    assert set(buy['event_ids']).issubset({event['id'] for event in detail['events']})


def test_single_direction_marker_has_default_layout(client):
    from tests.test_strategies import trades
    detail = detail_for_entries(client, trades((100, 10)))
    assert len(detail['markers']) == 1
    marker = detail['markers'][0]
    assert marker['direction'] == 'B'
    assert marker['position'] == 'below'
    assert marker['same_price'] is False
    assert marker['low'] == marker['high'] == marker['average'] == '10'


@pytest.mark.parametrize('code', ['120001', '123456'])
def test_convertible_bonds_are_not_offered_as_fund_detail(client, code):
    from stock_statement.ui_service import security_market
    assert security_market(code) is None
    assert client.get('/api/market/SZ/' + code).status_code == 422


@pytest.mark.parametrize(('market', 'code'), [('SH', '000001'), ('SZ', '600000'), ('BJ', '159001')])
def test_market_route_rejects_inconsistent_market_code(client, market, code):
    assert client.get(f'/api/market/{market}/{code}').status_code == 422


@pytest.mark.parametrize('code', ['159001', '160001', '180001'])
def test_supported_fund_prefixes_keep_shenzhen_market(client, code):
    from stock_statement.ui_service import security_market
    assert security_market(code) == 'SZ'
    assert client.get(f'/api/market/SZ/{code}?start=20260101&end=20260105').status_code == 200


def test_holding_average_cost_is_independent_of_strategy_pairing(client):
    from tests.test_strategies import trades

    detail = detail_for_entries(client, trades((100, 10), (100, 20), (-100, 30)))
    for strategy in detail['strategies']:
        assert strategy['holding_average_cost'] == '15'
        assert strategy['average_cost'] == '10'


def test_holding_profit_uses_ledger_cost_and_current_price(client):
    from tests.test_strategies import trades

    records = trades((100, 10), (100, 20), (-100, 30))
    detail = detail_for_entries(client, records, {'price': '18'})
    for strategy in detail['strategies']:
        assert strategy['holding_cost'] == '1500'
        assert strategy['holding_market_value'] == '1800'
        assert strategy['holding_profit'] == '300'
        assert strategy['holding_profit_rate'] == '20.0'
    no_quote = detail_for_entries(client, records)
    assert all(item['holding_profit'] is None for item in no_quote['strategies'])


def test_zero_position_keeps_strategy_history_but_has_no_sell_plan(client):
    from tests.test_strategies import trades

    detail = detail_for_entries(client, trades((500, 10), (-500, 9)))
    retained = next(item for item in detail['strategies'] if item['strategy'] == 'retained_buy_pairing')
    assert retained['quantity'] == '0'
    sell = next(plan for plan in retained['expectations'] if plan['side'] == 'sell')
    assert sell['remaining_quantity'] == '500'
    assert sell['plan_quantity'] == sell['operation_quantity'] == '0'
    assert sell['first_profitable_price'] is None
    assert retained['economic_profit'] == retained['cashflow_profit'] == '-500'
    assert [plan['side'] for plan in retained['expectations'] if int(plan['plan_quantity']) > 0] == ['buy']


@pytest.mark.parametrize(('price', 'expected'), [('8.99', '0'), ('8.89', '100')])
def test_repurchase_action_uses_live_price_and_fees_not_candidate(client, price, expected):
    from tests.test_strategies import trades

    detail = detail_for_entries(client, trades((100, 10), (-100, 9)), {'price': price, 'candidate': '1'})
    for strategy in detail['strategies']:
        plan = next(plan for plan in strategy['expectations'] if plan['side'] == 'buy')
        assert plan['current_executable_quantity'] == expected


def test_live_sell_actions_share_actual_sellable_position(client):
    from tests.test_strategies import trades

    detail = detail_for_entries(client, trades((100, 10), (100, 12), (-100, 9)), {'price': '15'})
    retained = next(item for item in detail['strategies'] if item['strategy'] == 'retained_buy_pairing')
    sell_plans = [plan for plan in retained['expectations'] if plan['side'] == 'sell']
    assert sum(int(plan['current_executable_quantity']) for plan in sell_plans) == 100
    assert all(int(plan['plan_quantity']) <= 100 for plan in sell_plans)


def test_live_sell_recalculates_minimum_fee_for_partial_available_quantity(client):
    from tests.test_strategies import trades

    detail = detail_for_entries(client, trades((100, 12), (100, 12), (-50, 9)), {'price': '12.15'})
    retained = next(item for item in detail['strategies'] if item['strategy'] == 'retained_buy_pairing')
    sells = [plan for plan in retained['expectations'] if plan['side'] == 'sell']
    assert [plan['operation_quantity'] for plan in sells] == ['100', '100']
    assert [plan['current_executable_quantity'] for plan in sells] == ['100', '0']


@pytest.mark.parametrize('deposit', ['-10000', '0'])
def test_report_has_no_return_percentage_for_nonpositive_net_investment(client, deposit):
    raw = RAW.replace(b'10000       ', f'{deposit:<12}'.encode(), 1)
    token = upload(client, [payload(raw)])
    report = client.get(f'/api/sessions/{token}/report').json()
    profit = next(row for row in report['overview'] if row['key'] == 'PROFIT')
    assert profit['note'].endswith('收益率不可计算')
    assert '%' not in profit['note']


def test_zero_investment_has_no_return_percentage():
    from decimal import Decimal

    from stock_statement.reporting import rate_text

    assert rate_text(Decimal(500), Decimal(0)) == '收益率不可计算'
