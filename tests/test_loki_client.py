import urllib.request

import pytest

from spam_filter import loki_client


def test_push_noop_when_disabled(monkeypatch):
    monkeypatch.setenv('LOKI_URL', 'http://example.invalid:3100')
    monkeypatch.setenv('LOKI_PUSH_ENABLED', 'false')

    def fail(*a, **k):
        raise AssertionError('urlopen should not be called when disabled')

    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    loki_client.filter_decision('t1', 'subj', 'a@b.com', 'marked_spam')


def test_push_noop_when_no_url(monkeypatch):
    monkeypatch.delenv('LOKI_URL', raising=False)

    def fail(*a, **k):
        raise AssertionError('urlopen should not be called with no LOKI_URL')

    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    loki_client.poll_complete(checked=1, marked_spam=0, next_poll_s=60)


def test_push_swallows_network_failure(monkeypatch):
    monkeypatch.setenv('LOKI_URL', 'http://example.invalid:3100')
    monkeypatch.setenv('LOKI_PUSH_ENABLED', 'true')

    def raise_error(*a, **k):
        raise OSError('connection refused')

    monkeypatch.setattr(urllib.request, 'urlopen', raise_error)
    # Must not raise — a Loki outage can never crash the filter.
    loki_client.error('api_error', service='gmail', status=500, msg='boom')


def test_push_sends_expected_payload(monkeypatch):
    monkeypatch.setenv('LOKI_URL', 'http://example.invalid:3100/')
    monkeypatch.setenv('LOKI_PUSH_ENABLED', 'true')

    captured = {}

    class FakeResponse:
        def close(self):
            pass

    def fake_urlopen(req, timeout=None):
        captured['url'] = req.full_url
        captured['body'] = req.data
        return FakeResponse()

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    loki_client.corpus_saved('t1', 'subj', 'a@b.com', '/path/out.json')

    assert captured['url'] == 'http://example.invalid:3100/loki/api/v1/push'
    assert b'"program": "save_corpus"' in captured['body'] or b'"program":"save_corpus"' in captured['body']
    assert b'corpus_saved' in captured['body']
