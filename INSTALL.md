# Bot Installation Guide

## Prerequisites
- Python 3.11 or newer installed
- A Discord account with access to the Discord Developer Portal
- An API key from tapahtumat.vihreaturku.fi (your user profile → API key tab)

---

## Step 1 — Create a Discord Application

1. Go to https://discord.com/developers/applications
2. Click **New Application** (top right)
3. Name it e.g. `Tapahtumabot` → click **Create**

---

## Step 2 — Create the Bot user

1. Left sidebar → **Bot**
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token** click **Reset Token** → copy the token (you will need it in Step 5)

---

## Step 3 — Enable required Privileged Intents

Still on the **Bot** page, scroll down to **Privileged Gateway Intents** and turn ON:

- ✅ **Message Content Intent**

Click **Save Changes**.

---

## Step 4 — Generate an invite link and add the bot to your server

1. Left sidebar → **OAuth2** → **URL Generator**
2. Under **Scopes** check:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Under **Bot Permissions** check:
   - ✅ Send Messages
   - ✅ Create Public Threads
   - ✅ Send Messages in Threads
   - ✅ Read Message History
   - ✅ View Channels
   - ✅ Manage Threads
4. Copy the **Generated URL** at the bottom
5. Paste the URL in your browser → select your server → click **Authorize**

---

## Step 5 — Configure the .env file

In the project folder, copy `.env.example` to `.env`:

```
copy .env.example .env
```

Open `.env` and fill in:

```
DISCORD_BOT_TOKEN=    ← paste the token from Step 2
ENCRYPTION_KEY=       ← generate below
```

Generate the encryption key by running:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output and paste it as the value of `ENCRYPTION_KEY` in `.env`.

**Important:** Never share or commit the `.env` file. The `.env.example` file must always have empty values only.

---

## Step 6 — Install dependencies

```
pip install -r requirements.txt
```

---

## Step 7 — Add organisations to the database (one-time setup)

```
python seed_organisers.py
```

This adds the Uusimaa associations to the organiser list. Run it again when you add more regions to `seed_organisers.py`.

---

## Step 8 — Start the bot

```
python bot.py
```

The terminal should show:
```
[bot] Logged in as Tapahtumabot#1234
[bot] Synced 5 slash command(s)
```

Leave this terminal window open — the bot runs as long as it is open.

---

## Step 9 — Restrict bot access to one channel (optional but recommended)

To prevent the bot from seeing all channels on your server:

1. Go to **Server Settings → Roles** → find the bot's role (named after the bot)
2. Under **General Permissions**, turn OFF `View Channels`
3. Go to the specific submission channel → **Edit Channel → Permissions**
4. Add the bot's role → turn ON `View Channel` and `Send Messages`

---

## Step 10 — Configure the bot in Discord

Run these slash commands in any channel on your server:

**1. Set up the submission channel and admin role:**
```
/setup channel:#your-channel role:@your-admin-role
```
- `channel` = the channel where members will post events
- `role` = your admin/moderator role (server owner can always run admin commands regardless)

**2. Set the Tapahtumat API key (opens a private popup — key is never visible in chat):**
```
/setapikey
```

**3. Verify everything is correct:**
```
/status
```

---

## Step 11 — Test the bot

Post any message in the channel you configured in Step 10.

The bot should immediately open a private thread and start the Q&A flow in Finnish.

---

## Day-to-day commands

| Command | What it does |
|---------|--------------|
| `/setup` | Change the submission channel or admin role |
| `/setapikey` | Update the API key |
| `/status` | Show current configuration |
| `/taxonomy add organiser "Name"` | Add an organisation to the list |
| `/taxonomy remove organiser "Name"` | Remove an organisation |
| `/taxonomy add municipality "City"` | Add a municipality |
| `/taxonomy add event_type "Type"` | Add an event type |
| `/listtaxonomy organiser` | List all organisations |
| `/listtaxonomy municipality` | List all municipalities |
| `/listtaxonomy event_type` | List all event types |

---

## Restarting the bot

Stop the bot with `Ctrl+C` in the terminal, then run `python bot.py` again.

A restart is required after:
- Editing any `.py` file
- Editing `.env`
- Running `seed_organisers.py`
