# genview/models.py
from __future__ import annotations

import os
import hashlib

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.db.models import Prefetch, Q
from django.db.models.signals import post_delete
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from mptt.models import MPTTModel, TreeForeignKey



def tree_media_directory_path(instance, filename):
    """
    Dynamically generates the upload path for a media file based on its Tree ID.
    Example: MEDIA_ROOT/trees/tree_5/my_photo.jpg
    """
    # Grab the tree ID. Fallback to 'unassigned' just in case,
    # though your forms should prevent this.
    tree_id = instance.gedcom_tree_id or "unassigned"

    # Build the path: trees/tree_<id>/<filename>
    return f"trees/tree_{tree_id}/{filename}"


def tree_annotated_media_directory_path(instance, filename):
    """
    Dynamically generates the upload path for a media file based on its Tree ID.
    Example: MEDIA_ROOT/trees/tree_5/my_photo.jpg
    """
    # Grab the tree ID. Fallback to 'unassigned' just in case,
    # though your forms should prevent this.
    tree_id = instance.gedcom_tree_id or "unassigned"

    # Build the path: trees/tree_<id>/<filename>
    return f"trees/tree_{tree_id}/annotated/{filename}"


def _hashed_thumb_directory_path(instance, filename, size: str) -> str:
    """
    Two-level hash sharding so leaf folders stay small and lookups stay fast.

    Example: trees/tree_5/thumbs/mini/9f/3a/photo_thumb_mini.jpg

    The hash is derived from the original media path (stable across regenerations)
    so re-creating a thumbnail overwrites the same location instead of orphaning files.
    """
    tree_id = instance.gedcom_tree_id or "unassigned"
    seed = instance.file.name if instance.file and instance.file.name else filename
    h = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return f"trees/tree_{tree_id}/thumbs/{size}/{h[:2]}/{h[2:4]}/{filename}"


def tree_thumbs_mini_directory_path(instance, filename):
    return _hashed_thumb_directory_path(instance, filename, "mini")


def tree_thumbs_small_directory_path(instance, filename):
    return _hashed_thumb_directory_path(instance, filename, "small")


