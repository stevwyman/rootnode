# genview/models.py
from __future__ import annotations

from datetime import date
from typing import Optional

from django.db import models
from django.core.exceptions import ValidationError
from django.urls import reverse
from mptt.models import MPTTModel, TreeForeignKey


# ----------------------------------------------------------------------
# 1️⃣ Helper‑Mixin – überall wo ein GEDCOM‑ID‑Feld nötig ist
# ----------------------------------------------------------------------
class GedcomIdMixin(models.Model):
    """
    Gemeinsames Feld für alle GEDCOM‑Objekte, die eine externe
    GEDCOM‑Referenz besitzen (z. B. @I1@, @F2@, @S3@ …).
    """
    gedcom_id = models.CharField(
        max_length=20,
        unique=True,
        help_text="GEDCOM‑Referenz, z. B. @I1@, @F2@ …",
    )

    class Meta:
        abstract = True


# ----------------------------------------------------------------------
# 2️⃣ SOURCE / REPOSITORY
# ----------------------------------------------------------------------
class Source(models.Model):
    """Quelle (SOUR) oder Repository (REPO)."""
    gedcom_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="z. B. @S1@",
        db_index=True,
    )
    title = models.CharField(max_length=255, help_text="Titel / Kurzbeschreibung")
    author = models.CharField(max_length=255, blank=True)
    publication_facts = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["title"])]

    def __str__(self) -> str:
        return self.title


