# Gmail Spam Filter — Python Implementation Design Spec

## Purpose of this document

This spec is written so that a new Claude session (or a human) can implement the project
from scratch without needing to investigate the existing n8n workflow, the homelab
infrastructure, or the history of past incidents. Everything needed is here.

---

## Two programs in this repo

This repo contains two distinct Python programs. They share OAuth credentials and the
corpus folder, but are otherwise independent.

| | `spam_filter/main.py` | `save_corpus.py` |
|---|---|---|
| **What it is** | Automatic spam filter | On-demand corpus saver |
| **Trigger** | Docker container, loops every 60s | Called by `/save-missed-spam` Claude skill |
| **Purpose** | Detect and move spam in real-time | Save full email data for analysis |
| **Inputs** | Gmail inbox (via history.list) | Gmail thread IDs (via CLI args) |
| **Outputs** | SPAM/INBOX label changes | JSON files in `missed-spams/new/` |
| **Lifetime** | Always-on, `restart: unless-stopped` | Runs once, exits |
| **Auth** | Shared `secrets/token.json` | Shared `secrets/token.json` |

Build and deploy them independently. The corpus saver can be used immediately (as soon as
OAuth is set up); the main filter goes in its Docker container.

---

## What this replaces and why

A running n8n workflow (`Gmail Spam Filter`, workflow ID `4L6yy2QKRLMAW3y3`) currently
polls Gmail every 60 seconds, applies deterministic signal checks, and calls Claude Haiku
as a fallback classifier. It works, but:

- **Updating signals requires an API round-trip.** Adding or changing a detection rule
  means: GET workflow JSON → mutate the node's `jsCode` string → PUT back via n8n REST
  API. It cannot be done with a text editor.
- **The `lastTimeChecked` state is fragile.** n8n stores a Unix timestamp in its SQLite
  DB to track which emails have been seen. This has silently corrupted to a future date
  (observed: Jan 2027) during workflow saves, causing all real emails to be skipped.
- **Header parsing has been patched four times.** n8n's Gmail Trigger node returns
  email headers as a mailparser `Headers` Map object. JavaScript's `Object.entries()`
  returns `[]` on a Map. This caused four separate incidents where all header-based
  signals silently returned empty strings.

Python eliminates all three: signals live in a plain `signals.yaml` file, Gmail's
`history.list` API tracks state server-side, and Python's `email` stdlib parses RFC 2822
headers correctly with no special handling.

---

## Architecture — Program 1 (automatic spam filter)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Docker container: gmail-spam-filter  (on docker-server)            │
│                                                                     │
│  main loop (every 60 s)                                             │
│    │                                                                │
│    ├─ Gmail API: users.history.list(startHistoryId)                 │
│    │    └─ for each new INBOX message:                              │
│    │         ├─ Gmail API: users.messages.get (full format)         │
│    │         ├─ parse headers (payload.headers list)                │
│    │         ├─ run_signals() → loads signals.yaml, evaluates each  │
│    │         │    ├─ if any match → mark spam (skip Claude)         │
│    │         │    └─ if no match  → call Claude Haiku               │
│    │         │         └─ if spam + confidence > 75 → mark spam     │
│    │         └─ mark spam: add SPAM label, remove INBOX label       │
│    │                                                                │
│    └─ persist new historyId to state file                           │
└─────────────────────────────────────────────────────────────────────┘
```

**Key design choices:**

- `users.history.list` uses Google's server-side `historyId` — immune to the
  `lastTimeChecked` corruption bug that plagued n8n.
- Deterministic signals run first; Claude is only invoked when no signal matches.
  This matches the n8n behavior exactly and keeps Claude Haiku costs near zero.
- All signal patterns live in `signals.yaml` (plain YAML, no Python required). Adding a
  signal = edit one file, restart container. `signals.py` contains only the evaluator and
  Signal 5 (the multi-recipient +tag-stripping logic that can't be expressed in YAML).

## Architecture — Program 2 (on-demand corpus saver)

```
  Claude skill (/save-missed-spam)
    │
    ├─ reasoning: identify spam candidates from user description
    ├─ Gmail MCP: search_threads → confirm candidates → collect thread IDs
    │
    └─ ssh_exec: python3 /srv/gmail-spam-filter/save_corpus.py --thread-ids <id1> <id2>
                   │
                   ├─ Gmail API: threads.get(format='full') for each thread ID
                   ├─ extract ALL headers + text/plain + text/html
                   └─ save to /srv/gmail-spam-filter/missed-spams/new/{date}_{id}.json
                        └─ stdout: "SAVED: filename" per file (skill reports this to user)
```

The skill handles discovery (what Claude is good at); the script handles data quality
(what the Gmail API is good at). The MCP tool is used only for searching — not for
fetching content, since it omits Return-Path, X-Gm-Features, Message-ID, In-Reply-To,
and body_html entirely.

---

## Existing filter logic — complete, no investigation needed

### The 7 signals (define in signals.yaml; Signal 5 stays in signals.py)

All inputs come from parsed email headers. Field names below match what to extract.

```
Signal 1 — X-Gm-Features pattern
  regex: ^AQt7F2rLGlvxzGKTC.*Tb6BMGUHcpBhWwzze9EE$  on x_gm_features
  Rationale: spammer infrastructure fingerprint. Prefix and suffix are stable;
  middle section rotates. Pattern match is immune to rotation (unlike exact-match list,
  which was the source of the 2026-06-28 and 2026-06-30 incidents).

Signal 2 — Message-ID substring
  substring: '+g9W_ddh+QDR18-tkYxyw01Rtjxhzw1NaGADQ'  in message_id
  Rationale: unique spam infrastructure string, appears in both old and new Message-ID
  variants. Updated 2026-06-28 from a longer prefix that had diverged.

Signal 3 — Gibberish .edu domain in In-Reply-To
  regex: [a-z]{6,}-[a-z]{5,}\.edu  (IGNORECASE)  on in_reply_to
  Rationale: spammer forges an In-Reply-To with a random-consonant .edu domain.
  Real university domains are recognizable words (stanford.edu, mit.edu, etc.).

Signal 4 — IPv6 URL in body
  any_substring: ['http://[::ffff:', 'https://[::ffff:']  in body_text
  (body_text = first 1000 chars of plain-text body)
  Rationale: spam campaigns use IPv6-mapped IPv4 addresses to obscure destinations.

