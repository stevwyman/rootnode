# genview/templatetags/pagination_tags.py
from django import template

register = template.Library()

@register.simple_tag
def paginate_range(page_obj, surrounding=2):
    """
    Gibt eine Liste von Seiten‑Nummern zurück, bei Bedarf mit dem Zeichen '...'.
    - `surrounding`   → wie viele Nachbarn links/rechts von `page_obj.number`
    - Beispiel: page 7 von 20 → [1, '...', 5, 6, 7, 8, 9, '...', 20]
    """
    total_pages = page_obj.paginator.num_pages
    current = page_obj.number

    # Klein genug → zeige alles
    if total_pages <= (surrounding * 2) + 5:
        return list(range(1, total_pages + 1))

    pages = []

    # immer die erste Seite
    pages.append(1)

    # linke Lücke?
    left = current - surrounding
    if left > 2:
        pages.append("...")
    else:
        # wenn die Lücke klein ist, ergänze die Zwischenzahlen
        pages.extend(range(2, left))

    # mittlere (Nachbarschaft) Zahlen
    start = max(2, current - surrounding)
    end = min(total_pages - 1, current + surrounding)
    pages.extend(range(start, end + 1))

    # rechte Lücke?
    right = current + surrounding
    if right < total_pages - 1:
        pages.append("...")
    else:
        pages.extend(range(right + 1, total_pages))

    # immer die letzte Seite
    pages.append(total_pages)

    # Entferne mögliche Duplikate (z. B. wenn left == 2)
    cleaned = []
    for p in pages:
        if not cleaned or cleaned[-1] != p:
            cleaned.append(p)
    return cleaned
