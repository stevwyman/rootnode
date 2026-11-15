from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.forms import EventForm, FamilyForm, IndividualForm, synced_raw_date
from genview.models import Event, EventType, Family, Individual, Place, Tree, TreeMembership


def _etype(tag, name, category=EventType.Category.INDIVIDUAL):
    et, _ = EventType.objects.get_or_create(
        tag=tag, defaults={"name": name, "category": category}
    )
    return et


class SyncedRawDateTests(TestCase):
    def test_fills_raw_when_parsed_is_set_and_raw_empty(self):
        self.assertEqual(synced_raw_date(date(1885, 1, 12), ""), "12 JAN 1885")

    def test_keeps_approximate_raw_if_parsed_unchanged(self):
        self.assertEqual(
            synced_raw_date(date(1900, 1, 1), "ABT 1900", date(1900, 1, 1)),
            "ABT 1900",
        )

    def test_rewrites_raw_when_parsed_changes(self):
        self.assertEqual(
            synced_raw_date(date(1900, 6, 15), "ABT 1900", date(1900, 1, 1)),
            "15 JUN 1900",
        )


class IndividualVitalPlaceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Person Form Tree")
        self.editor = User.objects.create_user("person_editor", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role=TreeMembership.Role.EDITOR
        )
        self.berlin = Place.objects.create(gedcom_tree=self.tree, name="Berlin")
        self.hamburg = Place.objects.create(gedcom_tree=self.tree, name="Hamburg")
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Ada", surname="Lovelace"
        )
        self.client.login(username="person_editor", password="password")
        _etype("BIRT", "Birth")
        _etype("DEAT", "Death")

    def test_edit_form_shows_place_search(self):
        url = reverse(
            "genview:individual-edit",
            kwargs={"tree_id": self.tree.pk, "pk": self.person.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Geburtsort")
        self.assertContains(response, "Sterbeort")
        self.assertContains(response, "select2-birth-place")
        self.assertContains(response, "select2-death-place")
        self.assertContains(response, "api/search/places")
        self.assertContains(response, "placeQuickCreateModal")

    def test_create_person_saves_birth_place(self):
        url = reverse("genview:individual-add", kwargs={"tree_id": self.tree.pk})
        response = self.client.post(
            url,
            {
                "given_name": "Alan",
                "surname": "Turing",
                "sex": "M",
                "birth_date_parsed": "1912-06-23",
                "birth_place": str(self.berlin.pk),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        person = Individual.objects.get(gedcom_tree=self.tree, given_name="Alan")
        birth = person.birth_event
        self.assertIsNotNone(birth)
        self.assertEqual(birth.place_id, self.berlin.pk)
        self.assertEqual(birth.raw_date, "23 JUN 1912")
        self.assertEqual(birth.parsed_date, date(1912, 6, 23))

    def test_update_saves_death_place_and_syncs_raw_date(self):
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.person,
            event_type=_etype("DEAT", "Death"),
            raw_date="ABT 1852",
            parsed_date=date(1852, 1, 1),
        )
        url = reverse(
            "genview:individual-edit",
            kwargs={"tree_id": self.tree.pk, "pk": self.person.pk},
        )
        response = self.client.post(
            url,
            {
                "given_name": "Ada",
                "surname": "Lovelace",
                "sex": "U",
                "death_date_raw": "ABT 1852",
                "death_date_parsed": "1852-11-27",
                "death_place": str(self.hamburg.pk),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        death = Individual.objects.get(pk=self.person.pk).death_event
        self.assertEqual(death.place_id, self.hamburg.pk)
        self.assertEqual(death.parsed_date, date(1852, 11, 27))
        self.assertEqual(death.raw_date, "27 NOV 1852")


class EventAndFamilyDateSyncTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Date Sync Tree")
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Test", surname="Person"
        )
        self.family = Family.objects.create(gedcom_tree=self.tree)
        self.place = Place.objects.create(gedcom_tree=self.tree, name="London")

    def test_event_form_rewrites_raw_when_parsed_changes(self):
        event = Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.person,
            event_type=_etype("BIRT", "Birth"),
            raw_date="ABT 1800",
            parsed_date=date(1800, 1, 1),
        )
        form = EventForm(
            data={
                "event_type": event.event_type_id,
                "individual": self.person.pk,
                "raw_date": "ABT 1800",
                "parsed_date": "1800-05-04",
                "place": "",
                "description": "",
            },
            instance=event,
            tree_id=self.tree.pk,
            target_type="individual",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["raw_date"], "4 MAY 1800")

    def test_family_form_rewrites_marriage_raw_when_parsed_changes(self):
        Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=_etype("MARR", "Marriage", EventType.Category.FAMILY),
            raw_date="ABT 1890",
            parsed_date=date(1890, 1, 1),
        )
        form = FamilyForm(
            data={
                "gedcom_id": "",
                "notes": "",
                "marriage_raw_date": "ABT 1890",
                "marriage_parsed_date": "1890-06-15",
                "marriage_place": str(self.place.pk),
            },
            instance=self.family,
            tree_id=self.tree.pk,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["marriage_raw_date"], "15 JUN 1890")
        family = form.save()
        marr = family.marriage_event
        self.assertEqual(marr.place_id, self.place.pk)
        self.assertEqual(marr.raw_date, "15 JUN 1890")
