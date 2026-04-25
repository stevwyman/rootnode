# genview/templatetags/family_tree.py
from django import template

register = template.Library()


@register.inclusion_tag("genview/_family_node.html", takes_context=True)
def render_family_node(context, family, depth=0, max_depth=5):
    """
    Rendert einen Familien‑Knoten (Husband / Wife + Kinder).
    depth      – aktuelle Rekursionstiefe (für Einrückungen)
    max_depth  – Schutz vor zu tiefen Zyklen.
    """
    if depth >= max_depth:
        return {"family": None, "depth": depth, "warning": "max depth reached"}

    # `family.children` ist bereits prefetched (wegen View) → iterierbar
    children = [
        {
            "individual": link.child,                     # das eigentliche Individual
            "relationship": link.get_relationship_type_display(),
            # mögliche Unter‑Familien, in denen das Kind selber ein Parent ist
            "sub_families": list(link.child.families_as_husband.all())
                            + list(link.child.families_as_wife.all()),
        }
        for link in family.children.all()                # <-- korrektes Related‑Name
    ]

    # Kontext vollständig weiterreichen (z. B. request, user)
    new_context = {
        "family": family,
        "children": children,
        "depth": depth,
        "max_depth": max_depth,
        **context,
    }
    return new_context