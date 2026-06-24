# genview/templatetags/lookup.py
from django import template

register = template.Library()

@register.filter
def lookup(container, key):
    """
    Gibt container[key] zurück, egal ob `container` ein dict, ein Mapping‑Objekt
    (z. B. QueryDict) oder ein Django‑Formular ist.
    """
    # 1. Mapping‑Objekte (dict, QueryDict, etc.)
    if hasattr(container, "get"):
        return container.get(key, "")

    # 2. Formulare, Listen, etc. – sie implementieren __getitem__
    if hasattr(container, "__getitem__"):
        try:
            return container[key]
        except Exception:
            # Schlüssel existiert nicht → leere Zeichenkette zurückgeben,
            # damit das Template nicht bricht.
            return ""

    # 3. Fallback – versuche, als Attribut zu lesen
    return getattr(container, key, "")