Signal 5 — Addressed to a stranger's personal email
  Fires when: NONE of the To addresses belongs to the user AND at least one To
  address is at a personal consumer domain.
  User's addresses: skip.morrow.mobile@gmail.com, skipmorrowmobile@gmail.com
  Strip +tag before comparing: 'skip.morrow.mobile+nyt@gmail.com' → 'skip.morrow.mobile@gmail.com'
  Personal domains: gmail.com, yahoo.com, hotmail.com, outlook.com, aol.com, icloud.com, live.com
  Rationale: spam campaigns recycle legitimate emails addressed to strangers.
  CRITICAL: check ALL To recipients (comma-separated), not just the first.
  The n8n v841 fix was specifically because only the first recipient was being checked.
  (This signal stays in signals.py — multi-recipient +tag logic can't be expressed in YAML.)

Signal 6 — noReply_ Return-Path
  regex: ^noReply_[a-z]{6,12}@  on return_path
  Rationale: Campaign 2 persistent infrastructure fingerprint. The camelCase
  noReply_ (capital R, capital P, underscore) followed by 6-12 random lowercase
  letters is consistent across all Campaign 2 variants regardless of subject or sender.
  Subject is irrelevant — do NOT require a secondary subject signal (that caused
  2026-06-22 misses).

Signal 7 — syclid= URL tracking parameter in body
  substring: 'syclid='  in body_text
  (body_text = first 1000 chars of plain-text body)
  Rationale: Campaign 3 (first seen 2026-06-30). Fake Mcafee security expiry /
  Cloud+ subscription cancellation / account verification warnings delivered via
  recycled university email bodies; sender uses admissions@ at rotating spam domains
  (e.g. belowtheprice.com). syclid= is a non-standard tracking parameter not used by
  any known legitimate ESP. Appears in every spam link in the body.
  Body slice is 1000 chars (not 500) because the McAfee-format template pushes
  syclid= past the 600-char mark — would be missed with the old 500-char limit.
```

### False positive rules (enforce in Claude prompt AND consider in signal logic)

Never mark as spam:
- Sender is `skip.morrow.mobile@gmail.com`
- From domain is `*@github.com` — GitHub notifications use `noreply.github.com` in To, which is normal
- From: Reddit (`redditmail.com`), IMDb, Amazon, Google, banks, major retailers — even if
  Return-Path uses a bounce domain like `bounces.amazon.com` or `amazonses.com`
- Return-Path from: `simplelists.com`, `mailchimp.com`, `constantcontact.com`, `amazonses.com`
- Do not treat click-tracking URLs or invisible whitespace as spam signals

Known past false positives (study these when adding new signals):
- The Humor List (simplelists.com) — triggered by random Return-Path string
- IMDb/Amazon marketing — `bounces.amazon.com` Return-Path
- NYT Wirecutter (`+nyt` alias) and CamelCamelCamel (`+camel` alias) — `+tag` stripping was missing
- `matthew.guckin@valkyrie.com` email — multi-recipient, only first To recipient was checked

### The Claude Haiku prompt (use verbatim, update signal descriptions as campaigns evolve)

```
You are a spam filter for skip.morrow.mobile@gmail.com. You check ONLY for these specific
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

Respond ONLY with JSON (no markdown): {"spam": true or false, "reason": "one sentence", "confidence": 0-100}
```

Model: `claude-haiku-4-5-20251001`
Max tokens: 200
Mark spam if: `spam == True and confidence > 75`

---

## signals.yaml — format and evaluator design

Signal patterns live in `signals.yaml` at the repo root, not in Python code. Editing this
file and restarting the container is all that's needed to add or change a campaign. No code
change, no PR, no redeploy cycle beyond `docker compose restart gmail-spam-filter`.

Signal 5 is the one exception — its multi-recipient +tag-stripping logic is too complex for
a simple field-match and stays implemented in `signals.py`. All other signals are YAML.

### signals.yaml (current — all 7 signals)

```yaml
# Spam signal definitions for gmail-spam-filter.
# Types: regex | substring | any_substring | complex
# Fields: x_gm_features | message_id | in_reply_to | return_path | body_text
# To add a new signal: append an entry here, docker compose restart. No Python change needed.

signals:
  - id: 1
    name: "Campaign 1: X-Gm-Features fingerprint"
    field: x_gm_features
    type: regex
    pattern: '^AQt7F2rLGlvxzGKTC.*Tb6BMGUHcpBhWwzze9EE$'
    note: "Prefix/suffix stable; middle rotates. Regex is immune to rotation."

  - id: 2
    name: "Campaign 1: Message-ID infrastructure substring"
    field: message_id
    type: substring
    value: '+g9W_ddh+QDR18-tkYxyw01Rtjxhzw1NaGADQ'
    note: "Common substring across old and new Message-ID variants."

  - id: 3
    name: "Campaign 1: Gibberish .edu domain in In-Reply-To"
    field: in_reply_to
    type: regex
    pattern: '[a-z]{6,}-[a-z]{5,}\.edu'
    flags: IGNORECASE
    note: "Random consonant-heavy strings. Real universities have recognizable names."

  - id: 4
    name: "Campaign 1: IPv6 URL in body"
    field: body_text
    type: any_substring
    values:
      - 'http://[::ffff:'
      - 'https://[::ffff:'
    note: "IPv6-mapped IPv4 used to obscure link destination."

  - id: 5
    name: "Campaign 1: Addressed to stranger's personal email"
    type: complex
    function: check_stranger_address
    note: >
      Multi-recipient +tag-stripping logic. Implemented in signals.py —
      cannot be expressed as a simple field check.

  - id: 6
    name: "Campaign 2: noReply_ Return-Path pattern"
    field: return_path
    type: regex
    pattern: '^noReply_[a-z]{6,12}@'
    note: "CamelCase noReply_ + random lowercase suffix. Subject irrelevant."

  - id: 7
    name: "Campaign 3: syclid= URL tracking parameter"
    field: body_text
    type: substring
    value: 'syclid='
    note: "Non-standard tracking param. Not used by any known legitimate ESP."
```

### signals.py — evaluator (only needs changing to add new complex signal types)

```python
import re
import yaml
from pathlib import Path

_SIGNALS_FILE = Path(__file__).parent.parent / 'signals.yaml'

def _load():
    with open(_SIGNALS_FILE) as f:
        return yaml.safe_load(f)['signals']

def check_stranger_address(fields: dict) -> bool:
    """Signal 5: multi-recipient +tag-stripping check. Lives here, not in YAML."""
    my_addresses = {'skip.morrow.mobile@gmail.com', 'skipmorrowmobile@gmail.com'}
    personal_domains = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                        'aol.com', 'icloud.com', 'live.com'}
    to_addrs = fields.get('to_addrs', [])
    def strip_tag(addr):
        return re.sub(r'\+[^@]*@', '@', addr)
    is_mine = any(
        a in my_addresses or strip_tag(a) in my_addresses for a in to_addrs
    )
    return not is_mine and any(
        a.split('@')[-1] in personal_domains for a in to_addrs
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
```

Note: `_load()` reads `signals.yaml` on every `run_signals()` call. For a small YAML file
polled once every 60 seconds this is negligible (~0.1 ms). The benefit is hot-reload during
development. If you prefer, cache in `main.py` at startup and pass as an argument.

`signals.yaml` is **not** gitignored — it is config, not a secret. Commit it to git.

---

## save_corpus.py — on-demand corpus saver (Program 2)

### Purpose

When a spam email slips through to the inbox, the `/save-missed-spam` Claude skill saves it
to the corpus for analysis and signal development. This script is what the skill calls to
do the actual fetching and saving.

The script exists because the Claude.ai Gmail MCP tool — the only tool available to the
skill directly — returns only a curated subset of email data. Return-Path, X-Gm-Features,
Message-ID, In-Reply-To, and body_html are all absent from MCP responses. Corpus files
built with the MCP tool have empty strings for the fields that matter most for fingerprinting.

This script uses the Gmail API directly and gets everything: the complete `payload.headers`
array, both MIME body parts, the display name in From — the same data the automatic spam
filter sees when evaluating a live email.

### How it's invoked

The `/save-missed-spam` skill:
1. Uses Claude's reasoning + Gmail MCP `search_threads` to identify candidate spam emails
2. Confirms candidates by reviewing subject/sender (use reasoning — don't save legitimate mail)
3. Collects Gmail thread IDs from the MCP search results
4. Calls this script via `mcp__homelab-mcp__ssh_exec`:
   ```
   python3 /srv/gmail-spam-filter/save_corpus.py --thread-ids 19f19b76 19f19b75
   ```
5. Captures stdout and reports saved filenames + any errors to the user
6. Suggests next step: run `/missed-spam` on the saved files

The skill handles discovery; the script handles data quality.

### CLI interface

```bash
python3 /srv/gmail-spam-filter/save_corpus.py --thread-ids <id1> [<id2> ...]
```

Optional flags:
- `--output-dir PATH` — override output directory (default: `/srv/gmail-spam-filter/missed-spams/new/`)
- `--secrets-dir PATH` — override secrets directory (default: `/srv/gmail-spam-filter/secrets/`)

Exit codes: `0` = all saved, `1` = one or more errors, `2` = auth failure.

### What it fetches

For each thread ID:
1. `service.users().threads().get(userId='me', id=thread_id, format='full')`
2. Take the **last message** in `thread['messages']` (spam emails are almost always
   single-message threads; last-message is safe for the rare multi-message case)
3. Extract from that message:
   - All headers via `message['payload']['headers']` — full `[{name, value}]` array
   - Plain-text body: traverse MIME parts for `mimeType == 'text/plain'` — **full text, not truncated**
   - HTML body: traverse MIME parts for `mimeType == 'text/html'` — full HTML, base64 image data stripped
   - `thread_id` from the response

For nested MIME trees (`multipart/alternative` inside `multipart/mixed`, etc.), recurse
into `parts` until leaf text parts are found.

**Stripping base64 image data from HTML:** replace `src="data:image/[^"]*"` with
`src="[base64-image-stripped]"`. Keep all link hrefs, external image src URLs, and
tracking pixel domains — these are potential future signals.

