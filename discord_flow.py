"""
Discord submission flow: watches a configured channel, opens a private thread,
runs the interactive Q&A + confirmation loop, and submits to the Event API.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

import discord

import db
from event_pipeline import EventData, FIELD_LABELS, REQUIRED_FIELDS, submit_event

QUESTION_TIMEOUT = 60   # seconds — user must type an answer
BUTTON_TIMEOUT   = 120  # seconds — user must click a button / select

TIMEOUT_MSG = "⏰ Vastasit liian hitaasti. Tapahtuman luominen peruutettu."
CANCELLED_MSG = "❌ Tapahtuman luominen peruutettu."


# ---------------------------------------------------------------------------
# Reusable UI Views
# ---------------------------------------------------------------------------

class AuthorCheck:
    """Mixin: only the original author may interact."""
    author_id: int

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Vain tapahtuman lähettäjä voi käyttää tätä painiketta.", ephemeral=True
            )
            return False
        return True


class YesNoView(AuthorCheck, discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=BUTTON_TIMEOUT)
        self.author_id = author_id
        self.value: Optional[bool] = None

    @discord.ui.button(label="Kyllä ✅", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Ei ❌", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class TaxonomySelectView(AuthorCheck, discord.ui.View):
    """Select menu for lists of up to 25 items (municipality, event_type)."""

    def __init__(self, author_id: int, options: list[str], placeholder: str, required: bool = True):
        super().__init__(timeout=BUTTON_TIMEOUT)
        self.author_id = author_id
        self.selected: Optional[str] = None

        select_options = [discord.SelectOption(label=o, value=o) for o in options[:25]]
        if not required:
            select_options.insert(0, discord.SelectOption(label="— Ei valintaa —", value="_none_"))

        select = discord.ui.Select(
            placeholder=placeholder,
            options=select_options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        self.selected = "" if val == "_none_" else val
        await interaction.response.defer()
        self.stop()


class SearchModal(discord.ui.Modal):
    """Single text-input modal for search queries."""

    search_input = discord.ui.TextInput(
        label="Hakusana",
        placeholder="esim. Helsinki",
        min_length=1,
        max_length=50,
        required=True,
    )

    def __init__(self, title: str):
        super().__init__(title=title[:45])
        self.query: Optional[str] = None
        self._done: asyncio.Event = asyncio.Event()

    async def on_submit(self, interaction: discord.Interaction):
        self.query = self.search_input.value.strip()
        await interaction.response.defer()
        self._done.set()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        self._done.set()


class SearchTriggerView(AuthorCheck, discord.ui.View):
    """Single button that opens a SearchModal."""

    def __init__(self, author_id: int, modal: SearchModal):
        super().__init__(timeout=BUTTON_TIMEOUT)
        self.author_id = author_id
        self._modal = modal
        self.clicked: bool = False

    @discord.ui.button(label="🔍 Hae", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.clicked = True
        await interaction.response.send_modal(self._modal)
        self.stop()


class SearchResultView(AuthorCheck, discord.ui.View):
    """Shows up to 5 search results as buttons. Used for large lists like organisers."""

    def __init__(self, author_id: int, matches: list[str], modal_title: str):
        super().__init__(timeout=BUTTON_TIMEOUT)
        self.author_id = author_id
        self.selected: Optional[str] = None
        self.retry_modal: Optional[SearchModal] = None
        self._modal_title = modal_title

        for match in matches[:5]:
            btn = discord.ui.Button(label=match[:80], style=discord.ButtonStyle.primary)
            btn.callback = self._make_callback(match)
            self.add_item(btn)

        retry_btn = discord.ui.Button(label="Hae uudelleen 🔍", style=discord.ButtonStyle.secondary, row=1)
        retry_btn.callback = self._retry
        self.add_item(retry_btn)

    def _make_callback(self, value: str):
        async def callback(interaction: discord.Interaction):
            if not await self.interaction_check(interaction):
                return
            self.selected = value
            await interaction.response.defer()
            self.stop()
        return callback

    async def _retry(self, interaction: discord.Interaction):
        if not await self.interaction_check(interaction):
            return
        modal = SearchModal(title=self._modal_title)
        self.retry_modal = modal
        await interaction.response.send_modal(modal)
        self.stop()


class CorrectionView(AuthorCheck, discord.ui.View):
    """One button per editable field + a Cancel button."""

    FIELDS = list(FIELD_LABELS.keys())

    def __init__(self, author_id: int):
        super().__init__(timeout=BUTTON_TIMEOUT)
        self.author_id = author_id
        self.chosen_field: Optional[str] = None

        for i, key in enumerate(self.FIELDS):
            label = FIELD_LABELS[key]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                row=i // 4,
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

        cancel_btn = discord.ui.Button(
            label="Peruuta ❌",
            style=discord.ButtonStyle.danger,
            row=3,
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    def _make_callback(self, field_key: str):
        async def callback(interaction: discord.Interaction):
            if not await self.interaction_check(interaction):
                return
            self.chosen_field = field_key
            await interaction.response.defer()
            self.stop()
        return callback

    async def _cancel(self, interaction: discord.Interaction):
        if not await self.interaction_check(interaction):
            return
        self.chosen_field = None
        await interaction.response.defer()
        self.stop()


# ---------------------------------------------------------------------------
# Main submission flow
# ---------------------------------------------------------------------------

class DiscordSubmissionFlow:
    """
    Manages one submission session inside a private Discord thread.
    Call .run() to start the interactive flow.
    """

    def __init__(
        self,
        thread: discord.Thread,
        author: discord.Member,
        guild_id: int,
        api_base_url: str,
        rate_limit_max: int,
        rate_limit_window: int,
        bot: discord.Client,
    ):
        self.thread = thread
        self.author = author
        self.bot = bot
        self.guild_id = guild_id
        self.api_base_url = api_base_url
        self.rate_limit_max = rate_limit_max
        self.rate_limit_window = rate_limit_window
        self.sub_id = str(uuid.uuid4())
        self.data = EventData()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self):
        db.audit("discord", "received", self.guild_id, str(self.author.id),
                 submission_id=self.sub_id)

        # Rate limit check
        if not db.check_rate_limit(str(self.author.id), self.guild_id,
                                   self.rate_limit_max, self.rate_limit_window):
            await self.thread.send(
                f"⚠️ Olet lähettänyt liian monta tapahtumailmoitusta lyhyessä ajassa. "
                f"Odota hetki ennen uutta ilmoitusta."
            )
            db.audit("discord", "rate_limited", self.guild_id, str(self.author.id),
                     submission_id=self.sub_id)
            return

        # Pre-fill organiser from server default
        cfg = db.get_guild_config(self.guild_id)
        if cfg and cfg.get("default_organiser"):
            self.data.organiser = cfg["default_organiser"]

        await self.thread.send(
            f"Hei {self.author.mention}! 👋 Täytetään tapahtuman tiedot yhdessä.\n"
            f"Vastaa jokaiseen kysymykseen {QUESTION_TIMEOUT} sekunnin sisällä."
        )

        # Collect all fields
        try:
            await self._collect_fields()
        except asyncio.TimeoutError:
            await self.thread.send(TIMEOUT_MSG)
            db.audit("discord", "timeout", self.guild_id, str(self.author.id),
                     submission_id=self.sub_id)
            return

        # Confirmation + correction loop
        try:
            confirmed = await self._confirmation_loop()
        except asyncio.TimeoutError:
            await self.thread.send(TIMEOUT_MSG)
            db.audit("discord", "timeout", self.guild_id, str(self.author.id),
                     submission_id=self.sub_id)
            return
        if not confirmed:
            await self.thread.send(CANCELLED_MSG)
            db.audit("discord", "rejected", self.guild_id, str(self.author.id),
                     submission_id=self.sub_id)
            return

        # Submit to API
        await self._submit()

    # ------------------------------------------------------------------
    # Field collection
    # ------------------------------------------------------------------

    async def _collect_fields(self):
        municipalities = db.get_taxonomy("municipality")
        event_types    = db.get_taxonomy("event_type")
        organisers     = db.get_taxonomy("organiser")

        self.data.title = await self._ask_text("📋 **Mikä on tapahtuman nimi?**")

        self.data.start_date = await self._ask_text(
            "📅 **Mikä on tapahtuman päivämäärä?** (muodossa YYYY-MM-DD)",
            validator=_validate_date,
            error_hint="Käytä muotoa YYYY-MM-DD, esim. 2024-06-15",
        )
        self.data.start_time = await self._ask_text(
            "🕐 **Mikä on aloitusaika?** (muodossa HH:MM)",
            validator=_validate_time,
            error_hint="Käytä muotoa HH:MM, esim. 14:00",
        )
        self.data.end_time = await self._ask_text(
            "🕑 **Mikä on lopetusaika?** (muodossa HH:MM) — tai kirjoita `-` jos ei ole",
            validator=_validate_time_or_empty,
            error_hint="Käytä muotoa HH:MM tai kirjoita -",
            transform=lambda v: "" if v.strip() == "-" else v.strip(),
        )

        # Ask remote first — affects how we ask about location
        self.data.remote = await self._ask_yesno("💻 **Onko tapahtuma etätapahtuma?**")
        if self.data.remote:
            self.data.invite_link = await self._ask_text(
                "🔗 **Tapahtuman linkki (Zoom, Meet tms.)?** — tai kirjoita `-` jos ei ole",
                transform=lambda v: "" if v.strip() == "-" else v.strip(),
            )
            self.data.place_name = await self._ask_text(
                "📍 **Tapahtumapaikan nimi?** — esim. `Etätapahtuma` tai `Zoom`"
            )
            # No street address for remote events
        else:
            self.data.place_name = await self._ask_text("📍 **Mikä on tapahtumapaikka?**")
            self.data.street_address = await self._ask_text(
                "🏠 **Mikä on katuosoite?** — tai kirjoita `-` jos ei ole",
                transform=lambda v: "" if v.strip() == "-" else v.strip(),
            )

        self.data.municipality = await self._ask_search(
            "🗺️ **Mikä kunta?**", municipalities, "Hae kunta"
        )
        # Organiser — search-based for large lists, free text if no taxonomy loaded
        if organisers:
            self.data.organiser = await self._ask_search(
                "🏛️ **Järjestäjä?**", organisers, "Hae järjestäjä"
            )
        else:
            self.data.organiser = await self._ask_text("🏛️ **Järjestäjä?**")

        self.data.event_type = await self._ask_select(
            "🏷️ **Tapahtuman tyyppi?**", event_types, "Valitse tyyppi", required=False
        )
        self.data.description = await self._ask_text(
            "📝 **Lyhyt kuvaus tapahtumasta?** — tai kirjoita `-` jos ei ole",
            transform=lambda v: "" if v.strip() == "-" else v.strip(),
        )
        self.data.for_everyone = await self._ask_yesno("🌍 **Onko tapahtuma kaikille avoin?**")

    # ------------------------------------------------------------------
    # Confirmation + correction loop
    # ------------------------------------------------------------------

    async def _confirmation_loop(self) -> bool:
        while True:
            await self._show_summary()
            view = YesNoView(self.author.id)
            await self.thread.send("Ovatko tapahtuman tiedot oikein?", view=view)
            await view.wait()

            if view.value is None:
                raise asyncio.TimeoutError()
            if view.value:
                db.audit("discord", "confirmed", self.guild_id, str(self.author.id),
                         details={"event_data": self.data.__dict__}, submission_id=self.sub_id)
                return True

            # Correction loop
            db.audit("discord", "correction_started", self.guild_id, str(self.author.id),
                     submission_id=self.sub_id)
            cancelled = await self._run_correction()
            if cancelled:
                return False

    async def _run_correction(self) -> bool:
        """Show correction buttons. Returns True if user cancelled entirely."""
        while True:
            corr_view = CorrectionView(self.author.id)
            await self.thread.send(
                "✏️ Valitse korjattava kenttä:", view=corr_view
            )
            await corr_view.wait()

            if corr_view.chosen_field is None:
                return True  # user hit Cancel

            field = corr_view.chosen_field
            label = FIELD_LABELS[field]

            try:
                new_value = await self._ask_field(field, label)
            except asyncio.TimeoutError:
                raise

            old_value = getattr(self.data, field)
            setattr(self.data, field, new_value)
            db.audit("discord", "correction", self.guild_id, str(self.author.id),
                     details={"field": field, "old": str(old_value), "new": str(new_value)},
                     submission_id=self.sub_id)

            # Ask if they want to correct another field before re-confirming
            another_view = YesNoView(self.author.id)
            await self.thread.send("Haluatko korjata vielä jotain muuta?", view=another_view)
            await another_view.wait()
            if not another_view.value:
                return False  # go back to confirmation loop

    async def _ask_field(self, field: str, label: str):
        """Ask for a single field value appropriate to its type."""
        municipalities = db.get_taxonomy("municipality")
        event_types    = db.get_taxonomy("event_type")
        organisers     = db.get_taxonomy("organiser")

        if field == "municipality":
            return await self._ask_search(f"🗺️ **{label}**", municipalities, "Hae kunta")
        if field == "event_type":
            return await self._ask_select(f"Uusi arvo — **{label}**:", event_types, "Valitse tyyppi", required=False)
        if field == "organiser" and organisers:
            return await self._ask_search(f"🏛️ **{label}**", organisers, "Hae järjestäjä")
        if field == "remote" or field == "for_everyone":
            return await self._ask_yesno(f"Uusi arvo — **{label}**:")
        if field == "invite_link":
            return await self._ask_text(
                f"Uusi arvo — **{label}** — tai kirjoita `-` jos ei ole:",
                transform=lambda v: "" if v.strip() == "-" else v.strip(),
            )
        if field == "start_date":
            return await self._ask_text(f"Uusi arvo — **{label}** (YYYY-MM-DD):",
                                        validator=_validate_date, error_hint="Muoto: YYYY-MM-DD")
        if field in ("start_time", "end_time"):
            return await self._ask_text(f"Uusi arvo — **{label}** (HH:MM tai -):",
                                        validator=_validate_time_or_empty,
                                        transform=lambda v: "" if v.strip() == "-" else v.strip())
        return await self._ask_text(f"Uusi arvo — **{label}**:")

    # ------------------------------------------------------------------
    # API submission
    # ------------------------------------------------------------------

    async def _submit(self):
        api_key = db.get_api_key(self.guild_id)
        if not api_key:
            await self.thread.send(
                "❌ Tällä palvelimella ei ole määritetty API-avainta. "
                "Pyydä ylläpitäjää asettamaan avain komennolla `/setapikey`."
            )
            return

        await self.thread.send("⏳ Lähetetään tapahtumaa kalenteriin…")
        db.audit("discord", "api_submitted", self.guild_id, str(self.author.id),
                 submission_id=self.sub_id)

        result = await submit_event(self.api_base_url, api_key, self.data)
        db.record_submission(str(self.author.id), self.guild_id)

        if result.success:
            urls = "\n".join(result.event_urls)
            await self.thread.send(
                f"✅ Tapahtuma lisätty onnistuneesti!\n{urls}"
            )
            db.audit("discord", "api_success", self.guild_id, str(self.author.id),
                     details={"urls": result.event_urls}, submission_id=self.sub_id)
        else:
            await self.thread.send(
                f"❌ API palautti virheen:\n```{result.error_message}```\n"
                f"Tarkista tiedot ja yritä uudelleen tai ota yhteyttä ylläpitäjään."
            )
            db.audit("discord", "api_error", self.guild_id, str(self.author.id),
                     details={"error": result.error_message}, submission_id=self.sub_id)

    # ------------------------------------------------------------------
    # Low-level input helpers
    # ------------------------------------------------------------------

    async def _show_summary(self):
        embed = discord.Embed(
            title="📋 Tapahtuman tiedot",
            color=discord.Color.green(),
        )
        for line in self.data.to_summary_lines():
            # split "**Label:** Value" into field/value
            parts = line.replace("**", "").split(": ", 1)
            name  = parts[0]
            value = parts[1] if len(parts) > 1 else "—"
            embed.add_field(name=name, value=value or "—", inline=True)
        await self.thread.send(embed=embed)

    async def _ask_text(
        self,
        prompt: str,
        *,
        validator=None,
        error_hint: str = "",
        transform=None,
    ) -> str:
        await self.thread.send(prompt)
        while True:
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author.id == self.author.id and m.channel.id == self.thread.id,
                    timeout=QUESTION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise

            value = msg.content.strip()
            if validator and not validator(value):
                await self.thread.send(f"⚠️ Virheellinen muoto. {error_hint}")
                continue
            return transform(value) if transform else value

    async def _ask_search(self, prompt: str, all_options: list[str], modal_title: str = "Hae") -> str:
        """
        Modal-based search picker for large lists.
        User clicks "Hae" → modal opens → types query → results shown as buttons.
        """
        next_query: Optional[str] = None
        first_attempt = True

        while True:
            # --- Get a query (from modal or carried over from retry_modal) ---
            if next_query is not None:
                query = next_query.lower()
                next_query = None
            else:
                modal = SearchModal(title=modal_title[:45])
                trigger = SearchTriggerView(self.author.id, modal)
                msg_text = prompt if first_attempt else "🔍 Yritä uudelleen:"
                first_attempt = False
                await self.thread.send(msg_text, view=trigger)

                timed_out = await trigger.wait()
                if timed_out or not trigger.clicked:
                    raise asyncio.TimeoutError()

                try:
                    await asyncio.wait_for(modal._done.wait(), timeout=QUESTION_TIMEOUT)
                except asyncio.TimeoutError:
                    raise

                if not modal.query:
                    raise asyncio.TimeoutError()
                query = modal.query.lower()

            # --- Search ---
            matches = [o for o in all_options if query in o.lower()]

            if not matches:
                await self.thread.send(f"⚠️ Ei tuloksia hakusanalle **{query}**.")
                continue  # next iteration shows retry button

            if len(matches) == 1:
                await self.thread.send(f"✅ Valittu: **{matches[0]}**")
                return matches[0]

            # --- Show result buttons ---
            n_more = max(0, len(matches) - 5)
            extra = f" (+{n_more} muuta — tarkenna hakua)" if n_more else ""
            view = SearchResultView(self.author.id, matches[:5], modal_title)
            await self.thread.send(f"Löytyi **{len(matches)}** tulosta{extra}. Valitse:", view=view)
            await view.wait()

            if view.selected is not None:
                return view.selected

            if view.retry_modal is not None:
                # "Hae uudelleen" opened a modal — wait for submission
                try:
                    await asyncio.wait_for(view.retry_modal._done.wait(), timeout=QUESTION_TIMEOUT)
                except asyncio.TimeoutError:
                    raise
                if view.retry_modal.query:
                    next_query = view.retry_modal.query
                continue  # process next_query at top of loop

            # view timed out with no interaction
            raise asyncio.TimeoutError()

    async def _ask_select(
        self,
        prompt: str,
        options: list[str],
        placeholder: str,
        required: bool = True,
    ) -> str:
        view = TaxonomySelectView(self.author.id, options, placeholder, required)
        await self.thread.send(prompt, view=view)
        await view.wait()
        if view.selected is None:
            raise asyncio.TimeoutError()
        return view.selected

    async def _ask_yesno(self, prompt: str) -> bool:
        view = YesNoView(self.author.id)
        await self.thread.send(prompt, view=view)
        await view.wait()
        if view.value is None:
            raise asyncio.TimeoutError()
        return view.value


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_date(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()))


def _validate_time(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", value.strip()))


def _validate_time_or_empty(value: str) -> bool:
    v = value.strip()
    if v == "-":
        return True
    return _validate_time(v)


# ---------------------------------------------------------------------------
# Bot-level helper: called from bot.py on_message
# ---------------------------------------------------------------------------

async def handle_submission_message(
    message: discord.Message,
    api_base_url: str,
    rate_limit_max: int,
    rate_limit_window: int,
    bot: discord.Client,
):
    """
    Called when a non-command message appears in the configured submission channel.
    Creates a private thread and runs the full Q&A flow.
    """
    guild = message.guild
    thread = await message.create_thread(
        name=f"Tapahtuma – {message.author.display_name}",
        auto_archive_duration=1440,
    )
    flow = DiscordSubmissionFlow(
        thread=thread,
        author=message.author,
        guild_id=guild.id,
        api_base_url=api_base_url,
        rate_limit_max=rate_limit_max,
        rate_limit_window=rate_limit_window,
        bot=bot,
    )
    await flow.run()
