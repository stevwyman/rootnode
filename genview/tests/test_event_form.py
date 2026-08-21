from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.models import Family, Individual, Tree, TreeMembership


class EventCreateFormHeadingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Event Form Tree")
        self.admin = User.objects.create_user(username="tree_admin", password="password")
        TreeMembership.objects.create(
            user=self.admin, gedcom_tree=self.tree, role="ADMIN"
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Clara",
            surname="Beispiel",
        )
        self.family = Family.objects.create(gedcom_tree=self.tree)
        self.client.login(username="tree_admin", password="password")

    def test_heading_shows_person_from_query_param(self):
        url = reverse("genview:event-create-person", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url, {"individual": self.person.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["person"], self.person)
        self.assertContains(response, "Clara Beispiel")

    def test_heading_shows_person_from_person_pk_url(self):
        url = reverse(
            "genview:event-add-for-person",
            kwargs={"tree_id": self.tree.pk, "person_pk": self.person.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["person"], self.person)
        self.assertContains(response, "Clara Beispiel")

    def test_heading_shows_family_from_query_param(self):
        url = reverse("genview:event-create-family", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url, {"family": self.family.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["family"], self.family)
        self.assertContains(response, self.family.gedcom_id)