### Output schema

Same schema as existing corpus files, but with all fields populated:

```json
{
  "saved_at": "2026-06-30T19:00:00Z",
  "gmail_thread_id": "19f19b7603f22a5e",
  "subject": "Re: [EXTERNAL] – WARNING: Security Status Notification",
  "from": "P A Y E M E N T-D E C L I N E D <admissions@belowtheprice.com>",
  "return_path": "admissions@belowtheprice.com",
  "to": ["skipmorrowmobile@gmail.com"],
  "message_id": "<CABWxxx@mail.gmail.com>",
  "x_gm_features": "AQt7F2rLGlvxzGKTC1NaGADQ...",
  "in_reply_to": "<xxx.edu>",
  "sender": "admissions@belowtheprice.com",
  "date": "2026-06-30T18:03:08Z",
  "body_text": "... full plain text, not truncated ...",
  "body_html": "... full HTML with base64 image data stripped ...",
  "all_headers": {
    "return-path": "...",
    "x-gm-features": "...",
    "message-id": "...",
    "in-reply-to": "...",
    "received": "...",
    "... every header lowercased ...": "..."
  },
  "notes": "",
  "saved_by": "save_corpus.py"
}
```

`all_headers` keys are lowercased. If a header appears multiple times (e.g. `Received`),
store only the first occurrence — or store as a list; either is fine, be consistent.

### Filename

`{YYYY-MM-DD}_{thread_id}.json` — date from the email's `Date` header (fall back to
today's date if absent). Thread ID guarantees uniqueness. Matches the existing corpus
file naming convention.

### stdout output

One line per file on success:
```
SAVED: /srv/gmail-spam-filter/missed-spams/new/2026-06-30_19f19b7603f22a5e.json
SAVED: /srv/gmail-spam-filter/missed-spams/new/2026-06-30_19f19b759cf5c8b7.json
```

On error for a specific thread:
```
ERROR: thread 19f19bad — <reason>
```

Final summary line always printed:
```
Done: 2 saved, 0 errors.
```

The skill reads this stdout via `ssh_exec` and relays the filenames and error count to
the user.

### Authentication

Shares `secrets/token.json` with the main spam filter — same file, same OAuth Desktop
client, same GCP project. This is safe: the corpus saver runs on-demand and completes
in a few seconds; the spam filter polls every 60 seconds. The probability of a
simultaneous token-refresh write is negligible, and the consequence of losing a race is
just a stale token that refreshes cleanly on the next invocation.

