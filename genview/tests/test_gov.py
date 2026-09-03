from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.gov import (
    date_to_julian_day,
    name_at,
    name_history,
    search_places,
    summarize_object,
)
from genview.models import Event, EventType, Individual, Place, Tree, TreeMembership
from genview.utils import merge_multiple_places

SAMPLE_GOV = {
    "id": "KONNAUJO93RX",
    "name": [
        {"value": "Königlich Blumenau", "lang": "deu", "endYear": 1931},
        {"value": "Königsblumenau", "lang": "deu", "beginYear": 1931},
        {"value": "Kwietniewo", "lang": "pol"},
    ],
    "position": {"lat": 53.9667, "lon": 19.45},
}


class GovParserTests(TestCase):
    def test_name_at_uses_german_name_for_historical_date(self):
        self.assertEqual(
            name_at(SAMPLE_GOV, date(1920, 6, 1), language="deu"),
            "Königlich Blumenau",
        )
        self.assertEqual(
            name_at(SAMPLE_GOV, date(1940, 1, 1), language="deu"),
            "Königsblumenau",
        )

    def test_name_at_prefers_requested_language(self):
        self.assertEqual(
            name_at(SAMPLE_GOV, date(1940, 1, 1), language="pol"),
            "Kwietniewo",
        )

    def test_name_history_includes_language_and_years(self):
        history = name_history(SAMPLE_GOV)
        values = [entry["value"] for entry in history]
        self.assertEqual(values, ["Königlich Blumenau", "Königsblumenau", "Kwietniewo"])
        first = history[0]
        self.assertEqual(first["lang_label"], "Deutsch")
        self.assertEqual(first["valid_to"], date(1931, 12, 31))

    def test_julian_roundtrip(self):
        day = date(2000, 1, 1)
        self.assertEqual(date_to_julian_day(day), 2451545)

    def test_summarize_object(self):
        summary = summarize_object(SAMPLE_GOV)
        self.assertEqual(summary["id"], "KONNAUJO93RX")
        self.assertIn("Kwietniewo", summary["names"])
        self.assertAlmostEqual(summary["lat"], 53.9667)


class PlaceGovModelTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="GOV Tree")
        self.place = Place.objects.create(
            gedcom_tree=self.tree,
            name="Königlich Neukirch, Westpreußen",
        )

    def test_name_at_falls_back_to_short_name(self):
        self.assertEqual(self.place.name_at(date(1880, 1, 1)), "Königlich Neukirch")

    def test_apply_payload_caches_names_and_coords(self):
        self.place.apply_gov_payload(SAMPLE_GOV)
        self.place.save()
        self.place.refresh_from_db()
        self.assertEqual(self.place.gov_id, "KONNAUJO93RX")
        self.assertEqual(self.place.name_at(date(1920, 1, 1)), "Königlich Blumenau")
        self.assertAlmostEqual(float(self.place.latitude), 53.9667, places=4)
        self.assertIsNotNone(self.place.gov_synced_at)

    def test_apply_payload_does_not_overwrite_existing_coords(self):
        self.place.latitude = "52.520000"
        self.place.longitude = "13.405000"
        self.place.apply_gov_payload(SAMPLE_GOV)
        self.assertEqual(str(self.place.latitude), "52.520000")

    def test_event_place_name_uses_event_date(self):
        self.place.apply_gov_payload(SAMPLE_GOV)
        self.place.save()
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Anna", surname="Test"
        )
        event_type, _ = EventType.objects.get_or_create(
            tag="BIRT", defaults={"name": "Geburt"}
        )
        event = Event.objects.create(
            gedcom_tree=self.tree,
            event_type=event_type,
            individual=person,
            place=self.place,
            parsed_date=date(1920, 5, 1),
        )
        self.assertEqual(event.place_name, "Königlich Blumenau")

    def test_merge_copies_gov_id_onto_master(self):
        master = Place.objects.create(gedcom_tree=self.tree, name="Master")
        duplicate = Place.objects.create(
            gedcom_tree=self.tree,
            name="Duplicate",
            gov_id="KONNAUJO93RX",
            gov_data=SAMPLE_GOV,
        )
        merge_multiple_places(master, [duplicate])
        master.refresh_from_db()
        self.assertEqual(master.gov_id, "KONNAUJO93RX")
        self.assertFalse(Place.objects.filter(pk=duplicate.pk).exists())


class PlaceGovViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="GOV Tree")
        self.editor = User.objects.create_user(username="editor", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        self.place = Place.objects.create(
            gedcom_tree=self.tree,
            name="Königlich Blumenau, Westpreußen",
        )
        self.client.login(username="editor", password="password")

    @patch("genview.views.search_places")
    def test_search_returns_json_hits(self, mock_search):
        mock_search.return_value = [summarize_object(SAMPLE_GOV)]
        url = reverse("genview:place-gov-search", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url, {"q": "Blumenau"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["results"][0]["id"], "KONNAUJO93RX")
        mock_search.assert_called_once_with("Blumenau")

    @patch("genview.views.fetch_object", return_value=SAMPLE_GOV)
    def test_link_caches_gov_payload(self, mock_fetch):
        url = reverse(
            "genview:place-gov-link",
            kwargs={"tree_id": self.tree.pk, "pk": self.place.pk},
        )
        response = self.client.post(
            url,
            {"gov_id": "KONNAUJO93RX"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.place.refresh_from_db()
        self.assertEqual(self.place.gov_id, "KONNAUJO93RX")
        self.assertEqual(self.place.name_at(date(1920, 1, 1)), "Königlich Blumenau")
        self.assertIsNotNone(self.place.latitude)
        mock_fetch.assert_called_once_with("KONNAUJO93RX")

    @patch("genview.views.fetch_object", return_value=SAMPLE_GOV)
    def test_unlink_clears_gov_fields(self, mock_fetch):
        self.place.apply_gov_payload(SAMPLE_GOV)
        self.place.save()
        url = reverse(
            "genview:place-gov-unlink",
            kwargs={"tree_id": self.tree.pk, "pk": self.place.pk},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.place.refresh_from_db()
        self.assertEqual(self.place.gov_id, "")
        self.assertIsNone(self.place.gov_data)

    def test_place_detail_shows_gov_card(self):
        self.place.apply_gov_payload(SAMPLE_GOV)
        self.place.save()
        url = reverse(
            "genview:place-detail",
            kwargs={"tree_id": self.tree.pk, "pk": self.place.pk},
        )
        response = self.client.get(url)
        self.assertContains(response, "KONNAUJO93RX")
        self.assertContains(response, "Königlich Blumenau")
        self.assertContains(response, "Kwietniewo")

    def test_search_places_direct_id_uses_fetch(self):
        with patch("genview.gov.fetch_object", return_value=SAMPLE_GOV) as mock_fetch:
            with patch("genview.gov._request_json", return_value=[]):
                results = search_places("KONNAUJO93RX")
        self.assertEqual(results[0]["id"], "KONNAUJO93RX")
        mock_fetch.assert_called()