# ----------------------------------------------------------------------
# 3️⃣ INDIVIDUAL (Person)
# ----------------------------------------------------------------------
class Individual(GedcomIdMixin):
    """GEDCOM‑Person (INDI)."""

    class Sex(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        UNKNOWN = "U", "Unknown"

    given_name = models.CharField(max_length=150, blank=True)
    surname = models.CharField(max_length=150, blank=True)
    name_prefix = models.CharField(max_length=50, blank=True,
                                   help_text="z. B. Dr., Sir")
    name_suffix = models.CharField(max_length=50, blank=True,
                                   help_text="z. B. Jr., III")
    sex = models.CharField(max_length=1,
                           choices=Sex.choices,
                           default=Sex.UNKNOWN)

    notes = models.TextField(blank=True)

    # Quellen, in denen diese Person auftaucht
    sources = models.ManyToManyField(
        Source,
        blank=True,
        related_name="individuals",
    )

    class Meta:
        ordering = ["surname", "given_name"]
        indexes = [
            models.Index(fields=["surname", "given_name"]),
            models.Index(fields=["sex"]),
        ]

    # ------------------------------------------------------------------
    # Helper‑Methoden
    # ------------------------------------------------------------------
    def __str__(self) -> str:
        return f"{self.full_name()} ({self.gedcom_id})"

    def full_name(self) -> str:
        """Vollständiger Name inkl. Prä‑ und Suffix, leere Teile werden weggelassen."""
        parts = [self.name_prefix,
                 self.given_name,
                 self.surname,
                 self.name_suffix]
        return " ".join(p for p in parts if p).strip() or "Unnamed"
    
    def get_absolute_url(self):
        return reverse("genview:individual-detail", kwargs={"pk": self.pk})

    # --------------------------------------------------------------
    # ★★ Neu: Convenience‑Properties für Geburts‑ und Sterbedatum ★★
    # --------------------------------------------------------------
    @property
    def birth_event(self) -> Optional["Event"]:
        """
        Liefert das zugehörige ``BIRT``‑Event (oder ``None``).
        Wir nutzen das bereits vorgefertigte ``related_name='events'``
        des ``Event``‑Modells.
        """
        # `events` ist ein RelatedManager; ``filter`` gibt ein QuerySet zurück.
        # Wir holen das **erste** passende Event (es sollte nur eines geben).
        return self.events.filter(event_type=Event.EventType.BIRTH).first()

    @property
    def death_event(self) -> Optional["Event"]:
        """Liefert das zugehörige ``DEAT``‑Event (oder ``None``)."""
        return self.events.filter(event_type=Event.EventType.DEATH).first()

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
        """True, wenn ein ``DEAT``‑Event vorhanden ist."""
        return self.death_event is not None

    @property
    def age(self) -> Optional[int]:
        """
        Berechnet das aktuelle Alter (oder das Alter zum Tod) anhand
        der vorhandenen Geburts‑ und Sterbedaten.
        Gibt ``None`` zurück, wenn kein Geburtsdatum vorhanden ist.
        """
        if not self.birth_date:
            return None

        # Wenn ein Sterbedatum existiert, verwenden wir das als End‑Datum,
        # sonst das heutige Datum.
        end = self.death_date or date.today()

        # Altersberechnung (Jahre) – berücksichtigt, ob der Geburtstag im
        # aktuellen Jahr schon vorbei war.
        years = end.year - self.birth_date.year
        if (end.month, end.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years

    # --------------------------------------------------------------
    # Optional: Hilfsmethoden, um das Roh‑Datum ebenfalls leicht zu holen
    # --------------------------------------------------------------
    @property
    def birth_date_raw(self) -> Optional[str]:
        """Der ungeparste GEDCOM‑Datums‑String vom BIRT‑Event."""
        ev = self.birth_event
        return ev.raw_date if ev else None

    @property
    def death_date_raw(self) -> Optional[str]:
        """Der ungeparste GEDCOM‑Datums‑String vom DEAT‑Event."""
        ev = self.death_event
        return ev.raw_date if ev else None


# ----------------------------------------------------------------------
# 4️⃣ FAMILY – MPTT‑Baumstruktur
# ----------------------------------------------------------------------
class Family(MPTTModel, GedcomIdMixin):
    """Familie (FAM). Durch MPTT kann eine Familie Unter‑Familien besitzen."""

    class Meta:
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

    # MPTT‑Hierarchie (z. B. Adoptiv‑/Stief‑Familien)
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

    def __str__(self) -> str:
        husb = self.husband.surname if self.husband else "?"
        wife = self.wife.surname if self.wife else "?"
        return f"Family {husb} / {wife} ({self.gedcom_id})"
    
    def get_absolute_url(self):
        return reverse("genview:family_detail", kwargs={"pk": self.pk})

    # ------------------------------------------------------------------
    # Convenience‑Methode – liefert ein QuerySet aller Kinder‑Individuals
    # ------------------------------------------------------------------
    def children_links(self) -> models.QuerySet["Individual"]:
        """
        Alle Kinder, die über das Through‑Model ``ChildFamilyLink`` dieser Familie
        verknüpft sind. (Kurzschreibweise: ``family.children().all()``)
        """
        return Individual.objects.filter(parental_families__family=self)
    
    # ------------------------------------------------------------------
    #  Helfer‑Property: das zugehörige MARR‑Event (falls vorhanden)
    # ------------------------------------------------------------------
    @property
    def marriage_event(self) -> Optional["Event"]:
        """
        Gibt das erste Event vom Typ MARR (Marriage) zurück
        oder ``None`` wenn die Familie kein Heirats‑Eintrag hat.
        """
        return self.events.filter(event_type=Event.EventType.MARRIAGE).first()

    # Optional: noch ein Property für den Ort (falls du es im Template
    # noch etwas kürzer schreiben willst)
    @property
    def marriage_place(self) -> str:
        ev = self.marriage_event
        return ev.place if ev else ""

    @property
    def marriage_date_raw(self) -> str:
        ev = self.marriage_event
        return ev.raw_date if ev else ""

    @property
    def marriage_date_parsed(self) -> Optional[date]:
        ev = self.marriage_event
        return ev.parsed_date if ev else None


# ----------------------------------------------------------------------
# 5️⃣ THROUGH‑MODEL: Kind‑zu‑Familie (CHIL / FAMC)
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
        related_name="parental_families",   # <‑‑ Families, in denen das Kind vorkommt
    )

    # --- 2️⃣ Die Familie, zu der das Kind gehört ----
    family = models.ForeignKey(
        Family,
        on_delete=models.CASCADE,
        related_name="children",            # <‑‑ **WICHTIG:** das ist das Feld, das wir prefetchen
    )

    relationship_type = models.CharField(
        max_length=1,
        choices=Relationship.choices,
        default=Relationship.BIOLOGICAL,
    )

    class Meta:
        unique_together = ("child", "family")
        indexes = [models.Index(fields=["child", "family"])]

    def __str__(self) -> str:
        return f"{self.child.full_name()} → {self.family}"


# ----------------------------------------------------------------------
# 6️⃣ EVENTS – können einer Person ODER einer Familie zugeordnet sein
# ----------------------------------------------------------------------
class Event(models.Model):
    """Einzel‑Event (z. B. BIRT, DEAT, MARR, DIV …)."""

    class EventType(models.TextChoices):
        # ---- Individual‑Events ----
        BIRTH = "BIRT", "Birth"
        CHRISTENING = "CHR", "Christening"
        DEATH = "DEAT", "Death"
        BURIAL = "BURI", "Burial"
        RELIGION = "RELI", "Religion"
        OCCUPATION = "OCCU", "Occupation"
        # ---- Family‑Events ----
        MARRIAGE = "MARR", "Marriage"
        DIVORCE = "DIV", "Divorce"
        ENGAGEMENT = "ENGA", "Engagement"

    event_type = models.CharField(max_length=10, choices=EventType.choices)

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
        help_text="Original GEDCOM‑Datum‑String, z. B. 'ABT 1900'",
    )
    parsed_date = models.DateField(
        null=True,
        blank=True,
        help_text="Geparstes Datum (für Sortierung/Filter)",
        db_index=True,
    )
    place = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    sources = models.ManyToManyField(
        Source,
        blank=True,
        related_name="events",
    )

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "parsed_date"]),
        ]

    def __str__(self) -> str:
        target = self.individual or self.family
        return f"{self.get_event_type_display()} – {target}"

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
        self.full_clean()      # ruft ``clean`` auf
        super().save(*args, **kwargs)


# ----------------------------------------------------------------------
# 7️⃣ MEDIA OBJECT – Bilder, PDF‑Dokumente, Links etc.
# ----------------------------------------------------------------------
class MediaObject(models.Model):
    """Multimedia‑Objekt (OBJE)."""

    gedcom_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Optional GEDCOM‑Referenz",
        db_index=True,
    )
    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(upload_to="gedcom_media/")
    description = models.TextField(blank=True)

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

    is_portrait = models.BooleanField(
        default=False,
        help_text="Dieses Bild wird als Portrait auf der Personen‑Detail‑Seite angezeigt.",
        db_index=True,
    )

    class Meta:
        ordering = ["-is_portrait", "title"]   # Portrait‑Bilder zuerst

    def __str__(self) -> str:
        return self.title or f"Media {self.id}"

    @property
    def is_image(self) -> bool:
        """True, wenn das gespeicherte File ein Bild ist – praktisch für Templates."""
        if not self.file:
            return False
        return self.file.name.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        )
    