from django import forms
from django.utils.translation import gettext_lazy as _
from mptt.forms import TreeNodeChoiceField

from .models import StoryPage


class StoryPageForm(forms.ModelForm):
    parent = TreeNodeChoiceField(
        queryset=StoryPage.objects.none(),
        required=False,
        level_indicator="— ",
        label=_("Übergeordnete Seite"),
        empty_label=_("— oberste Ebene —"),
    )

    class Meta:
        model = StoryPage
        fields = ("title", "parent", "sort_order", "kind", "status", "body")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "sort_order": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "step": 10}
            ),
            "kind": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "body": forms.Textarea(
                attrs={
                    "class": "form-control font-monospace",
                    "rows": 18,
                    "id": "id_body",
                }
            ),
        }
        help_texts = {
            "body": _(
                "Markdown. Codeblock mit fester Schrift: drei Backticks "
                "vor und nach dem Text. Verknüpfungen: "
                "[Anna Schmidt](person:12), [Familie Wyman](family:3), "
                "[Crediton](place:9)."
            ),
        }

    def __init__(self, *args, tree_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree_id = tree_id
        qs = StoryPage.objects.filter(gedcom_tree_id=tree_id)
        instance = self.instance
        if instance and instance.pk:
            qs = qs.exclude(pk__in=instance.get_descendants(include_self=True))
        self.fields["parent"].queryset = qs
        self.fields["parent"].widget.attrs.setdefault("class", "form-select")
        self.fields["sort_order"].required = False
        if not (instance and instance.pk):
            self.fields["sort_order"].initial = None

    def save(self, commit=True):
        page = super().save(commit=False)
        page.gedcom_tree_id = self.tree_id
        if not page.slug:
            page.assign_unique_slug()
        if commit:
            page.save()
            self.save_m2m()
        return page