Both programs use `gmail.modify` scope (the corpus saver only reads, but there is no
separate read-only scope that includes all the fields needed; `gmail.readonly` works too
if you prefer — just be consistent between the two programs).

If `token.json` does not exist yet, print a clear error and exit with code 2:
```
ERROR: No token.json found at /srv/gmail-spam-filter/secrets/token.json
Run python3 run_auth.py on Windows to generate it, then copy to docker-server:/srv/gmail-spam-filter/secrets/
See DESIGN.md §Gmail OAuth setup.
```

### Updated /save-missed-spam skill flow

The existing `/save-missed-spam` skill at `.claude/skills/save-missed-spam/skill.md`
should be updated to use this script. New flow:

1. Parse user's description into search criteria (sender domain, subject keywords,
   time window, display name pattern, etc.)
2. Build Gmail search query: `from:X in:inbox newer_than:2h`, `subject:"Y" in:inbox`, etc.
3. `mcp__claude_ai_Gmail__search_threads(query=...)` → candidate threads
4. For ambiguous results: `mcp__claude_ai_Gmail__get_thread(thread_id=...)` to read
   subject/sender and confirm spam via reasoning (do not save legitimate emails)
5. Collect confirmed thread IDs
6. `mcp__homelab-mcp__ssh_exec`:
   ```
   python3 /srv/gmail-spam-filter/save_corpus.py --thread-ids <id1> <id2> ...
   ```
7. Parse stdout: report saved filenames and any errors to the user
8. Suggest: "Run `/missed-spam` on the saved files to diagnose and fix the filter"

The skill's reasoning step (step 4) is the key improvement over the old flow — it
prevents accidentally saving legitimate emails that matched a broad search query.

Note: the skill still uses the Gmail MCP tool for searching (steps 3–4). That's fine —
MCP search results give us thread IDs and enough metadata (subject, sender) to confirm
candidates. The script takes over for the actual full-fidelity data fetch.

---

## Recommended file structure

```
/srv/gmail-spam-filter/
├── DESIGN.md                   ← this file
├── signals.yaml                ← signal definitions — edit here to add/change campaigns
├── save_corpus.py              ← Program 2: on-demand corpus saver (called by skill)
├── run_auth.py                 ← one-time OAuth flow; run locally, copy token.json to server
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .gitignore                  ← excludes credentials.json, token.json, *.env
├── README.md
│
├── spam_filter/                ← Program 1: automatic spam filter (Docker container)
│   ├── __init__.py
│   ├── main.py                 ← polling loop, orchestration
│   ├── signals.py              ← signal evaluator: loads signals.yaml, implements Signal 5
│   ├── gmail_client.py         ← Gmail API auth, history.list, messages.get, label ops
│   ├── claude_client.py        ← Anthropic SDK call, prompt, response parsing
│   ├── loki_client.py          ← structured Loki push (per-decision events, errors)
│   └── state.py                ← historyId persistence (read/write state file)
│
├── tests/
│   ├── test_signals.py         ← unit tests for each signal with known header dicts
│   ├── test_save_corpus.py     ← unit tests for MIME parsing, header extraction
│   └── fixtures/               ← saved email dicts for test cases
│
├── missed-spams/               ← corpus folder (create on docker-server, not in git)
│   ├── new/                    ← unprocessed — save_corpus.py writes here
│   └── done/                   ← processed — /missed-spam skill moves files here
│
└── secrets/                    ← gitignored; mounted as Docker volume
    ├── credentials.json        ← OAuth client secrets from GCP (never in git)
    └── token.json              ← OAuth tokens, shared by both programs (never in git)
```

---

## Configuration and secrets

### Environment variables (never hardcode, never log values)

| Variable | Purpose | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API | Program 1 only |
| `GMAIL_USER` | Gmail address to monitor | Both |
| `POLL_INTERVAL_SECONDS` | Polling cadence (default 60) | Program 1 only |
| `STATE_FILE` | Path for historyId persistence | Program 1 only |
| `SECRETS_DIR` | Path to credentials.json + token.json | Both |
| `LOKI_URL` | Loki push endpoint, e.g. `http://192.168.0.231:3100` | Both |
| `LOKI_PUSH_ENABLED` | Set to `false` to disable Loki push without removing the var (default: `true`) | Both |

`save_corpus.py` reads `SECRETS_DIR` and `GMAIL_USER` from environment (or CLI flags).
It does not need `ANTHROPIC_API_KEY` or `STATE_FILE`.

### /etc/homelab/gmail-spam-filter.env (create on docker-server, never in git)

```
ANTHROPIC_API_KEY=<get from Bitwarden — same key used by n8n "Anthropic account" credential,
                   which is sourced from /etc/homelab/mtv.env on smavm>
GMAIL_USER=skip.morrow.mobile@gmail.com
POLL_INTERVAL_SECONDS=60
STATE_FILE=/data/state.json
SECRETS_DIR=/srv/gmail-spam-filter/secrets
LOKI_URL=http://192.168.0.231:3100
LOKI_PUSH_ENABLED=true
```

Add this service to `.claude/homelab-services.md` using the same format as existing entries.
Add `ANTHROPIC_API_KEY` and `-GmailSpamFilterAnthropicKey` parameter to `setup-mcp.ps1` if
the key differs from any existing Anthropic key already tracked there.

### secrets/ directory (never in git)

`credentials.json` — download from GCP Console → APIs & Services → Credentials →
OAuth 2.0 Client IDs. Use existing GCP project `n8n-gmail-spam` (project number 272891742137).
Create a new OAuth Client ID of type "Desktop app" — do NOT reuse n8n's credential, as a
token refresh by one app can invalidate the other's tokens.

`token.json` — created automatically on first run by the OAuth flow. Self-renewing thereafter.
Since the GCP app is published to Production (not Testing), refresh tokens do not expire after
7 days. **Shared between Program 1 and Program 2** — both read from the same file path.

Both files must be in `.gitignore`. Never log their contents.

---

## Gmail OAuth setup (one-time, covers both programs)

### Python packages required

```
google-auth>=2.0
google-auth-oauthlib>=1.0
google-api-python-client>=2.0
anthropic>=0.40
pyyaml>=6.0
```

### OAuth scope needed

`https://www.googleapis.com/auth/gmail.modify`

