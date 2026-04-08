# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Event Submission Bot** — an internal tool for the Green Party (Vihreät) that allows members to submit events to the official event calendar via Discord and email. Users go through an interactive flow, verify the parsed data, and the bot posts it to the external Event API.

See [PRD.md](PRD.md) for the full product spec.

**Reference implementation:** `../Vihreät tapahtumabotti/` — a previous Discord bot (Python + discord.py) that does a sequential Q&A flow with button-based UI and Google Sheets logging. Borrow patterns from it but do NOT submit to that API — this new bot submits to the Tapahtumat API below.

---

## Tech Stack (decided)

- **Language:** Python
- **Discord:** `discord.py` (same as previous bot)
- **Email ingestion:** IMAP polling (`imaplib` or `imapclient`) — free, no external service
- **Database / audit log:** SQLite (local file) — for audit trail and per-server config
- **No LLM for MVP** — structured input via Discord form/questions or email template

---

## Event API

**Endpoint:** `POST https://tapahtumat.vihreaturku.fi/api/v1/events`
**Auth header:** `api-key: <your-api-key>`
**Content-Type:** `application/json`
**Full API docs:** https://tapahtumat.vihreaturku.fi/node/217

### Required fields
| Field | Type | Notes |
|-------|------|-------|
| `title` | string | Event name |
| `start` | ISO 8601 with timezone | e.g. `2024-06-01T14:00:00+03:00` |
| `organiser` | string | Must match an existing organization in the system |
| `place_name` | string | Venue name |
| `municipality` | string | Must match taxonomy term |

### Optional fields
| Field | Type | Notes |
|-------|------|-------|
| `end` | ISO 8601 with timezone | |
| `description` | string | |
| `event_type` | string | Must match taxonomy term |
| `street_address` | string | |
| `remote` | boolean | |
| `for_everyone` | boolean | Always sent as `true` — Vihreät events are always public. Not asked from users. |

### Validation notes
- `organiser`, `event_type`, `municipality` must match existing taxonomy values — returns HTTP 400 with a message if not found.
- `place` is auto-created if not found.
- Batch submissions supported via `"events": [...]` array wrapper.

### Taxonomy handling (municipality, event_type, organiser)
The API has **no taxonomy endpoint** for municipalities or event types (confirmed from live API docs).
The working set is hardcoded in `db.py` (`DEFAULT_MUNICIPALITIES`, `DEFAULT_EVENT_TYPES`) and seeded
into SQLite on startup. Admins can extend or trim the list via `/taxonomy add` and `/taxonomy remove`.
Organisers are fetched live from `GET /api/v1/organisers/search` — no local cache needed.

**Place creation:** The API's `POST /api/v1/events` does NOT accept `place_name`, `municipality`,
or `street_address` directly. Location is associated via `place_id` only. When a user enters a
new venue manually, the bot first calls `POST /api/v1/places` (which requires `name` +
`municipality_name`) to create the place, then uses the returned `place_id` in the event payload.

### Response
- Success `200`: `{ "status": "ok", "created_event_ids": [...], "event_urls": [...] }`
- Error `400`: `{ "status": "error", "message": "..." }`
- Auth error `403`: `{ "status": "error", "message": "Invalid API key" }`

---

## Architecture

```
Discord message  ──┐
                   ├──► Event Parser ──► Verification Flow ──► Tapahtumat API
IMAP email poll  ──┘                          │
                                         SQLite audit log
```

### Two ingestion channels
1. **Discord:** Bot watches a configured channel per server. User posts a message → bot opens a private thread → interactive Q&A with buttons.
2. **Email (IMAP):** Bot polls an inbox on a configurable interval. Parses the email body using a simple template. Bot sends a **reply email** to the submitter showing all parsed fields. If the submitter does not reply within **1 hour**, the submission is auto-confirmed and posted to the API. If they reply with corrections, the bot re-parses and sends another confirmation email. Log the pending submission in SQLite so the bot survives restarts during the confirmation window.

### Verification flow (Discord)
Mirrors the previous bot pattern:
1. Collect all fields via sequential questions in a private thread (60 s timeout per question).
2. Display full summary with all fields (including empty ones).
3. User clicks **Kyllä / Ei** (Yes/No) — "Ovatko tiedot oikein?"
4. If **Ei**: show `CorrectionView` — one button per field. User picks a field, sends corrected value, loop repeats.
5. If **Kyllä**: POST to Tapahtumat API. Show result URL or error message.

### Per-server config (SQLite)
Each Discord server (guild) stores:
- `submission_channel` — channel name/ID where submissions are accepted
- `admin_role` — role name/ID that can run admin commands
- `default_organiser` — pre-filled organiser name for that server
- `api_key` — **encrypted** with `cryptography.fernet` using a master key from `.env`. Never store API keys in plaintext in SQLite. The master encryption key lives only in `.env`.

### Rate limiting
- Limit: e.g. 10 submissions per user per hour (configurable).
- Allows a burst of ~10 events in a row but blocks bots/automation.
- On limit hit: send the user a message explaining the cooldown, do not silently drop.
- Track in SQLite (timestamp + user_id).

### Audit log (SQLite)
Log every event: submission received, parsed fields, user confirmation, API response (success/error), corrections made. Log everything.

---

## File layout (planned)

```
bot.py                  # Entry point — starts Discord client + IMAP poller
config.py               # Env vars / .env loading
db.py                   # SQLite schema + helper functions (audit log, server config, rate limiting)
discord_channel.py      # Discord ingestion + interaction handler (Q&A flow, buttons, threads)
email_channel.py        # IMAP poller + email parser
event_pipeline.py       # Shared: field validation, API submission
.env                    # Secrets (not committed)
.env.example            # Template for required env vars
requirements.txt
```

---

## Environment variables (`.env`)

```
DISCORD_BOT_TOKEN=
IMAP_HOST=
IMAP_USER=
IMAP_PASSWORD=
IMAP_POLL_INTERVAL_SECONDS=60
DATABASE_PATH=audit.db
RATE_LIMIT_MAX=10
RATE_LIMIT_WINDOW_SECONDS=3600
ENCRYPTION_KEY=          # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Per-server API keys are stored **encrypted** in SQLite using the `ENCRYPTION_KEY`. The key must never be committed.

---

## Development setup (MVP)

Run locally on your own machine:

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env
python bot.py
```

No Docker, no deployment pipeline needed for MVP.

---

## Key patterns from the previous bot

- Use `discord.ui.View` + `discord.ui.Button` for all interactive choices.
- Open a **private thread** per submission to keep the main channel clean.
- Each `wait_for` call needs a `timeout` (60 s questions, 120 s buttons) with a Finnish cancellation message on timeout.
- The correction loop: re-display the full summary after each correction, then ask for confirmation again.
- All user-facing messages in **Finnish**.
