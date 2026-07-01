#!/usr/bin/env python3
"""Program 2: on-demand corpus saver.

Called by the /save-missed-spam Claude skill via ssh_exec. Fetches full-fidelity
email data via the Gmail API directly (Return-Path, X-Gm-Features, Message-ID,
In-Reply-To, body_html — all absent from the Gmail MCP tool) and saves it as JSON
for spam signal analysis.

Usage:
    python3 save_corpus.py --thread-ids <id1> [<id2> ...]
                            [--output-dir PATH] [--secrets-dir PATH]

Exit codes: 0 = all saved, 1 = one or more errors, 2 = auth failure.
"""
import argparse
import datetime
import json
import os
import sys

from googleapiclient.errors import HttpError

from spam_filter import loki_client
from spam_filter.gmail_client import build_service, get_body_html, get_body_text

DEFAULT_OUTPUT_DIR = '/srv/gmail-spam-filter/missed-spams/new'
DEFAULT_SECRETS_DIR = '/srv/gmail-spam-filter/secrets'
SERVICE_ENV_FILE = '/etc/homelab/gmail-spam-filter.env'


def _load_loki_env(path: str = SERVICE_ENV_FILE) -> None:
    """save_corpus.py runs as a bare host script (no Docker env_file), so pick up
    LOKI_URL / LOKI_PUSH_ENABLED from the same env file Program 1 uses, unless
    already set in the environment. Best-effort — missing file is not an error."""
    if 'LOKI_URL' in os.environ:
        return
    try:
        with open(path) as f:
            for line in f:
                key, _, value = line.strip().partition('=')
                if key in ('LOKI_URL', 'LOKI_PUSH_ENABLED'):
                    os.environ.setdefault(key, value)
    except OSError:
        pass


def parse_args():
    parser = argparse.ArgumentParser(description='Save Gmail threads to the spam corpus.')
    parser.add_argument('--thread-ids', nargs='+', required=True)
    parser.add_argument('--output-dir', default=os.environ.get('OUTPUT_DIR', DEFAULT_OUTPUT_DIR))
    parser.add_argument('--secrets-dir', default=os.environ.get('SECRETS_DIR', DEFAULT_SECRETS_DIR))
    return parser.parse_args()


def extract_headers(message: dict) -> dict:
    """All headers, lowercased. If a header repeats, keep only the first occurrence."""
    headers = {}
    for h in message.get('payload', {}).get('headers', []):
        key = h['name'].lower()
        if key not in headers:
            headers[key] = h['value']
    return headers


def parse_to_addrs(to_raw: str) -> list:
    import re
    addrs = [a.strip() for a in to_raw.split(',') if '@' in a]
    return [re.sub(r'^.*<(.+)>$', r'\1', a) for a in addrs]


def filename_date(date_header: str) -> str:
    if date_header:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_header)
            if dt is not None:
                return dt.strftime('%Y-%m-%d')
        except (TypeError, ValueError):
            pass
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')


def save_thread(service, thread_id: str, output_dir: str) -> str:
    """Fetch a thread, build the corpus record, write it to disk. Returns the filepath."""
    thread = service.users().threads().get(
        userId='me', id=thread_id, format='full'
    ).execute()

    messages = thread.get('messages', [])
    if not messages:
        raise ValueError('thread has no messages')
    message = messages[-1]  # last message — spam is almost always single-message threads

    headers = extract_headers(message)
    record = {
        'saved_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'gmail_thread_id': thread.get('id', thread_id),
        'subject': headers.get('subject', ''),
        'from': headers.get('from', ''),
        'return_path': headers.get('return-path', '').strip('<> '),
        'to': parse_to_addrs(headers.get('to', '')),
        'message_id': headers.get('message-id', '').strip('<> '),
        'x_gm_features': headers.get('x-gm-features', ''),
        'in_reply_to': headers.get('in-reply-to', '').strip('<> '),
        'sender': headers.get('sender', ''),
        'date': headers.get('date', ''),
        'body_text': get_body_text(message, truncate=None),
        'body_html': get_body_html(message),
        'all_headers': headers,
        'notes': '',
        'saved_by': 'save_corpus.py',
    }

    date_str = filename_date(headers.get('date', ''))
    filename = f'{date_str}_{thread_id}.json'
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(record, f, indent=2)

    loki_client.corpus_saved(thread_id, record['subject'], record['from'], filepath)
    return filepath


def main():
    args = parse_args()
    _load_loki_env()

    try:
        service = build_service(args.secrets_dir, program='save_corpus')
    except FileNotFoundError:
        print(f'ERROR: No token.json found at {os.path.join(args.secrets_dir, "token.json")}')
        print('Run python3 run_auth.py on Windows to generate it, then copy to docker-server:/srv/gmail-spam-filter/secrets/')
        print('See DESIGN.md §Gmail OAuth setup.')
        loki_client.error('auth_error', program='save_corpus', msg='token.json missing')
        sys.exit(2)

    saved = 0
    errors = 0
    for thread_id in args.thread_ids:
        try:
            filepath = save_thread(service, thread_id, args.output_dir)
            print(f'SAVED: {filepath}')
            saved += 1
        except HttpError as e:
            print(f'ERROR: thread {thread_id} — {e}')
            loki_client.error('api_error', program='save_corpus', service='gmail',
                               status=e.resp.status, msg=str(e))
            errors += 1
        except Exception as e:
            print(f'ERROR: thread {thread_id} — {e}')
            errors += 1

    print(f'Done: {saved} saved, {errors} errors.')
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
