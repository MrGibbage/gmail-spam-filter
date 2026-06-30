"""historyId persistence for Program 1's history.list cursor.

Using Gmail's server-side historyId instead of a local timestamp avoids the
n8n lastTimeChecked corruption bug (it silently jumped to a future date during
workflow saves, causing all real mail to be skipped).
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def load_history_id(state_file: str) -> str | None:
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get('historyId')
    except (json.JSONDecodeError, OSError) as e:
        logger.warning('Could not read state file %s: %s', state_file, e)
        return None


def save_history_id(state_file: str, history_id: str):
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump({'historyId': history_id, 'last_poll': time.time()}, f)
