# genview/services/place_merge.py
from django.db import transaction
from typing import Iterable
from ..models import Place

def merge_places(master: Place, duplicates: Iterable[Place]) -> None:
    """
    Alle FK‑Beziehungen von den `duplicates` werden auf `master` umgeleitet,
    danach werden die doppelten Place‑Datensätze gelöscht.
    Die Funktion ist **transaktional** – bei einem Fehler wird alles zurückgerollt.
    """
    if master in duplicates:
        raise ValueError("Der Master‑Place darf nicht zu den Duplikaten gehören.")

    # ---------- Welche Modelle referenzieren Place? ----------
    # Passe diese Liste an dein tatsächliches Datenmodell an.
    related = [
        # (Model‑Name, FK‑Feld‑Name)
        ("event", "place"),                     # Event.place (FK)
        ("individual", "birth_place"),          # Individual.birth_place
        ("individual", "death_place"),          # Individual.death_place
        # ("source", "place"),                  # falls vorhanden
        # weitere Beziehungen hier ergänzen …
    ]

    with transaction.atomic():
        # 1. Fremdschlüssel‑Beziehungen umleiten
        for related_model, fk_name in related:
            # Get the related manager dynamically
            # Beispiel: Event.objects.filter(place__in=duplicates)
            Model = master._meta.apps.get_model("genview", related_model.title())
            qs = Model.objects.filter(**{f"{fk_name}__in": [p.id for p in duplicates]})
            qs.update(**{fk_name: master})

        # 2. Koordinaten ggf. übernehmen, falls der Master keine hat
        if not master.latitude or not master.longitude:
            for dup in duplicates:
                if dup.latitude and dup.longitude:
                    master.latitude = dup.latitude
                    master.longitude = dup.longitude
                    break
            master.save(update_fields=["latitude", "longitude"])

        # 3. Duplikate entfernen
        duplicate_ids = [p.id for p in duplicates]
        Place.objects.filter(id__in=duplicate_ids).delete()