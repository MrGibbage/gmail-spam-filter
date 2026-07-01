"""Structured Loki push — one JSON log line per filter decision, poll cycle,
corpus save, or warning/error. Never lets a Loki outage affect the caller;
_push() swallows all exceptions.
"""
import json
import os
import time
import urllib.request


def _push(program: str, level: str, event: str, **fields):
    # Read env fresh on every call, not once at import: main.py's container gets
    # LOKI_URL from Docker's env_file before the process starts, but save_corpus.py
    # runs as a bare host script over ssh_exec and may set os.environ after import.
    url = os.environ.get('LOKI_URL', '').rstrip('/')
    enabled = os.environ.get('LOKI_PUSH_ENABLED', 'true').lower() != 'false'
    if not enabled or not url:
        return
    line = json.dumps({'level': level, 'event': event, **fields})
    payload = json.dumps({
        'streams': [{
            'stream': {'job': 'gmail-spam-filter', 'host': 'docker-server', 'program': program},
            'values': [[str(time.time_ns()), line]],
        }]
    }).encode()
    req = urllib.request.Request(
        f'{url}/loki/api/v1/push',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass  # never let Loki failure crash the filter


def filter_decision(thread_id, subject, from_addr, action,
                     signal_id=None, signal_name=None,
                     claude_used=False, claude_confidence=None, claude_reason=None,
                     return_path=None, x_gm_features=None, message_id=None,
                     in_reply_to=None, sender=None, to_addrs=None):
    _push('main', 'INFO', 'filter_decision',
          thread_id=thread_id, subject=subject, from_addr=from_addr,
          action=action, signal_id=signal_id, signal_name=signal_name,
          claude_used=claude_used, claude_confidence=claude_confidence,
          claude_reason=claude_reason,
          return_path=return_path, x_gm_features=x_gm_features, message_id=message_id,
          in_reply_to=in_reply_to, sender=sender, to_addrs=to_addrs)


def poll_complete(checked: int, marked_spam: int, next_poll_s: int):
    _push('main', 'INFO', 'poll_complete',
          checked=checked, marked_spam=marked_spam, next_poll_s=next_poll_s)


def corpus_saved(thread_id, subject, from_addr, output_path):
    _push('save_corpus', 'INFO', 'corpus_saved',
          thread_id=thread_id, subject=subject, from_addr=from_addr,
          output_path=output_path)


def warn(event: str, program: str = 'main', **fields):
    _push(program, 'WARNING', event, **fields)


def error(event: str, program: str = 'main', **fields):
    _push(program, 'ERROR', event, **fields)
