"""
Shared event processing: build the API payload, validate fields, submit to API.
"""
from __future__ import annotations

import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo

FINLAND_TZ = ZoneInfo("Europe/Helsinki")

# Fields shown to users in Finnish
FIELD_LABELS: dict[str, str] = {
    "title":          "Otsikko",
    "start_date":     "Päivämäärä",
    "start_time":     "Aloitusaika",
    "end_time":       "Lopetusaika",
    "description":    "Kuvaus",
    "place_name":     "Paikka",
    "street_address": "Osoite",
    "municipality":   "Kunta",
    "organiser":      "Järjestäjä",
    "event_type":     "Tyyppi",
    "remote":         "Etätapahtuma",
    "invite_link":    "Tapahtumalinkki",
    "for_everyone":   "Kaikille avoin",
}

# Which fields are required for the API
REQUIRED_FIELDS = {"title", "start_date", "start_time", "place_name", "municipality", "organiser"}


@dataclass
class EventData:
    title: str = ""
    start_date: str = ""       # YYYY-MM-DD
    start_time: str = ""       # HH:MM
    end_time: str = ""         # HH:MM  (optional)
    description: str = ""
    place_name: str = ""
    street_address: str = ""
    municipality: str = ""
    organiser: str = ""
    event_type: str = ""
    remote: bool = False
    invite_link: str = ""      # optional link for remote events
    for_everyone: bool = False

    def missing_required(self) -> list[str]:
        """Return list of field names that are required but empty."""
        missing = []
        for f in REQUIRED_FIELDS:
            val = getattr(self, f)
            if not val:
                missing.append(f)
        return missing

    def to_summary_lines(self) -> list[str]:
        """Return human-readable field: value lines."""
        lines = []
        for key, label in FIELD_LABELS.items():
            val = getattr(self, key)
            if isinstance(val, bool):
                val = "Kyllä" if val else "Ei"
            lines.append(f"**{label}:** {val or '—'}")
        return lines


def _to_iso8601(date: str, time: str) -> str:
    """Combine YYYY-MM-DD + HH:MM into ISO 8601 with Finnish timezone offset."""
    from datetime import datetime
    dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    dt_aware = dt.replace(tzinfo=FINLAND_TZ)
    return dt_aware.isoformat(timespec="seconds")  # e.g. 2024-06-15T14:00:00+03:00


def build_payload(data: EventData) -> dict:
    payload: dict = {
        "title":       data.title,
        "start":       _to_iso8601(data.start_date, data.start_time),
        "organiser":   data.organiser,
        "place_name":  data.place_name,
        "municipality": data.municipality,
    }
    if data.end_time:
        payload["end"] = _to_iso8601(data.start_date, data.end_time)
    if data.description:
        payload["description"] = data.description
    if data.event_type:
        payload["event_type"] = data.event_type
    if data.street_address:
        payload["street_address"] = data.street_address
    if data.remote:
        payload["remote"] = True
    # Always send for_everyone — API defaults to true, so we must explicitly send false
    payload["for_everyone"] = data.for_everyone
    if data.invite_link:
        desc = payload.get("description", "")
        link_line = f"Tapahtumalinkki: {data.invite_link}"
        payload["description"] = f"{desc}\n\n{link_line}".strip() if desc else link_line
    return payload


@dataclass
class SubmitResult:
    success: bool
    event_urls: list[str] = field(default_factory=list)
    error_message: str = ""


async def submit_event(api_base_url: str, api_key: str, data: EventData) -> SubmitResult:
    payload = build_payload(data)
    url = f"{api_base_url.rstrip('/')}/api/v1/events"
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {}
                if resp.status == 200 and body.get("status") == "ok":
                    return SubmitResult(
                        success=True,
                        event_urls=body.get("event_urls", []),
                    )
                else:
                    # Extract the most useful error message available
                    api_msg = body.get("message") or body.get("error")
                    if api_msg:
                        msg = f"HTTP {resp.status}: {api_msg}"
                    else:
                        # Fall back to full body so nothing is hidden
                        import json as _json
                        msg = f"HTTP {resp.status} — {_json.dumps(body, ensure_ascii=False)}" if body else f"HTTP {resp.status}"
                    return SubmitResult(success=False, error_message=msg)
    except Exception as e:
        return SubmitResult(success=False, error_message=str(e))