class Tree(models.Model):
    """Represents a distinct family tree (a GEDCOM file import)."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_public = models.BooleanField(
        default=False,
        verbose_name="Öffentlicher Stammbaum",
        help_text="Wenn aktiviert, kann jeder den Baum sehen, ohne Membership."
    )
    show_living_people = models.BooleanField(
        default=False,
        verbose_name="Lebende Personen anzeigen",
        help_text=(
            "Wenn aktiviert, gelten auf einem öffentlichen Baum keine "
            "Lebend-Datenschutzregeln für Gäste. Editoren und Admins "
            "sehen lebende Personen immer. Mitglieder eines privaten "
            "Baums ebenfalls."
        ),
    )
    starting_individual = models.ForeignKey(
        "Individual",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Startperson",
        help_text="Person für Schnellzugriff und Start der interaktiven Baumansicht.",
    )

    def __str__(self):
        return self.name


class TreeMembership(models.Model):
    """Maps a User to a Tree and defines what they can do."""

    class Role(models.TextChoices):
        VIEWER = "VIEWER", "Can only view data"
        EDITOR = "EDITOR", "Can edit individuals and families"
        ADMIN  = "ADMIN",  "Can edit data, import GEDCOMs, and invite user"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        # A user can only have one permission level per tree
        unique_together = ("user", "gedcom_tree")

    def __str__(self):
        return f"{self.user.username} - {self.gedcom_tree.name} ({self.role})"


HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message=_("Farbe muss als Hex-Wert angegeben werden, z. B. #0d6efd."),
)


class EntityTag(models.Model):
    """Tree-scoped research flag (complete, missing data, unclear, …)."""

    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="entity_tags"
    )
    name = models.CharField(max_length=80, verbose_name=_("Name"))
    description = models.TextField(blank=True, verbose_name=_("Beschreibung"))
    color = models.CharField(
        max_length=7,
        default="#6c757d",
        validators=[HEX_COLOR_VALIDATOR],
        verbose_name=_("Farbe"),
        help_text=_("Hex-Farbe, z. B. #198754"),
    )

    class Meta:
        verbose_name = _("Markierung")
        verbose_name_plural = _("Markierungen")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["gedcom_tree", "name"],
                name="genview_entitytag_tree_name_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def text_color(self) -> str:
        hexcolor = (self.color or "#000000").lstrip("#")
        if len(hexcolor) != 6:
            return "#ffffff"
        red = int(hexcolor[0:2], 16)
        green = int(hexcolor[2:4], 16)
        blue = int(hexcolor[4:6], 16)
        luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
        return "#000000" if luminance > 0.6 else "#ffffff"

    @property
    def badge_style(self) -> str:
        return f"background-color: {self.color}; color: {self.text_color};"


# ----------------------------------------------------------------------
# 1️⃣ Helper-Mixin – überall wo ein GEDCOM-ID-Feld nötig ist
# ----------------------------------------------------------------------
class GedcomIdMixin(models.Model):
    """
    Gemeinsames Feld für alle GEDCOM-Objekte, die eine externe
    GEDCOM-Referenz besitzen (z. B. @I1@, @F2@, @S3@ …).
    """

    gedcom_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="GEDCOM Referenz, z.B. @I1@, @F2@ …",
        verbose_name="GEDCOM ID"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Standard-Präfix, falls ein Modell es mal vergisst
    gedcom_prefix = "X"

    class Meta:
        abstract = True
        # Der moderne Weg, um in abstrakten Modellen Eindeutigkeit zu erzwingen
        constraints = [
            models.UniqueConstraint(
                fields=['gedcom_tree', 'gedcom_id'],
                # Die Platzhalter sorgen dafür, dass der Constraint z.B. 
                # 'genview_individual_unique_gedcom_id' heißt
                name='%(app_label)s_%(class)s_unique_gedcom_id'
            )
        ]

    def save(self, *args, **kwargs):
        # 1. Prüfen, ob das Objekt brandneu ist
        is_new = self.pk is None
        
        # 2. Normalen Django-Speichervorgang ausführen (erzeugt den Primary Key / pk)
        super().save(*args, **kwargs)

        # 3. Automatische ID generieren, wenn neu und Feld leer
        if is_new and not self.gedcom_id:
            # Wir nutzen das Präfix des jeweiligen Kind-Modells 
            # und hängen das "M" für manuell an, um Import-Konflikte zu vermeiden
            prefix = self.gedcom_prefix
            self.gedcom_id = f"@{prefix}-M{self.pk}@"
            
            # 4. Nur die gedcom_id nochmals in der DB aktualisieren
            self.save(update_fields=['gedcom_id'])


# ----------------------------------------------------------------------
# 2️⃣ SOURCE / REPOSITORY GEDCOM:SOUR
# ----------------------------------------------------------------------
class Source(GedcomIdMixin):
    """Quelle (SOUR) oder Repository (REPO)."""
    gedcom_prefix = "S"  # Ergibt z.B. S-M102

    title = models.CharField(max_length=255, help_text="Titel / Kurzbeschreibung")
    author = models.CharField(max_length=255, blank=True)
    publication_facts = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)

    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="sources"
    )

    class Meta(GedcomIdMixin.Meta):
        ordering = ["title"]
        indexes = [models.Index(fields=["title"])]

    def __str__(self) -> str:
        return self.title
    
    def get_absolute_url(self):
        return reverse(
            "genview:source-detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )


# ----------------------------------------------------------------------
# 3️⃣ INDIVIDUAL (Person) GEDCOM:INDI
# ----------------------------------------------------------------------
class Individual(GedcomIdMixin):
    """GEDCOM-Person (INDI)."""
    gedcom_prefix = "I"  # Ergibt z.B. I-M102

    class Sex(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        UNKNOWN = "U", "Unknown"

    given_name = models.CharField(max_length=150, blank=True)
    surname = models.CharField(max_length=150, blank=True)
    name_prefix = models.CharField(
        max_length=50, blank=True, help_text="z. B. Dr., Sir"
    )
    name_suffix = models.CharField(
        max_length=50, blank=True, help_text="z. B. Jr., III"
    )
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)

    notes = models.TextField(blank=True)

    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="individuals"
    )

    # Quellen, in denen diese Person auftaucht
    sources = models.ManyToManyField(
        Source,
        blank=True,
        related_name="individuals",
    )
    entity_tags = models.ManyToManyField(
        EntityTag,
        blank=True,
        related_name="individuals",
        verbose_name=_("Markierungen"),
    )

    class Meta(GedcomIdMixin.Meta):
        ordering = ["surname", "given_name"]
        indexes = [
            models.Index(
                fields=["gedcom_tree", "surname", "given_name"],
                name="genview_ind_tree_name_idx",
            ),
        ]

    # ------------------------------------------------------------------
    # Helper-Methoden
    # ------------------------------------------------------------------
    def __str__(self) -> str:
        return f"{self.full_name()} ({self.gedcom_id})"

    def civil_name(self) -> str:
        """GIVN/SURN (and prefix/suffix), ignoring GEDCOM TITL."""
        parts = [self.name_prefix, self.given_name, self.surname, self.name_suffix]
        return " ".join(p for p in parts if p).strip() or "Unnamed"

    def _titl_events(self) -> list["Event"]:
        """TITL events with a description, oldest then newest."""
        cached = getattr(self, "title_events", None)
        if cached is not None:
            rows = [ev for ev in cached if (ev.description or "").strip()]
        else:
            prefetched = getattr(self, "_prefetched_objects_cache", None)
            if prefetched and "events" in prefetched:
                rows = [
                    ev
                    for ev in self.events.all()
                    if getattr(getattr(ev, "event_type", None), "tag", None) == "TITL"
                    and (ev.description or "").strip()
                ]
            else:
                rows = list(
                    self.events.filter(event_type__tag="TITL")
                    .exclude(description="")
                    .select_related("event_type")
                )
        rows.sort(
            key=lambda ev: (
                ev.parsed_date is not None,
                ev.parsed_date or date.min,
                ev.pk or 0,
            )
        )
        return rows

    def full_name(self) -> str:
        """Display name: GEDCOM TITL when set, otherwise the civil name."""
        title = self.primary_title
        if title:
            return title
        return self.civil_name()

    @staticmethod
    def title_search_q(term: str) -> Q:
        """Match GEDCOM TITL event descriptions (King, Earl, …)."""
        term = (term or "").strip()
        if not term:
            return Q()
        return Q(
            events__event_type__tag="TITL",
            events__description__icontains=term,
        )

    @staticmethod
    def titl_event_queryset():
        return Event.objects.filter(event_type__tag="TITL").select_related("event_type")

    @classmethod
    def titl_events_prefetch(cls, lookup: str = "events"):
        return Prefetch(
            lookup,
            queryset=cls.titl_event_queryset(),
            to_attr="title_events",
        )

    def get_absolute_url(self):
        return reverse(
            "genview:individual-detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )

    # --------------------------------------------------------------
    # ★★ Neu: Convenience-Properties für Geburts- und Sterbedatum ★★
    # --------------------------------------------------------------
    @property
    def birth_year(self):
        """Holt das Geburtsjahr für kompakte Stammbaum-Ansichten."""
        if self.birth_date:
            return self.birth_date.year
        # Fallback auf das rohe Datum, falls es nicht geparst werden konnte (z.B. "ABT 1890")
        if self.birth_date_raw:
            import re
            match = re.search(r'\d{4}', self.birth_date_raw)
            return match.group() if match else ""
        return ""

    @property
    def death_year(self):
        """Holt das Sterbejahr für kompakte Stammbaum-Ansichten."""
        if self.death_date:
            return self.death_date.year
        if self.death_date_raw:
            import re
            match = re.search(r'\d{4}', self.death_date_raw)
            return match.group() if match else ""
        return ""
    
    @property
    def short_given_name(self):
        """
        Kürzt lange Vornamen für Listenansichten.
        Beispiel: 'Johann Georg Wilhelm' -> 'Johann G. W.'
        """
        if not self.given_name:
            return ""
            
        # Teile den String an den Leerzeichen in eine Liste von Namen
        name_parts = self.given_name.split()
        
        # Wenn es nur ein Vorname ist, gib ihn direkt zurück
        if len(name_parts) <= 1:
            return self.given_name
            
        # Nimm den ersten Namen voll, vom Rest nur den ersten Buchstaben + Punkt
        first_name = name_parts[0]
        initials = [f"{part[0]}." for part in name_parts[1:] if part]
        
        # Füge alles wieder mit Leerzeichen zusammen
        return f"{first_name} {' '.join(initials)}"
    
    @property
    def birth_event(self) -> Optional["Event"]:
        """
        Liefert das zugehörige ``BIRT``-Event (oder ``None``).
        Wir nutzen das bereits vorgefertigte ``related_name='events'``
        des ``Event``-Modells.
        """
        # `events` ist ein RelatedManager; ``filter`` gibt ein QuerySet zurück.
        # Wir holen das **erste** passende Event (es sollte nur eines geben).
        return self.events.filter(event_type__tag='BIRT').first()

    @property
    def death_event(self) -> Optional["Event"]:
        """Liefert das zugehörige ``DEAT``-Event (oder ``None``)."""
        return self.events.filter(event_type__tag='DEAT').first()

    @property
    def birth_date(self) -> Optional[date]:
        """
        Das geparste Geburtsdatum (``Event.parsed_date``).
        Falls das Event existiert, aber kein ``parsed_date`` gesetzt ist,
        wird ``None`` zurückgegeben – das kann dann im Template mit ``raw_date``
        ausgegeben werden.
        """
        ev = self.birth_event
        return ev.parsed_date if ev else None

    @property
    def death_date(self) -> Optional[date]:
        """Das geparste Sterbedatum."""
        ev = self.death_event
        return ev.parsed_date if ev else None

    @property
    def is_deceased(self) -> bool:
        """True, wenn ein ``DEAT``-Event vorhanden ist."""
        return self.death_event is not None

    @property
    def age(self) -> Optional[int]:
        """
        Berechnet das aktuelle Alter (oder das Alter zum Tod) anhand
        der vorhandenen Geburts- und Sterbedaten.
        Gibt ``None`` zurück, wenn kein Geburtsdatum vorhanden ist.
        """
        if not self.birth_date:
            return None

        # Wenn ein Sterbedatum existiert, verwenden wir das als End-Datum,
        # sonst das heutige Datum.
        end = self.death_date or date.today()

        # Altersberechnung (Jahre) – berücksichtigt, ob der Geburtstag im
        # aktuellen Jahr schon vorbei war.
        years = end.year - self.birth_date.year
        if (end.month, end.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years

    # --------------------------------------------------------------
    # Optional: Hilfsmethoden, um das Roh-Datum ebenfalls leicht zu holen
    # --------------------------------------------------------------
    @property
    def birth_date_raw(self) -> Optional[str]:
        """Der ungeparste GEDCOM-Datums-String vom BIRT-Event."""
        ev = self.birth_event
        return ev.raw_date if ev else None

    @property
    def death_date_raw(self) -> Optional[str]:
        """Der ungeparste GEDCOM-Datums-String vom DEAT-Event."""
        ev = self.death_event
        return ev.raw_date if ev else None
    
    @property
    def father(self):
        """Returns the husband of the family where this person is a child."""
        # 1. Hole den ersten ChildFamilyLink für dieses Kind
        link = self.parental_families.first() 
        
        # 2. Wenn ein Link existiert, eine Familie verknüpft ist UND es einen Ehemann gibt
        if link and link.family and link.family.husband:
            return link.family.husband
        return None

    @property
    def mother(self):
        """Returns the wife of the family where this person is a child."""
        # 1. Hole den ersten ChildFamilyLink für dieses Kind
        link = self.parental_families.first()
        
        # 2. Wenn ein Link existiert, eine Familie verknüpft ist UND es eine Ehefrau gibt
        if link and link.family and link.family.wife:
            return link.family.wife
        return None
    
    @property
    def spousal_families(self):
        """
        Kombiniert die vorab geladenen (prefetched) Familien, 
        ohne eine neue, blockierende Datenbankabfrage auszulösen.
        """
        return list(self.families_as_husband.all()) + list(self.families_as_wife.all())
    
    @property
    def siblings(self):
        """
        Liefert alle Geschwister UND Halbgeschwister.
        Logik: Findet alle Eltern dieser Person -> Findet alle Familien dieser Eltern -> Holt die Kinder.
        """
        # 1. Alle Familien holen, in denen diese Person ein Kind ist
        my_families = Family.objects.filter(children__child=self)
        
        # 2. Die IDs der Väter und Mütter sammeln (None-Werte herausfiltern!)
        father_ids = [f for f in my_families.values_list('husband_id', flat=True) if f is not None]
        mother_ids = [m for m in my_families.values_list('wife_id', flat=True) if m is not None]
        
        # Wenn die Person gar keine bekannten Eltern hat, kann es auch keine ermittelbaren Geschwister geben
        if not father_ids and not mother_ids:
            return Individual.objects.none()

        # 3. Alle Familien finden, bei denen MINDESTENS EINER dieser Elternteile beteiligt ist
        sibling_families = Family.objects.filter(
            Q(husband_id__in=father_ids) | Q(wife_id__in=mother_ids)
        )
        
        # 4. Alle Kinder aus diesen Familien holen, sich selbst ausschließen und Duplikate (Vollgeschwister) filtern
        return Individual.objects.filter(
            parental_families__family__in=sibling_families
        ).exclude(pk=self.pk).distinct()
    
    @property
    def is_confidential(self):
        """
        True when living-person rules hide this person:
        - birth + 110 years is still after today
        - death + 80 years is still after today
        - marriage + 60 years is still after today
        - no parsed birth, death, or marriage date (fail closed)
        """
        from .privacy import (
            BIRTH_PRIVACY_YEARS,
            DEATH_PRIVACY_YEARS,
            MARRIAGE_PRIVACY_YEARS,
            is_within_privacy_window,
        )

        has_any_date = False

        if self.death_date:
            has_any_date = True
            if is_within_privacy_window(self.death_date, DEATH_PRIVACY_YEARS):
                return True

        if self.birth_date:
            has_any_date = True
            if is_within_privacy_window(self.birth_date, BIRTH_PRIVACY_YEARS):
                return True

        for spousal_link in self.spousal_families:
            marriage_date = getattr(spousal_link, "marriage_date_parsed", None)
            if marriage_date:
                has_any_date = True
                if is_within_privacy_window(marriage_date, MARRIAGE_PRIVACY_YEARS):
                    return True

        return not has_any_date
    
    @property
    def profile_image(self):
        """
        Holt bevorzugt das als Portrait markierte Bild. 
        Gibt es keins, wird das erstbeste Bild genommen.
        Gibt es gar keine Bilder, gibt .first() automatisch 'None' zurück.
        """
        # '-is_portrait' sortiert True vor False. 
        # 'id' (oder '-id') dient als Tie-Breaker, falls es versehentlich zwei Portraits gibt.
        return self.media_objects.order_by('-is_portrait', 'id').first()
    
    @property
    def noble_titles(self) -> list[str]:
        """Liefert alle erfassten Adelstitel der Person als Liste von Strings."""
        return [(ev.description or "").strip() for ev in self._titl_events()]

    @property
    def primary_title(self) -> str:
        """Latest dated TITL, or the newest untitled TITL."""
        rows = self._titl_events()
        if not rows:
            return ""
        dated = [ev for ev in rows if ev.parsed_date]
        chosen = (
            max(dated, key=lambda ev: (ev.parsed_date, ev.pk or 0))
            if dated
            else max(rows, key=lambda ev: ev.pk or 0)
        )
        return (chosen.description or "").strip()
    
    @property
    def timeline_events(self):
        """
        Sammelt persönliche Ereignisse, Familien-Ereignisse sowie die Geburten von Kindern,
        sortiert sie chronologisch und berechnet für jeden Punkt das exakte Alter der Person.
        """
        timeline = []
        birth_date_obj = self.birth_date

        # 1. Persönliche Ereignisse (Geburt, Tod, Titel, Beruf, etc.)
        for event in self.events.filter(event_type__is_visible=True):
            tag = event.event_type.tag
            if tag == 'BIRT': icon = '👶'
            elif tag == 'DEAT': icon = '🪦'
            elif tag == 'TITL': icon = '👑'
            elif tag == 'OCCU': icon = '💼'
            else: icon = '📌'

            timeline.append({
                'date_sort': event.parsed_date,
                'date_display': event.raw_date,
                'title': event.event_type.name,
                'description': event.description,
                'place': event.place.name if event.place else "",
                'icon': icon,
                'age': None,  # Wird unten berechnet
                'tags': list(event.entity_tags.all()),
            })

        # 2. Familien-Ereignisse (Heirat, Scheidung) und Kindergeburten
        for family in self.spousal_families:
            # --- A) Heirats- / Scheidungs-Events der Familie ---
            for fam_event in family.events.filter(event_type__is_visible=True):
                tag = fam_event.event_type.tag
                if tag == 'MARR': icon = '💍'
                elif tag == 'DIV': icon = '💔'
                else: icon = '🔗'

                partner = family.husband if family.wife == self else family.wife
                partner_name = partner.full_name() if partner else "Unbekannt"

                timeline.append({
                    'date_sort': fam_event.parsed_date,
                    'date_display': fam_event.raw_date,
                    'title': fam_event.event_type.name,
                    'description': f"mit {partner_name}",
                    'place': fam_event.place.name if fam_event.place else "",
                    'icon': icon,
                    'age': None,
                    'tags': list(fam_event.entity_tags.all()),
                })

            # --- B) 🔥 NEU: Geburten der Kinder aus dieser Familie ---
            # Wir nutzen children.all() auf dem Through-Model ChildFamilyLink
            for child_link in family.children.all():
                child = child_link.child
                if child:
                    # Wir holen das Geburts-Event des Kindes
                    child_birth = child.birth_event
                    if child_birth:
                        timeline.append({
                            'date_sort': child_birth.parsed_date,
                            'date_display': child_birth.raw_date,
                            'title': "Geburt eines Kindes",
                            'description': f"Sohn/Tochter: {child.full_name()}",
                            'place': child_birth.place.name if child_birth.place else "",
                            'icon': "🍼",
                            'age': None,
                            'tags': list(child_birth.entity_tags.all()),
                        })

        # 3. Sortieren (Älteste Ereignisse zuerst)
        # Ereignisse ohne Datum landen dank date.min ganz am Anfang (z.B. unvollständige Taufen)
        def get_sort_key(item):
            return item['date_sort'] or date.min 
            
        timeline.sort(key=get_sort_key)

        # 4. 🔥 NEU: Das Alter für jedes sortierte Ereignis berechnen
        if birth_date_obj:
            for item in timeline:
                event_date = item['date_sort']
                if event_date:
                    # Mathematisch genaue Altersberechnung für den Tag des Ereignisses
                    age_at_event = event_date.year - birth_date_obj.year
                    if (event_date.month, event_date.day) < (birth_date_obj.month, birth_date_obj.day):
                        age_at_event -= 1
                    
                    # Ein negatives Alter (z.B. bei fehlerhaften Daten) fangen wir ab
                    item['age'] = max(0, age_at_event)

        return timeline

# ----------------------------------------------------------------------
# 4️⃣ FAMILY – MPTT-Baumstruktur GEDCOM:FAM
# ----------------------------------------------------------------------
class Family(MPTTModel, GedcomIdMixin):
    """Familie (FAM). Durch MPTT kann eine Familie Unter-Familien besitzen."""
    gedcom_prefix = "F"  # Ergibt z.B. F-M102

    class Meta(GedcomIdMixin.Meta):
        verbose_name_plural = "Families"
        ordering = ["gedcom_id"]

    # Ehepartner (optional – GEDCOM erlaubt leere Rollen)
    husband = models.ForeignKey(
        Individual,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="families_as_husband",
    )
    wife = models.ForeignKey(
        Individual,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="families_as_wife",
    )
    notes = models.TextField(blank=True)

    # MPTT-Hierarchie (z. B. Adoptiv-/Stief-Familien)
    parent = TreeForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sub_families",
        db_index=True,
    )

    # Quellen, die die Familie betreffen
    sources = models.ManyToManyField(
        Source,
        blank=True,
        related_name="families",
    )
    entity_tags = models.ManyToManyField(
        EntityTag,
        blank=True,
        related_name="families",
        verbose_name=_("Markierungen"),
    )

    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="families"
    )

    def __str__(self) -> str:
        husb = self.husband.surname if self.husband else "?"
        wife = self.wife.surname if self.wife else "?"
        return f"Family {husb} / {wife} ({self.gedcom_id})"

    def get_absolute_url(self):
        return reverse(
            "genview:family-detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )

    # -----------------------------------------------------------------
    # Hilfsmethode: gibt den Partner zurück, wenn `person` einer der
    # beiden Elternteile ist; sonst None.
    # -----------------------------------------------------------------
    def spouse_of(self, person: "Individual") -> "Individual | None":
        if person == self.husband:
            return self.wife
        if person == self.wife:
            return self.husband
        return None

    # ------------------------------------------------------------------
    # Convenience-Methode – liefert ein QuerySet aller Kinder-Individuals
    # ------------------------------------------------------------------
    def children_links(self) -> models.QuerySet["Individual"]:
        """
        Alle Kinder, die über das Through-Model ``ChildFamilyLink`` dieser Familie
        verknüpft sind. (Kurzschreibweise: ``family.children().all()``)
        """
        return Individual.objects.filter(parental_families__family=self)

    # ------------------------------------------------------------------
    #  Helfer-Property: das zugehörige MARR-Event (falls vorhanden)
    # ------------------------------------------------------------------
    @property
    def marriage_event(self) -> Optional["Event"]:
        """
        Gibt das erste Event vom Typ MARR (Marriage) zurück
        oder ``None`` wenn die Familie kein Heirats-Eintrag hat.
        """
        return self.events.filter(event_type__tag='MARR').first()

    # Optional: noch ein Property für den Ort (falls du es im Template
    # noch etwas kürzer schreiben willst)
    @property
    def marriage_place(self) -> str:
        ev = self.marriage_event
        # Gib den Namen des Ortes zurück, falls es einen Ort gibt
        if ev and ev.place:
            return ev.place.name
        return ""

    @property
    def marriage_date_raw(self) -> str:
        ev = self.marriage_event
        return ev.raw_date if ev else ""

    @property
    def marriage_date_parsed(self) -> Optional[date]:
        ev = self.marriage_event
        return ev.parsed_date if ev else None
    
    @property
    def is_confidential(self):
        """
        A family is confidential if its marriage is within the 60-year window
        or if husband, wife, or any linked child is confidential.
        """
        from .privacy import MARRIAGE_PRIVACY_YEARS, is_within_privacy_window

        if is_within_privacy_window(self.marriage_date_parsed, MARRIAGE_PRIVACY_YEARS):
            return True

        if self.husband and self.husband.is_confidential:
            return True

        if self.wife and self.wife.is_confidential:
            return True

        for link in self.children.all():
            if link.child and link.child.is_confidential:
                return True

        return False
    

# ----------------------------------------------------------------------
# 5️⃣ THROUGH-MODEL: Kind-zu-Familie (CHIL / FAMC)
# ----------------------------------------------------------------------
class ChildFamilyLink(models.Model):
    """
    Verbindet ein Kind (CHIL) mit einer Familie (FAMC) und kennt die Art der Beziehung.
    """

    class Relationship(models.TextChoices):
        BIOLOGICAL = "B", "Biological"
        ADOPTED = "A", "Adopted"
        FOSTER = "F", "Foster"
        STEP = "S", "Step"
        UNKNOWN = "U", "Unknown"

    # --- 1️⃣ Das Kind (Individual) ---
    child = models.ForeignKey(
        Individual,
        on_delete=models.CASCADE,
        related_name="parental_families",  # <-- Families, in denen das Kind vorkommt
    )

    # --- 2️⃣ Die Familie, zu der das Kind gehört ----
    family = models.ForeignKey(
        Family,
        on_delete=models.CASCADE,
        related_name="children",  # <-- **WICHTIG:** das ist das Feld, das wir prefetchen
    )

    relationship_type = models.CharField(
        max_length=1,
        choices=Relationship.choices,
        default=Relationship.BIOLOGICAL,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("child", "family")

    def __str__(self) -> str:
        return f"{self.child.full_name()} → {self.family}"


# ----------------------------------------------------------------------
# 6️⃣ Places GEDCOM:PLAC
# ----------------------------------------------------------------------
class Place(GedcomIdMixin):
    gedcom_prefix = "P"  # Ergibt z.B. P-M102
    # Security: Tie the place to a specific tree
    gedcom_tree = models.ForeignKey('Tree', on_delete=models.CASCADE, related_name='places')
    
    name = models.CharField(max_length=255, verbose_name="Ortsname")
    
    # Coordinates (DecimalField is best for GPS coordinates)
    # 9 digits total, 6 after the decimal point gives sub-meter accuracy!
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Breitengrad (Latitude)")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Längengrad (Longitude)")
    gov_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="GOV-Kennung",
        help_text="Kennung im Geschichtlichen Ortsverzeichnis (z.B. NEURCHJO94KE).",
    )
    gov_data = models.JSONField(
        null=True,
        blank=True,
        verbose_name="GOV-Daten",
        help_text="Zwischengespeicherte GOV-Objektdaten (Namen, Zeiten, Koordinaten).",
    )
    gov_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="GOV zuletzt geladen",
    )
    entity_tags = models.ManyToManyField(
        EntityTag,
        blank=True,
        related_name="places",
        verbose_name=_("Markierungen"),
    )

    class Meta(GedcomIdMixin.Meta):
        # Prevent duplicate places in the same tree
        unique_together = ('gedcom_tree', 'name')
        ordering = ['name']

    def __str__(self):
        return self.name
    
    @property
    def short_name(self):
        """
        Gibt nur den vordersten Teil des Ortsnamens zurück.
        Aus "Herford, NRW, Deutschland" wird "Herford".
        Perfekt für Tabellen, Ahnentafeln und kleine Badges!
        """
        if self.name:
            # Teilt den String am ersten Komma und entfernt überflüssige Leerzeichen
            return self.name.split(',')[0].strip()
        return "Unbekannter Ort"

    @property
    def gov_item_url(self) -> str:
        from .gov import gov_item_url

        if not self.gov_id:
            return ""
        return gov_item_url(self.gov_id)

    def name_at(self, when=None, language="deu") -> str:
        """Historic GOV name at *when*, otherwise the GEDCOM short name."""
        from .gov import name_at as gov_name_at

        if self.gov_data:
            found = gov_name_at(self.gov_data, when, language)
            if found:
                return found
        return self.short_name

    def gov_name_history(self) -> list:
        from .gov import name_history

        return name_history(self.gov_data)

    def apply_gov_payload(self, payload: dict, *, fill_coords: bool = True) -> None:
        from .gov import position_of

        gov_id = (payload or {}).get("id") or self.gov_id
        self.gov_id = gov_id or ""
        self.gov_data = payload
        self.gov_synced_at = timezone.now()
        if fill_coords and (self.latitude is None or self.longitude is None):
            lat, lon = position_of(payload)
            if lat is not None and lon is not None:
                try:
                    if self.latitude is None:
                        self.latitude = Decimal(str(round(lat, 6)))
                    if self.longitude is None:
                        self.longitude = Decimal(str(round(lon, 6)))
                except (InvalidOperation, TypeError, ValueError):
                    pass

    def sync_from_gov(self, *, fill_coords: bool = True) -> dict:
        from .gov import fetch_object

        payload = fetch_object(self.gov_id)
        self.apply_gov_payload(payload, fill_coords=fill_coords)
        self.save(
            update_fields=[
                "gov_id",
                "gov_data",
                "gov_synced_at",
                "latitude",
                "longitude",
                "updated_at",
            ]
        )
        return payload

    def clear_gov(self) -> None:
        self.gov_id = ""
        self.gov_data = None
        self.gov_synced_at = None
        self.save(update_fields=["gov_id", "gov_data", "gov_synced_at", "updated_at"])
    
    def get_absolute_url(self):
        return reverse(
            "genview:place-detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )
    

# ----------------------------------------------------------------------
# 7️⃣ EVENT_Types – sind Teil eines Events
# ----------------------------------------------------------------------
class EventType(models.Model):
    class Category(models.TextChoices):
        INDIVIDUAL = 'IND', 'Personen-Ereignis'
        FAMILY = 'FAM', 'Familien-Ereignis'
        BOTH = 'BOTH', 'Beides'

    # Der feste GEDCOM-Standard-Tag (z.B. 'BIRT', 'DEAT', 'OCCU', 'MARR')
    # Das ist unser Anker für den Python-Code!
    tag = models.CharField(max_length=4, unique=True, verbose_name="GEDCOM Tag")
    
    # Der Name, der in der Oberfläche angezeigt wird (z.B. "Beruf", "Geburt")
    name = models.CharField(max_length=100, verbose_name="Anzeigename")
    
    category = models.CharField(
        max_length=4, 
        choices=Category.choices, 
        default=Category.INDIVIDUAL,
        verbose_name="Kategorie"
    )

    is_visible = models.BooleanField(
        default=True,
        verbose_name="Sichtbar",
        help_text="Wenn deaktiviert, werden Ereignisse dieses Typs im Frontend (z.B. Timeline) ausgeblendet."
    )

    class Meta:
        verbose_name = "Ereignis-Typ"
        verbose_name_plural = "Ereignis-Typen"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.tag})"


# ----------------------------------------------------------------------
# 8️⃣ EVENTS – können einer Person ODER einer Familie zugeordnet sein
# ----------------------------------------------------------------------
class Event(models.Model):
    """Einzel-Event (z. B. BIRT, DEAT, MARR, DIV …)."""

    event_type = models.ForeignKey(
        EventType, 
        on_delete=models.RESTRICT, # Verhindert, dass jemand aus Versehen "Geburt" löscht
        related_name="events",
        verbose_name="Ereignistyp"
    )

    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="events"
    )

    # **Exklusiver** FK – nur einer von beiden darf gesetzt sein
    individual = models.ForeignKey(
        Individual,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )
    family = models.ForeignKey(
        Family,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )

    raw_date = models.CharField(
        max_length=100,
        blank=True,
        help_text="Original GEDCOM-Datum-String, z. B. 'ABT 1900'",
    )

    parsed_date = models.DateField(
        null=True,
        blank=True,
        help_text="Geparstes Datum (für Sortierung/Filter)",
        db_index=True,
    )
    place = models.ForeignKey(
        Place, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='events',
        verbose_name="Ort"
    )
    description = models.TextField(blank=True)

    sources = models.ManyToManyField(
        Source,
        blank=True,
        related_name="events",
        verbose_name="Quellen"
    )
    entity_tags = models.ManyToManyField(
        EntityTag,
        blank=True,
        related_name="events",
        verbose_name=_("Markierungen"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ereignis"
        verbose_name_plural = "Ereignisse"
        
        indexes = [
            models.Index(fields=["individual", "event_type", "parsed_date"]),
            models.Index(fields=["family", "event_type", "parsed_date"]),
            models.Index(
                fields=["gedcom_tree", "event_type", "parsed_date"],
                name="genview_eve_tree_type_date_idx",
            ),
            models.Index(
                fields=["gedcom_tree", "parsed_date"],
                name="genview_eve_tree_date_idx",
            ),
            models.Index(
                fields=["place", "parsed_date"],
                name="genview_eve_place_date_idx",
            ),
        ]

    def event_type_name(self):
        return self.event_type.name if self.event_type else "Unbekanntes Ereignis"

    @property
    def place_name(self) -> str:
        """Place label valid at this event's date, if GOV data is linked."""
        if not self.place_id:
            return ""
        return self.place.name_at(self.parsed_date)

    def __str__(self):
        # 1. Den Namen des Events aus der neuen verknüpften Tabelle holen
        # (Sicherheits-Fallback, falls das Feld aus irgendeinem Grund leer sein sollte)
        event_name = self.event_type.name if self.event_type else "Unbekanntes Ereignis"
        
        # 2. Wem gehört das Ereignis?
        owner = ""
        if self.individual:
            # Nutzt automatisch die __str__ Methode der Person (z.B. "Max Mustermann")
            owner = f" von {self.individual}"
        elif self.family:
            # Nutzt automatisch die __str__ Methode der Familie
            owner = f" der Familie {self.family}"
            
        # 3. Wann ist es passiert? (Zieht das Jahr für mehr Übersichtlichkeit)
        date_str = ""
        if self.parsed_date:
            date_str = f" ({self.parsed_date.year})"
        elif self.raw_date:
            date_str = f" ({self.raw_date})"
            
        # Baut alles zusammen: z.B. "Geburt von Max Mustermann (1990)"
        return f"{event_name}{owner}{date_str}"

    def get_absolute_url(self):
        return reverse(
            "genview:event-detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )

    # --------------------------------------------------------------
    # Validierung: Es darf nie **beide** FK gleichzeitig gesetzt sein
    # --------------------------------------------------------------
    def clean(self):
        super().clean()
        if bool(self.individual) == bool(self.family):
            raise ValidationError(
                "Ein Event muss entweder einer Person ODER einer Familie zugeordnet werden, nicht beiden."
            )

    def save(self, *args, **kwargs):
        self.full_clean()  # ruft ``clean`` auf
        super().save(*args, **kwargs)

    @property
    def is_confidential(self):
        """Confidential if the owner is, or this event's own date is still in a privacy window."""
        from .privacy import (
            BIRTH_PRIVACY_YEARS,
            DEATH_PRIVACY_YEARS,
            MARRIAGE_PRIVACY_YEARS,
            is_within_privacy_window,
        )

        tag = getattr(self.event_type, "tag", None) if self.event_type_id else None
        if tag == "BIRT" and is_within_privacy_window(self.parsed_date, BIRTH_PRIVACY_YEARS):
            return True
        if tag == "DEAT" and is_within_privacy_window(self.parsed_date, DEATH_PRIVACY_YEARS):
            return True
        if tag == "MARR" and is_within_privacy_window(self.parsed_date, MARRIAGE_PRIVACY_YEARS):
            return True

        if self.individual_id and self.individual:
            return self.individual.is_confidential
        if self.family_id and self.family:
            return self.family.is_confidential
        return True


# ----------------------------------------------------------------------
# 9️⃣ MEDIA OBJECT – Bilder, PDF-Dokumente, Links etc.
# ----------------------------------------------------------------------
class MediaObject(GedcomIdMixin):
    gedcom_prefix = "M"  # Ergibt z.B. M-M102

    class Category(models.TextChoices):
        PHOTO = 'PHOTO', 'Foto / Portrait'
        DOCUMENT = 'DOCUMENT', 'Dokument / Urkunde'

    """Multimedia-Objekt (OBJE)."""

    title = models.CharField(max_length=255, blank=True)

    file = models.FileField(
        upload_to=tree_media_directory_path, verbose_name=_("Datei/Bild")
    )

    extracted_text = models.TextField(blank=True, null=True)

    gedcom_original_filepath = models.CharField(
        max_length=500, 
        blank=True, 
        help_text="Temporärer Speicher für den Dateipfad aus der GEDCOM-Datei."
    )

    category = models.CharField(
        max_length=10,
        choices=Category.choices,
        default=Category.PHOTO,
        verbose_name="Kategorie"
    )

    description = models.TextField(blank=True)

    is_private = models.BooleanField(default=False)

    # ----------------------------------------------------------------
    # Thumbnails – we store the relative path (same storage as `file`)
    # ----------------------------------------------------------------
    thumb_mini  = models.ImageField(
        upload_to=tree_thumbs_mini_directory_path,
        blank=True,
        null=True,
        editable=False,
        help_text=_("Mini-Thumbnail (≈ 80 × 80 px)")
    )
    thumb_small = models.ImageField(
        upload_to=tree_thumbs_small_directory_path,
        blank=True,
        null=True,
        editable=False,
        help_text=_("Small-Thumbnail (≈ 200 × 200 px)")
    )

    # Beziehungen zu den anderen Entitäten
    individuals = models.ManyToManyField(
        Individual,
        blank=True,
        related_name="media_objects",
    )
    families = models.ManyToManyField(
        Family,
        blank=True,
        related_name="media_objects",
    )
    sources = models.ManyToManyField(
        Source,
        blank=True,
        related_name="media_objects",
    )

    events = models.ManyToManyField(
        'Event', 
        blank=True, 
        related_name='media_objects',
        help_text=_("Ereignisse, mit denen dieses Medium verknüpft ist")
    )
    entity_tags = models.ManyToManyField(
        EntityTag,
        blank=True,
        related_name="tagged_media",
        verbose_name=_("Markierungen"),
    )

    is_portrait = models.BooleanField(
        default=False,
        help_text=_("Dieses Bild wird als Portrait auf der Personen-Detail-Seite angezeigt."),
        db_index=True,
    )

    gedcom_tree = models.ForeignKey(
        Tree, on_delete=models.CASCADE, related_name="mediaobjects"
    )

    class Meta(GedcomIdMixin.Meta):
        ordering = ["-is_portrait", "title"]  # Portrait-Bilder zuerst

    def __str__(self) -> str:
        return self.title or f"Media {self.id}"

    def get_absolute_url(self):
        return reverse(
            "genview:media-detail",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk},
        )

    @property
    def is_image(self) -> bool:
        """True, wenn das gespeicherte File ein Bild ist – crash-sicher!"""
        # Explizit prüfen, ob das Feld wirklich eine Datei enthält
        if not self.file or not self.file.name:
            return False
            
        return self.file.name.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )

    @property
    def is_pdf(self) -> bool:
        if not self.file or not self.file.name:
            return False
        return self.file.name.lower().endswith(".pdf")

    def get_thumbnail(self, size: str = "mini") -> str:
        """
        Returns the authenticated media-thumb URL for *size* (mini|small).
        Generates the thumbnail on-the-fly when missing.
        """
        if size not in ("mini", "small"):
            raise ValueError("size must be 'mini' or 'small'")

        thumb_field = getattr(self, f"thumb_{size}")
        if not thumb_field or not thumb_field.name:
            from .utils import generate_thumbnail_for_instance
            generate_thumbnail_for_instance(self, size)

        if not self.pk or not self.gedcom_tree_id:
            return ""
        return reverse(
            "genview:media-thumb",
            kwargs={"tree_id": self.gedcom_tree_id, "pk": self.pk, "size": size},
        )
    
    @property
    def is_confidential(self):
        """
        Ein Medienobjekt (Foto, Urkunde) ist vertraulich, wenn es mit 
        mindestens einer vertraulichen Person, Familie oder einem 
        vertraulichen Ereignis verknüpft ist.
        """
        # 1. Hängt das Medium an vertraulichen Personen?
        # (Nutze hier den related_name deiner Verknüpfung, z.B. 'individuals')
        if hasattr(self, 'individuals'):
            for person in self.individuals.all():
                if person.is_confidential:
                    return True
                    
        # 2. Hängt das Medium an vertraulichen Familien?
        if hasattr(self, 'families'):
            for family in self.families.all():
                if family.is_confidential:
                    return True
                    
        # 3. Hängt das Medium an vertraulichen Ereignissen?
        if hasattr(self, 'events'):
            for event in self.events.filter(event_type__is_visible=True):
                if event.is_confidential:
                    return True
                    
        # Wenn das Bild mit gar nichts Vertraulichem verknüpft ist (oder historische
        # Personen zeigt), darf es öffentlich angezeigt werden.
        return False


