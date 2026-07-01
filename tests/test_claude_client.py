import json

import pytest

from spam_filter.claude_client import _parse_json_response


def test_parses_plain_json():
    raw = '{"spam": false, "reason": "legit", "confidence": 10}'
    assert _parse_json_response(raw) == {'spam': False, 'reason': 'legit', 'confidence': 10}


def test_strips_json_markdown_fence():
    # The exact shape Claude Haiku actually returns in production despite the
    # prompt's "no markdown" instruction (observed 2026-07-01 — 100% of live
    # fallback calls were wrapped like this).
    raw = '```json\n{"spam": false, "reason": "legit", "confidence": 95}\n```'
    assert _parse_json_response(raw) == {'spam': False, 'reason': 'legit', 'confidence': 95}


def test_strips_bare_fence():
    raw = '```\n{"spam": true, "reason": "spam", "confidence": 99}\n```'
    assert _parse_json_response(raw) == {'spam': True, 'reason': 'spam', 'confidence': 99}


def test_truncated_json_still_raises():
    # Mirrors a real production case where max_tokens cut the response off mid-string —
    # must not silently succeed with partial data.
    raw = '```json\n{"spam": false, "reason": "Email is from LinkedIn job alerts, "confidence":'
    with pytest.raises(json.JSONDecodeError):
        _parse_json_response(raw)
