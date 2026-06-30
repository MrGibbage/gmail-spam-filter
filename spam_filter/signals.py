"""Signal evaluator. Loads patterns from signals.yaml; implements Signal 5
(multi-recipient +tag-stripping) which can't be expressed as a simple field match.
"""
import re
from pathlib import Path

import yaml

from .gmail_client import MY_ADDRESSES, PERSONAL_DOMAINS

_SIGNALS_FILE = Path(__file__).parent.parent / 'signals.yaml'


def _load():
    with open(_SIGNALS_FILE) as f:
        return yaml.safe_load(f)['signals']


def check_stranger_address(fields: dict) -> bool:
    """Signal 5: multi-recipient +tag-stripping check. Lives here, not in YAML."""
    to_addrs = fields.get('to_addrs', [])

    def strip_tag(addr):
        return re.sub(r'\+[^@]*@', '@', addr)

    is_mine = any(
        a in MY_ADDRESSES or strip_tag(a) in MY_ADDRESSES for a in to_addrs
    )
    return not is_mine and any(
        a.split('@')[-1] in PERSONAL_DOMAINS for a in to_addrs
    )


_COMPLEX = {'check_stranger_address': check_stranger_address}


def _eval(signal: dict, fields: dict) -> bool:
    t = signal['type']
    if t == 'complex':
        return _COMPLEX[signal['function']](fields)
    v = fields.get(signal['field'], '') or ''
    if t == 'regex':
        re_flags = re.IGNORECASE if signal.get('flags') == 'IGNORECASE' else 0
        return bool(re.search(signal['pattern'], v, re_flags))
    if t == 'substring':
        return signal['value'] in v
    if t == 'any_substring':
        return any(s in v for s in signal['values'])
    return False


def run_signals(fields: dict) -> tuple[bool, str]:
    """Returns (matched, reason). Reloads signals.yaml on each call (file is tiny).
    Hot-reload: add a signal, see it take effect on the next poll with no restart."""
    for sig in _load():
        if _eval(sig, fields):
            return True, f"Signal {sig['id']}: {sig['name']}"
    return False, ''
