# genview/tests.py
from django.urls import reverse
from django.test import TestCase
from genview.models import Individual

class IndividualSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Individual.objects.create(gedcom_id="@I1@", given_name="John", surname="Doe", sex=Individual.Sex.MALE)
        Individual.objects.create(gedcom_id="@I2@", given_name="Ada", surname="Lovelace", sex=Individual.Sex.FEMALE)

    def test_basic_search_by_name(self):
        url = reverse('genview:individual-search')
        response = self.client.get(url, {"q": "Ada"})
        self.assertContains(response, "Ada Lovelace")
        self.assertNotContains(response, "John Doe")

    def test_search_by_gedcom_id(self):
        url = reverse('genview:individual-search')
        response = self.client.get(url, {"q": "@I1@"})
        self.assertContains(response, "John Doe")
        self.assertNotContains(response, "Ada Lovelace")

    def test_multiple_terms(self):
        url = reverse('genview:individual-search')
        response = self.client.get(url, {"q": "John Doe"})
        self.assertContains(response, "John Doe")