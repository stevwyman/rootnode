# genview/management/commands/import_gedcom.py
import os
import re

from datetime import date
from dateutil import parser as dt_parser
from dateutil.parser import ParserError

from django.core.management.base import BaseCommand
from django.db import transaction

from typing import Optional

# Gedcom‑Parser aus dem Paket "python-gedcom"
from gedcom.parser import Parser
from gedcom.element.individual import IndividualElement
from gedcom.element.family import FamilyElement

# ----------------------------------------------------------------------
# Modelle aus deiner App "genview"
# ----------------------------------------------------------------------
from genview.models import (
    Individual,
    Family,
    ChildFamilyLink,
    Source,
    Event,
    MediaObject,
)

GEDCOM_DATE_PREFIXES = ("ABT", "CAL", "EST", "INT", "BET", "TO", "AND")

def _clean_gedcom_date(raw: str) -> str:
    """
    Entfernt GEDCOM‑Präfixe und mögliche Klammer‑Kommentare,
    sodass ein reiner Datum‑String entsteht,
    den dateutil gut parsen kann.
    """
    # Beispiel‑Eingaben:
    #   "ABT 1900"
    #   "BET 1900 AND 1910"
    #   "INT 12 JAN 1885 (Some remark)"
    #   "1 JAN 1900"
    #   "1900"
    # Wir behalten nur den ersten sinnvollen Teil.
    raw = raw.strip()

    # 1️⃣ Klammer­inhalte (Kommentare) entfernen
    raw = re.sub(r"\(.*?\)", "", raw).strip()

    # 2️⃣ Präfix‑Wort entfernen, falls vorhanden
    parts = raw.split()
    if parts and parts[0] in GEDCOM_DATE_PREFIXES:
        parts = parts[1:]
    # Für „BET … AND …“ nehmen wir nur das **erste** Datum
    if parts and parts[0] == "BET":
        parts = parts[1:]          # das eigentliche Start‑Datum
    # Wenn das Wort „AND“ noch drin ist, abschneiden
    if "AND" in parts:
        idx = parts.index("AND")
        parts = parts[:idx]

    return " ".join(parts)


def _parse_gedcom_to_date(raw: str) -> date | None:
    """
    Versucht, einen GEDCOM‑Datum‑String in ein datetime.date zu konvertieren.
    Gibt ``None`` zurück, wenn das Parsing fehlschlägt.
    """
    if not raw:
        return None

    cleaned = _clean_gedcom_date(raw)

    # Einige GEDCOM‑Einträge haben nur ein Jahr (z. B. „1900“)
    # dateutil kann das parsen, wir geben dann den 1. Januar zurück.
    try:
        dt = dt_parser.parse(cleaned, fuzzy=False, dayfirst=False)
        return dt.date()
    except (ParserError, ValueError):
        # Wenn dateutil es nicht versteht, fallback zu einfachen Heuristik:
        # - ein 4‑stelliger Jahreswert
        # - ein 2‑stelliger Monat + Jahr (z. B. „JAN 1900“)
        simple_year = re.fullmatch(r"\d{4}", cleaned)
        if simple_year:
            return date(int(simple_year.group()), 1, 1)

        month_year = re.fullmatch(r"([A-Za-z]{3})\s+(\d{4})", cleaned)
        if month_year:
            month_str, yr = month_year.groups()
            # Mapping von Monats‑Kurzbezeichnung zu Zahl
            months = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
                "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
                "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
            }
            month_num = months.get(month_str.upper())
            if month_num:
                return date(int(yr), month_num, 1)

    return None     # alles andere konnten wir nicht interpretieren

# ----------------------------------------------------------------------
# Kleine Hilfsfunktion (ermöglicht sauberen Code)
# ----------------------------------------------------------------------
def _get_tag_value(element, tag: str) -> Optional[str]:
    """
    Gibt den Wert des ersten Child‑Tags eines Elements zurück
    (z. B. HUSB, WIFE, CHIL).  Wenn das Tag nicht existiert,
    wird ``None`` zurückgegeben.
    """
    for child in element.get_child_elements():
        if child.get_tag() == tag:
            return child.get_value()
    return None


