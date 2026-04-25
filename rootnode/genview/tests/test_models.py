# genview/tests/test_models.py
import datetime

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from genview.models import Individual, Family, ChildFamilyLink, Event


class IndividualModelTest(TestCase):
    def test_full_name_builds_correctly(self):
        ind = Individual.objects.create(
            gedcom_id="@I1@", given_name="John", surname="Doe", name_prefix="Dr."
        )
        assert ind.full_name() == "Dr. John Doe"

    def test_age_calculation(self):
        # Geburt 1990‑01‑01, Tod 2020‑06‑15
        ind = Individual.objects.create(
            gedcom_id="@I2@", given_name="Anna", surname="Miller"
        )
        birth = Event.objects.create(
            event_type=Event.EventType.BIRTH,
            individual=ind,
            parsed_date=datetime.date(1990, 1, 1),
        )
        death = Event.objects.create(
            event_type=Event.EventType.DEATH,
            individual=ind,
            parsed_date=datetime.date(2020, 6, 15),
        )
        assert ind.age == 30   # 2020‑01‑01 bis 2020‑06‑15 → 30 Jahre


class FamilyMPTTTest(TestCase):
    def test_parent_child_relationship(self):
        root = Family.objects.create(gedcom_id="@F0@")
        child = Family.objects.create(gedcom_id="@F1@", parent=root)

        # MPTT‑Helper‑Methoden
        assert child.get_ancestors().first() == root
        assert list(root.get_descendants()) == [child]


class ChildFamilyLinkTest(TestCase):
    def test_unique_together_constraint(self):
        ind = Individual.objects.create(gedcom_id="@I3@", given_name="Luke")
        fam = Family.objects.create(gedcom_id="@F2@")
        link, created = ChildFamilyLink.objects.get_or_create(
            child=ind, family=fam, defaults={"relationship_type": "B"}
        )
        assert created is True

        # ein zweiter Versuch muss einen IntegrityError/ValidationError auslösen
        with self.assertRaises(ValidationError):
            dup = ChildFamilyLink(
                child=ind, family=fam, relationship_type="A"
            )
            dup.full_clean()          # prüft unique_together
            