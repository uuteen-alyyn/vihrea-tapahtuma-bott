# Implementation Plan — Open Issues

This document is a step-by-step plan for resolving all open GitHub issues.
Written during planning phase — no code has been changed yet.

---

## Summary of findings from API docs

Before planning, the live API docs at `/api/v1/docs` were inspected. This revealed a
critical discrepancy between what the code currently does and what the API actually accepts:

**POST /api/v1/events accepts ONLY:**
- `title`, `start`, `organiser_id` (required)
- `end`, `description`, `event_type`, `place_id`, `remote`, `for_everyone` (optional)

**`place_name`, `municipality`, and `street_address` are NOT valid fields for event creation.**
The current code sends these fields in the event payload — the API silently ignores them.
This is the root cause of Issues #10 and #8.

To associate a location with an event, you must:
1. Create or find a place via `POST /api/v1/places` (requires `name` + `municipality_name` or `municipality_id`)
2. Use the returned `place_id` in `POST /api/v1/events`

---

## Issue #10 — Bug: Custom venue has no location data (CRITICAL)

**Root cause:**
When a user searches for a place and selects one from results, `place_id` is correctly set
and gets included in the event payload — this works.

When a user chooses manual entry ("Syötä käsin"), `place_id` is `None`. The code falls back to
sending `place_name` and `municipality` directly in the event payload. The API does not
recognise these fields on event creation and ignores them. The event is created with no
location at all.

**Fix — two files affected:**

### Step 1: Add `create_place()` to `event_pipeline.py`

Add a new async function after `search_places()`:

```python
async def create_place(
    api_base_url: str,
    api_key: str,
    name: str,
    municipality_name: str,
    street_address: str = "",
) -> int:
    """
    Creates a new place via POST /api/v1/places.
    Returns the new place's integer ID.
    Raises PlaceSearchError on failure.
    """
```

The function POSTs to `/api/v1/places` with:
- `name`
- `municipality_name`
- `street_address` (only if non-empty)

Parse the response to extract the new place's `id` and return it.
Raise `PlaceSearchError` if the request fails or returns a non-200 status.

### Step 2: Fix `build_payload()` in `event_pipeline.py`

Remove `place_name`, `municipality`, and `street_address` from the event payload.
These fields are not accepted by the API. The location is conveyed via `place_id` only.

Current (wrong):
```python
payload = {
    "place_name": data.place_name,
    "municipality": data.municipality,
    ...
}
if data.street_address:
    payload["street_address"] = data.street_address
```

After fix: remove all three of those lines. `place_id` is already conditionally included.

**Note:** Keep `place_name`, `municipality`, `street_address` on the `EventData` dataclass —
they are still needed for the user-facing summary screen and for calling `create_place()`.

### Step 3: Create the place before submitting the event (`discord_flow.py`)

In `DiscordSubmissionFlow._submit()`, before calling `submit_event()`:

```python
# If manual place entry was used, create the place in the API first
if self.data.place_id is None and self.data.place_name:
    try:
        place_id = await create_place(
            self.api_base_url, api_key,
            name=self.data.place_name,
            municipality_name=self.data.municipality,
            street_address=self.data.street_address,
        )
        self.data.place_id = place_id
    except PlaceSearchError as exc:
        await self.thread.send(f"❌ Tapahtumapaikan luominen epäonnistui: {exc}")
        return
```

This way:
- Place creation is deferred to submission time (corrections still work naturally)
- If place creation fails, the user gets a clear error before the event is even attempted
- No duplicate places are created if the user edits fields before confirming

### Step 4: Update `_ask_place_manual()` in `discord_flow.py`

Currently asks only for `place_name` and `street_address`. Since municipality is now
required for place creation, it must be collected here too (instead of by the later
standalone municipality question).

New order of questions:
1. Ask `place_name` (free text)
2. Ask `street_address` (free text, optional with `-`)
3. Ask `municipality` (search picker from taxonomy list)

Return: `(place_name, street_address, municipality, None)` — `place_id` stays None until submit.

### Step 5: Remove the standalone municipality question in `_collect_fields()`

The existing fallback:
```python
if not self.data.municipality:
    self.data.municipality = await self._ask_search(...)
```

