"""Client and helpers for the Geschichtliches Ortsverzeichnis (GOV) API.

We consume the public REST read API at gov.genealogy.net (search + getObject)
and resolve historic names locally from the cached payload.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Optional
from urllib.parse import quote, urlencode

import requests

logger = logging.getLogger(__name__)

GOV_BASE_URL = "https://gov.genealogy.net"
GOV_ITEM_URL = "https://gov.genealogy.net/item/show/{gov_id}"
GOV_USER_AGENT = "Rootnode/1.0 (genealogy; +https://stevwyman.com)"
GOV_TIMEOUT = 20
GOV_SEARCH_LIMIT = 12

# ISO 639-2 codes used by GOV, plus common aliases.
LANGUAGE_LABELS = {
    "deu": "Deutsch",
    "de": "Deutsch",
    "ger": "Deutsch",
    "pol": "Polnisch",
    "pl": "Polnisch",
    "eng": "Englisch",
    "en": "Englisch",
    "fra": "Französisch",
    "fr": "Französisch",
    "ces": "Tschechisch",
    "cze": "Tschechisch",
    "cs": "Tschechisch",
    "slk": "Slowakisch",
    "sk": "Slowakisch",
    "hun": "Ungarisch",
    "hu": "Ungarisch",
    "rus": "Russisch",
    "ru": "Russisch",
    "lit": "Litauisch",
    "lt": "Litauisch",
    "lav": "Lettisch",
    "lv": "Lettisch",
    "est": "Estnisch",
    "et": "Estnisch",
    "nld": "Niederländisch",
    "nl": "Niederländisch",
    "dsb": "Niedersorbisch",
    "hsb": "Obersorbisch",
    "lat": "Latein",
    "la": "Latein",
}

_GOV_ID_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
_JULIAN_EPOCH_ORDINAL = 1721425  # date.toordinal() + this = Julian day number


class GovError(Exception):
    """Base error for GOV client failures."""


class GovUnavailableError(GovError):
    """The GOV server is unreachable or blocked (e.g. Anubis bot check)."""


class GovNotFoundError(GovError):
    """The requested GOV object does not exist."""


def normalize_gov_id(value: Optional[str]) -> str:
    return (value or "").strip()


def looks_like_gov_id(value: str) -> bool:
    return bool(_GOV_ID_RE.match((value or "").strip()))


def gov_item_url(gov_id: str) -> str:
    return GOV_ITEM_URL.format(gov_id=quote(gov_id, safe=""))


def date_to_julian_day(when: date) -> int:
    return when.toordinal() + _JULIAN_EPOCH_ORDINAL


def julian_day_to_date(jd: Optional[int]) -> Optional[date]:
    if jd is None:
        return None
    try:
        jd_int = int(jd)
    except (TypeError, ValueError):
        return None
    ordinal = jd_int - _JULIAN_EPOCH_ORDINAL
    if ordinal < 1:
        return None
    try:
        return date.fromordinal(ordinal)
    except (ValueError, OverflowError):
        return None


def language_label(code: Optional[str]) -> str:
    if not code:
        return ""
    return LANGUAGE_LABELS.get(code.lower(), code)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_attr(obj: Any, *names: str, default=None):
    if not isinstance(obj, dict):
        return default
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    return default


def _headers() -> dict:
    return {
        "User-Agent": GOV_USER_AGENT,
        "Accept": "application/json, application/xml, text/xml, */*",
    }


def _raise_if_challenge(response: requests.Response) -> None:
    content_type = response.headers.get("content-type", "")
    preview = (response.text or "")[:800]
    if "text/html" in content_type.lower() or preview.lstrip().startswith("<!DOCTYPE") or preview.lstrip().startswith("<html"):
        if "anubis" in preview.lower() or "not a bot" in preview.lower() or "browser wird geprüft" in preview.lower():
            raise GovUnavailableError(
                "GOV blockiert den Serverzugriff (Bot-Schutz). "
                "Bitte die GOV-Kennung von gov.genealogy.net kopieren und hier eintragen."
            )
        # HTML that is not a search page is still unusable as JSON.
        if "application/json" not in content_type.lower():
            raise GovUnavailableError(
                "GOV hat keine maschinenlesbare Antwort geliefert. "
                "Bitte die GOV-Kennung manuell eintragen."
            )


def _request_json(url: str, *, params: Optional[dict] = None) -> Any:
    try:
        response = requests.get(
            url,
            params=params,
            headers=_headers(),
            timeout=GOV_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GovUnavailableError(f"GOV ist nicht erreichbar: {exc}") from exc

    if response.status_code == 404:
        raise GovNotFoundError("GOV-Objekt nicht gefunden.")
    if response.status_code >= 400:
        raise GovUnavailableError(f"GOV antwortete mit HTTP {response.status_code}.")

    _raise_if_challenge(response)

    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


def check_object_id(gov_id: str) -> str:
    """Return the canonical GOV id (may differ if the object was merged)."""
    gov_id = normalize_gov_id(gov_id)
    if not gov_id:
        raise GovError("Keine GOV-Kennung angegeben.")

    payload = _request_json(f"{GOV_BASE_URL}/api/checkObjectId", params={"itemId": gov_id})
    if payload is None or payload == "":
        raise GovNotFoundError("GOV-Kennung ist ungültig.")
    if isinstance(payload, str):
        canonical = payload.strip().strip('"')
        if not canonical:
            raise GovNotFoundError("GOV-Kennung ist ungültig.")
        return canonical
    if isinstance(payload, dict):
        canonical = _first_attr(payload, "id", "itemId", "value")
        if canonical:
            return str(canonical).strip()
    return gov_id


def fetch_object(gov_id: str) -> dict:
    """Load the full GOV object for an id."""
    gov_id = normalize_gov_id(gov_id)
    if not gov_id:
        raise GovError("Keine GOV-Kennung angegeben.")

    try:
        canonical = check_object_id(gov_id)
    except GovNotFoundError:
        raise
    except GovError:
        canonical = gov_id

    payload = _request_json(f"{GOV_BASE_URL}/api/getObject", params={"itemId": canonical})
    if not isinstance(payload, dict):
        raise GovUnavailableError("GOV-Objekt konnte nicht gelesen werden.")
    payload.setdefault("id", canonical)
    return payload


def _extract_search_ids(payload: Any) -> list[str]:
    ids: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and looks_like_gov_id(value):
            if value not in ids:
                ids.append(value)
        elif isinstance(value, dict):
            found = _first_attr(value, "id", "itemId", "govId", "gov_id")
            if found and looks_like_gov_id(str(found)):
                sid = str(found)
                if sid not in ids:
                    ids.append(sid)

    if isinstance(payload, list):
        for item in payload:
            add(item)
        return ids

    if isinstance(payload, dict):
        for key in ("item", "items", "object", "objects", "result", "results"):
            if key in payload:
                for item in _as_list(payload[key]):
                    add(item)
        add(payload)
        return ids

    if isinstance(payload, str):
        for match in re.findall(r">([A-Za-z0-9_]{3,64})<", payload):
            add(match)
    return ids


def search_places(query: str, *, limit: int = GOV_SEARCH_LIMIT) -> list[dict]:
    """Search GOV by place name and return summarized hits."""
    query = (query or "").strip()
    if not query:
        return []

    results: list[dict] = []
    seen: set[str] = set()

    if looks_like_gov_id(query):
        try:
            obj = fetch_object(query)
            results.append(summarize_object(obj))
            seen.add(obj.get("id") or query)
        except GovNotFoundError:
            pass

    payload = _request_json(
        f"{GOV_BASE_URL}/api/searchByName",
        params={"placename": query},
    )

    hits: list[Any] = []
    if isinstance(payload, list):
        hits = payload
    elif isinstance(payload, dict):
        for key in ("item", "items", "object", "objects", "result", "results"):
            if key in payload:
                hits = _as_list(payload[key])
                break
        if not hits:
            hits = _extract_search_ids(payload)

    for item in hits:
        if len(results) >= limit:
            break
        if isinstance(item, dict) and (item.get("id") or item.get("name")):
            gov_id = str(_first_attr(item, "id", "itemId") or "")
            if gov_id and gov_id in seen:
                continue
            try:
                summary = summarize_object(item) if item.get("name") else None
            except Exception:
                summary = None
            if summary and summary.get("id"):
                seen.add(summary["id"])
                results.append(summary)
                continue
            if gov_id:
                item = gov_id
        if isinstance(item, str):
            gov_id = item
            if gov_id in seen or not looks_like_gov_id(gov_id):
                continue
            seen.add(gov_id)
            try:
                obj = fetch_object(gov_id)
                results.append(summarize_object(obj))
            except GovError:
                results.append(
                    {
                        "id": gov_id,
                        "name": gov_id,
                        "names": [],
                        "lat": None,
                        "lon": None,
                        "url": gov_item_url(gov_id),
                    }
                )
    return results


def _prop_julian_bounds(prop: dict) -> tuple[Optional[int], Optional[int]]:
    """Return (begin_jd inclusive, end_jd exclusive) for a GOV name/type/part-of."""
    begin = None
    end = None

    timespan = _first_attr(prop, "timespan", "time")
    if isinstance(timespan, dict):
        begin_node = timespan.get("begin") or {}
        end_node = timespan.get("end") or {}
        if isinstance(begin_node, dict):
            begin = _first_attr(begin_node, "jd")
        if isinstance(end_node, dict):
            end_jd = _first_attr(end_node, "jd")
            if end_jd is not None:
                end = int(end_jd) + 1  # GOV end dates are inclusive; we store exclusive

    begin_year = _first_attr(prop, "begin-year", "beginYear")
    end_year = _first_attr(prop, "end-year", "endYear")
    year = _first_attr(prop, "year")
    if begin is None and begin_year is not None:
        begin = date_to_julian_day(date(int(begin_year), 1, 1))
    if end is None and end_year is not None:
        end = date_to_julian_day(date(int(end_year) + 1, 1, 1))
    if begin is None and year is not None:
        begin = date_to_julian_day(date(int(year), 1, 1))
    if end is None and year is not None:
        end = date_to_julian_day(date(int(year) + 1, 1, 1))

    begin_direct = _first_attr(prop, "begin")
    end_direct = _first_attr(prop, "end")
    if begin is None and isinstance(begin_direct, dict):
        begin = _first_attr(begin_direct, "jd")
    if end is None and isinstance(end_direct, dict):
        end_jd = _first_attr(end_direct, "jd")
        if end_jd is not None:
            end = int(end_jd) + 1

    try:
        begin = int(begin) if begin is not None else None
    except (TypeError, ValueError):
        begin = None
    try:
        end = int(end) if end is not None else None
    except (TypeError, ValueError):
        end = None
    return begin, end


def iter_names(payload: Optional[dict]) -> list[dict]:
    """Normalize GOV name entries into a list of dicts."""
    if not payload:
        return []
    names = []
    for raw in _as_list(payload.get("name")):
        if not isinstance(raw, dict):
            continue
        value = _first_attr(raw, "value", "_value")
        if not value:
            continue
        lang = _first_attr(raw, "lang", "_lang", "language") or ""
        begin_jd, end_jd = _prop_julian_bounds(raw)
        names.append(
            {
                "value": str(value),
                "lang": str(lang),
                "lang_label": language_label(str(lang)) if lang else "",
                "begin_jd": begin_jd,
                "end_jd": end_jd,
                "valid_from": julian_day_to_date(begin_jd),
                "valid_to": julian_day_to_date(end_jd - 1) if end_jd else None,
            }
        )
    return names


def name_history(payload: Optional[dict]) -> list[dict]:
    names = iter_names(payload)
    names.sort(
        key=lambda n: (
            0 if (n["begin_jd"] or n["end_jd"]) else 1,
            n["begin_jd"] or 0,
            n["value"],
        )
    )
    return names


def _name_valid_at(entry: dict, jd: int) -> bool:
    begin = entry.get("begin_jd")
    end = entry.get("end_jd")
    if begin is not None and jd < begin:
        return False
    if end is not None and jd >= end:
        return False
    return True


def _language_rank(lang: str, preferred: str) -> int:
    lang = (lang or "").lower()
    preferred = (preferred or "deu").lower()
    aliases = {
        "deu": {"deu", "de", "ger"},
        "de": {"deu", "de", "ger"},
        "ger": {"deu", "de", "ger"},
        "pol": {"pol", "pl"},
        "pl": {"pol", "pl"},
        "eng": {"eng", "en"},
        "en": {"eng", "en"},
    }
    wanted = aliases.get(preferred, {preferred})
    if lang in wanted:
        return 0
    if not lang:
        return 1
    return 2


def name_at(payload: Optional[dict], when: Optional[date] = None, language: str = "deu") -> Optional[str]:
    """Return the GOV name valid at *when* (defaults to today)."""
    names = iter_names(payload)
    if not names:
        return None
    jd = date_to_julian_day(when or date.today())
    valid = [n for n in names if _name_valid_at(n, jd)]
    pool = valid or names
    pool.sort(key=lambda n: (_language_rank(n.get("lang") or "", language), n["value"]))
    return pool[0]["value"] if pool else None


def position_of(payload: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    if not payload:
        return None, None
    position = payload.get("position") or {}
    if not isinstance(position, dict):
        return None, None
    lat = _first_attr(position, "lat", "_lat")
    lon = _first_attr(position, "lon", "_lon")
    try:
        lat_f = float(lat) if lat is not None else None
        lon_f = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        return None, None
    return lat_f, lon_f


def summarize_object(payload: dict) -> dict:
    names = iter_names(payload)
    lat, lon = position_of(payload)
    gov_id = str(payload.get("id") or "")
    current = name_at(payload) or (names[0]["value"] if names else gov_id)
    unique_names = []
    for entry in names:
        if entry["value"] not in unique_names:
            unique_names.append(entry["value"])
    return {
        "id": gov_id,
        "name": current,
        "names": unique_names,
        "lat": lat,
        "lon": lon,
        "url": gov_item_url(gov_id) if gov_id else "",
    }
