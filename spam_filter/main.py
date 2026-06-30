"""Program 1: automatic Gmail spam filter.

Polls Gmail's history.list every POLL_INTERVAL_SECONDS, runs deterministic
signals.yaml checks first, and falls back to Claude Haiku only when no
signal matches. Replaces the n8n "Gmail Spam Filter" workflow.
"""
import logging
import os
import sys
import time

from googleapiclient.errors import HttpError

from . import claude_client, state
from .gmail_client import build_service, extract_fields, mark_spam
from .signals import run_signals

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger(__name__)


def bootstrap_history_id(service) -> str:
    """Get a current historyId without processing any existing mail."""
    result = service.users().messages().list(
        userId='me', labelIds=['INBOX'], maxResults=1
    ).execute()
    if result.get('messages'):
        msg = service.users().messages().get(
            userId='me', id=result['messages'][0]['id'], format='minimal'
        ).execute()
        return msg['historyId']
    return result.get('historyId')


def process_message(service, msg_id: str, anthropic_api_key: str) -> bool:
    """Fetch, evaluate, and (if spam) label a single message. Returns True if marked spam."""
    try:
        message = service.users().messages().get(
            userId='me', id=msg_id, format='full'
        ).execute()
    except HttpError as e:
        logger.error('[%s] Gmail API failure fetching message: %s', msg_id, e)
        return False

    # A message can appear in history as "added" but have already been moved
    # by the time we fetch it — re-check INBOX membership.
    if 'INBOX' not in message.get('labelIds', []):
        logger.info('[%s] No longer in INBOX, skipping', msg_id)
        return False

    fields = extract_fields(message, truncate=1000)
    subject = fields.get('subject', '')

    matched, reason = run_signals(fields)
    if matched:
        logger.info('[%s] "%s" — %s', msg_id, subject, reason)
    else:
        logger.info('[%s] "%s" — no signal match, calling Claude', msg_id, subject)
        try:
            result = claude_client.classify(fields, anthropic_api_key)
        except Exception as e:
            logger.error('[%s] Anthropic API failure: %s', msg_id, e)
            return False
        logger.info(
            '[%s] Claude: spam=%s, confidence=%s, reason="%s"',
            msg_id, result['spam'], result['confidence'], result['reason'],
        )
        matched = claude_client.is_spam(result)

    if matched:
        try:
            mark_spam(service, msg_id)
        except HttpError as e:
            logger.error('[%s] Gmail API failure marking spam: %s', msg_id, e)
            return False
        logger.info('[%s] Marked as SPAM', msg_id)
        return True

    logger.info('[%s] Passed (not spam)', msg_id)
    return False


def poll(service, state_file: str, anthropic_api_key: str, poll_num: int) -> int:
    """Run one poll cycle. Returns number of messages checked."""
    history_id = state.load_history_id(state_file)
    if history_id is None:
        logger.warning('No saved historyId, bootstrapping fresh cursor')
        history_id = bootstrap_history_id(service)
        state.save_history_id(state_file, history_id)
        return 0, 0

    logger.info('Poll #%d: checking history since %s', poll_num, history_id)

    try:
        response = service.users().history().list(
            userId='me',
            startHistoryId=history_id,
            historyTypes=['messageAdded'],
            labelId='INBOX',
        ).execute()
    except HttpError as e:
        if e.resp.status == 404:
            logger.warning('historyId %s invalid (too old), resetting cursor', history_id)
            new_history_id = bootstrap_history_id(service)
            state.save_history_id(state_file, new_history_id)
            return 0, 0
        logger.error('Gmail API failure on history.list: %s', e)
        return 0, 0

    checked = 0
    marked_spam = 0
    seen_msg_ids = set()
    for entry in response.get('history', []):
        for msg_ref in entry.get('messagesAdded', []):
            msg_id = msg_ref['message']['id']
            if msg_id in seen_msg_ids:
                continue
            seen_msg_ids.add(msg_id)
            checked += 1
            if process_message(service, msg_id, anthropic_api_key):
                marked_spam += 1

    new_history_id = response.get('historyId', history_id)
    state.save_history_id(state_file, new_history_id)

    return checked, marked_spam


def main():
    gmail_user = os.environ.get('GMAIL_USER', '')
    secrets_dir = os.environ.get('SECRETS_DIR', '/secrets')
    state_file = os.environ.get('STATE_FILE', '/data/state.json')
    poll_interval = int(os.environ.get('POLL_INTERVAL_SECONDS', '60'))
    anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')

    if not anthropic_api_key:
        logger.error('ANTHROPIC_API_KEY not set, exiting')
        sys.exit(1)

    logger.info('Starting gmail-spam-filter for %s, poll interval %ds', gmail_user, poll_interval)

    try:
        service = build_service(secrets_dir)
    except FileNotFoundError as e:
        logger.error('No token.json found at %s — see DESIGN.md Gmail OAuth setup', e)
        sys.exit(2)

    poll_num = 0
    while True:
        poll_num += 1
        try:
            checked, marked_spam = poll(service, state_file, anthropic_api_key, poll_num)
        except Exception:
            logger.exception('Unhandled error during poll #%d', poll_num)
            checked, marked_spam = 0, 0
            # Still record that we're alive so the healthcheck doesn't flap on a
            # transient per-message error.
            history_id = state.load_history_id(state_file)
            if history_id is not None:
                state.save_history_id(state_file, history_id)

        logger.info(
            'Poll complete. %d messages checked, %d marked spam. Next poll in %ds',
            checked, marked_spam, poll_interval,
        )
        time.sleep(poll_interval)


if __name__ == '__main__':
    main()
