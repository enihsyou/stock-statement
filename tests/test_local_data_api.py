"""本地真实交割单的 API 回归；未提供这些私有文件时跳过。"""
import base64
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

from stock_statement.web import create_app

DATA = Path(__file__).resolve().parents[1] / 'data'


@pytest.mark.parametrize('broker', ['东方财富', '招商证券'])
def test_local_statement_api_regressions(broker):
    path = DATA / f'2026-11-12 {broker}.txt'
    if not path.exists():
        pytest.skip('本地未提供该交割单')
    with TestClient(create_app(), headers={'Origin': 'http://testserver'}) as client:
        uploaded = client.post('/api/sessions', json={'files': [{
            'name': path.name, 'account': '默认账户',
            'data': base64.b64encode(path.read_bytes()).decode(),
        }]})
        assert uploaded.status_code == 200
        token = uploaded.json()['id']
        if broker == '招商证券':
            report = client.get(f'/api/sessions/{token}/report').json()
            overview = {row['key']: row for row in report['overview']}
            assert Decimal(overview['INVESTMENT']['amount']) < 0
            assert '%' not in overview['PROFIT']['note']
            assert overview['PROFIT']['note'] == '收益率不可计算'
        else:
            response = client.post(f'/api/sessions/{token}/stocks/SH:518600', json={})
            assert response.status_code == 200
            for strategy in response.json()['strategies']:
                assert Decimal(strategy['quantity']) == 0
                assert Decimal(strategy['economic_profit']) == Decimal('930.58')
                assert strategy['economic_profit'] == strategy['cashflow_profit']
                assert all(Decimal(plan['plan_quantity']) == 0 for plan in strategy['expectations'] if plan['side'] == 'sell')
