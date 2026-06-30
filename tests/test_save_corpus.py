from spam_filter.gmail_client import extract_fields, get_body_html, get_body_text
from save_corpus import extract_headers, filename_date, parse_to_addrs
from tests.fixtures.messages import (
    mixed_case_headers_message,
    nested_multipart_message,
    simple_plain_text_message,
)


def test_get_body_text_top_level():
    assert get_body_text(simple_plain_text_message()) == 'Plain top-level body.'


def test_get_body_text_nested_multipart():
    text = get_body_text(nested_multipart_message())
    assert text == 'Plain nested body with syclid=abc123'


def test_get_body_html_strips_base64_image_keeps_links():
    html = get_body_html(nested_multipart_message())
    assert 'data:image/png;base64' not in html
    assert 'src="[base64-image-stripped]"' in html
    assert 'href="https://example.com/track"' in html


def test_get_body_html_missing_returns_empty():
    assert get_body_html(simple_plain_text_message()) == ''


def test_extract_headers_lowercases_mixed_case():
    headers = extract_headers(mixed_case_headers_message())
    assert headers['from'] == 'noreply@example.com'
    assert headers['return-path'] == '<bounce@example.com>'
    assert headers['x-gm-features'] == 'somefeature'


def test_extract_headers_dedupes_keeps_first():
    message = {
        'payload': {
            'headers': [
                {'name': 'Received', 'value': 'first-hop'},
                {'name': 'Received', 'value': 'second-hop'},
            ]
        }
    }
    headers = extract_headers(message)
    assert headers['received'] == 'first-hop'


def test_parse_to_addrs_multi_recipient():
    addrs = parse_to_addrs('skipmorrowmobile@gmail.com, James Doe <james@outlook.com>')
    assert addrs == ['skipmorrowmobile@gmail.com', 'james@outlook.com']


def test_filename_date_from_date_header():
    assert filename_date('Tue, 30 Jun 2026 18:03:08 +0000') == '2026-06-30'


def test_filename_date_missing_falls_back_to_today():
    import datetime
    expected = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    assert filename_date('') == expected


def test_extract_fields_truncate_none_returns_full_body():
    long_body = 'x' * 2000
    message = {
        'labelIds': ['INBOX'],
        'payload': {
            'headers': [{'name': 'Subject', 'value': 'test'}],
            'mimeType': 'text/plain',
            'body': {'data': __import__('base64').urlsafe_b64encode(long_body.encode()).decode().rstrip('=')},
        },
    }
    fields = extract_fields(message, truncate=None)
    assert len(fields['body_text']) == 2000


def test_extract_fields_truncate_1000():
    long_body = 'x' * 2000
    message = {
        'labelIds': ['INBOX'],
        'payload': {
            'headers': [{'name': 'Subject', 'value': 'test'}],
            'mimeType': 'text/plain',
            'body': {'data': __import__('base64').urlsafe_b64encode(long_body.encode()).decode().rstrip('=')},
        },
    }
    fields = extract_fields(message, truncate=1000)
    assert len(fields['body_text']) == 1000


def test_extract_fields_multi_recipient_to_addrs():
    fields = extract_fields(nested_multipart_message())
    assert fields['to_addrs'] == ['skipmorrowmobile@gmail.com', 'james@outlook.com']