(Allows reading messages and modifying labels. Does not allow sending or deleting.
Used by both Program 1 and Program 2. Run auth once; token is shared.)

### Initial auth flow — run once locally before deploying

Because the initial OAuth flow requires a browser, run it on the Windows machine first:

```python
# run_auth.py — run once to create token.json, then it is self-renewing
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
flow = InstalledAppFlow.from_client_secrets_file('secrets/credentials.json', SCOPES)
creds = flow.run_local_server(port=0)   # opens browser for one-time consent
with open('secrets/token.json', 'w') as f:
    f.write(creds.to_json())
print("token.json created — copy to docker-server:/srv/gmail-spam-filter/secrets/")
```

After copying `token.json` to docker-server, both programs can use it. The Docker container
mounts it as a writable volume so auto-refresh works. The corpus saver reads it directly from
`/srv/gmail-spam-filter/secrets/` on the host filesystem.

### Subsequent token refresh (handled automatically in both programs)

```python
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

creds = Credentials.from_authorized_user_file(token_path, SCOPES)
if creds.expired and creds.refresh_token:
    creds.refresh(Request())   # silent, no browser needed
    with open(token_path, 'w') as f:
        f.write(creds.to_json())
```

### GCP project details

- Project name: `n8n-gmail-spam`, project number: `272891742137`
- OAuth consent screen: Published to Production — refresh tokens persist indefinitely
- Scopes: Gmail modify (sensitive — shows "unverified app" warning at re-auth; click
  Advanced → proceed, this is expected for personal-use unverified apps)

---

## State management (Program 1 only)

Use Gmail's `users.history.list` API instead of a local timestamp.

**First run bootstrap:**
```python
# Get a current historyId without processing any existing mail
result = service.users().messages().list(userId='me', labelIds=['INBOX'], maxResults=1).execute()
history_id = result.get('messages', [{}])[0].get('historyId') or result.get('historyId')
save_history_id(history_id)
# Do not process anything — start clean from this point forward
```

**Subsequent polls:**
```python
response = service.users().history().list(
    userId='me',
    startHistoryId=saved_history_id,
    historyTypes=['messageAdded'],
    labelId='INBOX'
).execute()

for history_entry in response.get('history', []):
    for msg_ref in history_entry.get('messagesAdded', []):
        msg_id = msg_ref['message']['id']
        ...

save_history_id(response['historyId'])
```

**State file format** (`/data/state.json`):
```json
{"historyId": "12345678", "last_poll": 1751234567.0}
```

**If historyId is invalid** (Gmail returns 404 on history.list — happens if historyId
is too old, ~1 week): log a warning, bootstrap a fresh historyId from messages.list,
save it, and skip that poll cycle. Do not crash.

**IMPORTANT:** After fetching a message via `messages.get`, verify it still has the
`INBOX` labelId before processing. A message can appear in history as "added" but have
already been moved by the time you fetch it.

---

## Header extraction (shared by both programs)

Gmail API returns headers as `message['payload']['headers']` — a list of
`{"name": str, "value": str}` dicts. Unambiguous, unlike n8n's mailparser Map.

Put `extract_fields()` and `get_body_text()` in a shared module (e.g. `spam_filter/gmail_client.py`
or a standalone `gmail_utils.py`). Both Program 1 and `save_corpus.py` import from it.

```python
def extract_fields(message: dict) -> dict:
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
        'body_text': get_body_text(message, truncate=1000),
        'all_headers': headers,     # save_corpus.py uses this; main filter ignores it
    }

def get_body_text(message: dict, truncate: int = None) -> str:
    """Recursively find text/plain part. truncate=None returns full text."""
    import base64

    def find_part(parts, mime):
        for part in parts:
            if part.get('mimeType') == mime:
                return part
            nested = part.get('parts', [])
            if nested:
                found = find_part(nested, mime)
                if found:
                    return found
        return None

    payload = message.get('payload', {})
    parts = payload.get('parts', [payload])
    part = find_part(parts, 'text/plain')
    if not part:
        return ''
    data = part.get('body', {}).get('data', '')
    if not data:
        return ''
    text = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
    return text[:truncate] if truncate else text

def get_body_html(message: dict) -> str:
    """Recursively find text/html part; strip base64 inline images."""
    import base64, re

    def find_part(parts, mime):
        for part in parts:
            if part.get('mimeType') == mime:
                return part
            nested = part.get('parts', [])
            if nested:
                found = find_part(nested, mime)
                if found:
                    return found
        return None

    payload = message.get('payload', {})
    parts = payload.get('parts', [payload])
    part = find_part(parts, 'text/html')
    if not part:
        return ''
    data = part.get('body', {}).get('data', '')
    if not data:
        return ''
    html = base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
    # Strip base64 inline images (noise), keep external src URLs (potential signals)
    html = re.sub(r'src="data:image/[^"]*"', 'src="[base64-image-stripped]"', html)
    return html
```

Program 1 (`main.py`) calls `extract_fields` with `truncate=1000` for body_text.
`save_corpus.py` calls with `truncate=None` to get the full body.

---

## Docker deployment (Program 1 only)

### docker-compose.yml

```yaml
services:
  gmail-spam-filter:
    build: .
    container_name: gmail-spam-filter
    restart: unless-stopped
    env_file:
      - /etc/homelab/gmail-spam-filter.env
    volumes:
      - ./secrets:/secrets      # credentials.json + token.json (writable for token refresh)
      - ./signals.yaml:/app/signals.yaml:ro  # signal definitions (read-only)
      - spam_filter_data:/data  # state.json persists across restarts
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  spam_filter_data:
```

`save_corpus.py` runs on the host (not in Docker) and reads `secrets/` directly from
`/srv/gmail-spam-filter/secrets/`. It does not need its own container.

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY spam_filter/ ./spam_filter/
HEALTHCHECK --interval=5m --timeout=10s --retries=3 \
  CMD python -c "import json,os,time; s=json.load(open(os.environ.get('STATE_FILE','/data/state.json'))); exit(0 if time.time()-s.get('last_poll',0)<400 else 1)"
