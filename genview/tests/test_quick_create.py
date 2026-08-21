from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.models import Place, Source, Tree, TreeMembership


class QuickCreateAPITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Quick Create Tree")
        self.other_tree = Tree.objects.create(name="Other Tree")
        self.editor = User.objects.create_user(username="editor", password="password")
        self.viewer = User.objects.create_user(username="viewer", password="password")
        self.tree_admin = User.objects.create_user(username="tree_admin", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        TreeMembership.objects.create(
            user=self.viewer, gedcom_tree=self.tree, role="VIEWER"
        )
        TreeMembership.objects.create(
            user=self.tree_admin, gedcom_tree=self.tree, role="ADMIN"
        )
        self.source_url = reverse(
            "genview:api-create-source", kwargs={"tree_id": self.tree.pk}
        )
        self.place_url = reverse(
            "genview:api-create-place", kwargs={"tree_id": self.tree.pk}
        )

    def test_editor_creates_source(self):
        self.client.login(username="editor", password="password")
        response = self.client.post(
            self.source_url,
            {
                "title": "Kirchenbuch Herford",
                "author": "Pfarramt",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        source = Source.objects.get(pk=payload["id"])
        self.assertEqual(source.gedcom_tree_id, self.tree.pk)
        self.assertEqual(source.title, "Kirchenbuch Herford")
        self.assertEqual(source.author, "Pfarramt")
        self.assertIn("Kirchenbuch Herford", payload["text"])
        self.assertIn(source.gedcom_id, payload["text"])

    def test_source_requires_title(self):
        self.client.login(username="editor", password="password")
        response = self.client.post(
            self.source_url,
            {"title": "   "},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json()["errors"])
        self.assertEqual(Source.objects.count(), 0)

    def test_editor_creates_place(self):
        self.client.login(username="editor", password="password")
        response = self.client.post(
            self.place_url,
            {"name": "  Berlin, Deutschland  ", "latitude": "52.52"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        place = Place.objects.get(pk=payload["id"])
        self.assertEqual(place.name, "Berlin, Deutschland")
        self.assertEqual(place.gedcom_tree_id, self.tree.pk)
        self.assertEqual(payload["text"], place.name)

    def test_duplicate_place_returns_existing(self):
        existing = Place.objects.create(
            gedcom_tree=self.tree, name="Herford, NRW, Deutschland"
        )
        self.client.login(username="editor", password="password")
        response = self.client.post(
            self.place_url,
            {"name": "Herford, NRW, Deutschland"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["existing"])
        self.assertEqual(payload["id"], existing.pk)
        self.assertEqual(Place.objects.filter(gedcom_tree=self.tree).count(), 1)

    def test_viewer_cannot_create_source(self):
        self.client.login(username="viewer", password="password")
        response = self.client.post(
            self.source_url,
            {"title": "Geheim"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Source.objects.count(), 0)

    def test_viewer_cannot_create_place(self):
        self.client.login(username="viewer", password="password")
        response = self.client.post(
            self.place_url,
            {"name": "Geheimort"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Place.objects.count(), 0)

    def test_cannot_create_source_on_other_tree(self):
        self.client.login(username="editor", password="password")
        url = reverse(
            "genview:api-create-source", kwargs={"tree_id": self.other_tree.pk}
        )
        response = self.client.post(
            url,
            {"title": "Fremder Baum"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(Source.objects.count(), 0)

    def test_event_form_includes_quick_create_controls(self):
        self.client.login(username="tree_admin", password="password")
        url = reverse(
            "genview:event-create-person", kwargs={"tree_id": self.tree.pk}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Neue Quelle")
        self.assertContains(response, "Neuer Ort")
        self.assertContains(response, 'id="sourceQuickCreateModal"')
        self.assertContains(response, 'id="placeQuickCreateModal"')
        self.assertContains(response, reverse(
            "genview:api-create-source", kwargs={"tree_id": self.tree.pk}
        ))
        self.assertContains(response, reverse(
            "genview:api-create-place", kwargs={"tree_id": self.tree.pk}
        ))