...exists because manual entry previously didn't collect municipality.
After Step 4, this is no longer needed for non-remote events (municipality is always
collected as part of place selection or manual entry). Remove this block for non-remote
events. Keep it as a safety fallback for the remote-event flow (see Issue #8 below).

### Issue #6 — already resolved by this fix

Issue #6 ("search existing locations first, then allow adding new one") is already
implemented — `_ask_place_live()` shows search results first with a "Syötä käsin" fallback.
The manual entry path is now fixed by the steps above. No additional work for #6.

---

## Issue #8 — Remote event location data (BUG)

**Current behaviour:**
For remote events, the bot asks for an invite link and a `place_name` (e.g. "Zoom").
Municipality is asked separately. Neither ends up in the event because, again, the API
does not accept `place_name` or `municipality` on event creation — only `place_id`.

**Desired behaviour (per issue + API capabilities):**
Events need to be sortable by city. A remote event can be:
- **National** (no specific city) — no place needed
- **City-specific** (e.g. Turku Greens' online event) — needs a municipality

**Fix in `discord_flow.py` — `_collect_fields()`:**

Replace the current remote-event block:
```python
# OLD
if self.data.remote:
    self.data.invite_link = ...
    self.data.place_name = await self._ask_text("📍 Tapahtumapaikan nimi?")
```

With:
```python
# NEW
if self.data.remote:
    self.data.invite_link = await self._ask_text("🔗 Tapahtuman linkki...?")
    is_city_specific = await self._ask_yesno(
        "🗺️ **Liittyykö tapahtuma tiettyyn kuntaan?**\n"
        "Valitse *Kyllä* jos kyseessä on esim. paikallisosaston etätapahtuma.\n"
        "Valitse *Ei* kansallisille tapahtumille."
    )
    if is_city_specific:
        self.data.municipality = await self._ask_search(
            "🗺️ **Mikä kunta?**", municipalities, "Hae kunta"
        )
        self.data.place_name = "Etätapahtuma"  # used when creating the place in _submit()
```

**Fix in `_submit()` — handle remote city-specific place creation:**

The place-creation block added in Issue #10 already handles this correctly:
- Remote + city-specific: `place_name = "Etätapahtuma"`, `municipality` is set → place gets created
- Remote + national: `place_name` is empty, `place_id` stays None → no place sent to API

No additional submit-time logic needed beyond the Issue #10 fix.

**Fix in `_ask_field()` — correction for remote location:**

When correcting `place_name` on a remote event, the current `_ask_place_live()` search flow
is inappropriate. For remote events, correcting location should re-ask the
city-specific yes/no question and municipality — not show a venue search. Add a branch:

```python
if field == "place_name" and self.data.remote:
    # re-run the remote location sub-flow
    ...
```

---

## Issue #5 — Remove "Kaikille avoin" question

**Question to confirm:** Are all Vihreät events always open to everyone?

**Proposed resolution:**
The `for_everyone` field in the API is optional. The comment in `build_payload()` says
"API defaults to true". Since these are Green Party public events, the assumption is
they are always open to all.

**If confirmed — changes required:**

1. **`event_pipeline.py`** — `FIELD_LABELS`: remove the `for_everyone` entry.
2. **`event_pipeline.py`** — `EventData`: remove the `for_everyone` field.
3. **`event_pipeline.py`** — `build_payload()`: remove the `for_everyone` line from the payload
   entirely (let the API default apply). If the API default is confirmed to be `True`, this is safe.
   If uncertain, explicitly hardcode `payload["for_everyone"] = True`.
4. **`discord_flow.py`** — `_collect_fields()`: remove the `_ask_yesno` call for `for_everyone`.
5. **`discord_flow.py`** — `CorrectionView.FIELDS`: `for_everyone` is derived from `FIELD_LABELS`
   so removing it there is sufficient.

**If NOT all events are always public:** keep the question, close the issue as won't-fix.

---

## Issue #7 — Fetch taxonomy from API

**Finding:** The API has no taxonomy endpoint. Municipalities and event types cannot
be fetched dynamically. This is confirmed by the live API docs.

**Resolution:** No code change needed. The current approach is correct:
- Default values are hardcoded in `db.py` (`DEFAULT_MUNICIPALITIES`, `DEFAULT_EVENT_TYPES`)
- Admins can extend them via `/taxonomy add` and `/taxonomy remove`
- The `taxonomy_cache` table in SQLite stores the working set

**Housekeeping only:**
- Remove the outdated 24-hour TTL comment from `CLAUDE.md` (it assumed an API endpoint exists)
- Update `CLAUDE.md` to state clearly that taxonomy is managed manually, not fetched from API

---

## Issue #4 — Multiple organisers

**Finding:** The API's `POST /api/v1/events` only accepts a single `organiser_id` (integer).
There is no array or multi-value field for organisers in the documented API.

**Resolution:** Cannot be implemented without a change to the API.
This issue should be moved to a backlog or discussed with the API maintainer.
No code changes.

---

## Implementation order

| # | Issue | Priority | Effort | Depends on |
|---|-------|----------|--------|------------|
| 1 | #10 Fix `build_payload()` | Critical | Small | — |
| 2 | #10 Add `create_place()` | Critical | Medium | Step 1 |
| 3 | #10 Update `_submit()` | Critical | Small | Steps 1–2 |
| 4 | #10 Update `_ask_place_manual()` + remove standalone municipality question | Critical | Small | — |
| 5 | #8 Fix remote event flow | High | Small | Steps 1–3 |
| 6 | #5 Remove `for_everyone` (pending confirmation) | Low | Small | — |
| 7 | #7 Update CLAUDE.md | Low | Trivial | — |

Issues #4 and #6 require no code changes.

---

## Files that will change

| File | Changes |
|------|---------|
| `event_pipeline.py` | Add `create_place()`, fix `build_payload()`, remove `for_everyone` from `EventData` (if #5 confirmed) |
| `discord_flow.py` | Fix remote flow, fix `_ask_place_manual()`, update `_submit()`, remove `for_everyone` question (if #5 confirmed) |
| `CLAUDE.md` | Update taxonomy section |

`db.py`, `bot.py`, `email_channel.py`, `config.py` — no changes needed.