CMD ["python", "-m", "spam_filter.main"]
```

`secrets/`, `signals.yaml`, and `save_corpus.py` are NOT copied into the image — all
mounted or run on the host at runtime.

---

## Testing

### Program 1 — signals

Test `signals.py` with plain dicts — no network, no API keys needed.

| Signal | Positive (should fire) | Negative (should not fire) |
|---|---|---|
| 1 | `x_gm_features='AQt7F2rLGlvxzGKTC1NaGADQ1A8RjTb6BMGUHcpBhWwzze9EE'` | `x_gm_features='AQt7F2rLGlvxzGKTClegitimateXYZ'` |
| 2 | `message_id='<...+g9W_ddh+QDR18-tkYxyw01Rtjxhzw1NaGADQ...>'` | `message_id='<CABWrandom@mail.gmail.com>'` |
| 3 | `in_reply_to='aograotnctng-jbbvdh.edu'` | `in_reply_to='cs.stanford.edu'` |
| 4 | `body_text='http://[::ffff:192.0.2.1]/go'` | `body_text='https://example.com'` |
| 5 | `to_addrs=['stranger@gmail.com']` | `to_addrs=['skip.morrow.mobile+nyt@gmail.com']` |
| 5 | `to_addrs=['james@outlook.com', 'skip.morrow.mobile@gmail.com']` → must NOT fire | |
| 6 | `return_path='noReply_tuiftndq@fit001.com'` | `return_path='noreply@amazon.com'` |
| 7 | `body_text='...?syclid=abc123...'` | `body_text='normal email body'` |

Also test:
- Signal 5 `+tag` stripping: `skip.morrow.mobile+camel@gmail.com` must NOT fire
- Signal 7 within 1000 chars: confirm syclid= is captured in the McAfee-format template
  (appears around char 640 — would be missed with a 500-char slice)

### Program 2 — corpus saver

Test `get_body_text`, `get_body_html`, and `extract_fields` with fixture message dicts
(no network needed). Key cases:
- `text/plain` at top level (simple email)
- `text/plain` nested inside `multipart/alternative` inside `multipart/mixed`
- `text/html` with `src="data:image/png;base64,..."` — confirm stripped
- Headers with mixed casing — confirm lowercased in `all_headers`
- Multi-recipient To field — confirm all addresses in `to_addrs`
- Missing Date header — confirm fallback to today's date in filename

---

## Logging guidelines

INFO level:
- Poll start: `Poll #{n}: checking history since {historyId}`
- Per email: `[{msg_id}] "{subject}" — signal {n} matched` or `no signal match → Claude`
- Claude result: `[{msg_id}] Claude: spam={bool}, confidence={n}, reason="{reason}"`
- Action taken: `[{msg_id}] Marked as SPAM` or `[{msg_id}] Passed (not spam)`
- Heartbeat: `Poll complete. {n} messages checked, {m} marked spam. Next poll in {s}s`

WARNING level:
- historyId invalid, resetting cursor
- Claude returned non-JSON response (log sanitized snippet, treat as spam=False)
- Token refresh occurred

ERROR level:
- Gmail API failure (include status code)
- Anthropic API failure

Never log raw header values beyond subject line. Never log the Anthropic API key, token
contents, or credentials.json contents.

---

## Loki logging

Structured Loki push gives per-decision forensic data queryable in Grafana by signal,
campaign, action, and date. This is the layer that would have made the 2026-06-30 incident
immediately visible: a panel of "emails evaluated, no signal matched" would have shown the
three Campaign 3 emails accumulating at `action=claude_fallback` before the pattern was
identified.

### Two-layer approach

**Layer 1 — structured push from Python (per-decision events):**
The program posts one JSON log line per filter decision to `/loki/api/v1/push`. Labels
are queryable in LogQL. No extra library needed — `urllib.request` handles it.

**Layer 2 — container stdout scraping (operational events):**
If Alloy or promtail is already running on docker-server and scraping Docker container
logs, the container's stdout (INFO/WARNING/ERROR lines) appears in Loki automatically
with no code change. Check with `docker ps | grep -E "alloy|promtail"`. If neither is
running, stdout logs are `docker logs`-only. Adding Alloy is a future step — it requires
no changes to this program.

### Label schema

All structured pushes use these stream labels (low-cardinality — fine as labels):

```
{job="gmail-spam-filter", host="docker-server", program="main"}
```

For `save_corpus.py` events:
```
{job="gmail-spam-filter", host="docker-server", program="save_corpus"}
```

Per-event data goes in the **log line as JSON** — never as labels. High-cardinality
values (thread_id, subject, from) must not be labels or Loki's index explodes.

### Log line schemas

**filter_decision** (one per email evaluated):
```json
{
  "level": "INFO",
  "event": "filter_decision",
  "thread_id": "19f19b7603f22a5e",
  "subject": "Re: [EXTERNAL] – WARNING",
  "from_addr": "admissions@belowtheprice.com",
  "action": "marked_spam",
  "signal_id": 7,
  "signal_name": "Campaign 3: syclid= URL tracking parameter",
  "claude_used": false,
  "claude_confidence": null,
  "claude_reason": null,
  "return_path": "noReply_tuiftndq@fit001.com",
  "x_gm_features": "AQt7F2rLGlvxzGKTC1NaGADQ1A8RjTb6BMGUHcpBhWwzze9EE",
  "message_id": "CABW1v11...NaGADQ...@autodiscover.mychaeldanna.com",
  "in_reply_to": "",
  "sender": "",
  "to_addrs": ["skip.morrow.mobile@gmail.com"]
}
```

Full header values are included (2026-07-01 decision — see below), not just which signal
matched. This turns "Claude caught something a signal almost matched" from a
`save_corpus.py` round-trip into a glance at the log line: compare the raw
`x_gm_features`/`return_path`/`message_id`/`in_reply_to` string directly against known
campaign fingerprints. Two signals get no extra visibility from this — Signal 4 (IPv6 URL)
and Signal 7 (`syclid=`) are body-dependent, and body text is deliberately excluded (see
below), so near-misses on those two still require `save_corpus.py` to diagnose.

`action` is one of: `marked_spam` | `passed` | `claude_fallback_spam` | `claude_fallback_passed`

Use `claude_fallback_spam` / `claude_fallback_passed` when Claude made the call (no signal
matched) — this separates deterministic catches from Claude catches in queries.

**poll_complete** (one per poll cycle):
```json
{"level": "INFO", "event": "poll_complete", "checked": 3, "marked_spam": 2, "next_poll_s": 60}
```

