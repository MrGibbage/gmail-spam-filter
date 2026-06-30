"""Anthropic Claude Haiku fallback classifier.

Only invoked when no deterministic signal (signals.py) matches. Keeps Claude
costs near zero while still catching campaigns that haven't been fingerprinted yet.
"""
import json
import logging

from anthropic import Anthropic

logger = logging.getLogger(__name__)

MODEL = 'claude-haiku-4-5-20251001'
MAX_TOKENS = 200
CONFIDENCE_THRESHOLD = 75

PROMPT_TEMPLATE = """You are a spam filter for skip.morrow.mobile@gmail.com. You check ONLY for these specific
known spam campaigns. Do not classify anything as spam unless it matches one of these exact
technical signals — false positives are worse than missed spam.

CLASSIFY AS SPAM (confidence 99) if ANY ONE of these matches:

1. X-Gm-Features starts with AQt7F2rLGlvxzGKTC and ends with Tb6BMGUHcpBhWwzze9EE
   (known spam infrastructure fingerprint; variants seen include:
   AQt7F2rLGlvxzGKTC2oLv4plPb8ga7nsr43bHXWfPfbTb6BMGUHcpBhWwzze9EE,
   AQt7F2rLGlvxzGKTC1NaGADQym38Tb6BMGUHcpBhWwzze9EE,
   AQt7F2rLGlvxzGKTC1NaGADQ7UxZuTb6BMGUHcpBhWwzze9EE,
   AQt7F2rLGlvxzGKTC1NaGADQ1A8RjTb6BMGUHcpBhWwzze9EE)

2. Message-ID contains the string: +g9W_ddh+QDR18-tkYxyw01Rtjxhzw1NaGADQ
   (Unique spam infrastructure fingerprint seen across multiple campaign variants —
   legitimate emails never contain this string.)

3. In-Reply-To contains a gibberish .edu domain — the domain is made of random
   consonant-heavy strings joined by hyphens with no resemblance to a real university.
   Examples: wrnibqgpwzlp-chxgki.edu, eepzibfescaq-rjxmlw.edu, zvgnzoypdgsg-oeijdr.edu.
   Real universities have recognizable names like stanford.edu or mit.edu.

4. Body or any header contains an IPv6-formatted URL: http://[::ffff: or https://[::ffff:

5. To address is a personal consumer email address (at gmail.com, yahoo.com, hotmail.com,
   outlook.com, or a similar personal mailbox provider) belonging to a completely different
   person — meaning this email was addressed to a total stranger and delivered here by mistake.
   Do NOT flag this signal if the To address is at a service or notification domain
   (github.com, noreply.*, or any domain that is clearly a platform or service rather than
   a personal mailbox).

6. Return-Path username starts with "noReply_" (capital R, capital P, underscore) followed
   by 6-12 random lowercase letters — e.g. noReply_oymoxjhf@..., noReply_kdrwsfke@...,
   noReply_eehpmzlx@... Subject may be anything; the noReply_ infrastructure pattern alone
   is sufficient to identify this campaign.

7. Body contains the string: syclid=
   (Custom tracking parameter used by Campaign 3 spam infrastructure — fake Mcafee
   protection expiry / Cloud+ subscription warnings delivered via recycled legitimate
   university email bodies. "syclid=" is not used by any known legitimate ESP.)

NEVER classify as spam:
- Emails where From is skip.morrow.mobile@gmail.com
- Emails from GitHub (*@github.com) — GitHub notifications use noreply.github.com in To, which is normal
- Emails from Reddit (redditmail.com), IMDb, Amazon, Google, banks, or major retailers —
  even if Return-Path uses a bounce domain like bounces.amazon.com or amazonses.com
- Mailing list emails with Return-Path from simplelists.com, mailchimp.com,
  constantcontact.com, or amazonses.com
- Do not treat invisible/zero-width whitespace or click-tracking URLs as spam signals

If none of the 7 signals above match, respond with spam: false, even if the email looks
suspicious on other grounds.

Analyze this email:
From: {from_addr}
Return-Path: {return_path}
Sender: {sender}
To: {to_addresses}
Subject: {subject}
Message-ID: {message_id}
X-Gm-Features: {x_gm_features}
In-Reply-To: {in_reply_to}
Body: {body}

Respond ONLY with JSON (no markdown): {{"spam": true or false, "reason": "one sentence", "confidence": 0-100}}"""


def classify(fields: dict, api_key: str) -> dict:
    """Call Claude Haiku to classify an email. Returns {'spam': bool, 'reason': str, 'confidence': int}.

    On a malformed (non-JSON) response, logs a sanitized snippet and treats as spam=False.
    """
    client = Anthropic(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        from_addr=fields.get('from_addr', ''),
        return_path=fields.get('return_path', ''),
        sender=fields.get('sender', ''),
        to_addresses=', '.join(fields.get('to_addrs', [])),
        subject=fields.get('subject', ''),
        message_id=fields.get('message_id', ''),
        x_gm_features=fields.get('x_gm_features', ''),
        in_reply_to=fields.get('in_reply_to', ''),
        body=fields.get('body_text', ''),
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{'role': 'user', 'content': prompt}],
    )
    raw = response.content[0].text.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('Claude returned non-JSON response: %.200s', raw)
        return {'spam': False, 'reason': 'unparseable Claude response', 'confidence': 0}

    return {
        'spam': bool(result.get('spam', False)),
        'reason': result.get('reason', ''),
        'confidence': int(result.get('confidence', 0)),
    }


def is_spam(result: dict) -> bool:
    return result.get('spam') is True and result.get('confidence', 0) > CONFIDENCE_THRESHOLD
