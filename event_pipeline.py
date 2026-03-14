"""
Shared event processing: build the API payload, validate fields, submit to API.
"""
from __future__ import annotations

import aiohttp
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo

import logging
log = logging.getLogger(__name__)

FINLAND_TZ = ZoneInfo("Europe/Helsinki")
# The API strips timezone offsets and treats times as UTC, then the website
# always displays in UTC+3 (EEST). To get the correct display, we convert
# user input from EEST (UTC+3) to UTC, effectively using summer time year-round.
from datetime import timezone as _timezone, timedelta as _timedelta
_EEST = _timezone(_timedelta(hours=3))

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
    organiser: str = ""        # display name (set alongside organiser_id)
    organiser_id: Optional[int] = None  # API node ID — used in the payload
    place_id: Optional[int] = None      # API node ID — set when place selected from search
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
    """
    Combine YYYY-MM-DD + HH:MM into an ISO 8601 UTC string.

    The API ignores the timezone offset and treats the raw time as UTC, then the
    website always displays in UTC+3 (EEST). We therefore treat user input as EEST
    (UTC+3) year-round and convert to UTC before sending, so that the displayed
    time matches what the user entered.
    """
    from datetime import datetime
    dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    dt_eest = dt.replace(tzinfo=_EEST)
    dt_utc = dt_eest.astimezone(_timezone(_timedelta(hours=0)))
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payload(data: EventData) -> dict:
    payload: dict = {
        "title":        data.title,
        "start":        _to_iso8601(data.start_date, data.start_time),
        "organiser_id": data.organiser_id,
        "place_name":   data.place_name,
        "municipality":  data.municipality,
    }
    if data.place_id is not None:
        payload["place_id"] = data.place_id
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


class OrganiserSearchError(Exception):
    """Raised when the organiser search API call fails (network error or non-200 response)."""


async def search_organisers(api_base_url: str, api_key: str, query: str) -> list[dict]:
    """
    Search for organisers by name via the API.
    Returns a list of {"id": int, "name": str} dicts, empty list when the API
    returned 200 but found nothing.
    Raises OrganiserSearchError on network errors or non-200 HTTP responses.
    """
    url = f"{api_base_url.rstrip('/')}/api/v1/organisers/search"
    headers = {"api-key": api_key}
    params = {"name": query}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("organiser search returned HTTP %s: %s", resp.status, body[:200])
                    raise OrganiserSearchError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
                # Normalise: the API may return a plain list or a wrapped object
                items = data if isinstance(data, list) else data.get("results", data.get("organisers", data.get("data", [])))
                results = []
                for item in items:
                    org_id   = item.get("id") or item.get("nid")
                    org_name = item.get("name") or item.get("title") or item.get("label", "")
                    if org_id and org_name:
                        results.append({"id": int(org_id), "name": str(org_name)})
                return results
    except OrganiserSearchError:
        raise
    except Exception as exc:
        log.warning("organiser search failed: %s", exc)
        raise OrganiserSearchError(str(exc)) from exc


class PlaceSearchError(Exception):
    """Raised when the place search API call fails (network error or non-200 response)."""


async def search_places(api_base_url: str, api_key: str, query: str) -> list[dict]:
    """
    Search for places by name via the API.
    Returns a list of {"id", "name", "municipality", "street_address"} dicts, empty list when
    the API returned 200 but found nothing.
    Raises PlaceSearchError on network errors or non-200 HTTP responses.
    """
    url = f"{api_base_url.rstrip('/')}/api/v1/places/search"
    headers = {"api-key": api_key}
    params = {"q": query}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("place search returned HTTP %s: %s", resp.status, body[:200])
                    raise PlaceSearchError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
                items = data if isinstance(data, list) else data.get("results", data.get("data", []))
                return [
                    {
                        "id": item.get("id"),
                        "name": item.get("name", ""),
                        "municipality": item.get("municipality", ""),
                        "street_address": item.get("street_address", ""),
                    }
                    for item in items
                    if item.get("name")
                ]
    except PlaceSearchError:
        raise
    except Exception as exc:
        log.warning("place search failed: %s", exc)
        raise PlaceSearchError(str(exc)) from exc


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