**corpus_saved** (one per file written by save_corpus.py):
```json
{
  "level": "INFO",
  "event": "corpus_saved",
  "thread_id": "19f19b7603f22a5e",
  "subject": "Re: [EXTERNAL] – WARNING",
  "from_addr": "admissions@belowtheprice.com",
  "output_path": "/srv/gmail-spam-filter/missed-spams/new/2026-06-30_19f19b7603f22a5e.json"
}
```

**error events** (`level: "ERROR"`):
```json
{"level": "ERROR", "event": "api_error", "service": "gmail", "status": 429, "msg": "..."}
{"level": "ERROR", "event": "auth_error", "msg": "token.json missing"}
{"level": "WARNING", "event": "history_reset", "old_history_id": "12345", "new_history_id": "99999"}
{"level": "WARNING", "event": "token_refresh", "msg": "OAuth token refreshed"}
```

### What NOT to include in log lines

- Body text or body_html — not a privacy concern (see below), but a size/cost one:
  `filter_decision` fires on every evaluated email, spam or not, so unbounded body content
  would make Loki's storage grow far faster than header values do. If body-signal tuning
  (Signal 4 IPv6 URL, Signal 7 syclid=) is needed, use `save_corpus.py` instead.
- Anthropic API key, token.json contents, credentials.json

**2026-07-01 decision — full header values (Return-Path, X-Gm-Features, Message-ID,
In-Reply-To, Sender, To) are included**, not just signal_id/signal_name. Originally this
doc excluded them on cardinality grounds, but that concern only applies to Loki *stream
labels* (the index) — these values live in the JSON log line body, which Loki doesn't
index, so there's no cardinality cost. The owner's call: no information in these headers
is more sensitive than what's already logged (subject, from_addr), and anyone with Loki
access would have Gmail access anyway. Retention is whatever this Loki instance is
configured for globally (currently 7 days, `retention_period` in `loki-config.yaml`) —
not configurable per-application, so this data ages out automatically with everything else.

### loki_client.py

```python
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
```

### Calling it from main.py

```python
import re
from . import loki_client

_SIGNAL_REASON_RE = re.compile(r'^Signal (\d+): (.*)$')

def _parse_signal_reason(reason: str):
    m = _SIGNAL_REASON_RE.match(reason)
    return (int(m.group(1)), m.group(2)) if m else (None, reason)

# After each filter decision:
matched, reason = run_signals(fields)
signal_id = signal_name = None
claude_used = False
claude_confidence = claude_reason = None
if matched:
    mark_spam(service, msg_id)
    signal_id, signal_name = _parse_signal_reason(reason)
    action = 'marked_spam'
else:
    claude_used = True
    result = call_claude(fields)
    claude_confidence, claude_reason = result['confidence'], result['reason']
    action = 'claude_fallback_spam' if result['spam'] else 'claude_fallback_passed'
    if result['spam']:
        mark_spam(service, msg_id)

loki_client.filter_decision(
    thread_id=msg_id, subject=fields['subject'], from_addr=fields['from_addr'],
    action=action, signal_id=signal_id, signal_name=signal_name,
    claude_used=claude_used, claude_confidence=claude_confidence, claude_reason=claude_reason,
    return_path=fields.get('return_path'), x_gm_features=fields.get('x_gm_features'),
    message_id=fields.get('message_id'), in_reply_to=fields.get('in_reply_to'),
    sender=fields.get('sender'), to_addrs=fields.get('to_addrs'),
)

# At end of poll cycle:
loki_client.poll_complete(checked=n_checked, marked_spam=n_spam, next_poll_s=POLL_INTERVAL)
```

### Calling it from save_corpus.py

```python
import loki_client
# after writing the file:
loki_client.corpus_saved(thread_id, fields['subject'], fields['from_addr'], output_path)
```

### Finding the Loki URL

```bash
# On docker-server:
docker ps | grep loki
# Look for a container with port 3100. The URL is http://192.168.0.231:3100
# Or check Grafana → Settings → Data Sources → Loki → URL field
```

Confirm the address before setting `LOKI_URL` in the env file. If Loki is behind Caddy
with auth, check `.claude/homelab-services.md` for whether an internal bypass URL exists.

### Useful LogQL queries for Grafana

```logql
# All spam caught (deterministic signals only)
{job="gmail-spam-filter"} | json | event=`filter_decision` | action=`marked_spam`

# Emails Claude had to evaluate (no signal matched — watch this for new campaigns)
{job="gmail-spam-filter"} | json | event=`filter_decision` | claude_used=`true`

# Emails that reached Claude AND got through (potential false negatives)
{job="gmail-spam-filter"} | json | event=`filter_decision` | action=`claude_fallback_passed`

# Signal 7 hits specifically
{job="gmail-spam-filter"} | json | event=`filter_decision` | signal_id=`7`

# Claude fallback catches — compare x_gm_features against known campaign fingerprints
# to spot infrastructure variants Signal 1 doesn't match yet
{job="gmail-spam-filter"} | json | event=`filter_decision` | action=`claude_fallback_spam`
  | line_format `{{.x_gm_features}} | {{.return_path}} | {{.message_id}}`

# All errors
{job="gmail-spam-filter"} | json | level=`ERROR`

# Corpus saves (emails sent to manual analysis)
{job="gmail-spam-filter"} | json | event=`corpus_saved`

# Daily spam count panel (Grafana time series)
sum(count_over_time({job="gmail-spam-filter"} | json | event=`filter_decision` | action=`marked_spam` [$__interval]))
```

The `claude_used=true, action=claude_fallback_passed` query is the early-warning panel:
it shows emails that slipped past all signals AND past Claude. A spike here means a new
campaign is active — check those subjects and senders before adding a new signal.

---

## GitHub repo setup

Repo name suggestion: `gmail-spam-filter` under MrGibbage account.

`.gitignore` must include (`signals.yaml` is config, NOT ignored — commit it to git):
```
secrets/
missed-spams/
*.env
token.json
credentials.json
__pycache__/
*.pyc
.pytest_cache/
.env
```

`README.md` should cover:
- What this is and the two-program architecture
- Prerequisites: GCP project, OAuth credentials, Anthropic API key
- Setup steps: clone, create secrets/, run run_auth.py, create env file, docker compose up
- Adding a new spam signal: edit `signals.yaml`, `docker compose restart gmail-spam-filter`
  (no Python change needed for regex/substring signals)
