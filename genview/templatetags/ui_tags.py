from django import template
from django.utils.translation import gettext as _

register = template.Library()

DEFAULT_LABELS = {
    "detail": _("Details"),
    "edit": _("Bearbeiten"),
    "delete": _("Löschen"),
    "open": _("Öffnen"),
}


@register.inclusion_tag("genview/_action_buttons.html", takes_context=True)
def action_buttons(
    context,
    layout="compact",
    detail_url=None,
    edit_url=None,
    delete_url=None,
    open_url=None,
    open_new_tab=False,
    show_labels=False,
    btn_extra="",
    edit_label=None,
    delete_label=None,
    detail_label=None,
    open_label=None,
    edit_variant=None,
):
    """Render a consistent set of CRUD action buttons.

    layout:
      - compact: small icon buttons in a btn-group (list tables)
      - sidebar: full-width stacked buttons with labels (detail pages)
      - toolbar: compact group with optional extra classes (e.g. border-0)
      - split: two equal-width buttons (card footers)
      - labeled: inline buttons with icon + short label
    """
    if edit_variant is None:
        edit_variant = "primary" if layout == "sidebar" else "secondary"

    return {
        "layout": layout,
        "detail_url": detail_url,
        "edit_url": edit_url,
        "delete_url": delete_url,
        "open_url": open_url,
        "open_new_tab": open_new_tab,
        "show_labels": show_labels,
        "btn_extra": btn_extra,
        "edit_variant": edit_variant,
        "labels": {
            "detail": detail_label or DEFAULT_LABELS["detail"],
            "edit": edit_label or DEFAULT_LABELS["edit"],
            "delete": delete_label or DEFAULT_LABELS["delete"],
            "open": open_label or DEFAULT_LABELS["open"],
        },
    }