class FaceTag(models.Model):
    """
    Speichert den Bildausschnitt (crop) und die Verknüpfung zu einer Person.
    """
    media          = models.ForeignKey(MediaObject, on_delete=models.CASCADE, related_name="facetags")
    individual     = models.ForeignKey(Individual, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="tagged_faces")
    suggested_individual = models.ForeignKey(
        Individual,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suggested_face_tags",
        help_text=_("Automatisch vorgeschlagene Person (noch nicht bestätigt)."),
    )
    match_distance = models.FloatField(
        null=True,
        blank=True,
        help_text=_("Kosinus-Abstand zum besten Embedding-Match (kleiner = ähnlicher)."),
    )
    
    # Position / Größe des Rechtecks (einfach u/v Koordinaten, relativ zum Original)
    x_percent      = models.FloatField()   # linke obere Ecke, Pixel
    y_percent      = models.FloatField()
    width_percent  = models.FloatField()
    height_percent = models.FloatField()
    confidence = models.FloatField()
    # 🔥 NEU: Hier drinnen speichern wir die 512 Zahlen als Liste
    embedding = models.JSONField(
        null=True, 
        blank=True, 
        help_text="Der mathematische Gesichts-Vektor aus DeepFace."
    )

    # Optional: ein kommentierbares Feld, falls du ein manuelles Tag setzen willst
    tag_label = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("media", "x_percent", "y_percent", "width_percent", "height_percent")   # vermeidet Duplikate

    def __str__(self):
        return f"FaceTag für {self.individual or self.suggested_individual or 'unbekannt'} ({self.media.id})"


