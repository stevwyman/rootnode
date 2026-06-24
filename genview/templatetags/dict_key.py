# genview/templatetags/dict_key.py
from django import template

register = template.Library()

@register.filter
def dict_key(d, key):
    """
    Gibt d[key] zurück. Funktioniert für Formulare,
    Dictionaries, QueryDicts ….
    """
    try:
        return d[key]
    except Exception:
        return ""          # leer, damit das Template nicht bricht