class Command(BaseCommand):
    """
    Usage:
        python manage.py import_gedcom /path/to/file.ged
        python manage.py import_gedcom /path/to/file.ged --skip-events
    """

    help = "Importiert eine GEDCOM‑Datei in die Django‑Datenbank"

    # ---------------------------------------------------------------
    # CLI‑Argumente
    # ---------------------------------------------------------------
    def add_arguments(self, parser):
        parser.add_argument(
            "file_path",
            type=str,
            help="Pfad zur GEDCOM‑Datei, die importiert werden soll",
        )
        parser.add_argument(
            "--skip-events",
            action="store_true",
            help="Nur Personen, Familien & Kinder importieren (Events werden übersprungen)",
        )

    # ---------------------------------------------------------------
    # Haupt‑Logik (alles in einer Datenbank‑Transaktion)
    # ---------------------------------------------------------------
    @transaction.atomic
    def handle(self, *args, **options):
        file_path = options["file_path"]
        skip_events = options["skip_events"]

        # -----------------------------------------------------------
        # 1️⃣ Datei‑Existenz prüfen
        # -----------------------------------------------------------
        if not os.path.isfile(file_path):
            self.stderr.write(self.style.ERROR(f"Datei nicht gefunden: {file_path}"))
            return

        self.stdout.write(self.style.NOTICE(f"Parse {file_path} …"))
        gedcom_parser = Parser()
        gedcom_parser.parse_file(file_path, False)
        root_elements = gedcom_parser.get_root_child_elements()

        # -----------------------------------------------------------
        # 2️⃣ Personen importieren
        # -----------------------------------------------------------
        self.stdout.write(self.style.NOTICE("Importiere Personen …"))
        for element in root_elements:
            if not isinstance(element, IndividualElement):
                continue

            gedcom_id = element.get_pointer()
            first, last = element.get_name()                 # (Vornamen, Nachname)
            sex = element.get_gender() or "U"                # M / F / U

            Individual.objects.update_or_create(
                gedcom_id=gedcom_id,
                defaults={
                    "given_name": first,
                    "surname": last,
                    "sex": sex,
                },
            )

        # -----------------------------------------------------------
        # 3️⃣ Familien + Kinder‑Links importieren
        # -----------------------------------------------------------
        self.stdout.write(self.style.NOTICE("Importiere Familien …"))
        for element in root_elements:
            if not isinstance(element, FamilyElement):
                continue

            fam_id = element.get_pointer()

            # HUSB / WIFE können fehlen → None
            husb_ged_id = _get_tag_value(element, "HUSB")
            wife_ged_id = _get_tag_value(element, "WIFE")

            husband = (
                Individual.objects.filter(gedcom_id=husb_ged_id).first()
                if husb_ged_id
                else None
            )
            wife = (
                Individual.objects.filter(gedcom_id=wife_ged_id).first()
                if wife_ged_id
                else None
            )

            family, _ = Family.objects.update_or_create(
                gedcom_id=fam_id,
                defaults={"husband": husband, "wife": wife},
            )

            # -------------------------------------------------------
            # Kinder (CHIL) über das Through‑Model verbinden
            # -------------------------------------------------------
            for child_elem in element.get_child_elements():
                if child_elem.get_tag() != "CHIL":
                    continue
                child_ged_id = child_elem.get_value()
                child = Individual.objects.filter(gedcom_id=child_ged_id).first()
                if child:
                    ChildFamilyLink.objects.update_or_create(
                        child=child,
                        family=family,
                        defaults={"relationship_type": "B"},   # default: biologisch
                    )

        # -----------------------------------------------------------
        # 4️⃣ (Optional) Events importieren
        # -----------------------------------------------------------
        if not skip_events:
            self._import_events(root_elements)

        self.stdout.write(self.style.SUCCESS("GEDCOM‑Import erfolgreich abgeschlossen!"))

    # -------------------------------------------------------------------
    # Private Helper: Events (kann später ausgebaut werden)
    # -------------------------------------------------------------------
    # In der Methode _import_events (innerhalb von Command)
    def _import_events(self, root_elements):
        self.stdout.write(self.style.NOTICE("Importiere Events …"))
        for element in root_elements:
            # ----------- Individual‑Events ----------
            if isinstance(element, IndividualElement):
                indiv = Individual.objects.filter(gedcom_id=element.get_pointer()).first()
                if not indiv:
                    continue

                for ev in element.get_child_elements():
                    tag = ev.get_tag()
                    if tag not in {
                        "BIRT", "DEAT", "CHR", "OCCU", "RELI",
                        "BURI", "MARR", "DIV", "ENGA",
                    }:
                        continue

                    # Sammle Daten aus den Unter‑Elementen
                    data = {}
                    for sub in ev.get_child_elements():
                        sub_tag = sub.get_tag()
                        if sub_tag == "DATE":
                            raw = sub.get_value()
                            data["raw_date"] = raw
                            parsed = _parse_gedcom_to_date(raw)
                            if parsed:
                                data["parsed_date"] = parsed
                        elif sub_tag == "PLAC":
                            data["place"] = sub.get_value()
                        elif sub_tag == "NOTE":
                            data.setdefault("description", "")
                            data["description"] += sub.get_value() + "\n"

                    Event.objects.update_or_create(
                        event_type=tag,
                        individual=indiv,
                        defaults=data,
                    )

            # ----------- Family‑Events ----------
            if isinstance(element, FamilyElement):
                fam = Family.objects.filter(gedcom_id=element.get_pointer()).first()
                if not fam:
                    continue

                for ev in element.get_child_elements():
                    tag = ev.get_tag()
                    if tag not in {"MARR", "DIV"}:
                        continue

                    data = {}
                    for sub in ev.get_child_elements():
                        sub_tag = sub.get_tag()
                        if sub_tag == "DATE":
                            raw = sub.get_value()
                            data["raw_date"] = raw
                            parsed = _parse_gedcom_to_date(raw)
                            if parsed:
                                data["parsed_date"] = parsed
                        elif sub_tag == "PLAC":
                            data["place"] = sub.get_value()
                        elif sub_tag == "NOTE":
                            data.setdefault("description", "")
                            data["description"] += sub.get_value() + "\n"

                    Event.objects.update_or_create(
                        event_type=tag,
                        family=fam,
                        defaults=data,
                    )
                        