class DocumentExtractionSuggestion(models.Model):
    """Structured event hints parsed from OCR text on a document."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Ausstehend")
        ACCEPTED = "accepted", _("Übernommen")
        REJECTED = "rejected", _("Abgelehnt")

    media = models.ForeignKey(
        MediaObject,
        on_delete=models.CASCADE,
        related_name="document_suggestions",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    event_type_tag = models.CharField(max_length=4)
    person_name = models.CharField(max_length=255, blank=True)
    individual = models.ForeignKey(
        Individual,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_suggestions",
    )
    raw_date = models.CharField(max_length=100, blank=True)
    parsed_date = models.DateField(null=True, blank=True)
    place_name = models.CharField(max_length=255, blank=True)
    place = models.ForeignKey(
        Place,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_suggestions",
    )
    context_line = models.CharField(max_length=500, blank=True)
    created_event = models.ForeignKey(
        Event,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_document_suggestions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type_tag} ({self.get_status_display()}) — {self.media_id}"


# ----------------------------------------------------------------------
# 🔟 alternative names for i.e. marriage
# ----------------------------------------------------------------------
class AlternativeName(models.Model):
    class NameType(models.TextChoices):
        MARRIED = 'married', 'Ehename'
        MAIDEN = 'maiden', 'Geburtsname (abweichend)'
        AKA = 'aka', 'Alias / Spitzname'
        IMMIGRANT = 'immigrant', 'Einwanderer-Name'
        UNKNOWN = 'unknown', 'Alternativer Name'

    individual = models.ForeignKey(
        'Individual', 
        on_delete=models.CASCADE, 
        related_name='alternative_names'
    )
    given_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Vorname")
    surname = models.CharField(max_length=100, blank=True, null=True, verbose_name="Nachname")
    
    # Speichert den GEDCOM-Typ (z.B. married, aka)
    name_type = models.CharField(
        max_length=20, 
        choices=NameType.choices, 
        default=NameType.MARRIED,
        verbose_name="Namens-Typ"
    )

    class Meta:
        verbose_name = "Alternativer Name"
        verbose_name_plural = "Alternative Namen"

    def __str__(self):
        type_display = self.get_name_type_display()
        return f"{self.given_name or ''} {self.surname or ''} ({type_display})".strip()
    

@receiver(post_delete, sender=MediaObject)
def auto_delete_file_on_delete(sender, instance, **kwargs):
    """
    Löscht die physischen Dateien vom Server, wenn das MediaObject
    (z.B. durch das Löschen eines Stammbaums) aus der Datenbank entfernt wird.
    """
    for field in (instance.file, instance.thumb_mini, instance.thumb_small):
        if field and field.name and os.path.isfile(field.path):
            os.remove(field.path)
