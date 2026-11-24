import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
import uuid
import pytest
from app.plugins.manifest import parse_manifest
from app.plugins.report_view import parse_view
from app.models.account import Account
from app.models.transaction import Transaction
from tests import test_code_plugins as cp

code_catalog = cp.catalog
plugin_runner = cp.plugin_runner
ROOT = Path(os.environ['FIXED_COSTS_PLUGIN_DIR'])

@pytest.mark.asyncio
async def test_fixed_costs_through_securo(client, auth_headers, code_catalog, plugin_runner, session, test_user, test_workspace):
    manifest = json.loads((ROOT / 'securo-plugin.json').read_text())
    parse_manifest(manifest)
    manifest['id'] = cp.PLUGIN
    manifest['repository'] = cp.REPO
    manifest['runtime']['entrypoint'] = 'ledger:plugin'
    code_catalog.manifests[cp.V1] = manifest
    code_catalog.archives[cp.V1] = cp._tarball((ROOT / 'fixed_costs.py').read_text())
    resp = await client.get(f'/api/plugins/{cp.PLUGIN}/plan', params={'action': 'install'}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    resp = await client.post(f'/api/plugins/{cp.PLUGIN}/apply', json={'action': 'install', 'commit': plan['commit'], 'token': plan['token']}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    account = Account(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name='Checking', type='checking', balance=Decimal('0'), currency='BRL')
    session.add(account)
    await session.flush()
    for month in (1, 2, 3):
        session.add(Transaction(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id, description='Streaming', amount=Decimal('10'), date=date(2025, month, 1), type='debit', source='manual', currency='BRL', status='posted', payee='Streaming'))
    await session.commit()
    resp = await client.get(f'/api/reports/plugins/{cp.PLUGIN}/fixed-costs', params={'start_date': '2025-01-01', 'end_date': '2025-03-31'}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    view = resp.json()['view']
    parse_view(view)
    assert view['blocks'][1]['value'] == 1
    table = next(b for b in view['blocks'] if b.get('title') == 'Possible recurring payments')
    assert table['rows'][0]['payee'] == 'Streaming'
    assert not any(b['type'] == 'heatmap' for b in view['blocks'])
    trend = next(b for b in view['blocks'] if b.get('title') == 'Estimated monthly costs over time')
    assert trend['series'][0]['values'] == [None, 10.0, 10.0]
    assert view['blocks'][2]['label'] == 'Annualized estimate'
    assert view['blocks'][2]['value'] == 120.0
    assert view['blocks'][3]['hint'] == 'Streaming'
    assert view['blocks'][0]['delta'] == 0.0
    for key in ('recurring-payments', 'monthly-fixed-costs', 'annual-fixed-costs', 'recurring-payments-compact'):
        resp = await client.get(f'/api/dashboard/plugins/{cp.PLUGIN}/{key}', params={'start_date': '2024-04-01', 'end_date': '2025-03-31'}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        widget = resp.json()['view']
        parse_view(widget)
        if key == 'recurring-payments':
            assert [b['value'] for b in widget['blocks'][:3]] == [10.0, 120.0, 1]
        elif key == 'monthly-fixed-costs':
            assert widget['blocks'][0]['value'] == 10.0
        elif key == 'annual-fixed-costs':
            assert widget['blocks'][0]['value'] == 120.0
        if key in ('recurring-payments', 'recurring-payments-compact'):
            assert next(b for b in widget['blocks'] if b['type'] == 'table')['rows'][0]['payee'] == 'Streaming'