- Using the corpus saver: `/save-missed-spam` Claude skill calls `save_corpus.py` automatically
- Where secrets live and what goes in Bitwarden

Store Anthropic API key in Bitwarden under: **Anthropic API Key — Gmail Spam Filter**
(or note if it is the same key as an existing entry).

---

## Holocron documentation

After the service is running, create:
`C:/srv/holocron/docker-server/docker-services/gmail-spam-filter.md`

Cover (following holocron philosophy — WHY not WHAT):
- Why n8n was replaced (the three failure modes)
- Why historyId instead of timestamp polling
- The two-program design: automatic filter (Docker) + on-demand corpus saver (host script)
- Why corpus saver is a host script, not in Docker: needs to be callable by the Claude skill
  via ssh_exec without spinning up a container
- OAuth: GCP project, Production mode, shared token.json, one-time local auth
- Secrets: `/etc/homelab/gmail-spam-filter.env`, `./secrets/` volume
- How to add a new signal: edit `signals.yaml`, `docker compose restart`
- Campaign fingerprint history (copy "Known incidents" list from `gmail-spam-filter-n8n.md`)
- Final Notes format: current health, critical gotchas, GitHub repo link, last known issue

Update `gmail-spam-filter-n8n.md` to add a migration note at the top:
```
> **Migrated** — replaced by Python implementation as of [date].
> See `gmail-spam-filter.md`. This page is retained for historical incident record.
```

Update `scheduled-tasks.md` in the Holocron (find it with `grep -r "scheduled" C:/srv/holocron`
to confirm the path). Add an entry for the gmail-spam-filter service. Key things to note:

- This is **not a cron job** — it is a `restart: unless-stopped` Docker service that runs
  its own internal 60-second polling loop via `time.sleep()` in `main.py`
- Trigger: Docker restart policy + internal loop (starts automatically on docker-server boot)
- What it does: polls Gmail `history.list` every 60 s, evaluates signals, moves spam
- How to verify it's running: `docker ps` (look for `gmail-spam-filter`), `docker logs gmail-spam-filter`
- Healthcheck: `docker inspect gmail-spam-filter --format='{{.State.Health.Status}}'`
  (goes unhealthy if `last_poll` timestamp in state.json is more than 400 s old)

---

## Health monitoring

The existing n8n "Gmail Spam Filter Health Monitor" workflow checks `lastTimeChecked`
in n8n's staticData — it will be irrelevant after migration. Options:
- Delete it once migration is confirmed stable
- Replace with a simple check that emails via Mailrise if the Docker container is not healthy

The Docker `HEALTHCHECK` (defined above) provides `docker ps` visibility into whether the
poll loop is completing cycles. A container that is running but hung shows as unhealthy.

---

## Migration from n8n

1. Implement and test locally (all signals pass unit tests)
2. Run `run_auth.py` on Windows to generate `token.json`
3. Copy `token.json` and `credentials.json` to `/srv/gmail-spam-filter/secrets/` on docker-server
4. Create `/etc/homelab/gmail-spam-filter.env` on docker-server
5. `docker compose up -d` — verify logs show polling
6. Test `save_corpus.py`: `python3 /srv/gmail-spam-filter/save_corpus.py --thread-ids <any-thread-id>`
7. Watch logs for 24 hours: confirm spam caught, no false positives
8. Update `/save-missed-spam` skill to use `save_corpus.py` (replace Step 3–6 in the skill)
9. **Deactivate** n8n workflow (do not delete):
   `curl -X POST http://192.168.0.231:5678/api/v1/workflows/4L6yy2QKRLMAW3y3/deactivate -H "X-N8N-API-KEY: $N8N_API_KEY"`
10. Run deactivated n8n + active Python for 1 week as shadow check
11. After 1 week stable: delete n8n workflow, update Holocron, push to GitHub
12. Verify Loki logging: run a manual filter cycle, then check
    `{job="gmail-spam-filter"}` in Grafana — confirm `filter_decision` and `poll_complete`
    events are arriving. If `LOKI_URL` is wrong the filter still works (push failures are
    swallowed), but fix it before declaring the migration done.
13. Update `scheduled-tasks.md` in the Holocron — add gmail-spam-filter as a Docker service
    (not a cron job), document the 60-second internal poll loop, restart policy, and
    healthcheck command

---

## What NOT to do

- **Do not put high-cardinality values (thread_id, subject, From address) in Loki stream
  labels.** They go in the JSON log line only. Loki's index is built from labels; unique
  values per log line will exhaust Loki's label index and degrade query performance.
- **Do not let Loki push failures propagate as exceptions.** The `_push()` function
  wraps `urlopen` in a bare `except Exception: pass` — this is intentional. A Loki outage
  must not take down the spam filter.
- **Do not embed signal patterns in Python code.** All regex/substring signals belong in
  `signals.yaml`. Only Signal 5 (multi-recipient +tag logic) lives in `signals.py`.
  Adding a new campaign should require a YAML edit, not a code change.
- **Do not use the Gmail MCP tool to fetch email content for the corpus.** It omits
  Return-Path, X-Gm-Features, Message-ID, In-Reply-To, and body_html. Use `save_corpus.py`
  (which calls the Gmail API directly) for all corpus saves.
- **Do not put save_corpus.py in the Docker container.** It needs to be callable directly
  from the host by the Claude skill via ssh_exec. Running it inside Docker would require
  `docker exec`, which adds complexity and credential-sharing complexity.
- **Do not use a service account.** Personal Gmail does not support service account
  domain-wide delegation. You need a user OAuth2 credential (the flow above).
- **Do not store token.json inside the Docker image.** Mount it as a volume so it
  survives image rebuilds and can be updated by token refresh without a rebuild.
- **Do not hardcode the Anthropic API key.** Always read from environment.
- **Do not use messages.list polling** for deduplication. It is prone to race conditions
  and missed messages. `history.list` is the correct Gmail API pattern.
- **Do not process messages without checking INBOX label.** After `history.list` returns
  a messageAdded event, confirm `'INBOX' in msg['labelIds']` before running signals.
- **Do not delete the n8n workflow immediately.** Keep it deactivated for at least one
  week as a fallback before deleting.
- **Do not mount secrets/ read-only** unless you handle token refresh writes separately.
  Silent token refresh failures will look exactly like n8n's OAuth expiry incidents.
