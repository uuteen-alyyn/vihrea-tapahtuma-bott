"""
Email ingestion channel:
  - IMAP poller: reads new emails, parses structured event data
  - Reply sender: sends confirmation email to submitter
  - Timeout checker: auto-confirms submissions after 1 hour
"""
from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import re
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import aiosmtplib

import db
from event_pipeline import EventData, FIELD_LABELS, submit_event

# Map Finnish email labels → EventData field names
_LABEL_TO_FIELD = {
    "otsikko":      "title",
    "päivämäärä":   "start_date",
    "aloitusaika":  "start_time",
    "lopetusaika":  "end_time",
    "paikka":       "place_name",
    "osoite":       "street_address",
    "kunta":        "municipality",
    "järjestäjä":   "organiser",
    "tyyppi":       "event_type",
    "kuvaus":       "description",
    "etätapahtuma": "remote",
    "kaikille avoin": "for_everyone",
}


# ---------------------------------------------------------------------------
# Email template text
# ---------------------------------------------------------------------------

EMAIL_TEMPLATE = """\
Otsikko: {title}
Päivämäärä: {start_date}
Aloitusaika: {start_time}
Lopetusaika: {end_time}
Paikka: {place_name}
Osoite: {street_address}
Kunta: {municipality}
Järjestäjä: {organiser}
Tyyppi: {event_type}
Kuvaus: {description}
Etätapahtuma: {remote}
Kaikille avoin: {for_everyone}
"""


def _bool_to_fi(val: bool) -> str:
    return "Kyllä" if val else "Ei"


def _fi_to_bool(val: str) -> bool:
    return val.strip().lower() in ("kyllä", "kylla", "yes", "k", "y", "1", "true")


