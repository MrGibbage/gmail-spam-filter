# gmail-spam-filter

Python replacement for the n8n "Gmail Spam Filter" workflow. Two independent programs
sharing OAuth credentials and a corpus folder — see `DESIGN.md` for the full spec and
rationale.

| | `spam_filter/main.py` | `save_corpus.py` |
|---|---|---|
| What it is | Automatic spam filter | On-demand corpus saver |
| Trigger | Docker container, loops every 60s | `/save-missed-spam` Claude skill |
| Lifetime | Always-on (`restart: unless-stopped`) | Runs once, exits |

## Prerequisites

- A GCP project with a Gmail API OAuth 2.0 Client ID (Desktop app type). This repo uses
  project `n8n-gmail-spam` (272891742137) — see `DESIGN.md` § Gmail OAuth setup.
- An Anthropic API key (Claude Haiku is used as a fallback classifier).
- Python 3.12+, Docker + Docker Compose on the deployment host.

## Setup

1. Clone this repo onto the deployment host (e.g. `/srv/gmail-spam-filter` on docker-server).
2. Create `secrets/` and place `credentials.json` in it (downloaded from GCP Console).
3. Run the OAuth flow **locally** (needs a browser):
   ```bash
   pip install -r requirements.txt
   python3 run_auth.py
   ```
   This creates `secrets/token.json`. Copy both `secrets/credentials.json` and
   `secrets/token.json` to the deployment host's `secrets/` directory.
4. Create `/etc/homelab/gmail-spam-filter.env` on the deployment host:
   ```
   ANTHROPIC_API_KEY=<from Bitwarden>
   GMAIL_USER=skip.morrow.mobile@gmail.com
   POLL_INTERVAL_SECONDS=60
   STATE_FILE=/data/state.json
   SECRETS_DIR=/secrets
   LOKI_URL=http://192.168.0.231:3100
   LOKI_PUSH_ENABLED=true
   ```
   `SECRETS_DIR` must be `/secrets` here — this env file is consumed *inside* the
   container, where `docker-compose.yml` mounts `./secrets:/secrets`.
5. `docker compose up -d` — check `docker logs gmail-spam-filter` for polling activity.

## Adding a new spam signal

Edit `signals.yaml`, then `docker compose restart gmail-spam-filter`. No Python change
needed for `regex` / `substring` / `any_substring` signals. Only Signal 5 (multi-recipient
+tag-stripping) requires editing `spam_filter/signals.py`.

## Using the corpus saver

The `/save-missed-spam` Claude skill calls `save_corpus.py` automatically when you ask it
to save a missed spam email for analysis:

```bash
python3 save_corpus.py --thread-ids <id1> <id2>
```

Saved files land in `missed-spams/new/`. Run `/missed-spam` on them to diagnose and fix
the filter.

## Tests

```bash
pip install -r requirements.txt pytest
pytest tests/
```

All tests run against plain dicts — no network access or API keys required.

## Secrets

`secrets/credentials.json` and `secrets/token.json` are gitignored and never committed.
Store the Anthropic API key in Bitwarden under **Anthropic API Key — Gmail Spam Filter**.
`signals.yaml` is config, not a secret — it IS committed to git.
