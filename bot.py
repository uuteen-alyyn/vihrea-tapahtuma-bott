"""
Entry point.
Starts the Discord bot, registers slash commands, and launches background tasks.
"""
from __future__ import annotations

import asyncio
import discord
from discord import app_commands
from discord.ext import commands

import db
from config import load_config
from discord_flow import handle_submission_message
from email_channel import email_poll_loop

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True   # privileged — enable in Discord Developer Portal
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
cfg = load_config()


# ---------------------------------------------------------------------------
# Ready
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    db.init_db(cfg.database_path, cfg.encryption_key)
    print(f"[bot] Logged in as {bot.user} (id={bot.user.id})")

    try:
        synced = await bot.tree.sync()
        print(f"[bot] Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"[bot] Failed to sync commands: {e}")

    if cfg.email_enabled:
        bot.loop.create_task(email_poll_loop(cfg))
        print("[bot] Email polling started")
    else:
        print("[bot] Email channel disabled (IMAP/SMTP not configured)")


# ---------------------------------------------------------------------------
# Submission channel listener
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    # Ignore DMs, own messages, and commands
    if message.guild is None or message.author.bot:
        return
    await bot.process_commands(message)
    if message.content.startswith(bot.command_prefix):
        return

    cfg_data = db.get_guild_config(message.guild.id)
    if not cfg_data:
        return

    channel_id = cfg_data.get("submission_channel_id")
    if not channel_id or message.channel.id != channel_id:
        return

    # Don't respond inside threads (those are our own Q&A threads)
    if isinstance(message.channel, discord.Thread):
        return

    await handle_submission_message(
        message=message,
        api_base_url=cfg.api_base_url,
        rate_limit_max=cfg.rate_limit_max,
        rate_limit_window=cfg.rate_limit_window,
        bot=bot,
    )


# ---------------------------------------------------------------------------
# Admin slash commands
# ---------------------------------------------------------------------------

def _is_admin(interaction: discord.Interaction) -> bool:
    """Returns True if the user has the configured admin role (or is guild owner)."""
    if interaction.user.id == interaction.guild.owner_id:
        return True
    cfg_data = db.get_guild_config(interaction.guild_id)
    if not cfg_data or not cfg_data.get("admin_role_id"):
        # No role configured — only owner can run admin commands
        return False
    role_id = cfg_data["admin_role_id"]
    return any(r.id == role_id for r in interaction.user.roles)


@bot.tree.command(name="setup", description="Konfiguroi botti tälle palvelimelle")
@app_commands.describe(
    channel="Kanava, jossa tapahtumailmoitukset tehdään",
    admin_role="Rooli, jolla on ylläpito-oikeudet",
)
async def cmd_setup(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    admin_role: discord.Role,
):
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Ei oikeuksia.", ephemeral=True)
        return

    db.upsert_guild_config(
        guild_id=interaction.guild_id,
        submission_channel_id=channel.id,
        admin_role_id=admin_role.id,
    )
    await interaction.response.send_message(
        f"✅ Botti konfiguoitu!\n"
        f"• Kanava: {channel.mention}\n"
        f"• Ylläpitorooli: {admin_role.mention}\n"
        f"Lisää järjestäjät komennolla `/taxonomy add organiser \"Järjestäjän nimi\"`",
        ephemeral=True,
    )


class ApiKeyModal(discord.ui.Modal, title="Aseta API-avain"):
    key = discord.ui.TextInput(
        label="Tapahtumat API-avain",
        placeholder="Liitä avain tähän…",
        min_length=8,
        max_length=256,
    )

    async def on_submit(self, interaction: discord.Interaction):
        db.set_api_key(interaction.guild_id, self.key.value)
        await interaction.response.send_message("✅ API-avain tallennettu.", ephemeral=True)


@bot.tree.command(name="setapikey", description="Aseta Tapahtumat API-avain tälle palvelimelle")
async def cmd_setapikey(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Ei oikeuksia.", ephemeral=True)
        return
    await interaction.response.send_modal(ApiKeyModal())


@bot.tree.command(name="status", description="Näytä botin nykyinen konfiguraatio")
async def cmd_status(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Ei oikeuksia.", ephemeral=True)
        return

    cfg_data = db.get_guild_config(interaction.guild_id)
    if not cfg_data:
        await interaction.response.send_message(
            "⚠️ Bottia ei ole vielä konfiguoitu tällä palvelimella. Käytä `/setup`.",
            ephemeral=True,
        )
        return

    channel = interaction.guild.get_channel(cfg_data["submission_channel_id"] or 0)
    role    = interaction.guild.get_role(cfg_data["admin_role_id"] or 0)
    has_key = bool(cfg_data.get("api_key_encrypted"))

    await interaction.response.send_message(
        f"**Botin konfiguraatio:**\n"
        f"• Kanava: {channel.mention if channel else '(ei asetettu)'}\n"
        f"• Ylläpitorooli: {role.mention if role else '(ei asetettu)'}\n"
        f"• Oletusjärjestäjä: {cfg_data.get('default_organiser') or '(ei asetettu)'}\n"
        f"• API-avain: {'✅ asetettu' if has_key else '❌ puuttuu'}",
        ephemeral=True,
    )


@bot.tree.command(name="taxonomy", description="Hallitse taksonomiarvoja (kunnat, tyypit)")
@app_commands.describe(
    action="add tai remove",
    term_type="municipality tai event_type",
    value="Lisättävä tai poistettava arvo",
)
async def cmd_taxonomy(
    interaction: discord.Interaction,
    action: str,
    term_type: str,
    value: str,
):
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Ei oikeuksia.", ephemeral=True)
        return
    if action not in ("add", "remove"):
        await interaction.response.send_message("❌ Käytä `add` tai `remove`.", ephemeral=True)
        return
    if term_type not in ("municipality", "event_type", "organiser"):
        await interaction.response.send_message(
            "❌ Tyyppi pitää olla `municipality`, `event_type` tai `organiser`.", ephemeral=True
        )
        return

    if action == "add":
        db.add_taxonomy_term(term_type, value)
        await interaction.response.send_message(f"✅ Lisätty: {value}", ephemeral=True)
    else:
        db.remove_taxonomy_term(term_type, value)
        await interaction.response.send_message(f"✅ Poistettu: {value}", ephemeral=True)


@bot.tree.command(name="listtaxonomy", description="Listaa tallennetut taksonomiarvot")
@app_commands.describe(term_type="municipality tai event_type")
async def cmd_listtaxonomy(interaction: discord.Interaction, term_type: str):
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Ei oikeuksia.", ephemeral=True)
        return
    if term_type not in ("municipality", "event_type", "organiser"):
        await interaction.response.send_message(
            "❌ Tyyppi pitää olla `municipality`, `event_type` tai `organiser`.", ephemeral=True
        )
        return
    terms = db.get_taxonomy(term_type)
    if not terms:
        await interaction.response.send_message("(tyhjä lista)", ephemeral=True)
        return
    await interaction.response.send_message(
        f"**{term_type}** ({len(terms)} kpl):\n" + "\n".join(f"• {t}" for t in terms),
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

@bot.tree.command(name="help", description="Näytä kaikki botin komennot")
async def cmd_help(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**Tapahtumabot — komennot**\n\n"
        "**Ylläpito:**\n"
        "• `/setup channel:#kanava role:@rooli` — Aseta ilmoituskanava ja ylläpitorooli\n"
        "• `/setapikey` — Aseta Tapahtumat API-avain (yksityinen lomake)\n"
        "• `/status` — Näytä botin nykyinen konfiguraatio\n\n"
        "**Taksonomienhallinta:**\n"
        "• `/taxonomy add organiser \"Nimi\"` — Lisää järjestäjä listaan\n"
        "• `/taxonomy remove organiser \"Nimi\"` — Poista järjestäjä listasta\n"
        "• `/taxonomy add municipality \"Kunta\"` — Lisää kunta listaan\n"
        "• `/taxonomy remove municipality \"Kunta\"` — Poista kunta listasta\n"
        "• `/taxonomy add event_type \"Tyyppi\"` — Lisää tapahtumatyyppi\n"
        "• `/taxonomy remove event_type \"Tyyppi\"` — Poista tapahtumatyyppi\n"
        "• `/listtaxonomy organiser` — Listaa kaikki järjestäjät\n"
        "• `/listtaxonomy municipality` — Listaa kaikki kunnat\n"
        "• `/listtaxonomy event_type` — Listaa kaikki tapahtumatyypit\n\n"
        "**Tapahtumailmoitus:**\n"
        "Kirjoita mikä tahansa viesti konfiguroidulle kanavalle, niin botti avaa yksityisen "
        "ketjun ja ohjaa sinut läpi tapahtuman tietojen täyttämisen.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bot.run(cfg.discord_token)