def _format_event_for_email(data: EventData) -> str:
    return EMAIL_TEMPLATE.format(
        title=data.title or "",
        start_date=data.start_date or "",
        start_time=data.start_time or "",
        end_time=data.end_time or "",
        place_name=data.place_name or "",
        street_address=data.street_address or "",
        municipality=data.municipality or "",
        organiser=data.organiser or "",
        event_type=data.event_type or "",
        description=data.description or "",
        remote=_bool_to_fi(data.remote),
        for_everyone=_bool_to_fi(data.for_everyone),
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_email_body(body: str) -> EventData:
    """Parse a key: value email body into EventData."""
    data = EventData()
    for line in body.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key_raw, _, val = line.partition(":")
        key = key_raw.strip().lower()
        val = val.strip()
        field = _LABEL_TO_FIELD.get(key)
        if field is None:
            continue
        if field in ("remote", "for_everyone"):
            setattr(data, field, _fi_to_bool(val))
        else:
            setattr(data, field, val)
    return data


def _get_email_body(msg) -> str:
    """Extract plain text body from an email.message.Message object."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        charset = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(charset, errors="replace")
    return ""


# ---------------------------------------------------------------------------
# SMTP reply
# ---------------------------------------------------------------------------

async def send_confirmation_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_from: str,
    to_address: str,
    data: EventData,
    sub_id: str,
):
    body_text = (
        "Hei!\n\n"
        "Olemme vastaanottaneet tapahtumailmoituksesi. "
        "Tarkista tiedot alla:\n\n"
        f"{_format_event_for_email(data)}\n"
        "Jos tiedot ovat oikein, ei tarvitse tehdä mitään. "
        "Tapahtuma julkaistaan automaattisesti 1 tunnin kuluttua.\n\n"
        "Jos haluat tehdä muutoksia, vastaa tähän viestiin korjatuilla tiedoilla "
        "samassa avain: arvo -muodossa.\n\n"
        "Jos haluat peruuttaa ilmoituksen, vastaa viestillä: PERUUTA\n\n"
        "Ystävällisin terveisin,\nTapahtumabot"
    )

    msg = MIMEMultipart()
    msg["From"]    = smtp_from
    msg["To"]      = to_address
    msg["Subject"] = f"Vahvistus: tapahtumailmoitus [{sub_id[:8]}]"
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=smtp_host,
        port=smtp_port,
        username=smtp_user,
        password=smtp_password,
        start_tls=True,
    )


async def send_result_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_from: str,
    to_address: str,
    success: bool,
    detail: str,
):
    if success:
        body_text = f"Tapahtumasi on julkaistu onnistuneesti!\n\n{detail}"
        subject = "Tapahtuma julkaistu"
    else:
        body_text = f"Tapahtuman julkaiseminen epäonnistui:\n\n{detail}\n\nOta yhteyttä ylläpitäjään."
        subject = "Tapahtuman julkaisu epäonnistui"

    msg = MIMEMultipart()
    msg["From"]    = smtp_from
    msg["To"]      = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=smtp_host,
        port=smtp_port,
        username=smtp_user,
        password=smtp_password,
        start_tls=True,
    )


# ---------------------------------------------------------------------------
# IMAP poller
# ---------------------------------------------------------------------------

def _fetch_unseen_emails(
    imap_host: str, imap_port: int, imap_user: str, imap_password: str
) -> list[tuple[str, str, str]]:
    """
    Returns list of (from_address, subject, body) for each unseen email.
    Marks fetched emails as seen.
    Runs synchronously — call via run_in_executor.
    """
    results = []
    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        conn.login(imap_user, imap_password)
        conn.select("INBOX")
        _, data = conn.search(None, "UNSEEN")
        ids = data[0].split()
        for uid in ids:
            _, msg_data = conn.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            from_addr = email_lib.utils.parseaddr(msg.get("From", ""))[1]
            subject   = msg.get("Subject", "")
            body      = _get_email_body(msg)
            results.append((from_addr, subject, body))
            conn.store(uid, "+FLAGS", "\\Seen")
        conn.logout()
    except Exception as e:
        print(f"[email] IMAP error: {e}")
    return results


async def process_incoming_email(
    from_addr: str,
    subject: str,
    body: str,
    smtp_cfg: dict,
    api_base_url: str,
    guild_id: Optional[int],
):
    """Handle one incoming email — either a new submission or a reply/correction."""
    # Check if this is a reply to a pending submission
    pending = db.get_pending_by_email(from_addr)

    if pending:
        # It's a correction reply or cancellation
        body_stripped = body.strip()
        if body_stripped.upper().startswith("PERUUTA"):
            db.close_pending_email(pending["id"], "cancelled")
            db.audit("email", "email_cancelled", guild_id, from_addr,
                     submission_id=pending["id"])
            await _smtp_send(smtp_cfg, from_addr, "Tapahtuma peruutettu",
                             "Tapahtumailmoituksesi on peruutettu.")
            return

        # Parse corrections and merge with existing data
        existing = EventData(**pending["event_data"])
        corrections = parse_email_body(body_stripped)
        # Merge: only overwrite non-empty fields from the correction
        for field in FIELD_LABELS:
            new_val = getattr(corrections, field)
            if new_val not in ("", False) or field in ("remote", "for_everyone"):
                if field not in ("remote", "for_everyone") and new_val:
                    setattr(existing, field, new_val)
                elif field in ("remote", "for_everyone"):
                    # always take the correction for boolean fields if the body had the key
                    line_has_key = any(
                        k in body_stripped.lower()
                        for k in ("etätapahtuma", "kaikille avoin")
                        if _LABEL_TO_FIELD.get(k) == field
                    )
                    if line_has_key:
                        setattr(existing, field, new_val)

        new_reply_count = pending["reply_count"] + 1
        db.update_pending_email(pending["id"], existing.__dict__, new_reply_count)
        db.audit("email", "email_correction", guild_id, from_addr,
                 submission_id=pending["id"])

        # Send updated confirmation
        try:
            await send_confirmation_email(
                **smtp_cfg, to_address=from_addr, data=existing, sub_id=pending["id"]
            )
        except Exception as e:
            print(f"[email] Failed to send correction confirmation: {e}")
        return

    # New submission
    data = parse_email_body(body)
    sub_id = db.create_pending_email(
        email_from=from_addr,
        email_subject=subject,
        event_data=data.__dict__,
        confirmation_window_seconds=3600,
        guild_id=guild_id,
    )
    db.audit("email", "email_received", guild_id, from_addr,
             details={"subject": subject}, submission_id=sub_id)

    try:
        await send_confirmation_email(
            **smtp_cfg, to_address=from_addr, data=data, sub_id=sub_id
        )
        db.audit("email", "reply_sent", guild_id, from_addr, submission_id=sub_id)
    except Exception as e:
        print(f"[email] Failed to send confirmation: {e}")
        db.audit("email", "reply_send_failed", guild_id, from_addr,
                 details={"error": str(e)}, submission_id=sub_id)


async def process_expired_submissions(smtp_cfg: dict, api_base_url: str):
    """Auto-confirm and submit all pending email submissions past their deadline."""
    expired = db.get_expired_pending_emails()
    for pending in expired:
        sub_id   = pending["id"]
        from_addr = pending["email_from"]
        guild_id  = pending.get("guild_id")

        data = EventData(**pending["event_data"])
        db.close_pending_email(sub_id, "confirmed")
        db.audit("email", "email_confirmed", guild_id, from_addr,
                 submission_id=sub_id)

        # Find API key for this guild
        api_key = db.get_api_key(guild_id) if guild_id else None
        if not api_key:
            db.audit("email", "api_error", guild_id, from_addr,
                     details={"error": "No API key configured for guild"},
                     submission_id=sub_id)
            continue

        result = await submit_event(api_base_url, api_key, data)
        if result.success:
            db.audit("email", "api_success", guild_id, from_addr,
                     details={"urls": result.event_urls}, submission_id=sub_id)
            try:
                await send_result_email(
                    **smtp_cfg,
                    to_address=from_addr,
                    success=True,
                    detail="\n".join(result.event_urls),
                )
            except Exception:
                pass
        else:
            db.audit("email", "api_error", guild_id, from_addr,
                     details={"error": result.error_message}, submission_id=sub_id)
            try:
                await send_result_email(
                    **smtp_cfg,
                    to_address=from_addr,
                    success=False,
                    detail=result.error_message,
                )
            except Exception:
                pass


async def _smtp_send(smtp_cfg: dict, to_addr: str, subject: str, body: str):
    msg = MIMEMultipart()
    msg["From"]    = smtp_cfg["smtp_from"]
    msg["To"]      = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    await aiosmtplib.send(
        msg,
        hostname=smtp_cfg["smtp_host"],
        port=smtp_cfg["smtp_port"],
        username=smtp_cfg["smtp_user"],
        password=smtp_cfg["smtp_password"],
        start_tls=True,
    )


# ---------------------------------------------------------------------------
# Background task (called from bot.py)
# ---------------------------------------------------------------------------

async def email_poll_loop(cfg, guild_id: Optional[int] = None):
    """Runs forever. Polls IMAP and processes expired submissions on each tick."""
    smtp_cfg = {
        "smtp_host":     cfg.smtp_host,
        "smtp_port":     cfg.smtp_port,
        "smtp_user":     cfg.smtp_user,
        "smtp_password": cfg.smtp_password,
        "smtp_from":     cfg.smtp_from,
    }
    loop = asyncio.get_event_loop()
    while True:
        try:
            emails = await loop.run_in_executor(
                None,
                _fetch_unseen_emails,
                cfg.imap_host,
                cfg.imap_port,
                cfg.imap_user,
                cfg.imap_password,
            )
            for from_addr, subject, body in emails:
                await process_incoming_email(
                    from_addr, subject, body, smtp_cfg, cfg.api_base_url, guild_id
                )
            await process_expired_submissions(smtp_cfg, cfg.api_base_url)
        except Exception as e:
            print(f"[email] Poll loop error: {e}")

        await asyncio.sleep(cfg.imap_poll_interval)
