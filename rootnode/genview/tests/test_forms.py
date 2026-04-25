# genview/tests.py
from django.test import TestCase
from genview.models import Individual, Event
from genview.forms import IndividualForm
from datetime import date

class IndividualFormTests(TestCase):
    def test_create_with_birth_and_death(self):
        data = {
            "gedcom_id": "@I5@",
            "given_name": "Ada",
            "surname": "Lovelace",
            "sex": Individual.Sex.FEMALE,
            "notes": "",
            "sources": [],
            "birth_date_raw": "12 JAN 1815",
            "birth_date_parsed": "1815-01-12",
            "death_date_raw": "27 NOV 1852",
            "death_date_parsed": "1852-11-27",
        }
        form = IndividualForm(data=data)
        self.assertTrue(form.is_valid())
        person = form.save()
        self.assertIsNotNone(person.pk)

        # Prüfen, ob die Events angelegt wurden
        self.assertTrue(Event.objects.filter(
            individual=person,
            event_type=Event.EventType.BIRTH,
            raw_date="12 JAN 1815",
            parsed_date=date(1815, 1, 12)
        ).exists())

        self.assertTrue(Event.objects.filter(
            individual=person,
            event_type=Event.EventType.DEATH,
            raw_date="27 NOV 1852",
            parsed_date=date(1852, 11, 27)
        ).exists())