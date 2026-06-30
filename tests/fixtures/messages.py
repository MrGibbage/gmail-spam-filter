"""Gmail API message dict fixtures, shaped like real users.messages.get(format='full')
responses, for testing MIME parsing and header extraction without network access.
"""
import base64


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii').rstrip('=')


def simple_plain_text_message():
    """text/plain at the top level, no multipart wrapper."""
    return {
        'id': 'msg1',
        'labelIds': ['INBOX'],
        'payload': {
            'headers': [
                {'name': 'From', 'value': 'sender@example.com'},
                {'name': 'To', 'value': 'skip.morrow.mobile@gmail.com'},
                {'name': 'Subject', 'value': 'Hello'},
                {'name': 'Date', 'value': 'Tue, 30 Jun 2026 18:03:08 +0000'},
            ],
            'mimeType': 'text/plain',
            'body': {'data': _b64('Plain top-level body.')},
        },
    }


def nested_multipart_message():
    """text/plain nested inside multipart/alternative inside multipart/mixed."""
    return {
        'id': 'msg2',
        'labelIds': ['INBOX'],
        'payload': {
            'headers': [
                {'name': 'From', 'value': 'admissions@belowtheprice.com'},
                {'name': 'To', 'value': 'skipmorrowmobile@gmail.com, james@outlook.com'},
                {'name': 'Subject', 'value': 'WARNING: Security Status Notification'},
                {'name': 'Return-Path', 'value': '<admissions@belowtheprice.com>'},
                {'name': 'Message-ID', 'value': '<abc123@mail.gmail.com>'},
                {'name': 'X-Gm-Features', 'value': 'AQt7F2rLGlvxzGKTC1NaGADQ1A8RjTb6BMGUHcpBhWwzze9EE'},
                {'name': 'In-Reply-To', 'value': '<pnlwchkynudk-ktxxmb.fau.edu>'},
            ],
            'mimeType': 'multipart/mixed',
            'parts': [
                {
                    'mimeType': 'multipart/alternative',
                    'parts': [
                        {'mimeType': 'text/plain', 'body': {'data': _b64('Plain nested body with syclid=abc123')}},
                        {
                            'mimeType': 'text/html',
                            'body': {'data': _b64(
                                '<html><body><img src="data:image/png;base64,iVBORw0KGgoAAAANS"/>'
                                '<a href="https://example.com/track">link</a></body></html>'
                            )},
                        },
                    ],
                },
            ],
        },
    }


def mixed_case_headers_message():
    """Headers with mixed casing — confirm extract_headers lowercases all keys."""
    return {
        'id': 'msg3',
        'labelIds': ['INBOX'],
        'payload': {
            'headers': [
                {'name': 'FROM', 'value': 'noreply@example.com'},
                {'name': 'Return-PATH', 'value': '<bounce@example.com>'},
                {'name': 'X-GM-FEATURES', 'value': 'somefeature'},
            ],
            'mimeType': 'text/plain',
            'body': {'data': _b64('body text')},
        },
    }


def missing_date_message():
    """No Date header — filename should fall back to today's date."""
    return {
        'id': 'msg4',
        'labelIds': ['INBOX'],
        'payload': {
            'headers': [
                {'name': 'From', 'value': 'sender@example.com'},
                {'name': 'To', 'value': 'skip.morrow.mobile@gmail.com'},
                {'name': 'Subject', 'value': 'No date here'},
            ],
            'mimeType': 'text/plain',
            'body': {'data': _b64('body text')},
        },
    }
