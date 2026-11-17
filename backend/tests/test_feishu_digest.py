import json
from datetime import datetime, timezone

import httpx
import pytest

from app.services import feishu_digest as f

NOW = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)


def samples():
    c = {'cutoff': '2026-11-17', 'valid_until': '2026-11-18T21:30:00Z', 'stale': False}
    a = {'data_date': c['cutoff'], 'buy_budget_usd': 1000, 'next_open_plan': {'signal_date': c['cutoff'], 'next_open_date': '2026-11-18', 'actions': []}}
    b = {'market_ready': True, 'market_date': c['cutoff'], 'valid_until': c['valid_until'], 'assessment': {'trend': '中期上行', 'short_term': '短期回撤', 'risk': '偏高'}, 'quotes': []}
    return c, a, b


def test_empty_plan_and_buy_sell_are_explicit():
    c, a, b = samples()
    assert '今日无买卖计划' in f.daily_text(c, a, b, NOW, 'https://example.com')
    a['next_open_plan']['actions'] = [{'symbol': 'TQQQ', 'side': 'buy'}, {'symbol': 'SOXL', 'side': 'sell'}]
    text = f.daily_text(c, a, b, NOW, 'https://example.com')
    assert '53' not in text and 'TQQQ 拟买入' in text and 'SOXL 拟卖出' in text
    assert '2026-11-18（美东）' in text and '未成交' in text


@pytest.mark.parametrize('case', ['stale', 'account_date', 'signal_date', 'expired', 'unknown_side'])
def test_invalid_plan_is_never_sent(case):
    c, a, b = samples()
    if case == 'stale': c['stale'] = True
    if case == 'account_date': a['data_date'] = '2026-11-16'
    if case == 'signal_date': a['next_open_plan']['signal_date'] = '2026-11-16'
    if case == 'expired': c['valid_until'] = '2026-11-17T21:30:00Z'
    if case == 'unknown_side': a['next_open_plan']['actions'] = [{'symbol': 'TQQQ', 'side': 'other'}]
    with pytest.raises(f.DeliveryError): f.daily_text(c, a, b, NOW, '')


def test_old_brief_does_not_masquerade_as_current():
    c, a, b = samples()
    b['market_date'] = '2026-11-16'
    text = f.daily_text(c, a, b, NOW, '')
    assert '市场环境待核验' in text and '中期上行' not in text


def test_signed_delivery_and_redacted_transport_error(monkeypatch):
    url = 'https://open.feishu.cn/open-apis/bot/v2/hook/test-placeholder'
    secret = 'fake-test-secret'
    def post(target, **kwargs):
        body = kwargs['json']
        assert body['sign'] == f.signature(secret, body['timestamp'])
        assert secret not in json.dumps(body)
        assert kwargs['follow_redirects'] is False
        raise httpx.ConnectError(target + secret)
    monkeypatch.setattr(f.httpx, 'post', post)
    with pytest.raises(f.DeliveryError) as caught: f.send(url, secret, '53 test')
    assert url not in str(caught.value) and secret not in str(caught.value)


def test_only_success_creates_receipt_and_repeated_day_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'https://open.feishu.cn/open-apis/bot/v2/hook/test-placeholder')
    monkeypatch.setenv('FEISHU_WEBHOOK_SECRET', 'fake-test-secret')
    public = tmp_path/'frontend/public/data'; public.mkdir(parents=True)
    for name, value in zip(['catalog-rank2', 'account', 'market-brief'], samples()):
        (public/f'{name}.json').write_text(json.dumps(value), encoding='utf8')
    calls = []
    def failed(*args): raise f.DeliveryError('failed')
    monkeypatch.setattr(f, 'send', failed)
    with pytest.raises(f.DeliveryError): f.run(tmp_path, now=NOW)
    assert not list(tmp_path.rglob('*receipt*.json'))
    monkeypatch.setattr(f, 'send', lambda *args: calls.append(args))
    assert f.run(tmp_path, now=NOW).startswith('sent:')
    assert f.run(tmp_path, now=NOW).startswith('skipped:')
    assert len(calls) == 1


def test_missing_secret_never_sends(monkeypatch, tmp_path):
    monkeypatch.delenv('FEISHU_WEBHOOK_URL', raising=False)
    monkeypatch.delenv('FEISHU_WEBHOOK_SECRET', raising=False)
    assert f.run(tmp_path).startswith('skipped:')


def test_unsigned_and_wrong_destination_are_rejected():
    with pytest.raises(f.DeliveryError): f.send('https://example.com/hook/test', 'secret', '53')
    with pytest.raises(f.DeliveryError): f.send('https://open.feishu.cn/open-apis/bot/v2/hook/test', '', '53')
