# genview/templatetags/event_extras.py
from __future__ import annotations

from datetime import date
from typing import Union

from django import template
from django.utils import formats

register = template.Library()


@register.filter
def display_event_date(value: Union["Event", str, None]) -> str:   # type: ignore
    """
    Gibt ein lesbares Datum zurück:

    * Wenn *value* ein ``Event``‑Objekt ist und ``parsed_date`` vorhanden ist,
      wird das Datum im kurzen Datumsformat (z. B. ``01.01.2020``) zurückgegeben.
    * Wenn das ``parsed_date`` fehlt, wird das originale ``raw_date`` zurückgegeben.
    * Wenn *value* bereits ein String ist (z. B. ``event.raw_date``),
      wird dieser unverändert zurückgegeben.
    * Falls weder Datum noch Roh‑String vorhanden sind, wird ``"Unbekannt"`` zurückgegeben.
    """
    # ---------------------------------------------------------
    # 1️⃣ Wenn ein Event‑Objekt übergeben wurde …
    # ---------------------------------------------------------
    if hasattr(value, "parsed_date") or hasattr(value, "raw_date"):
        # Wir haben ein Model‑Objekt (oder ein Mock‑Objekt mit den Attributen)
        parsed = getattr(value, "parsed_date", None)
        raw = getattr(value, "raw_date", "")

        if isinstance(parsed, date):
            # Locale‑abhängiges kurzes Datumsformat (Deutsch → d.m.Y)
            return formats.date_format(parsed, "Y M d")
        if raw:
            return raw

        return "Unbekannt"

    # ---------------------------------------------------------
    # 2️⃣ Wenn ein einfacher String (z. B. `event.raw_date`) übergeben wird …
    # ---------------------------------------------------------
    if isinstance(value, str) and value.strip():
        return value.strip()

    # ---------------------------------------------------------
    # 3️⃣ Alles andere (None, leere Strings, falsche Typen)
    # ---------------------------------------------------------
    return "Unbekannt"
