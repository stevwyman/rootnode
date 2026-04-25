# genview/tests/test_import.py
import os
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from genview.models import Individual, Family, ChildFamilyLink


TEST_GEDCOM = Path(__file__).parent / "sample.ged"


class ImportGedcomCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Schreibe eine minimale GEDCOM‑Beispieldatei, die das Command parsen kann.
        # (Nur die Tags, die unser Command verarbeitet)
        sample = """0 @I1@ INDI
1 NAME John /Doe/
1 SEX M
0 @I2@ INDI
1 NAME Anna /Miller/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 @I3@ INDI
1 NAME Luke /Smith/
1 SEX M
"""
        TEST_GEDCOM.write_text(sample, encoding="utf-8")

    @override_settings(MEDIA_ROOT=os.devnull)  # kein echtes Media‑Verzeichnis nötig
    def test_import_creates_records(self):
        # Aufruf des Commands
        call_command("import_gedcom", str(TEST_GEDCOM))

        # Erwartete Objekte prüfen
        self.assertEqual(Individual.objects.count(), 3)
        self.assertEqual(Family.objects.count(), 1)

        fam = Family.objects.get(gedcom_id="@F1@")
        self.assertEqual(fam.husband.given_name, "John")
        self.assertEqual(fam.wife.given_name, "Anna")

        # Kinder‑Link prüfen
        child_link = ChildFamilyLink.objects.get()
        self.assertEqual(child_link.child.given_name, "Luke")
        self.assertEqual(child_link.family, fam)
        