from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from mptt.models import MPTTModel, TreeForeignKey


class StoryPage(MPTTModel):
    """Tree-scoped narrative page (guide, history, or family story)."""

    class Kind(models.TextChoices):
        GUIDE = "guide", _("Anleitung")
        HISTORY = "history", _("Historie")
        STORY = "story", _("Erzählung")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Entwurf")
        PUBLISHED = "published", _("Veröffentlicht")

    gedcom_tree = models.ForeignKey(
        "genview.Tree",
        on_delete=models.CASCADE,
        related_name="story_pages",
    )
    parent = TreeForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subpages",
        db_index=True,
    )
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    slug = models.SlugField(max_length=220, allow_unicode=True)
    sort_order = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Reihenfolge"),
        help_text=_(
            "Kleine Zahlen stehen weiter oben. Die Reihenfolge gilt "
            "unter Seiten derselben Ebene. Leer lassen, um die Seite "
            "am Ende einzufügen."
        ),
    )
    body = models.TextField(blank=True, verbose_name=_("Text"))
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.STORY,
        verbose_name=_("Art"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name=_("Status"),
    )
    mentioned_individuals = models.ManyToManyField(
        "genview.Individual",
        blank=True,
        related_name="story_pages",
    )
    mentioned_families = models.ManyToManyField(
        "genview.Family",
        blank=True,
        related_name="story_pages",
    )
    mentioned_places = models.ManyToManyField(
        "genview.Place",
        blank=True,
        related_name="story_pages",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="story_pages_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="story_pages_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class MPTTMeta:
        order_insertion_by = ["sort_order", "title"]

    class Meta:
        verbose_name = _("Geschichte")
        verbose_name_plural = _("Geschichten")
        ordering = ["tree_id", "lft"]
        constraints = [
            models.UniqueConstraint(
                fields=["gedcom_tree", "slug"],
                name="stories_storypage_tree_slug_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["gedcom_tree", "status"]),
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self):
        return reverse(
            "stories:detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )

    @classmethod
    def next_sort_order(cls, tree_id: int, parent_id: int | None, *, exclude_pk=None) -> int:
        qs = cls.objects.filter(gedcom_tree_id=tree_id, parent_id=parent_id)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        last = qs.aggregate(models.Max("sort_order"))["sort_order__max"]
        return (last or 0) + 10

    def assign_unique_slug(self) -> None:
        base = slugify(self.title, allow_unicode=True) or "seite"
        slug = base
        n = 2
        qs = StoryPage.objects.filter(gedcom_tree_id=self.gedcom_tree_id, slug=slug)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        while qs.exists():
            slug = f"{base}-{n}"
            n += 1
            qs = StoryPage.objects.filter(gedcom_tree_id=self.gedcom_tree_id, slug=slug)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
        self.slug = slug

    def save(self, *args, **kwargs):
        if not self.slug:
            self.assign_unique_slug()
        if self.sort_order is None:
            self.sort_order = self.next_sort_order(
                self.gedcom_tree_id, self.parent_id, exclude_pk=self.pk
            )
        super().save(*args, **kwargs)
        self.sync_mentions()

    def sync_mentions(self) -> None:
        from .mentions import extract_mention_ids

        if not self.pk or not self.gedcom_tree_id:
            return

        from genview.models import Family, Individual, Place

        ids = extract_mention_ids(self.body)
        people = Individual.objects.filter(
            gedcom_tree_id=self.gedcom_tree_id, pk__in=ids["person"]
        )
        families = Family.objects.filter(
            gedcom_tree_id=self.gedcom_tree_id, pk__in=ids["family"]
        )
        places = Place.objects.filter(
            gedcom_tree_id=self.gedcom_tree_id, pk__in=ids["place"]
        )
        self.mentioned_individuals.set(people)
        self.mentioned_families.set(families)
        self.mentioned_places.set(places)