"""Gmail API auth, header/body extraction, and label operations.

Shared by both programs: the automatic filter (spam_filter/main.py) and the
on-demand corpus saver (save_corpus.py).
"""
import base64
import re
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

PERSONAL_DOMAINS = {
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
    'aol.com', 'icloud.com', 'live.com',
}
MY_ADDRESSES = {'skip.morrow.mobile@gmail.com', 'skipmorrowmobile@gmail.com'}


def load_credentials(secrets_dir: str) -> Credentials:
    """Load token.json, refreshing it if expired. Raises FileNotFoundError if missing."""
    token_path = Path(secrets_dir) / 'token.json'
    if not token_path.exists():
        raise FileNotFoundError(str(token_path))

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds


def build_service(secrets_dir: str):
    creds = load_credentials(secrets_dir)
    return build('gmail', 'v1', credentials=creds)


def _find_part(parts, mime):
    for part in parts:
        if part.get('mimeType') == mime:
            return part
        nested = part.get('parts', [])
        if nested:
            found = _find_part(nested, mime)
            if found:
                return found
    return None


def get_body_text(message: dict, truncate: int = None) -> str:
    """Recursively find text/plain part. truncate=None returns full text."""
    payload = message.get('payload', {})
    parts = payload.get('parts', [payload])
    part = _find_part(parts, 'text/plain')
    if not part:
        return ''
    data = part.get('body', {}).get('data', '')
    if not data:
        return ''
    text = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
    return text[:truncate] if truncate else text


def get_body_html(message: dict) -> str:
    """Recursively find text/html part; strip base64 inline images."""
    payload = message.get('payload', {})
    parts = payload.get('parts', [payload])
    part = _find_part(parts, 'text/html')
    if not part:
        return ''
    data = part.get('body', {}).get('data', '')
    if not data:
        return ''
    html = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
    # Strip base64 inline images (noise), keep external src URLs (potential signals)
    html = re.sub(r'src="data:image/[^"]*"', 'src="[base64-image-stripped]"', html)
    return html


def extract_fields(message: dict, truncate: int = 1000) -> dict:
    """Extract the fields used by signal evaluation (and corpus saving).

    truncate=1000 matches what the live filter sees; save_corpus.py passes
    truncate=None to get the full body for the corpus.
    """
    headers = {h['name'].lower(): h['value']
               for h in message.get('payload', {}).get('headers', [])}

    to_raw = headers.get('to', '')
    to_addrs = [addr.strip().lower() for addr in to_raw.split(',') if '@' in addr]
    to_addrs = [re.sub(r'^.*<(.+)>$', r'\1', a) for a in to_addrs]
    return_path = headers.get('return-path', '').strip('<> ')

    return {
        'return_path': return_path,
        'from_addr': headers.get('from', ''),
        'sender': headers.get('sender', ''),
        'to_addrs': to_addrs,
        'subject': headers.get('subject', ''),
        'message_id': headers.get('message-id', '').strip('<> '),
        'x_gm_features': headers.get('x-gm-features', ''),
        'in_reply_to': headers.get('in-reply-to', '').strip('<> '),
        'date': headers.get('date', ''),
        'body_text': get_body_text(message, truncate=truncate),
        'all_headers': headers,  # save_corpus.py uses this; main filter ignores it
    }


def mark_spam(service, msg_id: str):
    """Add SPAM label, remove INBOX label."""
    service.users().messages().modify(
        userId='me', id=msg_id,
        body={'addLabelIds': ['SPAM'], 'removeLabelIds': ['INBOX']},
    ).execute()
